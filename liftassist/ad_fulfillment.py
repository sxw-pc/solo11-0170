"""Ad fulfillment verification: provable visible seconds, makeup duties, reversals.

A play command schedules a window and a screen ack only proves the command was
received; neither proves a single second of actual display. A camera frame proves
only its own instant. Provable display time comes only from continuous evidence:
the screen's protected frame-buffer switch log, external camera A/V segments and
retestable player counters, bounded by power state and safety preemption priority.
Whatever stays unproven is retained as a visibility gap, never interpolated.
"""
from datetime import datetime,timedelta,timezone
from .store import one,many,log

def _parse(ts):return datetime.fromisoformat(ts.replace('Z','+00:00'))
def _iso(dt):return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')

def _merge(ivs):
    out=[]
    for a,b in sorted(ivs):
        if out and a<=out[-1][1]:out[-1]=(out[-1][0],max(out[-1][1],b))
        else:out.append((a,b))
    return out

def _measure(ivs):return sum((b-a).total_seconds() for a,b in _merge(ivs))

def _clip(iv,w):
    a,b=max(iv[0],w[0]),min(iv[1],w[1])
    return (a,b) if a<b else None

def _subtract(ivs,blocks):
    out=[]
    for a,b in ivs:
        segs=[(a,b)]
        for c,d in blocks:
            nxt=[]
            for s,e in segs:
                if d<=s or c>=e:nxt.append((s,e));continue
                if s<c:nxt.append((s,c))
                if d<e:nxt.append((d,e))
            segs=nxt
        out.extend(segs)
    return out

def _intersects(xs,ys):return any(a<d and c<b for a,b in xs for c,d in ys)

def _calibrated(db,screen_id,captured_at):
    cal=one(db,'SELECT * FROM camera_calibration WHERE screen_id=? AND at<=? ORDER BY at DESC LIMIT 1',(screen_id,captured_at))
    t=_parse(captured_at)
    return t+timedelta(milliseconds=cal['offset_ms']) if cal else t

def _screen_evidence(db,screen_id,asset,window,priority):
    """Proven-visible and proven-not-visible intervals for one screen in one window.

    Precedence per instant: power-off > safety preemption (higher priority) >
    camera segments > player switch log. Camera vs player disagreement outside
    preempted/dark intervals is an evidence conflict.
    """
    w0,w1=window
    dark=[];off=None
    for p in many(db,'SELECT * FROM screen_power WHERE screen_id=? ORDER BY at',(screen_id,)):
        at=_parse(p['at'])
        if p['state']=='off':off=off if off is not None else at
        elif off is not None:dark.append((off,at));off=None
    if off is not None:dark.append((off,w1))
    preempt=[(_parse(o['starts']),_parse(o['ends'])) for o in
             many(db,'SELECT * FROM safety_override WHERE screen_id=? AND priority>?',(screen_id,priority))]
    blocked=_merge([iv for iv in (_clip(x,window) for x in dark+preempt) if iv])
    cam_h,cam_n=[],[]
    for s in many(db,'SELECT * FROM av_segment WHERE screen_id=?',(screen_id,)):
        iv=_clip((_parse(s['starts']),_parse(s['ends'])),window)
        if iv:(cam_h if s['visible_asset_hash']==asset else cam_n).append(iv)
    cam_h,cam_n=_merge(cam_h),_merge(cam_n)
    sws=many(db,'SELECT * FROM screen_switch WHERE screen_id=? ORDER BY seq',(screen_id,))
    play_h,play_n=[],[]
    for i,sw in enumerate(sws):
        nxt=sws[i+1] if i+1<len(sws) else None
        if nxt is not None and nxt['seq']!=sw['seq']+1:continue  # missing protected records: leave unproven
        end=_parse(nxt['at']) if nxt is not None else w1
        iv=_clip((_parse(sw['at']),end),window)
        if iv:(play_h if sw['to_asset_hash']==asset else play_n).append(iv)
    play_h,play_n=_merge(play_h),_merge(play_n)
    cam_he,play_he=_subtract(cam_h,blocked),_subtract(play_h,blocked)
    cam_ne,play_ne=_subtract(cam_n,blocked),_subtract(play_n,blocked)
    conflict=(_intersects(cam_he,cam_ne) or _intersects(play_he,cam_ne) or _intersects(play_ne,cam_he))
    noth=_merge(blocked+cam_n+play_n)
    h=[iv for iv in (_clip(x,window) for x in _subtract(_merge(cam_h+play_h),noth)) if iv]
    counter=0.0
    rows=many(db,'SELECT * FROM player_counter WHERE screen_id=? AND asset_hash=? ORDER BY at',(screen_id,asset))
    for a,b in zip(rows,rows[1:]):
        ta,tb=_parse(a['at']),_parse(b['at'])
        if w0<=ta and tb<=w1 and b['total_seconds']>a['total_seconds']:
            counter+=b['total_seconds']-a['total_seconds']
    return h,noth,counter,conflict

def _commands(db,campaign_id):
    return many(db,'SELECT p.* FROM play_command p JOIN display_screen s ON s.id=p.screen_id '
                   'WHERE p.campaign_id=? AND s.mirror_of IS NULL ORDER BY p.starts',(campaign_id,))

def _evaluate(db,contract):
    """Provable min/max visible seconds for a contract; mirror screens never count."""
    camp=one(db,'SELECT * FROM ad_campaign WHERE id=?',(contract['campaign_id'],))
    asset=camp['asset_hash'];cmds=_commands(db,contract['campaign_id'])
    proven=disproven=attested=counter=window_total=0.0;conflict=False
    for c in cmds:
        w=(_parse(c['starts']),_parse(c['ends']))
        window_total+=(w[1]-w[0]).total_seconds()
        h,noth,ctr,cf=_screen_evidence(db,c['screen_id'],asset,w,c['priority'])
        proven+=_measure(h);disproven+=_measure(noth);counter+=ctr;conflict=conflict or cf
        conf=one(db,'SELECT MAX(confirmed_seconds) AS m FROM playback_confirmation WHERE command_id=?',(c['id'],))['m']
        if conf:attested+=min(conf,(w[1]-w[0]).total_seconds())
    max_possible=max(0.0,window_total-disproven)
    proven_final=min(max(proven+counter,attested),max_possible)
    return asset,cmds,proven_final,max_possible,max_possible-proven_final,conflict

def _samples(db,campaign_id,asset):
    """Independent proven instants; duplicate same-second frames count once."""
    cmds=_commands(db,campaign_id);seen=set()
    rows=many(db,'SELECT f.* FROM camera_frame f JOIN display_screen s ON s.id=f.screen_id '
                 'WHERE f.visible_asset_hash=? AND s.mirror_of IS NULL',(asset,))
    for f in rows:
        eff=_calibrated(db,f['screen_id'],f['captured_at'])
        if any(c['screen_id']==f['screen_id'] and _parse(c['starts'])<=eff<=_parse(c['ends']) for c in cmds):
            seen.add((f['image_hash'],eff))
    return len(seen)

def _makeup_window(db,cmds,owed):
    if not cmds:return None,None
    latest=max(cmds,key=lambda c:c['ends'])
    start=_parse(latest['ends'])
    fires=[(_parse(o['starts']),_parse(o['ends'])) for o in
           many(db,"SELECT * FROM safety_override WHERE screen_id=? AND category='fire' ORDER BY starts",(latest['screen_id'],))]
    while True:
        end=start+timedelta(seconds=owed)
        hit=[f for f in fires if f[0]<end and start<f[1]]
        if not hit:return _iso(start),_iso(end)
        start=max(f[1] for f in hit)  # makeup may never cover a fire alert

def _settle_continuous(db,contract,state,proven,event_id,cmds):
    cid=contract['id'];g=contract['guaranteed_seconds'];rate=contract['rate_per_second_cents']
    issued=one(db,"SELECT COALESCE(SUM(seconds),0) AS s FROM ad_bill WHERE contract_id=? AND state='issued' "
                  "AND kind IN ('continuous','reversal')",(cid,))['s']
    if state=='fulfilled' and issued<g:
        db.execute("UPDATE ad_bill SET state='superseded' WHERE contract_id=? AND kind='continuous' AND state='suspended'",(cid,))
        db.execute('INSERT INTO ad_bill(contract_id,kind,seconds,samples,amount_cents,state,reason,event_id) VALUES(?,?,?,0,?,?,?,?)',
                   (cid,'continuous',g-issued,(g-issued)*rate,'issued','continuous_seconds_proven',event_id))
    if state=='shortfall' and issued>proven:
        delta=int(issued-proven)  # keep the issued bill; append a reversal for the unproven part
        db.execute('INSERT INTO ad_bill(contract_id,kind,seconds,samples,amount_cents,state,reason,event_id) VALUES(?,?,?,0,?,?,?,?)',
                   (cid,'reversal',-delta,-delta*rate,'issued','late_evidence_shortfall',event_id))
    if state!='fulfilled' and not one(db,"SELECT 1 FROM ad_bill WHERE contract_id=? AND kind='continuous'",(cid,)):
        db.execute('INSERT INTO ad_bill(contract_id,kind,seconds,samples,amount_cents,state,reason,event_id) VALUES(?,?,?,0,?,?,?,?)',
                   (cid,'continuous',g,g*rate,'suspended','unproven_seconds_retained',event_id))
    owed=0 if state=='fulfilled' else max(0,int(g-proven))
    last=one(db,'SELECT * FROM makeup WHERE contract_id=? ORDER BY id DESC LIMIT 1',(cid,))
    prev=last['owed_seconds'] if last else None
    ws=we=None
    if owed>0:ws,we=_makeup_window(db,cmds,owed)
    if prev!=owed or (owed>0 and last and (last['window_start'],last['window_end'])!=(ws,we)):
        if prev==owed:reason='window_rescheduled'
        elif state=='unproven':reason='unproven_seconds_retained'
        elif state=='shortfall':reason='proven_shortfall'
        else:reason='makeup_closed'
        db.execute('INSERT INTO makeup(contract_id,delta_seconds,owed_seconds,window_start,window_end,reason,event_id) VALUES(?,?,?,?,?,?,?)',
                   (cid,owed-(prev or 0),owed,ws,we,reason,event_id))

def _settle_sample(db,contract,state,samples,event_id):
    if state!='fulfilled':return
    if one(db,"SELECT 1 FROM ad_bill WHERE contract_id=? AND kind='sample'",(contract['id'],)):return
    db.execute('INSERT INTO ad_bill(contract_id,kind,seconds,samples,amount_cents,state,reason,event_id) VALUES(?,?,0,?,?,?,?,?)',
               (contract['id'],'sample',samples,contract['sample_amount_cents'],'issued','required_samples_visible',event_id))

def reconcile_contract(db,contract,event_id):
    asset,cmds,proven,mx,unproven,conflict=_evaluate(db,contract)
    samples=_samples(db,contract['campaign_id'],asset) if contract['product']=='sample' else 0
    if conflict:state,reason='evidence_conflict','camera_and_player_disagree'
    elif contract['product']=='continuous':
        if proven>=contract['guaranteed_seconds']:state,reason='fulfilled','continuous_seconds_proven'
        elif unproven<=0:state,reason='shortfall','proven_below_guarantee'
        else:state,reason='unproven','visibility_gap_retained'
    else:
        if samples>=contract['required_samples']:state,reason='fulfilled','required_samples_visible'
        else:state,reason='unproven','samples_missing'
    if not conflict:
        if contract['product']=='continuous':_settle_continuous(db,contract,state,proven,event_id,cmds)
        else:_settle_sample(db,contract,state,samples,event_id)
    db.execute('INSERT INTO fulfillment VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(contract_id) DO UPDATE SET '
               'proven_seconds=excluded.proven_seconds,max_seconds=excluded.max_seconds,unproven_seconds=excluded.unproven_seconds,'
               'samples=excluded.samples,state=excluded.state,reason=excluded.reason,event_id=excluded.event_id',
               (contract['id'],contract['campaign_id'],contract['product'],int(round(proven)),int(round(mx)),
                int(round(unproven)),samples,state,reason,event_id))
    log(db,event_id,'display',contract['id'],state,reason)
    return state

def reconcile_all(db,event_id):
    for c in many(db,'SELECT * FROM ad_contract ORDER BY rowid'):
        reconcile_contract(db,c,event_id)

def contract(db,e):
    if not one(db,'SELECT 1 FROM ad_campaign WHERE id=?',(e['campaign_id'],)):raise ValueError('unknown_campaign')
    if e['product'] not in ('continuous','sample'):raise ValueError('unknown_product')
    if e['product']=='continuous' and e.get('guaranteed_seconds',0)<=0:raise ValueError('invalid_guarantee')
    if e['product']=='sample' and e.get('required_samples',0)<=0:raise ValueError('invalid_sample_requirement')
    payload=(e['campaign_id'],e['product'],e.get('guaranteed_seconds',0),e.get('required_samples',0),
             e.get('rate_per_second_cents',0),e.get('sample_amount_cents',0),e['signed_at'])
    old=one(db,'SELECT * FROM ad_contract WHERE id=?',(e['contract_id'],))
    if old:
        if tuple(old[k] for k in ('campaign_id','product','guaranteed_seconds','required_samples',
                                  'rate_per_second_cents','sample_amount_cents','signed_at'))!=payload:
            raise ValueError('ad_contract_conflict')
        log(db,e['id'],'display',e['contract_id'],'stale','contract_already_active');return 'stale'
    db.execute('INSERT INTO ad_contract VALUES(?,?,?,?,?,?,?,?,?)',(e['contract_id'],)+payload+(e['id'],))
    log(db,e['id'],'display',e['contract_id'],'contract_active','signed_terms')
    reconcile_all(db,e['id'])
    return 'contract_active'

def switch(db,e):
    if not one(db,'SELECT 1 FROM display_screen WHERE id=?',(e['screen_id'],)):raise ValueError('unknown_screen')
    old=one(db,'SELECT * FROM screen_switch WHERE screen_id=? AND seq=?',(e['screen_id'],e['seq']))
    if old:
        if (old['at'],old['to_asset_hash'])!=(e['at'],e['to_asset_hash']):raise ValueError('switch_sequence_conflict')
        log(db,e['id'],'display',e['screen_id'],'stale','switch_already_recorded');return 'stale'
    db.execute('INSERT INTO screen_switch VALUES(?,?,?,?,?)',(e['switch_id'],e['screen_id'],e['seq'],e['at'],e['to_asset_hash']))
    log(db,e['id'],'display',e['switch_id'],'switch_recorded','protected_frame_buffer_log')
    reconcile_all(db,e['id'])
    return 'switch_recorded'

def segment(db,e):
    if not one(db,'SELECT 1 FROM display_screen WHERE id=?',(e['screen_id'],)):raise ValueError('unknown_screen')
    if e['starts']>=e['ends']:raise ValueError('invalid_segment_window')
    db.execute('INSERT INTO av_segment VALUES(?,?,?,?,?,?)',
               (e['segment_id'],e['screen_id'],e['starts'],e['ends'],e['visible_asset_hash'],e['media_hash']))
    log(db,e['id'],'display',e['segment_id'],'segment_recorded','external_camera_continuous_index')
    reconcile_all(db,e['id'])
    return 'segment_recorded'

def calibration(db,e):
    if not one(db,'SELECT 1 FROM display_screen WHERE id=?',(e['screen_id'],)):raise ValueError('unknown_screen')
    db.execute('INSERT INTO camera_calibration VALUES(?,?,?,?)',(e['calibration_id'],e['screen_id'],e['at'],e['offset_ms']))
    log(db,e['id'],'display',e['calibration_id'],'calibrated','camera_time_offset')
    reconcile_all(db,e['id'])
    return 'calibrated'

def power(db,e):
    if not one(db,'SELECT 1 FROM display_screen WHERE id=?',(e['screen_id'],)):raise ValueError('unknown_screen')
    if e['state'] not in ('on','off'):raise ValueError('invalid_power_state')
    db.execute('INSERT INTO screen_power VALUES(?,?,?,?)',(e['power_id'],e['screen_id'],e['at'],e['state']))
    log(db,e['id'],'display',e['power_id'],'power_recorded',e['state'])
    reconcile_all(db,e['id'])
    return 'power_recorded'

def override(db,e):
    if not one(db,'SELECT 1 FROM display_screen WHERE id=?',(e['screen_id'],)):raise ValueError('unknown_screen')
    if e['starts']>=e['ends']:raise ValueError('invalid_override_window')
    db.execute('INSERT INTO safety_override VALUES(?,?,?,?,?,?)',
               (e['override_id'],e['screen_id'],e['starts'],e['ends'],e['priority'],e['category']))
    log(db,e['id'],'display',e['override_id'],'override_recorded','safety_preemption_priority')
    reconcile_all(db,e['id'])
    return 'override_recorded'

def confirmation(db,e):
    if not one(db,'SELECT 1 FROM play_command WHERE id=?',(e['command_id'],)):raise ValueError('unknown_command')
    payload=(e['command_id'],e['confirmed_seconds'],e['confirmed_at'],e['confirmer'])
    old=one(db,'SELECT * FROM playback_confirmation WHERE receipt_id=?',(e['receipt_id'],))
    if old:
        if (old['command_id'],old['confirmed_seconds'],old['confirmed_at'],old['confirmer'])!=payload:
            raise ValueError('receipt_conflict')
        log(db,e['id'],'display',e['receipt_id'],'replayed','receipt_already_recorded')
        return 'replayed'  # resume by receipt id after interruption; never double-count
    db.execute('INSERT INTO playback_confirmation VALUES(?,?,?,?,?)',(e['receipt_id'],)+payload)
    log(db,e['id'],'display',e['receipt_id'],'confirmed','external_playback_confirmation')
    reconcile_all(db,e['id'])
    return 'confirmed'

def counter(db,e):
    if not one(db,'SELECT 1 FROM display_screen WHERE id=?',(e['screen_id'],)):raise ValueError('unknown_screen')
    if e['total_seconds']<0:raise ValueError('invalid_counter_reading')
    db.execute('INSERT INTO player_counter VALUES(?,?,?,?,?)',
               (e['counter_id'],e['screen_id'],e['asset_hash'],e['at'],e['total_seconds']))
    log(db,e['id'],'display',e['counter_id'],'counter_recorded','retestable_player_counter')
    reconcile_all(db,e['id'])
    return 'counter_recorded'

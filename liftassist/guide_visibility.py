"""Continuous visible wayfinding-chain survey certification.

A wayfinding certificate is never derived from a committed passenger route
(``journeys.reconcile``) and never from node-to-nearest-sign distance alone.
The whole walkable band of every route passage is sampled at wheelchair eye
height: each viewpoint must keep a readable *next* sign (distance, face angle
and an unobstructed 3-D line of sight). Gaps are returned as station intervals
with minimal move/add-sign advice, or as non-visual assistive coverage when a
tactile/voice scheme carries the gap. Missing measurements stay ``pending``
instead of defaulting to "clear".
"""
import json,math
from .store import one,many,log,jdumps

STEP_CM=100
LATERAL_FRACTIONS=(-0.75,-0.25,0.0,0.25,0.75)
MAX_FACE_ANGLE_DEG=60
LEGIBILITY_FACTOR=200
EPS=1e-9
SCHEME_KINDS=('static_visual','tactile_voice')

# ---------- geometry ----------
def _cumulative(points):
    segs=[];total=0.0
    for a,b in zip(points,points[1:]):
        length=math.hypot(b[0]-a[0],b[1]-a[1])
        if length<=0:raise ValueError('invalid_passage')
        segs.append((a,b,total,total+length));total+=length
    return segs,total

def station_point(segs,s):
    for a,b,t0,t1 in segs:
        if s<=t1+EPS:
            u=0.0 if t1==t0 else min(1.0,max(0.0,(s-t0)/(t1-t0)))
            x=a[0]+(b[0]-a[0])*u;y=a[1]+(b[1]-a[1])*u
            dx=(b[0]-a[0])/(t1-t0);dy=(b[1]-a[1])/(t1-t0)
            return (x,y),(-dy,dx),(dx,dy)
    a,b,t0,t1=segs[-1]
    return (b[0],b[1]),((a[1]-b[1])/(t1-t0),(b[0]-a[0])/(t1-t0)),((b[0]-a[0])/(t1-t0),(b[1]-a[1])/(t1-t0))

def project_station(segs,total,x,y):
    best=0.0;best_d=float('inf')
    for a,b,t0,t1 in segs:
        vx,vy=b[0]-a[0],b[1]-a[1]
        u=max(0.0,min(1.0,((x-a[0])*vx+(y-a[1])*vy)/((t1-t0)**2)))
        px,py=a[0]+u*vx,a[1]+u*vy
        d=math.hypot(x-px,y-py)
        if d<best_d:best_d=d;best=t0+u*(t1-t0)
    return min(total,max(0.0,best))

def point_in_polygon(x,y,poly):
    inside=False;n=len(poly)
    for i in range(n):
        ax,ay=poly[i];bx,by=poly[(i+1)%n]
        if (ay>y)!=(by>y) and x<(bx-ax)*(y-ay)/(by-ay)+ax:inside=not inside
    return inside

def segment_polygon_t(p0,p1,poly):
    """Return (t0,t1) of segment P0->P1 inside polygon, or None."""
    x0,y0=p0;x1,y1=p1;dx,dy=x1-x0,y1-y0
    ts=[]
    if point_in_polygon(x0,y0,poly):ts.append(0.0)
    if point_in_polygon(x1,y1,poly):ts.append(1.0)
    n=len(poly)
    for i in range(n):
        ax,ay=poly[i];bx,by=poly[(i+1)%n]
        ex,ey=bx-ax,by-ay
        den=dx*ey-dy*ex
        if abs(den)<=EPS:continue
        t=(ex*(y0-ay)-ey*(x0-ax))/den
        u=(dx*(y0-ay)-dy*(x0-ax))/den
        if -EPS<=t<=1+EPS and -EPS<=u<=1+EPS:ts.append(min(1.0,max(0.0,t)))
    if len(ts)<2:return None
    ts.sort()
    t0,t1=ts[0],ts[-1]
    return (t0,t1) if t1-t0>EPS else None

def _angle_diff(a,b):
    d=(a-b)%360.0
    if d>180.0:d-=360.0
    return abs(d)

# ---------- loading ----------
def _load_passage(db,pid):
    row=one(db,'SELECT * FROM passage WHERE id=?',(pid,))
    if not row:raise ValueError('unknown_passage')
    pts=json.loads(row['centerline_json'])
    segs,total=_cumulative(pts)
    return {'id':pid,'row':row,'points':pts,'segs':segs,'length':total,'width':row['width_cm']}

def _load_obstacles(db,pid,at):
    out=[]
    for r in many(db,'SELECT * FROM obstacle WHERE passage_id=?',(pid,)):
        poly=json.loads(r['polygon_json'])
        active=r['valid_from']<=at<=r['valid_to']
        out.append({'id':r['id'],'polygon':poly,'height':r['height_cm'],'from':r['valid_from'],'to':r['valid_to'],'active':active})
    return out

def _obstacle_windows_overlap(obstacles):
    for i,a in enumerate(obstacles):
        for b in obstacles[i+1:]:
            if a['from']<b['to'] and b['from']<a['to']:return (a['id'],b['id'])
    return None

def _effective_signs(db,passage_ids,scheme):
    """Surveyed signs overridden/extended by the approved scheme, per passage."""
    by_passage={pid:[] for pid in passage_ids}
    for r in many(db,f"SELECT * FROM guide_sign WHERE passage_id IN ({','.join('?'*len(passage_ids))})",passage_ids):
        by_passage[r['passage_id']].append(dict(r))
    if scheme:
        for s in json.loads(scheme['signs_json']):
            pid=s['passage_id']
            if pid not in by_passage:continue
            bucket=by_passage[pid]
            sid=s['sign_id']
            hit=next((x for x in bucket if x.get('id')==sid or x.get('sign_id')==sid),None)
            if hit:
                bucket.remove(hit)
                merged=dict(hit)
                merged.update({('id' if k=='sign_id' else k):v for k,v in s.items() if v is not None})
            else:
                merged=dict(s);merged['id']=sid
            bucket.append(merged)
    normalized={}
    for pid,bucket in by_passage.items():
        normalized[pid]=[]
        for s in bucket:
            sid=s.get('sign_id') or s.get('id')
            normalized[pid].append({'id':sid,'sign_id':sid,'passage_id':pid,
                'x':s['x_cm'],'y':s['y_cm'],'z':s.get('z_cm'),'face':s['face_deg'],
                'text':s.get('text',''),'text_height':s.get('text_height_cm')})
    return normalized

def _load_aids(db,scheme,at):
    if not scheme:return {}
    ids=json.loads(scheme['aids_json'])
    aids=[]
    for aid_id in ids:
        r=one(db,'SELECT * FROM wayfinding_aid WHERE id=?',(aid_id,))
        if not r:raise ValueError('unknown_aid')
        aids.append({'id':aid_id,'passage_id':r['passage_id'],'polygon':json.loads(r['polygon_json']),
                     'verified':bool(r['verified']),'active':r['valid_from']<=at<=r['valid_to']})
    return aids

# ---------- visibility ----------
def _sign_readable(sign,eye,station,segs,total,obstacles,retain_cm,max_angle,max_distance):
    ex,ey,ez=eye;sx,sy,sz=sign['x'],sign['y'],sign['z']
    s_s=project_station(segs,total,sx,sy)
    if s_s<station-retain_cm-EPS:return False,'behind_viewer'
    dist=math.sqrt((sx-ex)**2+(sy-ey)**2+(sz-ez)**2)
    if dist>max_distance+EPS:return False,'out_of_readable_distance'
    face=math.degrees(math.atan2(ey-sy,ex-sx))
    if _angle_diff(face,sign['face'])>max_angle:return False,'face_angle_exceeded'
    for ob in obstacles:
        if not ob['active']:continue
        interval=segment_polygon_t((ex,ey),(sx,sy),ob['polygon'])
        if not interval:continue
        t0,t1=interval
        z0=ez+t0*(sz-ez);z1=ez+t1*(sz-ez)
        if min(z0,z1)<=ob['height']+EPS:return False,'obstruction:'+ob['id']
    return True,'covered'

def _sample_viewpoints(passage,step_cm):
    segs,total,width=passage['segs'],passage['length'],passage['width']
    half=width/2.0
    stations=[]
    s=0.0
    while s<total-EPS:stations.append(round(s,6));s+=step_cm
    stations.append(round(total,6))
    out=[]
    for st in stations:
        (cx,cy),normal,_tangent=station_point(segs,st)
        for frac in LATERAL_FRACTIONS:
            out.append({'station':st,'lateral':frac*half,'x':cx+normal[0]*frac*half,'y':cy+normal[1]*frac*half})
    return stations,out

def junction_distance_ok(db,passage_ids,max_distance_cm=1000):
    """The flawed conventional check: junction/centerline nodes to nearest sign.

    Deliberately ignores line of sight and face angle. Kept as a comparator so
    tests can prove a route can pass this while the visual chain is broken.
    """
    for pid in passage_ids:
        p=_load_passage(db,pid)
        signs=_effective_signs(db,[pid],None)[pid]
        if not signs:return False
        for (x,y) in p['points']:
            nearest=min(math.hypot(s['x']-x,s['y']-y) for s in signs)
            if nearest>max_distance_cm+EPS:return False
    return True

def evaluate_route(db,passage_ids,eye_low_cm,eye_high_cm,eval_at,lit_windows,
                   scheme=None,retain_cm=200,max_angle=MAX_FACE_ANGLE_DEG,step_cm=STEP_CM,
                   default_text_height_cm=None):
    """Evaluate continuous sign coverage over every walkable viewpoint.

    Returns a dict with ``status`` in issued/refused/pending, per-passage
    breaks, samples, advice and the effective signs used.
    """
    if eye_low_cm<=0 or eye_high_cm<eye_low_cm:raise ValueError('invalid_eye_height')
    passages=[_load_passage(db,pid) for pid in passage_ids]
    if any(p['row']['layer_offset_cm'] is not None for p in passages):
        return {'status':'pending','reason':'map_layer_offset_unresolved','breaks':[],'samples':[],'advice':[],'signs':{}}
    if not lit_windows:
        return {'status':'pending','reason':'lighting_window_missing','breaks':[],'samples':[],'advice':[],'signs':{}}
    if not any(w['from']<=eval_at<=w['to'] for w in lit_windows):
        return {'status':'pending','reason':'lighting_window_not_covered','breaks':[],'samples':[],'advice':[],'signs':{}}
    if scheme and scheme['map_version']!=passages[0]['row']['survey_at']:
        return {'status':'pending','reason':'map_version_mismatch','breaks':[],'samples':[],'advice':[],'signs':{}}
    signs_by_passage=_effective_signs(db,passage_ids,scheme)
    flat=[s for bucket in signs_by_passage.values() for s in bucket]
    if any(s['z'] is None for s in flat):
        return {'status':'pending','reason':'unknown_sign_height','breaks':[],'samples':[],'advice':[],'signs':signs_by_passage}
    text_heights={s['id']:s['text_height'] for s in flat}
    if any(h is None for h in text_heights.values()) and default_text_height_cm is None:
        return {'status':'pending','reason':'unknown_text_height','breaks':[],'samples':[],'advice':[],'signs':signs_by_passage}
    obstacles={p['id']:_load_obstacles(db,p['id'],eval_at) for p in passages}
    for obs in obstacles.values():
        if _obstacle_windows_overlap(obs):
            return {'status':'pending','reason':'obstacle_window_overlap','breaks':[],'samples':[],'advice':[],'signs':signs_by_passage}
    if any(ob['active'] and ob['height'] is None for obs in obstacles.values() for ob in obs):
        return {'status':'pending','reason':'unknown_obstacle_height','breaks':[],'samples':[],'advice':[],'signs':signs_by_passage}
    aids=_load_aids(db,scheme,eval_at)
    if any(not a['verified'] for a in aids):
        return {'status':'pending','reason':'aid_not_verified','breaks':[],'samples':[],'advice':[],'signs':signs_by_passage}

    all_samples=[];all_breaks=[]
    for passage in passages:
        pid=passage['id'];segs,total=passage['segs'],passage['length']
        stations,viewpoints=_sample_viewpoints(passage,step_cm)
        obs=obstacles[pid]
        station_fail=[]
        for st in stations:
            reasons=[];walkable=0;aid_covered=0
            for vp in (v for v in viewpoints if abs(v['station']-st)<=EPS):
                inside=any(point_in_polygon(vp['x'],vp['y'],o['polygon']) for o in obs if o['active'])
                if inside:
                    for ez in (eye_low_cm,eye_high_cm):
                        all_samples.append({'passage_id':pid,'station':st,'lateral':vp['lateral'],'eye':ez,'covered':0,'reason':'inside_obstacle'})
                    continue
                walkable+=1
                if any(a['passage_id']==pid and a['active'] and point_in_polygon(vp['x'],vp['y'],a['polygon']) for a in aids):
                    aid_covered+=1
                vp_reason='no_sign_in_sight'
                for ez in (eye_low_cm,eye_high_cm):
                    ok=False
                    for sign in signs_by_passage[pid]:
                        th=sign['text_height'] or default_text_height_cm
                        max_dist=LEGIBILITY_FACTOR*th
                        good,why=_sign_readable(sign,(vp['x'],vp['y'],ez),st,segs,total,obs,retain_cm,max_angle,max_dist)
                        if good:ok=True;break
                        vp_reason=why
                    reason='covered' if ok else vp_reason
                    all_samples.append({'passage_id':pid,'station':st,'lateral':vp['lateral'],'eye':ez,'covered':int(ok),'reason':reason})
                    if not ok:reasons.append(reason)
            if walkable==0:
                station_fail.append((st,'walkable_band_blocked',True,False,None))
            elif reasons:
                dominant=_dominant_reason(reasons)
                by_aid=aid_covered==walkable
                blocked=next((r.split(':',1)[1] for r in reasons if r.startswith('obstruction:')),None)
                station_fail.append((st,dominant,False,by_aid,blocked))
        # merge consecutive failed stations into break intervals
        run=[]
        def flush(run):
            if not run:return
            start=run[0][0]
            last_idx=stations.index(run[-1][0])
            end=stations[last_idx+1] if last_idx+1<len(stations) else run[-1][0]
            reason=_dominant_reason([r[1] for r in run])
            covered=all(r[3] for r in run)
            blocked=next((r[4] for r in run if r[4]),None)
            all_breaks.append({'passage_id':pid,'start_cm':start,'end_cm':end,'reason':reason,
                               'blocked_by':blocked,'covered_by_aid':covered})
        for fail in station_fail:
            if run and fail[0]-run[-1][0]>step_cm+EPS:flush(run);run=[]
            run.append(fail)
        flush(run)
    visual_ok=not all_breaks
    aid_ok=all(b['covered_by_aid'] for b in all_breaks) if all_breaks else False
    advice=_advise(passages,signs_by_passage,all_breaks,passage_ids)
    if visual_ok:
        status,reason='issued','visual_continuous'
    elif scheme and scheme['kind']=='tactile_voice' and aid_ok:
        status,reason='issued','non_visual_assist'
    elif scheme and scheme['kind']=='tactile_voice' and all_breaks and not aid_ok:
        status,reason='refused','assistive_gap'
    else:
        status,reason='refused','visual_chain_broken'
    return {'status':status,'reason':reason,'breaks':all_breaks,'samples':all_samples,
            'advice':advice,'signs':signs_by_passage}

REASON_PRIORITY=('walkable_band_blocked','obstruction','face_angle_exceeded','out_of_readable_distance','behind_viewer','no_sign_in_sight')

def _dominant_reason(reasons):
    def rank(r):
        for i,prefix in enumerate(REASON_PRIORITY):
            if r.startswith(prefix):return i
        return len(REASON_PRIORITY)
    return min(reasons,key=rank)

def _advise(passages,signs_by_passage,breaks,passage_ids):
    advice=[];seen=set()
    pmap={p['id']:p for p in passages}
    for b in breaks:
        p=pmap[b['passage_id']];segs=p['segs']
        target=max(0.0,b['start_cm']-150.0)
        (px,py),_n,tangent=station_point(segs,target)
        behind=[s for s in signs_by_passage[b['passage_id']]
                if project_station(segs,p['length'],s['x'],s['y'])<b['start_cm']+EPS]
        key=None
        if behind:
            s=min(behind,key=lambda q:b['start_cm']-project_station(segs,p['length'],q['x'],q['y']))
            action={'action':'move_sign','sign_id':s['id'],'to_x_cm':round(px,1),'to_y_cm':round(py,1),
                    'face_deg':s['face'],'reason':b['reason'],'blocked_by':b['blocked_by']}
            key=('move',s['id'])
        else:
            face=math.degrees(math.atan2(-tangent[1],-tangent[0]))
            action={'action':'add_sign','at_station_cm':round(target,1),'x_cm':round(px,1),'y_cm':round(py,1),
                    'face_deg':round(face,1),'reason':b['reason'],'blocked_by':b['blocked_by']}
            key=('add',b['passage_id'],round(target,1))
        if key not in seen:seen.add(key);advice.append(action)
    # one sign serving two route branches: moving it must revalidate both chains
    ids={}
    for pid,bucket in signs_by_passage.items():
        for s in bucket:ids.setdefault(s['id'],set()).add(pid)
    for sid,pids in ids.items():
        if len(pids)>1:
            advice.append({'action':'revalidate_chains','sign_id':sid,'passage_ids':sorted(pids)})
    return advice

# ---------- cert events and persistence ----------
def scheme(db,e):
    if e['scheme_kind'] not in SCHEME_KINDS:raise ValueError('invalid_scheme_kind')
    for pid in e['passage_ids']:
        if not one(db,'SELECT 1 FROM passage WHERE id=?',(pid,)):raise ValueError('unknown_passage')
    db.execute('INSERT INTO guide_scheme VALUES(?,?,?,?,?,?,?,?)',
               (e['scheme_id'],jdumps(e['passage_ids']),e['scheme_kind'],e['map_version'],
                e['approved_at'],jdumps(e.get('signs',[])),jdumps(e.get('aids',[])),e['id']))
    log(db,e['id'],'guide_visibility',e['scheme_id'],'captured','scheme_approved')
    return 'captured'

def aid(db,e):
    if not one(db,'SELECT 1 FROM passage WHERE id=?',(e['passage_id'],)):raise ValueError('unknown_passage')
    db.execute('INSERT INTO wayfinding_aid(id,passage_id,kind,polygon_json,valid_from,valid_to,verified,event_id) VALUES(?,?,?,?,?,?,?,?)',
               (e['aid_id'],e['passage_id'],e['aid_kind'],jdumps(e['polygon']),
                e['valid_from'],e['valid_to'],int(bool(e['verified'])),e['id']))
    log(db,e['id'],'guide_visibility',e['aid_id'],'captured','aid_verified' if e['verified'] else 'aid_unverified')
    return 'captured'

def cert(db,e):
    passage_ids=e['passage_ids']
    scheme_row=None
    if e.get('scheme_id'):
        scheme_row=one(db,'SELECT * FROM guide_scheme WHERE id=?',(e['scheme_id'],))
        if not scheme_row:raise ValueError('unknown_scheme')
        if json.loads(scheme_row['passage_ids_json'])!=passage_ids:raise ValueError('scheme_passage_mismatch')
    visual_claimed=e['visual_claimed']
    if scheme_row and scheme_row['kind']=='tactile_voice' and visual_claimed:
        raise ValueError('assistive_scheme_cannot_claim_visual')
    survey_at=one(db,'SELECT survey_at FROM passage WHERE id=?',(passage_ids[0],))['survey_at']
    map_version=e.get('map_version',survey_at)
    if map_version!=survey_at:
        result={'status':'pending','reason':'map_version_mismatch','breaks':[],'samples':[],'advice':[],'signs':{}}
    else:
        result=evaluate_route(db,passage_ids,e['eye_low_cm'],e['eye_high_cm'],e['eval_at'],
                              e['lit_windows'],scheme=scheme_row,retain_cm=e.get('retain_cm',200),
                              max_angle=e.get('max_face_angle_deg',MAX_FACE_ANGLE_DEG),
                              step_cm=e.get('step_cm',STEP_CM),
                              default_text_height_cm=e.get('default_text_height_cm'))
    last=one(db,'SELECT MAX(revision) AS m FROM guide_cert WHERE id=?',(e['cert_id'],))
    revision=1 if not last or last['m'] is None else last['m']+1
    state=result['status'];reason=result['reason']
    db.execute('''INSERT INTO guide_cert(id,revision,passage_ids_json,scheme_id,kind,map_version,survey_at,
                 eye_low_cm,eye_high_cm,eval_at,lit_window_json,visual_claimed,state,reason,advice_json,event_id)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               (e['cert_id'],revision,jdumps(passage_ids),e.get('scheme_id'),
                scheme_row['kind'] if scheme_row else 'static_visual',
                e.get('map_version',survey_at),survey_at,e['eye_low_cm'],e['eye_high_cm'],e['eval_at'],
                jdumps(e['lit_windows']),int(bool(visual_claimed)),state,reason,
                jdumps(result['advice']),e['id']))
    for b in result['breaks']:
        db.execute('INSERT INTO visibility_break(cert_id,revision,passage_id,start_cm,end_cm,reason,blocked_by,covered_by_aid) VALUES(?,?,?,?,?,?,?,?)',
                   (e['cert_id'],revision,b['passage_id'],b['start_cm'],b['end_cm'],b['reason'],b['blocked_by'],int(b['covered_by_aid'])))
    for smp in result['samples']:
        db.execute('INSERT INTO cert_sample(cert_id,revision,passage_id,station_cm,lateral_cm,eye_cm,covered,reason) VALUES(?,?,?,?,?,?,?,?)',
                   (e['cert_id'],revision,smp['passage_id'],smp['station'],smp['lateral'],smp['eye'],smp['covered'],smp['reason']))
    log(db,e['id'],'guide_visibility',e['cert_id'],state,reason)
    return state

def revoke_for_obstacle(db,event):
    """A late obstacle record may withdraw a visual cert but never rewrites it."""
    rows=many(db,'SELECT id,MAX(revision) AS rev FROM guide_cert GROUP BY id ORDER BY id')
    for row in rows:
        latest=one(db,'SELECT * FROM guide_cert WHERE id=? AND revision=?',(row['id'],row['rev']))
        if latest['state']!='issued' or not latest['visual_claimed']:continue
        if event['passage_id'] not in json.loads(latest['passage_ids_json']):continue
        if not (event['valid_from']<=latest['eval_at']<=event['valid_to']):continue
        db.execute('''INSERT INTO guide_cert(id,revision,passage_ids_json,scheme_id,kind,map_version,survey_at,
                     eye_low_cm,eye_high_cm,eval_at,lit_window_json,visual_claimed,state,reason,advice_json,event_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (latest['id'],row['rev']+1,latest['passage_ids_json'],latest['scheme_id'],latest['kind'],
                    latest['map_version'],latest['survey_at'],latest['eye_low_cm'],latest['eye_high_cm'],
                    latest['eval_at'],latest['lit_window_json'],latest['visual_claimed'],'revoked',
                    'late_obstacle_record','[]',event['id']))
        log(db,event['id'],'guide_visibility',latest['id'],'revoked','late_obstacle_record')

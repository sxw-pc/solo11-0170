"""Noise source identifiability analysis and interim remediation dossier.

Sound pressure is logged in dB (logarithmic).  A complaint window where the
exhaust fan runs and a train passes at the same moment collapses to one
observation row [1, 1] in the energy domain: the summed energy is identical
whether the true split is fan 0.8 / train 0.2 (history A) or fan 0.2 /
train 0.8 (history B), so piling up identical complaints never raises the
design matrix rank.  This module therefore refuses point splits (a
minimum-norm least squares answer would fabricate a 0.5/0.5 split with zero
residual and fits both worlds perfectly) and instead

* builds the energy-domain multi-point mixing model from the evidence
  ledger (microphone position/calibration, band spectra, fan run speed,
  train axle passes, station impulse response),
* proves whether the identifiability condition (full-rank gain matrix)
  holds,
* reports per-source provable share *ranges* only when identifiable,
* records time-limited interim measures that are explicitly not a final
  fault determination,
* designs the minimum safe control experiments (no-train window, fan speed
  at or above the fire-ventilation minimum, or a repositioned microphone)
  and rejects experiments that would breach the ventilation minimum.

Passenger journey, lift and evacuation ledgers are never touched here.
"""
import json,math
from datetime import datetime
from .store import one,many,log

TOL_DB=0.5  # band level tolerance propagated into share ranges
DEFAULT_MIN_AIRFLOW_PCT=40
DEFAULT_REVERB_S=2.0

INTERIM_CATALOG={
 'fan_muffler':{'party':'station','obligations':['equipment_work_order','ventilation_capacity_verification'],
                'requires':['independent_airflow_logging']},
 'train_speed_limit':{'party':'vehicle','obligations':['schedule_time_loss'],'requires':[]},
 'wheel_rail_lubrication':{'party':'vehicle','obligations':['track_work'],'requires':[]},
}
EXPERIMENT_DESIGNS=('no_train','fan_speed','reposition')

def _parse(ts):return datetime.fromisoformat(ts.replace('Z','+00:00')).timestamp()
def _dist(ax,ay,bx,by):return math.hypot(ax-bx,ay-by)

def reverb_fraction(start,end,passed,tau):
    """Fraction of a unit energy pulse emitted at `passed` that arrives
    inside [start,end] under an exponential station impulse response."""
    if end<=passed:return 0.0
    lo=max(start,passed)
    return math.exp(-(lo-passed)/tau)-math.exp(-(end-passed)/tau)

def station_acoustics(db,e):
    if e['reverb_time_s']<=0 or not 0<e['min_airflow_pct']<=100:raise ValueError('invalid_station_acoustics')
    db.execute('INSERT OR REPLACE INTO station_acoustics VALUES(?,?,?,?,?,?,?)',
               (e['station'],e['reverb_time_s'],e['fan_x_m'],e['fan_y_m'],e['track_x_m'],e['track_y_m'],e['min_airflow_pct']))
    log(db,e['id'],'noise',e['station'],'captured','station_acoustics')
    return 'captured'

def complaint(db,e):
    mic=one(db,'SELECT * FROM microphone WHERE id=?',(e['microphone_id'],))
    if not mic:raise ValueError('unknown_microphone')
    tr=one(db,'SELECT * FROM acoustic_trace WHERE id=?',(e['trace_id'],))
    if not tr or tr['microphone_id']!=e['microphone_id']:raise ValueError('unknown_trace')
    bands=json.loads(tr['bands_json'])
    if str(e['band_hz']) not in bands:raise ValueError('band_not_in_trace')
    if e['ended_at']<=e['started_at']:raise ValueError('invalid_complaint_window')
    level=float(bands[str(e['band_hz'])])
    saturated=int(mic['saturation_db'] is not None and level>=mic['saturation_db'])
    db.execute('INSERT INTO noise_complaint VALUES(?,?,?,?,?,?,?,?,?)',
               (e['complaint_id'],e['station'],e['microphone_id'],e['trace_id'],e['band_hz'],
                e['started_at'],e['ended_at'],level,saturated))
    log(db,e['id'],'noise',e['complaint_id'],'captured','noise_complaint')
    return 'captured'

def interim_measure(db,e):
    spec=INTERIM_CATALOG.get(e['measure'])
    if not spec:raise ValueError('unknown_interim_measure')
    if e['party']!=spec['party']:raise ValueError('measure_party_mismatch')
    if not e.get('expires_at') or e['expires_at']<=e['issued_at']:raise ValueError('interim_measure_must_be_time_limited')
    db.execute('INSERT INTO interim_measure VALUES(?,?,?,?,?,?,?,?,NULL,NULL)',
               (e['measure_id'],e['station'],e['party'],e['measure'],e['issued_at'],e['expires_at'],'issued','attribution_unproven'))
    log(db,e['id'],'noise',e['measure_id'],'issued','attribution_unproven')
    return 'issued'

def measure_update(db,e):
    m=one(db,'SELECT * FROM interim_measure WHERE id=?',(e['measure_id'],))
    if not m:raise ValueError('unknown_measure')
    if e['action']=='executed':
        if m['status']!='issued':raise ValueError('measure_not_pending')
        db.execute("UPDATE interim_measure SET status='executed',executed_at=? WHERE id=?",(e['at'],e['measure_id']))
    elif e['action']=='cancelled':
        if m['status']=='executed':raise ValueError('executed_measure_immutable')
        if m['status']!='issued':raise ValueError('measure_not_pending')
        db.execute("UPDATE interim_measure SET status='cancelled',cancelled_at=? WHERE id=?",(e['at'],e['measure_id']))
    else:raise ValueError('unknown_measure_action')
    log(db,e['id'],'noise',e['measure_id'],e['action'],e.get('note','measure_update'))
    return e['action']

def control_experiment(db,e):
    if e['design'] not in EXPERIMENT_DESIGNS:raise ValueError('unknown_experiment_design')
    acc=one(db,'SELECT * FROM station_acoustics WHERE station=?',(e['station'],))
    min_airflow=acc['min_airflow_pct'] if acc else DEFAULT_MIN_AIRFLOW_PCT
    if e['design']=='fan_speed' and (e.get('fan_speed_pct') is None or e['fan_speed_pct']<min_airflow):
        raise ValueError('below_minimum_ventilation')
    status='planned'
    if e.get('trace_id'):
        tr=one(db,'SELECT * FROM acoustic_trace WHERE id=?',(e['trace_id'],))
        if not tr:raise ValueError('unknown_trace')
        if str(e['band_hz']) not in json.loads(tr['bands_json']):raise ValueError('band_not_in_trace')
        status='observed'
    db.execute('INSERT INTO control_experiment VALUES(?,?,?,?,?,?,?,?,?,?)',
               (e['experiment_id'],e['station'],e['design'],e['started_at'],e['ended_at'],
                e.get('fan_speed_pct'),e.get('microphone_id'),e.get('trace_id'),e.get('band_hz'),status))
    log(db,e['id'],'noise',e['experiment_id'],status,e['design'])
    return status

def _windows(db,station,band_hz):
    rows=[]
    for c in many(db,'SELECT * FROM noise_complaint WHERE station=? AND band_hz=? ORDER BY started_at,id',(station,band_hz)):
        rows.append({'origin':'complaint','id':c['id'],'microphone_id':c['microphone_id'],'trace_id':c['trace_id'],
                     'started_at':c['started_at'],'ended_at':c['ended_at'],'level_db':c['level_db'],'saturated':bool(c['saturated'])})
    for x in many(db,"SELECT * FROM control_experiment WHERE station=? AND band_hz=? AND status='observed' ORDER BY started_at,id",(station,band_hz)):
        tr=one(db,'SELECT * FROM acoustic_trace WHERE id=?',(x['trace_id'],))
        mic=one(db,'SELECT * FROM microphone WHERE id=?',(x['microphone_id'],))
        level=float(json.loads(tr['bands_json'])[str(band_hz)])
        rows.append({'origin':'experiment','id':x['id'],'microphone_id':x['microphone_id'],'trace_id':x['trace_id'],
                     'started_at':x['started_at'],'ended_at':x['ended_at'],'level_db':level,
                     'saturated':bool(mic['saturation_db'] is not None and level>=mic['saturation_db'])})
    return rows

def _reference(db,station,ref_window):
    """Reference operating point: the first complaint window defines the
    normalized gains [1, 1] and the normalized observation energy 1.0."""
    fan=one(db,'SELECT * FROM fan_run WHERE station=? AND started_at<? AND stopped_at>? ORDER BY started_at LIMIT 1',
            (station,ref_window['ended_at'],ref_window['started_at']))
    acc=one(db,'SELECT * FROM station_acoustics WHERE station=?',(station,))
    tau=acc['reverb_time_s'] if acc else DEFAULT_REVERB_S
    w0,w1=_parse(ref_window['started_at']),_parse(ref_window['ended_at'])
    ref_pass=None
    for tp in many(db,'SELECT * FROM train_pass WHERE station=? ORDER BY passed_at',(station,)):
        frac=reverb_fraction(w0,w1,_parse(tp['passed_at']),tau)
        if frac>0:ref_pass=(tp,frac);break
    return fan,ref_pass,tau

def _gains(db,station,w,ref,acc,mic_positions,ref_pos):
    fan_run,ref_pass,tau,ref_fan_speed,ref_train_speed,ref_frac=ref
    w0,w1=_parse(w['started_at']),_parse(w['ended_at'])
    if acc:
        r_fan_ref=_dist(acc['fan_x_m'],acc['fan_y_m'],*ref_pos);r_fan=_dist(acc['fan_x_m'],acc['fan_y_m'],*mic_positions[w['microphone_id']])
        r_track_ref=_dist(acc['track_x_m'],acc['track_y_m'],*ref_pos);r_track=_dist(acc['track_x_m'],acc['track_y_m'],*mic_positions[w['microphone_id']])
        fan_geo=(r_fan_ref/r_fan)**2 if r_fan>0 else 1.0;train_geo=(r_track_ref/r_track)**2 if r_track>0 else 1.0
    else:
        if mic_positions[w['microphone_id']]!=ref_pos:raise ValueError('station_acoustics_required')
        fan_geo=train_geo=1.0
    g_fan=0.0
    for fr in many(db,'SELECT * FROM fan_run WHERE station=? AND started_at<? AND stopped_at>?',(station,w['ended_at'],w['started_at'])):
        g_fan+=(fr['speed_pct']/ref_fan_speed)**2*fan_geo
    g_train=0.0
    for tp in many(db,'SELECT * FROM train_pass WHERE station=?',(station,)):
        frac=reverb_fraction(w0,w1,_parse(tp['passed_at']),tau)
        if frac>0:g_train+=(tp['speed_kph']/ref_train_speed)**2*(frac/ref_frac)*train_geo
    return g_fan,g_train

def _rank(rows):
    base=next((r for r in rows if abs(r[0])>1e-12 or abs(r[1])>1e-12),None)
    if base is None:return 0
    for r in rows:
        if abs(base[0]*r[1]-base[1]*r[0])>1e-9:return 2
    return 1

def _share_ranges(rows):
    """Feasible (fan, train) share ranges from interval/lower-bound rows."""
    if not rows or all(hi is None for _,_,_,hi in rows):return None
    lines=[(1.0,0.0,0.0),(0.0,1.0,0.0)]
    for a,b,lo,hi in rows:
        lines.append((a,b,lo))
        if hi is not None:lines.append((a,b,hi))
    pts=[]
    for i in range(len(lines)):
        for j in range(i+1,len(lines)):
            a1,b1,c1=lines[i];a2,b2,c2=lines[j]
            det=a1*b2-a2*b1
            if abs(det)<1e-12:continue
            x=(c1*b2-c2*b1)/det;y=(a1*c2-a2*c1)/det
            if x<-1e-9 or y<-1e-9:continue
            if all(a*x+b*y>=lo-1e-9 and (hi is None or a*x+b*y<=hi+1e-9) for a,b,lo,hi in rows):pts.append((x,y))
    if not pts:return None
    xs=[p[0] for p in pts];ys=[p[1] for p in pts]
    return (max(0.0,min(xs)),max(xs)),(max(0.0,min(ys)),max(ys))

def _measures(db,station):
    out=[]
    for m in many(db,'SELECT * FROM interim_measure WHERE station=? ORDER BY id',(station,)):
        spec=INTERIM_CATALOG[m['measure']]
        out.append({'id':m['id'],'party':m['party'],'measure':m['measure'],'status':m['status'],'basis':m['basis'],
                    'issued_at':m['issued_at'],'expires_at':m['expires_at'],'executed_at':m['executed_at'],'cancelled_at':m['cancelled_at'],
                    'obligations':spec['obligations'],'requires':spec['requires'],'final_fault':False})
    return out

def propose_experiments(db,station,ref_fan_speed=80):
    """Minimum safe window designs that make the fan and train columns
    independent.  Turning the fan off would isolate the train but breaches
    the fire-ventilation minimum airflow, so a repositioned microphone is
    offered as the safe alternative sampling plan."""
    acc=one(db,'SELECT * FROM station_acoustics WHERE station=?',(station,))
    min_airflow=acc['min_airflow_pct'] if acc else DEFAULT_MIN_AIRFLOW_PCT
    return {'min_additional_windows':1,'options':[
        {'design':'no_train','added_row':[1.0,0.0],'safe':True,
         'note':'fan keeps ventilation duty; observe a window with no train pass'},
        {'design':'fan_speed','fan_speed_pct':min_airflow,'added_row':[round((min_airflow/ref_fan_speed)**2,6),0.0],'safe':True,
         'note':'fan at the fire-ventilation minimum airflow, no train in window'},
        {'design':'fan_speed','fan_speed_pct':0,'added_row':[0.0,1.0],'safe':False,'reason':'below_minimum_ventilation',
         'alternative':'reposition','note':'turning the fan off would isolate the train but breaches the ventilation minimum'},
        {'design':'reposition','added_row':'geometry_dependent','safe':True,
         'note':'a second microphone where the fan/track distance ratio differs breaks the collinearity'}]}

def _review_at(db,station):
    candidates=[x['started_at'] for x in many(db,"SELECT started_at FROM control_experiment WHERE station=? AND status='planned'",(station,))]
    candidates+=[m['expires_at'] for m in many(db,"SELECT expires_at FROM interim_measure WHERE station=? AND status='issued'",(station,))]
    return min(candidates) if candidates else None

def dossier(db,station,band_hz=125):
    """Attribution dossier: provable per-source ranges, interim measures,
    control experiments and follow-up review.  Read-only."""
    acc=one(db,'SELECT * FROM station_acoustics WHERE station=?',(station,))
    min_airflow=acc['min_airflow_pct'] if acc else DEFAULT_MIN_AIRFLOW_PCT
    base={'station':station,'band_hz':band_hz,'sources':['fan','train'],
          'contract':'only identifiable shares may be finally attributed; interim measures are never a final fault determination',
          'safety':{'min_airflow_pct':min_airflow},
          'ledger_note':'noise attribution never alters journey, lift or evacuation ledgers'}
    windows=_windows(db,station,band_hz)
    complaints=[w for w in windows if w['origin']=='complaint']
    experiments=_experiments_summary(db,station)
    measures=_measures(db,station)
    if not complaints:
        return {**base,'observations':[],'design_matrix':[],
                'identifiability':{'rank':0,'required':2,'identifiable':False,'explanation':'no_complaint_windows'},
                'shares':None,'pending_shares':['fan','train'],'final_attribution':None,
                'interim_measures':measures,'experiments':experiments,
                'evidence':{'traces':[]},'review_at':_review_at(db,station)}
    fan_run,ref_pass,tau=_reference(db,station,complaints[0])
    if fan_run is None or ref_pass is None:
        return {**base,'observations':[],'design_matrix':[],
                'identifiability':{'rank':0,'required':2,'identifiable':False,'explanation':'incomplete_reference_window'},
                'shares':None,'pending_shares':['fan','train'],'final_attribution':None,
                'interim_measures':measures,'experiments':experiments,
                'evidence':{'traces':[]},'review_at':_review_at(db,station)}
    ref_train,ref_frac=ref_pass
    ref=(fan_run,ref_pass,tau,fan_run['speed_pct'],ref_train['speed_kph'],ref_frac)
    mic_positions={m['id']:(m['x_m'],m['y_m']) for m in many(db,'SELECT * FROM microphone WHERE station=?',(station,))}
    ref_pos=mic_positions[complaints[0]['microphone_id']]
    ref_level=complaints[0]['level_db']
    factor=10**(TOL_DB/10)
    observations=[];rows=[];constraints=[]
    for w in windows:
        g_fan,g_train=_gains(db,station,w,ref,acc,mic_positions,ref_pos)
        y=10**((w['level_db']-ref_level)/10)
        lo=y/factor;hi=None if w['saturated'] else y*factor
        rows.append((g_fan,g_train));constraints.append((g_fan,g_train,lo,hi))
        observations.append({'id':w['id'],'origin':w['origin'],'microphone_id':w['microphone_id'],'trace_id':w['trace_id'],
                             'started_at':w['started_at'],'ended_at':w['ended_at'],'level_db':w['level_db'],'saturated':w['saturated'],
                             'energy':round(y,6),'energy_lo':round(lo,6),'energy_hi':None if hi is None else round(hi,6),
                             'gains':{'fan':round(g_fan,6),'train':round(g_train,6)}})
    rank=_rank(rows);identifiable=rank==2
    shares=None;final=None;dominant=None;explanation=None
    if identifiable:
        ranges=_share_ranges(constraints)
        if ranges is None:
            identifiable=False;explanation='observations are inconsistent or leave shares unbounded'
        else:
            (sf_lo,sf_hi),(st_lo,st_hi)=ranges
            shares={'fan':[round(sf_lo,4),round(sf_hi,4)],'train':[round(st_lo,4),round(st_hi,4)]}
            if sf_lo>st_hi:dominant='fan_dominant'
            elif st_lo>sf_hi:dominant='train_dominant'
            else:dominant='shared_unresolved'
            final={'dominant':dominant,'fan_share_range':shares['fan'],'train_share_range':shares['train'],
                   'basis':'identifiable_share_ranges','measure_recommendations':_recommendations(measures,dominant),
                   'note':'executed measures are immutable history and are never cancelled'}
    if not identifiable:
        explanation=explanation or ('design matrix rank %d < 2: every observation row is collinear [1,1]; fan and train shares cannot be '
                     'separated and a minimum-norm least squares split would fabricate 0.5/0.5, which is refused'%rank)
        proposals=propose_experiments(db,station,ref[3])
    else:
        explanation='design matrix full rank: source shares are identifiable within +/-%s dB tolerance'%TOL_DB
        proposals={'min_additional_windows':0,'options':[]}
    return {**base,'observations':observations,'design_matrix':[[round(a,6),round(b,6)] for a,b in rows],
            'identifiability':{'rank':rank,'required':2,'identifiable':identifiable,'explanation':explanation},
            'shares':shares,'pending_shares':[] if identifiable else ['fan','train'],
            'final_attribution':final,'interim_measures':measures,
            'experiments':{**experiments,'proposals':proposals},
            'evidence':{'traces':sorted({w['trace_id'] for w in windows}),'raw_waveforms':'kept in acoustic_trace and source_file ledger'},
            'review_at':_review_at(db,station)}

def _recommendations(measures,dominant):
    recs=[]
    for m in measures:
        if m['status']!='issued':continue
        if dominant=='fan_dominant' and m['party']=='vehicle':
            recs.append({'measure_id':m['id'],'action':'cancel','reason':'train share provably minor; vehicle interim measure no longer relevant'})
        elif dominant=='train_dominant' and m['party']=='station':
            recs.append({'measure_id':m['id'],'action':'cancel','reason':'fan share provably minor; station interim measure no longer effective'})
        else:
            recs.append({'measure_id':m['id'],'action':'keep','reason':'provable share range remains consistent with this source'})
    return recs

def _experiments_summary(db,station):
    rows=many(db,'SELECT * FROM control_experiment WHERE station=? ORDER BY started_at,id',(station,))
    def view(x):return {'id':x['id'],'design':x['design'],'started_at':x['started_at'],'ended_at':x['ended_at'],
                        'fan_speed_pct':x['fan_speed_pct'],'microphone_id':x['microphone_id'],'trace_id':x['trace_id']}
    return {'planned':[view(x) for x in rows if x['status']=='planned'],
            'observed':[view(x) for x in rows if x['status']=='observed']}

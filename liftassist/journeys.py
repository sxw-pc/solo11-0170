"""Passenger commitments and route reassessment on lift changes."""
from datetime import datetime,timedelta
from .store import one,many,log

def request(db,e):
    if e['wheelchair_width']<=0 or e['route_minutes']<=0 or e['need_by']<=e['requested_at']:
        raise ValueError('invalid_journey_window')
    if not one(db,'SELECT 1 FROM lift WHERE id=?',(e['lift_id'],)):raise ValueError('unknown_route_lift')
    db.execute("INSERT INTO journey VALUES(?,?,?,?,?,?,?,?,'pending','not_evaluated')",
        (e['journey_id'],e['passenger'],e['lift_id'],e['wheelchair_width'],e['requested_at'],e['need_by'],e['route_minutes'],e['staff_id']))
    return reconcile(db,e['journey_id'],e['id'])

def _arrival_ok(e):
    begin=datetime.fromisoformat(e['requested_at'].replace('Z','+00:00'))
    due=datetime.fromisoformat(e['need_by'].replace('Z','+00:00'))
    return begin+timedelta(minutes=e['route_minutes'])<=due

def reconcile(db,key,event_id):
    j=one(db,'SELECT * FROM journey WHERE id=?',(key,))
    lift=one(db,'SELECT * FROM lift WHERE id=?',(j['lift_id'],))
    delivered=one(db,"SELECT * FROM assistance WHERE journey_id=? AND state IN ('fulfilled','remedy') ORDER BY delivered_at DESC LIMIT 1",(key,))
    if delivered:
        state,reason=('served','confirmed_delivery') if delivered['state']=='fulfilled' else ('delivered_late','compensation_due')
    elif j['wheelchair_width']>lift['width_cm']:state,reason='unreachable','door_too_narrow'
    elif not _arrival_ok(j):state,reason='missed_connection','route_exceeds_connection'
    elif lift['state']!='open':state,reason='reroute','lift_'+lift['state']
    else:
        cert=one(db,"SELECT * FROM staff_qualification WHERE staff_id=? AND issued_at<=? AND expires_at>? AND (revoked_at IS NULL OR revoked_at>?) ORDER BY issued_at DESC LIMIT 1",
                 (j['staff_id'],j['requested_at'],j['need_by'],j['requested_at']))
        shift=one(db,"SELECT * FROM staff_shift WHERE staff_id=? AND station=? AND state='active' AND starts<=? AND ends>=? LIMIT 1",
                  (j['staff_id'],lift['station'],j['requested_at'],j['need_by']))
        if not cert or not shift:
            state,reason='awaiting_staff','qualification_or_shift_missing'
        else:
            competing=many(db,"SELECT * FROM journey WHERE staff_id=? AND id<>? AND state='committed' AND requested_at<? AND need_by>?",
                           (j['staff_id'],key,j['need_by'],j['requested_at']))
            state,reason=('waitlist','staff_window_taken') if competing else ('committed','accessible_route')
    old_state=j['state']
    db.execute('UPDATE journey SET state=?,reason=? WHERE id=?',(state,reason,key))
    last=one(db,'SELECT revision,state,lift_revision FROM route WHERE journey_id=? ORDER BY revision DESC LIMIT 1',(key,))
    if not last or (last['state'],last['lift_revision'])!=(state,lift['revision']):
        rev=1 if not last else last['revision']+1
        db.execute('INSERT INTO route(journey_id,revision,lift_revision,state,reason,event_id) VALUES(?,?,?,?,?,?)',
                   (key,rev,lift['revision'],state,reason,event_id))
        if old_state=='committed' and state not in ('committed','served','delivered_late'):
            db.execute('INSERT INTO obligation(journey_id,kind,reason) VALUES(?,?,?)',(key,'replacement',reason))
    log(db,event_id,'journey',key,state,reason)
    return state

def reconcile_for_lift(db,lift_id,event_id):
    for row in many(db,'SELECT id FROM journey WHERE lift_id=?',(lift_id,)):
        reconcile(db,row['id'],event_id)
    if one(db,"SELECT state FROM lift WHERE id=?",(lift_id,))['state']=='open':
        for row in many(db,"SELECT o.id,o.journey_id FROM obligation o JOIN journey j ON j.id=o.journey_id WHERE j.lift_id=? AND o.kind='replacement' AND o.resolved=0",(lift_id,)):
            active=one(db,"SELECT 1 FROM assistance WHERE journey_id=? AND state IN ('assigned','fulfilled','remedy')",(row['journey_id'],))
            if not active:db.execute('UPDATE obligation SET resolved=1 WHERE id=?',(row['id'],))

def reconcile_for_staff(db,staff_id,event_id):
    for row in many(db,'SELECT id FROM journey WHERE staff_id=?',(staff_id,)):
        reconcile(db,row['id'],event_id)

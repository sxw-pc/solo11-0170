"""Passenger commitments and route reassessment on lift changes."""
from datetime import datetime,timedelta
from .store import one,many,log

def request(db,e):
    if e['wheelchair_width']<=0 or e['route_minutes']<=0 or e['need_by']<=e['requested_at']:
        raise ValueError('invalid_journey_window')
    segments=e.get('route') or [{'lift_id':e['lift_id'],'from':'entrance','to':'platform','minutes':e['route_minutes']}]
    entrance=e.get('entrance',segments[0]['from']);platform=e.get('platform',segments[-1]['to'])
    if segments[0]['from']!=entrance or segments[-1]['to']!=platform or any(a['to']!=b['from'] for a,b in zip(segments,segments[1:])):
        raise ValueError('disconnected_route')
    if any(int(s['minutes'])<=0 or not one(db,'SELECT 1 FROM lift WHERE id=?',(s['lift_id'],)) for s in segments):
        raise ValueError('unknown_or_invalid_route_segment')
    station=one(db,'SELECT station FROM lift WHERE id=?',(segments[0]['lift_id'],))['station']
    if any(one(db,'SELECT station FROM lift WHERE id=?',(s['lift_id'],))['station']!=station for s in segments):
        raise ValueError('cross_station_route_without_handoff')
    db.execute("INSERT INTO journey VALUES(?,?,?,?,?,?,?,?,?,?,'pending','not_evaluated')",
        (e['journey_id'],e['passenger'],segments[0]['lift_id'],entrance,platform,e['wheelchair_width'],e['requested_at'],e['need_by'],sum(int(s['minutes']) for s in segments),e['staff_id']))
    for i,s in enumerate(segments):
        db.execute('INSERT INTO route_segment(journey_id,position,lift_id,from_node,to_node,minutes) VALUES(?,?,?,?,?,?)',
                   (e['journey_id'],i,s['lift_id'],s['from'],s['to'],int(s['minutes'])))
    return reconcile(db,e['journey_id'],e['id'])

def _arrival_ok(e):
    begin=datetime.fromisoformat(e['requested_at'].replace('Z','+00:00'))
    due=datetime.fromisoformat(e['need_by'].replace('Z','+00:00'))
    return begin+timedelta(minutes=e['route_minutes'])<=due

def reconcile(db,key,event_id):
    j=one(db,'SELECT * FROM journey WHERE id=?',(key,))
    lift=one(db,'SELECT * FROM lift WHERE id=?',(j['lift_id'],))
    segments=many(db,'SELECT s.*,l.width_cm,l.state AS lift_state,l.revision AS lift_revision FROM route_segment s JOIN lift l ON l.id=s.lift_id WHERE s.journey_id=? ORDER BY s.position',(key,))
    route_revision=sum((s['position']+1)*s['lift_revision'] for s in segments)
    delivered=one(db,"SELECT * FROM assistance WHERE journey_id=? AND state IN ('fulfilled','remedy') ORDER BY delivered_at DESC LIMIT 1",(key,))
    if delivered:
        state,reason=('served','confirmed_delivery') if delivered['state']=='fulfilled' else ('delivered_late','compensation_due')
    elif any(j['wheelchair_width']>s['width_cm'] for s in segments):state,reason='unreachable','door_too_narrow'
    elif not _arrival_ok(j):state,reason='missed_connection','route_exceeds_connection'
    elif any(s['lift_state']!='open' for s in segments):state,reason='reroute','route_lift_unavailable'
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
    if not last or (last['state'],last['lift_revision'])!=(state,route_revision):
        rev=1 if not last else last['revision']+1
        db.execute('INSERT INTO route(journey_id,revision,lift_revision,state,reason,event_id) VALUES(?,?,?,?,?,?)',
                   (key,rev,route_revision,state,reason,event_id))
        if old_state=='committed' and state not in ('committed','served','delivered_late'):
            db.execute('INSERT INTO obligation(journey_id,kind,reason) VALUES(?,?,?)',(key,'replacement',reason))
    log(db,event_id,'journey',key,state,reason)
    return state

def reconcile_for_lift(db,lift_id,event_id):
    for row in many(db,'SELECT DISTINCT journey_id AS id FROM route_segment WHERE lift_id=?',(lift_id,)):
        reconcile(db,row['id'],event_id)
    if one(db,"SELECT state FROM lift WHERE id=?",(lift_id,))['state']=='open':
        for row in many(db,"SELECT DISTINCT o.id,o.journey_id FROM obligation o JOIN route_segment s ON s.journey_id=o.journey_id WHERE s.lift_id=? AND o.kind='replacement' AND o.resolved=0",(lift_id,)):
            active=one(db,"SELECT 1 FROM assistance WHERE journey_id=? AND state IN ('assigned','fulfilled','remedy')",(row['journey_id'],))
            if not active:db.execute('UPDATE obligation SET resolved=1 WHERE id=?',(row['id'],))

def reconcile_for_staff(db,staff_id,event_id):
    for row in many(db,'SELECT id FROM journey WHERE staff_id=?',(staff_id,)):
        reconcile(db,row['id'],event_id)

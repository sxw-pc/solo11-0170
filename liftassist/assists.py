"""Independent staffing, delivery and compensation evidence."""
from datetime import datetime
import math
from .store import one,many,log

def terms(db,e):
    if e['revision']<=0 or e['rate_per_5min_cents']<0 or e['max_cents']<0:
        raise ValueError('invalid_compensation_contract')
    old=one(db,'SELECT * FROM compensation_contract WHERE id=? ORDER BY revision DESC LIMIT 1',(e['contract_id'],))
    if old:
        if old['revision']==e['revision'] and (old['rate_per_5min_cents'],old['max_cents'])!=(e['rate_per_5min_cents'],e['max_cents']):
            raise ValueError('parallel_compensation_contract')
        if old['revision']>=e['revision']:return 'stale'
    db.execute('INSERT INTO compensation_contract VALUES(?,?,?,?,?,?)',
               (e['contract_id'],e['revision'],e['effective_from'],e['rate_per_5min_cents'],e['max_cents'],e['id']))
    log(db,e['id'],'assistance',e['contract_id'],'terms_active','compensation_contract_version')
    return 'terms_active'

def qualify(db,e):
    if e['issued_at']>=e['expires_at'] or e['role']!='accessible_assist':raise ValueError('invalid_staff_qualification')
    db.execute('INSERT INTO staff_qualification VALUES(?,?,?,?,?,NULL)',
        (e['qualification_id'],e['staff_id'],e['role'],e['issued_at'],e['expires_at']))
    log(db,e['id'],'assistance',e['staff_id'],'qualified','valid_assistance_role')
    return 'qualified'

def schedule(db,e):
    if e['starts']>=e['ends']:raise ValueError('invalid_shift_window')
    if not one(db,'SELECT 1 FROM staff_qualification WHERE staff_id=?',(e['staff_id'],)):
        raise ValueError('staff_not_qualified')
    overlap=one(db,"SELECT 1 FROM staff_shift WHERE staff_id=? AND state='active' AND starts<? AND ends>?",
                (e['staff_id'],e['ends'],e['starts']))
    if overlap:raise ValueError('overlapping_staff_shift')
    db.execute("INSERT INTO staff_shift VALUES(?,?,?,?,?,'active')",
        (e['shift_id'],e['staff_id'],e['station'],e['starts'],e['ends']))
    log(db,e['id'],'assistance',e['staff_id'],'scheduled','shift_covers_station')
    return 'scheduled'

def revoke(db,e):
    cert=one(db,'SELECT * FROM staff_qualification WHERE id=?',(e['qualification_id'],))
    if not cert:raise ValueError('unknown_qualification')
    if e['revoked_at']<=cert['issued_at']:raise ValueError('invalid_revocation_time')
    db.execute('UPDATE staff_qualification SET revoked_at=? WHERE id=?',(e['revoked_at'],e['qualification_id']))
    from .journeys import reconcile_for_staff
    reconcile_for_staff(db,cert['staff_id'],e['id'])
    log(db,e['id'],'assistance',cert['staff_id'],'revoked','staff_no_longer_authorized')
    return 'revoked'

def record(db,e):
    j=one(db,'SELECT * FROM journey WHERE id=?',(e['journey_id'],))
    if not j:raise ValueError('unknown_journey')
    if j['state'] in ('unreachable','missed_connection'):raise ValueError('journey_has_no_authorized_route')
    if one(db,"SELECT 1 FROM assistance WHERE journey_id=? AND state IN ('fulfilled','remedy')",(e['journey_id'],)):
        raise ValueError('journey_delivery_already_recorded')
    if e['staff_id']!=j['staff_id']:raise ValueError('unassigned_staff')
    if e['arrived_at']<j['requested_at'] or e['delivered_at']<e['arrived_at']:
        raise ValueError('assist_time_order')
    cert=one(db,"SELECT * FROM staff_qualification WHERE staff_id=? AND issued_at<=? AND expires_at>=? AND (revoked_at IS NULL OR revoked_at>?) ORDER BY issued_at DESC LIMIT 1",
             (e['staff_id'],e['arrived_at'],e['delivered_at'],e['arrived_at']))
    lift=one(db,'SELECT station FROM lift WHERE id=?',(j['lift_id'],))
    shift=one(db,"SELECT 1 FROM staff_shift WHERE staff_id=? AND station=? AND state='active' AND starts<=? AND ends>=?",
              (e['staff_id'],lift['station'],e['arrived_at'],e['delivered_at']))
    if not cert or not shift:
        state,reason='disputed','staff_not_qualified_for_delivery'
    elif not e.get('witness') or e['witness']==e['staff_id'] or not e.get('passenger_confirmed'):
        state,reason='unverified','independent_passenger_evidence_missing'
    elif e['delivered_at']>j['need_by']:
        state,reason='remedy','late_delivery'
    else:state,reason='fulfilled','on_time_passenger_confirmation'
    db.execute('INSERT INTO assistance VALUES(?,?,?,?,?,?,?,?,?)',
        (e['assist_id'],e['journey_id'],e['staff_id'],e['arrived_at'],e['delivered_at'],e.get('witness'),int(bool(e.get('passenger_confirmed'))),state,reason))
    if state=='remedy':
        db.execute('INSERT INTO obligation(journey_id,kind,reason) VALUES(?,?,?)',(e['journey_id'],'delay_compensation',reason))
        delay=math.ceil((datetime.fromisoformat(e['delivered_at'].replace('Z','+00:00'))-datetime.fromisoformat(j['need_by'].replace('Z','+00:00'))).total_seconds()/60)
        contract=one(db,'SELECT * FROM compensation_contract WHERE effective_from<=? ORDER BY effective_from DESC,revision DESC LIMIT 1',(j['requested_at'],))
        amount=min(math.ceil(delay/5)*contract['rate_per_5min_cents'],contract['max_cents']) if contract else 0
        db.execute('INSERT INTO compensation(journey_id,assist_id,contract_id,contract_revision,delay_minutes,amount_cents,state,reason) VALUES(?,?,?,?,?,?,?,?)',
                   (j['id'],e['assist_id'],contract['id'] if contract else None,contract['revision'] if contract else None,
                    delay,amount,'due' if contract else 'pending_terms','contract_rate' if contract else 'missing_effective_terms'))
    if state=='fulfilled':
        db.execute("UPDATE obligation SET resolved=1 WHERE journey_id=? AND kind='replacement'",(e['journey_id'],))
    from .journeys import reconcile
    reconcile(db,e['journey_id'],e['id'])
    log(db,e['id'],'assistance',e['assist_id'],state,reason)
    return state

def report(db):
    return [dict(r) for r in many(db,'SELECT * FROM assistance ORDER BY id')]

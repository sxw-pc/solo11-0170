"""Facility revision, faults and independent load retests."""
from .store import one,many,log

def register(db,e):
    if e['width_cm']<=0 or e['capacity']<=0 or e['revision']<=0:raise ValueError('invalid_lift_spec')
    old=one(db,'SELECT * FROM lift WHERE id=?',(e['lift_id'],))
    if old and e['revision']<=old['revision']:
        if e['revision']==old['revision'] and (e['station'],e['width_cm'],e['capacity'])!=(old['station'],old['width_cm'],old['capacity']):
            db.execute("UPDATE lift SET state='frozen',reason='parallel_revision' WHERE id=?",(e['lift_id'],))
            log(db,e['id'],'lift',e['lift_id'],'frozen','parallel_revision');return 'frozen'
        return 'stale'
    db.execute("INSERT INTO lift VALUES(?,?,?,?,?,'open','qualified') ON CONFLICT(id) DO UPDATE SET station=excluded.station,width_cm=excluded.width_cm,capacity=excluded.capacity,revision=excluded.revision,state='open',reason='qualified'",(e['lift_id'],e['station'],e['width_cm'],e['capacity'],e['revision']))
    return reconcile(db,e['lift_id'],e['id'])

def fault(db,e):
    if not one(db,'SELECT 1 FROM lift WHERE id=?',(e['lift_id'],)):raise ValueError('unknown_lift')
    old=one(db,'SELECT * FROM fault WHERE id=?',(e['fault_id'],))
    if old:
        if old['lift_id']!=e['lift_id'] or old['opened_at']!=e['opened_at']:
            db.execute("UPDATE lift SET state='frozen',reason='fault_identity_conflict' WHERE id=?",(e['lift_id'],))
            log(db,e['id'],'lift',e['lift_id'],'frozen','fault_identity_conflict');return 'frozen'
        return 'stale'
    db.execute('INSERT INTO fault(id,lift_id,opened_at,sha) VALUES(?,?,?,?)',(e['fault_id'],e['lift_id'],e['opened_at'],e['id']))
    return reconcile(db,e['lift_id'],e['id'])

def repair(db,e):
    f=one(db,'SELECT * FROM fault WHERE id=?',(e['fault_id'],))
    if not f:raise ValueError('unknown_fault')
    if f['closed_at']:raise ValueError('fault_already_closed')
    if e['closed_at']<=f['opened_at'] or not e.get('witness'):raise ValueError('repair_witness_required')
    db.execute('UPDATE fault SET closed_at=?,witness=? WHERE id=?',(e['closed_at'],e['witness'],e['fault_id']))
    return reconcile(db,f['lift_id'],e['id'])

def retest(db,e):
    lift=one(db,'SELECT * FROM lift WHERE id=?',(e['lift_id'],))
    if not lift:raise ValueError('unknown_lift')
    passed=int(bool(e['passed']) and e['load_kg']>=lift['capacity'] and bool(e['inspector']))
    db.execute('INSERT INTO retest VALUES(?,?,?,?,?,?)',(e['id'],e['lift_id'],e['at'],e['load_kg'],e['inspector'],passed))
    return reconcile(db,e['lift_id'],e['id'])

def reconcile(db,key,event_id):
    lift=one(db,'SELECT * FROM lift WHERE id=?',(key,))
    open_faults=many(db,'SELECT * FROM fault WHERE lift_id=? AND closed_at IS NULL',(key,))
    repaired=many(db,'SELECT * FROM fault WHERE lift_id=? AND closed_at IS NOT NULL ORDER BY closed_at DESC',(key,))
    state='frozen' if lift['state']=='frozen' else 'blocked' if open_faults else 'open'
    reason='parallel_revision' if state=='frozen' else 'active_fault' if open_faults else 'qualified'
    if repaired and not open_faults and state!='frozen':
        recent=one(db,'SELECT * FROM retest WHERE lift_id=? AND at>=? ORDER BY at DESC LIMIT 1',(key,repaired[0]['closed_at']))
        if not recent or not recent['passed']:state,reason='awaiting_retest','repair_without_valid_retest'
    db.execute('UPDATE lift SET state=?,reason=? WHERE id=?',(state,reason,key))
    log(db,event_id,'lift',key,state,reason)
    from .journeys import reconcile_for_lift
    reconcile_for_lift(db,key,event_id)
    return state

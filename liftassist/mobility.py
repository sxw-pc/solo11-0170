"""Offline wheelchair asset, reservation and handover events."""
from .store import one, log
from .asset_custody import reconcile_asset

CUSTODY_NOT_LENDABLE = ('pending_verification', 'sealed', 'in_transit', 'missing', 'conflict')


def register(db, e):
    if e['width_cm'] <= 0 or not e['station']:
        raise ValueError('invalid_mobility_asset')
    if one(db, 'SELECT 1 FROM mobility_asset WHERE id=?', (e['asset_id'],)):
        raise ValueError('asset_id_conflict')
    db.execute("INSERT INTO mobility_asset VALUES(?,?,?,'ready','registered')",
               (e['asset_id'], e['station'], e['width_cm']))
    log(db, e['id'], 'mobility', e['asset_id'], 'ready', 'asset_registered')
    return 'ready'


def reserve(db, e):
    j = one(db, 'SELECT * FROM journey WHERE id=?', (e['journey_id'],))
    a = one(db, 'SELECT * FROM mobility_asset WHERE id=?', (e['asset_id'],))
    if not j or not a:
        raise ValueError('mobility_inputs_missing')
    station = one(db, 'SELECT station FROM lift WHERE id=?', (j['lift_id'],))['station']
    if a['station'] != station or a['width_cm'] < j['wheelchair_width']:
        raise ValueError('mobility_asset_not_fit')
    if a['state'] != 'ready' or j['state'] not in ('committed', 'waitlist'):
        raise ValueError('mobility_reservation_unavailable')
    custody = one(db, 'SELECT physical_state FROM asset_custody WHERE asset_id=?', (a['id'],))
    if custody and custody['physical_state'] in CUSTODY_NOT_LENDABLE:
        raise ValueError('mobility_asset_' + custody['physical_state'])
    if e['needed_at'] < j['requested_at'] or e['needed_at'] > j['need_by']:
        raise ValueError('mobility_reservation_outside_journey')
    db.execute("INSERT INTO mobility_reservation VALUES(?,?,?,?,'reserved')",
               (e['reservation_id'], a['id'], j['id'], e['needed_at']))
    db.execute("UPDATE mobility_asset SET state='reserved',reason='journey_reservation' WHERE id=?", (a['id'],))
    log(db, e['id'], 'mobility', a['id'], 'reserved', 'journey_reservation')
    return 'reserved'


def loan(db, e):
    r = one(db, 'SELECT * FROM mobility_reservation WHERE id=?', (e['reservation_id'],))
    if not r or r['state'] != 'reserved' or e['loaned_at'] > r['needed_at']:
        raise ValueError('mobility_loan_unavailable')
    db.execute("INSERT INTO mobility_loan VALUES(?,?,?,NULL,NULL,'loaned')",
               (e['loan_id'], r['id'], e['loaned_at']))
    db.execute("UPDATE mobility_reservation SET state='loaned' WHERE id=?", (r['id'],))
    db.execute("UPDATE mobility_asset SET state='loaned',reason='handed_to_passenger' WHERE id=?", (r['asset_id'],))
    log(db, e['id'], 'mobility', r['asset_id'], 'loaned', 'handed_to_passenger')
    reconcile_asset(db, r['asset_id'], e['id'])
    return 'loaned'


def return_asset(db, e):
    l = one(db, 'SELECT * FROM mobility_loan WHERE id=?', (e['loan_id'],))
    if not l or l['state'] != 'loaned' or e['returned_at'] < l['loaned_at']:
        raise ValueError('mobility_return_unavailable')
    r = one(db, 'SELECT * FROM mobility_reservation WHERE id=?', (l['reservation_id'],))
    state = 'ready' if e['condition'] == 'clean' and e.get('witness') else 'quarantine'
    db.execute('UPDATE mobility_loan SET returned_at=?,condition=?,state=? WHERE id=?',
               (e['returned_at'], e['condition'], 'returned', l['id']))
    db.execute('UPDATE mobility_reservation SET state=? WHERE id=?', ('completed', r['id']))
    db.execute('UPDATE mobility_asset SET state=?,reason=? WHERE id=?',
               (state, 'return_inspected' if state == 'ready' else 'return_requires_inspection', r['asset_id']))
    log(db, e['id'], 'mobility', r['asset_id'], state, 'return_condition')
    reconcile_asset(db, r['asset_id'], e['id'])
    return state

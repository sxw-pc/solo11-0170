"""Separated physical-asset vs RFID-tag identity, handover reconciliation and loss tracing.

Three identities are never mixed: the detachable tag (EPC), the non-fungible
physical wheelchair (engraved frame serial) and the custodian (holder). Tag
scans and manual receipts are only *claims*; frame witnesses, location
weight/silhouette observations and carrier-sealed frame receipts are
independent physical evidence. Pending-verification stock is never lendable.
"""
import json
from datetime import datetime
from .store import one, many, log

FRAME_TRAVEL_MINUTES = 30  # two frame sightings closer than this cannot both be true


def _ts(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


# ---------- evidence intake ----------

def enroll_frame(db, e):
    if not one(db, 'SELECT 1 FROM mobility_asset WHERE id=?', (e['asset_id'],)):
        raise ValueError('unknown_mobility_asset')
    old = one(db, 'SELECT * FROM asset_frame WHERE asset_id=?', (e['asset_id'],))
    if old:
        if old['frame_serial'] == e['frame_serial']:
            return 'stale'
        raise ValueError('asset_frame_already_enrolled')
    if one(db, 'SELECT 1 FROM asset_frame WHERE frame_serial=?', (e['frame_serial'],)):
        raise ValueError('frame_serial_conflict')
    db.execute('INSERT INTO asset_frame VALUES(?,?,?,?)',
               (e['asset_id'], e['frame_serial'], e['enrolled_at'], e['certificate']))
    return reconcile_asset(db, e['asset_id'], e['id'])


def register_reader(db, e):
    db.execute('INSERT INTO reader_position VALUES(?,?,?,?) ON CONFLICT(reader) DO UPDATE SET '
               'location=excluded.location,station=excluded.station,registered_at=excluded.registered_at',
               (e['reader'], e['location'], e['station'], e['registered_at']))
    log(db, e['id'], 'asset_custody', e['reader'], 'captured', 'reader_position')
    return 'captured'


def tag_certificate(db, e):
    if e['action'] not in ('write', 'attach', 'detach'):
        raise ValueError('invalid_tag_action')
    if not one(db, 'SELECT 1 FROM mobility_asset WHERE id=?', (e['asset_id'],)):
        raise ValueError('unknown_mobility_asset')
    if one(db, 'SELECT 1 FROM tag_certificate WHERE id=?', (e['cert_id'],)):
        return 'stale'
    db.execute('INSERT INTO tag_certificate VALUES(?,?,?,?,?,?,?)',
               (e['cert_id'], e['epc'], e['asset_id'], e['action'], e['actor'], e['effective_at'], e['certificate']))
    # a late detach/rewrite recomputes every asset currently bound to this EPC
    affected = sorted({e['asset_id']} | set(assets_for_epc(db, e['epc'])))
    state = None
    for asset_id in affected:
        state = reconcile_asset(db, asset_id, e['id'])
    return state


def frame_witness(db, e):
    if not one(db, 'SELECT 1 FROM mobility_asset WHERE id=?', (e['asset_id'],)):
        raise ValueError('unknown_mobility_asset')
    if not e.get('observer') or not e.get('frame_serial'):
        raise ValueError('frame_witness_incomplete')
    db.execute('INSERT INTO frame_witness VALUES(?,?,?,?,?,?)',
               (e['witness_id'], e['asset_id'], e['frame_serial'], e['location'], e['observed_at'], e['observer']))
    return reconcile_asset(db, e['asset_id'], e['id'])


def observe_location(db, e):
    db.execute('INSERT INTO location_observation VALUES(?,?,?,?,?,?)',
               (e['obs_id'], e['location'], e['observed_at'], e['weight_kg'], e['silhouette'], e['observer']))
    affected = {r['asset_id'] for r in many(db, 'SELECT DISTINCT asset_id FROM custody_receipt WHERE location=?', (e['location'],))}
    affected |= {r['asset_id'] for r in many(db, 'SELECT asset_id FROM asset_custody WHERE location=?', (e['location'],))}
    for asset_id in sorted(affected):
        reconcile_asset(db, asset_id, e['id'])
    log(db, e['id'], 'asset_custody', e['obs_id'], 'captured', 'location_observation')
    return 'captured'


def hold(db, e):
    if not one(db, 'SELECT 1 FROM mobility_asset WHERE id=?', (e['asset_id'],)):
        raise ValueError('unknown_mobility_asset')
    if one(db, "SELECT 1 FROM custody_hold WHERE asset_id=? AND state='active'", (e['asset_id'],)):
        raise ValueError('custody_hold_active')
    if one(db, "SELECT 1 FROM asset_transfer WHERE asset_id=? AND state IN ('in_transit','overdue_claim')", (e['asset_id'],)):
        raise ValueError('asset_in_transit')
    db.execute('INSERT INTO custody_hold VALUES(?,?,?,?,?,?,?)',
               (e['hold_id'], e['asset_id'], e['location'], e['ordered_by'], e['ordered_at'],
                'active', 'sealed_pending_physical_check'))
    return reconcile_asset(db, e['asset_id'], e['id'])


def inventory_check(db, e):
    h = one(db, 'SELECT * FROM custody_hold WHERE id=?', (e['hold_id'],))
    if not h or h['state'] != 'active':
        raise ValueError('custody_hold_not_active')
    if h['asset_id'] != e['asset_id']:
        raise ValueError('inventory_check_asset_mismatch')
    frame = one(db, 'SELECT frame_serial FROM asset_frame WHERE asset_id=?', (e['asset_id'],))
    found = int(bool(e.get('found')) and frame and e.get('frame_serial') == frame['frame_serial'])
    db.execute('INSERT INTO inventory_check VALUES(?,?,?,?,?,?,?,?)',
               (e['check_id'], e['hold_id'], e['asset_id'], e['location'], e.get('frame_serial') or '',
                e['checked_at'], e['clerk'], found))
    if found:
        db.execute("UPDATE custody_hold SET state='released',reason='frame_verified' WHERE id=?", (e['hold_id'],))
    else:
        db.execute("UPDATE custody_hold SET state='escalated',reason='missing_investigation' WHERE id=?", (e['hold_id'],))
    return reconcile_asset(db, e['asset_id'], e['id'])


def transfer_authorize(db, e):
    if not one(db, 'SELECT 1 FROM mobility_asset WHERE id=?', (e['asset_id'],)):
        raise ValueError('unknown_mobility_asset')
    if not e.get('authorized_by') or not e.get('carrier') or e['due_by'] <= e['authorized_at']:
        raise ValueError('invalid_transfer_window')
    if one(db, "SELECT 1 FROM custody_hold WHERE asset_id=? AND state='active'", (e['asset_id'],)):
        raise ValueError('custody_hold_active')
    if one(db, "SELECT 1 FROM asset_transfer WHERE asset_id=? AND state IN ('in_transit','overdue_claim')", (e['asset_id'],)):
        raise ValueError('transfer_already_open')
    db.execute('INSERT INTO asset_transfer VALUES(?,?,?,?,?,?,?,?,?,?)',
               (e['transfer_id'], e['asset_id'], e['from_station'], e['to_station'], e['carrier'],
                e['authorized_by'], e['authorized_at'], e['due_by'], 'in_transit', 'owner_authorized'))
    return reconcile_asset(db, e['asset_id'], e['id'])


def transfer_receive(db, e):
    t = one(db, 'SELECT * FROM asset_transfer WHERE id=?', (e['transfer_id'],))
    if not t or t['state'] not in ('in_transit', 'overdue_claim'):
        raise ValueError('transfer_not_receivable')
    if t['asset_id'] != e['asset_id']:
        raise ValueError('transfer_asset_mismatch')
    db.execute('INSERT INTO transfer_receipt VALUES(?,?,?,?,?,?,?,?)',
               (e['receive_id'], e['transfer_id'], e['asset_id'], e['frame_serial'], e['location'],
                e['received_at'], e['receiver'], int(bool(e.get('seal_intact')))))
    frame = one(db, 'SELECT frame_serial FROM asset_frame WHERE asset_id=?', (e['asset_id'],))
    if frame and e['frame_serial'] == frame['frame_serial']:
        if t['state'] == 'overdue_claim':
            state = 'recovered_after_claim'
        else:
            state = 'completed_late' if e['received_at'] > t['due_by'] else 'completed'
        db.execute('UPDATE asset_transfer SET state=?,reason=? WHERE id=?', (state, 'frame_verified_receipt', e['transfer_id']))
        if state == 'recovered_after_claim':
            db.execute("UPDATE carrier_claim SET state='closed' WHERE transfer_id=? AND state='open'", (e['transfer_id'],))
    # a wrong-frame receipt is preserved as evidence; the transfer stays open
    # and reconcile raises a locatable frame_serial_mismatch conflict instead
    return reconcile_asset(db, e['asset_id'], e['id'])


def carrier_claim(db, e):
    t = one(db, 'SELECT * FROM asset_transfer WHERE id=?', (e['transfer_id'],))
    if not t or t['state'] != 'in_transit':
        raise ValueError('carrier_claim_unavailable')
    if e['claimed_at'] <= t['due_by']:
        raise ValueError('carrier_claim_before_due')
    db.execute('INSERT INTO carrier_claim VALUES(?,?,?,?,?)',
               (e['claim_id'], e['transfer_id'], e['claimed_at'], e['claimant'], 'open'))
    db.execute("UPDATE asset_transfer SET state='overdue_claim',reason='overdue_no_receipt' WHERE id=?", (e['transfer_id'],))
    return reconcile_asset(db, t['asset_id'], e['id'])


# ---------- tag binding generations ----------

def _tag_windows(db, asset_id):
    """Binding generations (epc, start, end) from installs and write/detach certificates."""
    events = [(t['installed_at'], 'attach', t['epc'])
              for t in many(db, 'SELECT epc,installed_at FROM mobility_tag WHERE asset_id=?', (asset_id,))]
    events += [(c['effective_at'], c['action'], c['epc'])
               for c in many(db, 'SELECT epc,action,effective_at FROM tag_certificate WHERE asset_id=?', (asset_id,))]
    events.sort()
    windows, current = [], None
    for at, action, epc in events:
        if action in ('attach', 'write'):
            if current:
                windows.append((current[0], current[1], at))
            current = (epc, at)
        elif action == 'detach' and current:
            windows.append((current[0], current[1], at))
            current = None
    if current:
        windows.append((current[0], current[1], None))
    return windows


def _open_binding(db, asset_id):
    open_windows = [w for w in _tag_windows(db, asset_id) if w[2] is None]
    return open_windows[-1] if open_windows else None


def assets_for_epc(db, epc, at=None):
    """Assets with an open binding on epc, or whose binding covered time `at`."""
    out = set()
    for row in many(db, 'SELECT id FROM mobility_asset ORDER BY id'):
        for w_epc, start, end in _tag_windows(db, row['id']):
            if w_epc != epc:
                continue
            if at is None and end is None:
                out.add(row['id'])
            elif at is not None and start <= at and (end is None or at < end):
                out.add(row['id'])
    return sorted(out)


# ---------- claims vs physical evidence ----------

def _claims(db, asset_id, asset):
    """Tag-class location claims: (time, location, holder, source). Holder may be None."""
    claims = [(r['observed_at'], r['location'], r['holder'], 'receipt:' + r['id'])
              for r in many(db, 'SELECT * FROM custody_receipt WHERE asset_id=?', (asset_id,))]
    for epc, start, end in _tag_windows(db, asset_id):
        for s in many(db, 'SELECT * FROM rfid_scan WHERE epc=? AND observed_at>=?', (epc, start)):
            if end and s['observed_at'] >= end:
                continue  # tag already detached: the read says nothing about the asset
            rp = one(db, 'SELECT location FROM reader_position WHERE reader=?', (s['reader'],))
            claims.append((s['observed_at'], rp['location'] if rp else s['reader'], None, 'scan:' + s['id']))
    ret = one(db, "SELECT ml.returned_at FROM mobility_loan ml JOIN mobility_reservation mr ON mr.id=ml.reservation_id "
                  "WHERE mr.asset_id=? AND ml.state='returned' ORDER BY ml.returned_at DESC LIMIT 1", (asset_id,))
    if ret:
        claims.append((ret['returned_at'], asset['station'], 'station:' + asset['station'], 'return:' + asset_id))
    claims.sort()
    return claims


def _physical(db, asset_id, frame_serial):
    """Independent physical evidence: (time, location, source, observer)."""
    proof = []
    for w in many(db, 'SELECT * FROM frame_witness WHERE asset_id=?', (asset_id,)):
        if frame_serial and w['frame_serial'] != frame_serial:
            continue  # mismatch is surfaced as a conflict, not as proof
        proof.append((w['observed_at'], w['location'], 'frame_witness:' + w['id'], w['observer']))
    for c in many(db, 'SELECT * FROM inventory_check WHERE asset_id=? AND found=1', (asset_id,)):
        proof.append((c['checked_at'], c['location'], 'inventory_check:' + c['id'], c['clerk']))
    for r in many(db, 'SELECT * FROM transfer_receipt WHERE asset_id=?', (asset_id,)):
        if frame_serial and r['frame_serial'] != frame_serial:
            continue
        proof.append((r['received_at'], r['location'], 'transfer_receipt:' + r['id'], r['receiver']))
    proof.sort()
    return proof


def _holder(location, claims, proof):
    for claim in reversed(claims):
        if claim[2] and claim[1] == location:
            return claim[2]
    if proof and proof[-1][2].startswith('transfer_receipt:'):
        return proof[-1][3]
    return 'unknown'


def _conflicts(db, asset_id, frame_serial, claims, proof, obs):
    out = []
    open_w = _open_binding(db, asset_id)
    if open_w:
        others = [a for a in assets_for_epc(db, open_w[0]) if a != asset_id]
        if others:
            out.append(('epc_shared', '', open_w[1],
                        json.dumps({'epc': open_w[0], 'other_assets': others}, sort_keys=True)))
    if frame_serial:
        for w in many(db, 'SELECT id,location,observed_at,frame_serial FROM frame_witness WHERE asset_id=? AND frame_serial<>?',
                      (asset_id, frame_serial)):
            out.append(('frame_serial_mismatch', w['location'], w['observed_at'],
                        json.dumps({'expected': frame_serial, 'observed': w['frame_serial'], 'event': w['id']})))
        for r in many(db, 'SELECT id,location,received_at,frame_serial FROM transfer_receipt WHERE asset_id=? AND frame_serial<>?',
                      (asset_id, frame_serial)):
            out.append(('frame_serial_mismatch', r['location'], r['received_at'],
                        json.dumps({'expected': frame_serial, 'observed': r['frame_serial'], 'event': r['id']})))
        for c in many(db, "SELECT id,location,checked_at,frame_serial FROM inventory_check WHERE asset_id=? AND frame_serial<>'' AND frame_serial<>?",
                      (asset_id, frame_serial)):
            out.append(('frame_serial_mismatch', c['location'], c['checked_at'],
                        json.dumps({'expected': frame_serial, 'observed': c['frame_serial'], 'event': c['id']})))
    witnesses = [w for w in many(db, 'SELECT * FROM frame_witness WHERE asset_id=? ORDER BY observed_at', (asset_id,))
                 if not frame_serial or w['frame_serial'] == frame_serial]
    for a, b in zip(witnesses, witnesses[1:]):
        if a['location'] != b['location'] and (_ts(b['observed_at']) - _ts(a['observed_at'])).total_seconds() <= FRAME_TRAVEL_MINUTES * 60:
            out.append(('frame_in_two_places', b['location'], b['observed_at'],
                        json.dumps({'locations': [a['location'], b['location']], 'witnesses': [a['id'], b['id']]})))
    if claims and proof:
        claim, phys = claims[-1], proof[-1]
        # a signed transfer receipt explains the new location; anything else is unexplained movement
        if phys[0] >= claim[0] and phys[1] != claim[1] and not phys[2].startswith('transfer_receipt:'):
            out.append(('claim_contradicted', claim[1], phys[0],
                        json.dumps({'claim': claim[3], 'claim_location': claim[1],
                                    'physical': phys[2], 'physical_location': phys[1]})))
    if claims and obs and obs['silhouette'] in ('tray', 'empty'):
        if not (proof and proof[-1][0] >= obs['observed_at'] and proof[-1][1] != claims[-1][1]):
            out.append(('absent_at_claimed_location', claims[-1][1], obs['observed_at'],
                        json.dumps({'observation': obs['id'], 'silhouette': obs['silhouette'], 'claim': claims[-1][3]})))
    return out


# ---------- derived current custody ----------

def reconcile_asset(db, asset_id, event_id):
    asset = one(db, 'SELECT * FROM mobility_asset WHERE id=?', (asset_id,))
    if not asset:
        raise ValueError('unknown_mobility_asset')
    frame = one(db, 'SELECT * FROM asset_frame WHERE asset_id=?', (asset_id,))
    frame_serial = frame['frame_serial'] if frame else None
    claims = _claims(db, asset_id, asset)
    proof = _physical(db, asset_id, frame_serial)
    obs = None
    if claims:
        obs = one(db, 'SELECT * FROM location_observation WHERE location=? AND observed_at>=? '
                      'ORDER BY observed_at DESC LIMIT 1', (claims[-1][1], claims[-1][0]))
        if obs and obs['silhouette'] == 'wheelchair':
            proof.append((obs['observed_at'], claims[-1][1], 'location_observation:' + obs['id'], obs['observer']))
            proof.sort()
    conflicts = _conflicts(db, asset_id, frame_serial, claims, proof, obs)
    # separated tag identity view: where the tag was last seen, regardless of the asset
    open_w = _open_binding(db, asset_id)
    windows = _tag_windows(db, asset_id)
    if open_w:
        tag_epc = open_w[0]
        tag_state = 'shared' if len(assets_for_epc(db, tag_epc)) > 1 else 'bound'
    elif windows:
        tag_epc, tag_state = windows[-1][0], 'detached'
    else:
        tag_epc, tag_state = '', 'none'
    tag_location = ''
    if tag_epc:
        scan = one(db, 'SELECT reader FROM rfid_scan WHERE epc=? ORDER BY observed_at DESC LIMIT 1', (tag_epc,))
        if scan:
            rp = one(db, 'SELECT location FROM reader_position WHERE reader=?', (scan['reader'],))
            tag_location = rp['location'] if rp else scan['reader']
    loan = one(db, "SELECT mr.journey_id FROM mobility_loan ml JOIN mobility_reservation mr ON mr.id=ml.reservation_id "
                   "WHERE mr.asset_id=? AND ml.state='loaned' ORDER BY ml.loaned_at DESC LIMIT 1", (asset_id,))
    active_hold = one(db, "SELECT * FROM custody_hold WHERE asset_id=? AND state IN ('active','escalated') "
                          "ORDER BY ordered_at DESC LIMIT 1", (asset_id,))
    transfer = one(db, "SELECT * FROM asset_transfer WHERE asset_id=? AND state IN ('in_transit','overdue_claim') "
                       "ORDER BY authorized_at DESC LIMIT 1", (asset_id,))
    only_absence = conflicts and all(k == 'absent_at_claimed_location' for k, *_ in conflicts)
    if loan:
        state, reason, location, holder = 'on_loan', 'handed_to_passenger', 'OUT_OF_STATION', 'journey:' + loan['journey_id']
    elif active_hold and active_hold['state'] == 'escalated':
        state, reason, location, holder = 'missing', 'missing_investigation', active_hold['location'], active_hold['ordered_by']
    elif transfer and transfer['state'] == 'overdue_claim':
        state, reason, location, holder = 'missing', 'carrier_overdue', 'IN_TRANSIT', transfer['carrier']
    elif conflicts and only_absence:
        state, reason, location = 'missing', 'absent_at_claimed_location', claims[-1][1]
        holder = _holder(location, claims, proof)
    elif conflicts:
        state, reason = 'conflict', conflicts[0][0]
        location = proof[-1][1] if proof else (claims[-1][1] if claims else asset['station'])
        holder = _holder(location, claims, proof)
    elif transfer:
        state, reason, location, holder = 'in_transit', 'owner_authorized_transfer', 'IN_TRANSIT', transfer['carrier']
    elif active_hold:
        state, reason, location = 'sealed', 'sealed_pending_physical_check', active_hold['location']
        holder = _holder(location, claims, proof)
        if holder == 'unknown':
            holder = active_hold['ordered_by']
    elif proof and (not claims or proof[-1][0] >= claims[-1][0]):
        state, reason, location = 'verified', proof[-1][2].split(':')[0], proof[-1][1]
        holder = _holder(location, claims, proof)
    elif claims:
        state, reason, location = 'pending_verification', 'claims_without_physical_proof', claims[-1][1]
        holder = _holder(location, claims, proof)
    else:
        state, reason, location, holder = 'untracked', 'no_custody_evidence', asset['station'], 'station:' + asset['station']
    db.execute('DELETE FROM asset_conflict WHERE asset_id=?', (asset_id,))
    for kind, loc, at, detail in conflicts:
        db.execute('INSERT INTO asset_conflict(asset_id,kind,location,observed_at,detail,state) VALUES(?,?,?,?,?,?)',
                   (asset_id, kind, loc, at, detail, 'open'))
    db.execute('INSERT INTO asset_custody VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET '
               'holder=excluded.holder,location=excluded.location,physical_state=excluded.physical_state,'
               'reason=excluded.reason,tag_epc=excluded.tag_epc,tag_location=excluded.tag_location,'
               'tag_state=excluded.tag_state,updated_by=excluded.updated_by',
               (asset_id, holder, location, state, reason, tag_epc, tag_location, tag_state, event_id))
    log(db, event_id, 'asset_custody', asset_id, state, reason)
    return state


def location_report(db):
    """Per-location physical stock: only verified+ready assets count as available."""
    out = {}
    keys = ('available', 'pending_verification', 'sealed', 'in_transit', 'missing', 'conflict', 'on_loan', 'untracked')
    for c in many(db, 'SELECT * FROM asset_custody ORDER BY asset_id'):
        slot = out.setdefault(c['location'], {'location': c['location'], **{k: 0 for k in keys}})
        mob = one(db, 'SELECT state FROM mobility_asset WHERE id=?', (c['asset_id'],))
        if c['physical_state'] == 'verified' and mob and mob['state'] == 'ready':
            slot['available'] += 1
        elif c['physical_state'] in keys:
            slot[c['physical_state']] += 1
    return [out[k] for k in sorted(out)]

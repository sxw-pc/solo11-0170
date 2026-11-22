"""Full stop-envelope bridge-board fit certification.

A single stationary photograph (one ``dock_measurement``) can only prove one
stop offset.  A certificate must conservatively cover the whole operational
stopping envelope on a curved platform: edge notches may sit *inside* the
envelope, board corners can overhang even when the board centre is safe, and
the effective support distance shrinks as the lateral gap grows.

Geometry convention: the platform edge is a polyline in a horizontal plane,
``y`` grows from the train towards the platform; platform surface is every
point with ``y >= edge(x)``.  Surveyed edge geometry is the continuous
authority; ``dock_measurement`` rows calibrate the train threshold position
and the vertical profile, and a mismatch beyond survey precision flags a
conflict instead of being silently interpolated away.
"""
import json
from .store import one, many, log
from .boarding import dock_rows

SAMPLE_STEP_CM = 1
# Transverse probes across the board width catch narrow edge notches that a
# two-corner check would miss.
WIDTH_PROBE_CM = 5
_EPS = 1e-9
# Priority when several checks fail at one sampled offset.
_CHECK_PRIORITY = ('corner_overhang', 'bearing_insufficient', 'slope_exceeded', 'width_insufficient',
                   'survey_coverage_gap', 'height_data_gap')


def _edge_at(edge, x):
    """Piecewise-linear platform edge y at longitudinal coordinate x."""
    if x < edge[0][0] or x > edge[-1][0]:
        return None
    for (x1, y1), (x2, y2) in zip(edge, edge[1:]):
        if x1 <= x <= x2:
            if x2 == x1:
                return y1
            t = (x - x1) / (x2 - x1)
            return y1 + t * (y2 - y1)
    return edge[-1][1]


def _height_at(docks, offset):
    """Linear interpolation of measured height differences; no extrapolation."""
    return _interp(docks, 'height_diff_cm', offset)


def _gap_at(docks, offset):
    """Linear interpolation of measured lateral gaps; no extrapolation."""
    return _interp(docks, 'gap_cm', offset)


def _interp(rows, field, offset):
    pts = sorted((r['stop_offset_cm'], r[field]) for r in rows)
    if offset < pts[0][0] or offset > pts[-1][0]:
        return None
    for (s1, v1), (s2, v2) in zip(pts, pts[1:]):
        if s1 <= offset <= s2:
            if s2 == s1:
                return v1
            return v1 + (v2 - v1) * (offset - s1) / (s2 - s1)
    return pts[-1][1]


def survey(db, e):
    if e['survey_version'] <= 0 or not e['edge_points']:
        raise ValueError('invalid_fit_survey')
    if min(e['horizontal_precision_cm'], e['vertical_precision_cm'], e['yaw_margin_cm']) < 0:
        raise ValueError('invalid_fit_survey')
    if not one(db, 'SELECT 1 FROM platform_profile WHERE id=?', (e['profile_id'],)):
        raise ValueError('unknown_platform_profile')
    pts = sorted((float(x), float(y)) for x, y in e['edge_points'])
    if any(x2 <= x1 for (x1, _), (x2, _) in zip(pts, pts[1:])):
        raise ValueError('invalid_fit_survey')
    old = one(db, 'SELECT id,survey_version FROM fit_survey WHERE profile_id=? ORDER BY survey_version DESC LIMIT 1',
              (e['profile_id'],))
    if old and e['survey_version'] <= old['survey_version']:
        raise ValueError('stale_survey_version')
    conflict = _survey_conflict(db, e['profile_id'], pts, e['horizontal_precision_cm'])
    state = 'conflicting' if conflict else 'ok'
    if old:
        db.execute("UPDATE fit_survey SET superseded_at=? WHERE profile_id=? AND superseded_at IS NULL",
                   (e['survey_at'], e['profile_id']))
        _revoke_for_survey(db, old['id'], e['survey_at'], 'superseded_by_survey:' + e['survey_id'])
    db.execute('INSERT INTO fit_survey VALUES(?,?,?,?,?,?,?,?,?,?)',
               (e['survey_id'], e['profile_id'], e['survey_version'], json.dumps(pts),
                e['horizontal_precision_cm'], e['vertical_precision_cm'], e['yaw_margin_cm'],
                None, state, e['id']))
    log(db, e['id'], 'boarding_fit', e['survey_id'],
        'survey_conflict' if conflict else 'captured', 'survey_geometry_conflict' if conflict else 'fit_survey')
    return 'survey_conflict' if conflict else 'captured'


def _survey_conflict(db, profile_id, edge, horiz_precision):
    """The surveyed edge must reproduce the measured zero-offset gap.

    Dock measurements are the gap authority; the edge polyline only adds
    continuous shape.  A zero-offset mismatch beyond horizontal precision
    means the two evidence sources disagree and no certificate may be issued.
    """
    for d in dock_rows(db, profile_id):
        if d['stop_offset_cm'] != 0:
            continue
        edge_gap = _edge_at(edge, _door_x(db, d['door_id']))
        if edge_gap is None or abs(edge_gap - d['gap_cm']) > horiz_precision:
            return True
    return False


def _door_x(db, door_id):
    return one(db, 'SELECT door_x_cm FROM train_door WHERE id=?', (door_id,))['door_x_cm']


def door_profile(db, e):
    if not one(db, 'SELECT 1 FROM train_door WHERE id=?', (e['door_id'],)):
        raise ValueError('unknown_train_door')
    if e['floor_height_cm'] <= 0 or e['load_variation_cm'] < 0:
        raise ValueError('invalid_door_load_profile')
    db.execute('INSERT INTO door_load_profile VALUES(?,?,?,?,?)',
               (e['profile_id'], e['door_id'], e['floor_height_cm'], e['load_variation_cm'], e['id']))
    log(db, e['id'], 'boarding_fit', e['door_id'], 'captured', 'door_load_profile')
    return 'captured'


def board_calibration(db, e):
    board = one(db, 'SELECT 1 FROM bridge_board WHERE id=?', (e['board_id'],))
    if not board:
        raise ValueError('unknown_bridge_board')
    if e['bearing_end_cm'] <= 0 or e['flex_cm'] < 0:
        raise ValueError('invalid_board_calibration')
    old = one(db, 'SELECT id FROM board_calibration WHERE board_id=? AND superseded_at IS NULL', (e['board_id'],))
    if old:
        db.execute('UPDATE board_calibration SET superseded_at=? WHERE id=?', (e['calibrated_at'], old['id']))
        _revoke_for_calibration(db, old['id'], e['calibrated_at'], 'deflection_recalibration:' + e['calibration_id'])
    db.execute('INSERT INTO board_calibration VALUES(?,?,?,?,?,?)',
               (e['calibration_id'], e['board_id'], e['bearing_end_cm'], e['flex_cm'], e['id'], None))
    log(db, e['id'], 'boarding_fit', e['board_id'], 'captured', 'board_calibration')
    return 'captured'


def stop_control(db, e):
    if e['window_half_width_cm'] <= 0:
        raise ValueError('invalid_stop_window')
    if e['method'] not in ('precise_stop_marker', 'independent_stop_verification'):
        raise ValueError('invalid_stop_control_method')
    db.execute('INSERT INTO stop_control VALUES(?,?,?,?,?,?,?,?)',
               (e['control_id'], e['station'], e['track'], e['window_half_width_cm'],
                e['method'], e['active_from'], 'active', e['id']))
    log(db, e['id'], 'boarding_fit', e['control_id'], 'captured', 'stop_control_window')
    return 'captured'


def certify(db, e):
    half = float(e['requested_half_width_cm'])
    if half <= 0:
        raise ValueError('invalid_stop_envelope')
    if e['mode'] not in ('controlled_window', 'full_envelope'):
        raise ValueError('invalid_certification_mode')
    sv = one(db, 'SELECT * FROM fit_survey WHERE id=?', (e['survey_id'],))
    door = one(db, 'SELECT * FROM train_door WHERE id=?', (e['door_id'],))
    board = one(db, 'SELECT * FROM bridge_board WHERE id=?', (e['board_id'],))
    cal = one(db, 'SELECT * FROM board_calibration WHERE id=? AND superseded_at IS NULL', (e['calibration_id'],))
    profile = one(db, 'SELECT * FROM platform_profile WHERE id=?', (e['profile_id'],))
    if not sv or not door or not board or not cal or not profile:
        raise ValueError('unknown_certification_evidence')
    if sv['profile_id'] != e['profile_id'] or cal['board_id'] != e['board_id']:
        raise ValueError('evidence_reference_mismatch')
    load = one(db, 'SELECT * FROM door_load_profile WHERE id=? AND door_id=?',
               (e['profile_id'], e['door_id']))
    wheelchair_width = float(e.get('wheelchair_width_cm', 80))

    missing = _missing_evidence(db, e, sv, door, cal, load, half)
    if missing:
        return _issue(db, e, sv, cal, 'retest_pending', missing, [], [], None, control=None, extra_actions=[])

    edge = json.loads(sv['edge_points_json'])
    docks = [d for d in dock_rows(db, e['profile_id']) if d['door_id'] == e['door_id']]
    zero = next(d for d in docks if d['stop_offset_cm'] == 0)

    # Constant-width checks fail identically at every offset; record once.
    if board['width_cm'] < wheelchair_width or board['width_cm'] > door['door_width_cm']:
        rows = [(0.0, 'width_insufficient', float(zero['gap_cm']), float(zero['height_diff_cm']), 0.0, 0.0)]
        return _issue(db, e, sv, cal, 'denied_full_envelope', 'width_insufficient', [], rows, None,
                      control=None, extra_actions=[])

    samples = _sample_offsets(edge, door['door_x_cm'], docks, half)
    passes, failures = [], []
    for s in samples:
        verdict = _check_offset(sv, door, board, cal, load, edge, docks, s)
        (passes if verdict is None else failures).append((s, verdict))

    intervals = _safe_intervals(samples, {s for s, _ in passes})
    if failures:
        rank = {name: i for i, name in enumerate(_CHECK_PRIORITY)}
        worst = sorted(failures, key=lambda f: rank.get(f[1][0], len(rank)))[0]
        reason = 'full_envelope_failed:' + worst[1][0] + '@' + _cm(worst[0])
        rows = [(s, v[0], v[1], v[2], v[3], v[4]) for s, v in failures]
        return _resolve_failure(db, e, sv, cal, half, intervals, rows, reason)
    return _issue(db, e, sv, cal, 'issued_full', 'full_envelope_geometry_ok',
                  [(-half, half)], [], half, control=None, extra_actions=[])


def _missing_evidence(db, e, sv, door, cal, load, half):
    if sv['conflict_state'] == 'conflicting':
        return 'survey_conflict'
    if sv['superseded_at'] is not None or cal['superseded_at'] is not None:
        return 'evidence_superseded'
    if not load:
        return 'missing_door_load_profile'
    docks = [d for d in dock_rows(db, e['profile_id']) if d['door_id'] == e['door_id']]
    if not any(d['stop_offset_cm'] == 0 for d in docks):
        return 'missing_zero_offset_dock'
    offsets = [d['stop_offset_cm'] for d in docks]
    if min(offsets) > -half or max(offsets) < half:
        return 'height_data_gap'
    edge = json.loads(sv['edge_points_json'])
    board = one(db, 'SELECT * FROM bridge_board WHERE id=?', (e['board_id'],))
    x0 = door['door_x_cm']
    if any(_edge_at(edge, x) is None for x in (x0 - half - board['width_cm'] / 2.0,
                                                x0 + half + board['width_cm'] / 2.0)):
        return 'survey_coverage_gap'
    return None


def _sample_offsets(edge, door_x, docks, half):
    offsets = {round(-half + k * SAMPLE_STEP_CM, 6) for k in range(int(2 * half / SAMPLE_STEP_CM) + 1)}
    for x, _ in edge:
        s = x - door_x
        if -half <= s <= half:
            offsets.add(round(float(s), 6))
    for d in docks:
        if -half <= d['stop_offset_cm'] <= half:
            offsets.add(float(d['stop_offset_cm']))
    return sorted(offsets)


def _check_offset(sv, door, board, cal, load, edge, docks, s):
    """Conservative fit at one longitudinal stop offset.

    Two independent evidence sources are combined:

    * ``dock_measurement`` rows are the measurement authority for the lateral
      gap and vertical step at this offset; they are interpolated, never
      extrapolated, and inflated by survey/yaw/load/deflection margins.
    * The surveyed edge polyline describes curvature and notches along the
      train.  A probe across the board width whose edge locally recesses more
      than the bearing end beyond the centre edge loses its bearing surface —
      a corner overhang that a centre-point measurement cannot detect.
    """
    x0 = door['door_x_cm'] + s
    half_width = board['width_cm'] / 2.0
    probe_x = [x0 - half_width + k * WIDTH_PROBE_CM
               for k in range(int(board['width_cm'] // WIDTH_PROBE_CM) + 1)] + [x0 + half_width]
    local_edges = []
    for xc in probe_x:
        e_y = _edge_at(edge, xc)
        if e_y is None:
            return ('survey_coverage_gap', 0.0, 0.0, 0.0, 0.0)
        local_edges.append(e_y)
    edge_center = _edge_at(edge, x0)
    edge_far = max(local_edges)
    # A local notch deeper than the bearing end leaves a board corner hanging
    # even when the board centre is inside the safe zone.
    if edge_far - edge_center > cal['bearing_end_cm'] + _EPS:
        return ('corner_overhang', max(0.0, edge_far - edge_center), 0.0, 0.0, 0.0)
    measured_gap = _gap_at(docks, s)
    height = _height_at(docks, s)
    if measured_gap is None or height is None:
        return ('height_data_gap', measured_gap or 0.0, 0.0, 0.0, 0.0)
    gap = measured_gap + sv['horizontal_precision_cm'] + sv['yaw_margin_cm']
    # Slope depends on the absolute climb; load, survey precision and measured
    # board deflection all add conservatively to the worst vertical step.
    h = abs(height) + sv['vertical_precision_cm'] + load['load_variation_cm'] + cal['flex_cm']
    effective_bearing = board['length_cm'] - gap - 2 * cal['bearing_end_cm']
    if effective_bearing < 0:
        return ('bearing_insufficient', gap, h, effective_bearing, 0.0)
    support_distance = board['length_cm'] - 2 * cal['bearing_end_cm']
    slope = 1000.0 * h / support_distance
    if slope > board['max_slope_permille']:
        return ('slope_exceeded', gap, h, effective_bearing, slope)
    return None


def _safe_intervals(samples, safe_set):
    """Merge consecutive safe samples, shrinking each end by half a step.

    The envelope is checked on a finite grid; the boundary of a safe run is
    reported conservatively so the neighbouring unchecked position is not
    claimed as safe.
    """
    intervals, lo = [], None
    step = SAMPLE_STEP_CM
    for s in samples:
        if s in safe_set:
            if lo is None:
                lo = s
            hi = s
        elif lo is not None:
            intervals.append((lo + step / 2.0, hi - step / 2.0)); lo = None
    if lo is not None:
        intervals.append((lo + step / 2.0, hi - step / 2.0))
    return [(lo, hi) for lo, hi in intervals if hi >= lo]


def _resolve_failure(db, e, sv, cal, half, intervals, rows, reason):
    control = _active_control(db, e)
    if e['mode'] == 'full_envelope':
        actions = _replacement_actions(db, e, sv, half)
        return _issue(db, e, sv, cal, 'denied_full_envelope', reason, intervals, rows, None,
                      control=None, extra_actions=actions)
    window = _zero_window(intervals, control)
    if window is None:
        return _issue(db, e, sv, cal, 'denied_no_safe_window', reason, intervals, rows, None,
                      control=control, extra_actions=[])
    if control is None:
        return _issue(db, e, sv, cal, 'retest_pending', 'stop_control_evidence_missing', intervals, rows,
                      (window[1] - window[0]) / 2.0, control=None, extra_actions=[])
    return _issue(db, e, sv, cal, 'issued_restricted', 'controlled_window_certified', [window], rows,
                  control['window_half_width_cm'], control=control, extra_actions=[])


def _zero_window(intervals, control):
    candidates = [iv for iv in intervals if iv[0] <= 0 <= iv[1]]
    if not candidates:
        return None
    lo, hi = max(candidates, key=lambda iv: iv[1] - iv[0])
    if control is not None:
        lo = max(lo, -control['window_half_width_cm'])
        hi = min(hi, control['window_half_width_cm'])
    if lo > hi:
        return None
    return (lo, hi)


def _active_control(db, e):
    ref = e.get('stop_control_id')
    if ref:
        c = one(db, "SELECT * FROM stop_control WHERE id=? AND state='active'", (ref,))
        return c
    return one(db, "SELECT * FROM stop_control WHERE station=(SELECT station FROM platform_profile WHERE id=?) AND track=(SELECT track FROM platform_profile WHERE id=?) AND state='active' ORDER BY active_from DESC LIMIT 1",
               (e['profile_id'], e['profile_id']))


def _replacement_actions(db, e, sv, half):
    """A long board validated over the whole envelope, else the door must be sealed."""
    profile = one(db, 'SELECT * FROM platform_profile WHERE id=?', (e['profile_id'],))
    door = one(db, 'SELECT * FROM train_door WHERE id=?', (e['door_id'],))
    for cal in many(db, 'SELECT c.* FROM board_calibration c JOIN bridge_board b ON b.id=c.board_id WHERE b.station=? AND c.superseded_at IS NULL AND c.id<>? ORDER BY c.id',
                    (profile['station'], e['calibration_id'])):
        board = one(db, 'SELECT * FROM bridge_board WHERE id=?', (cal['board_id'],))
        if board['width_cm'] > door['door_width_cm']:
            continue
        trial = dict(e)
        trial['board_id'] = board['id']
        trial['calibration_id'] = cal['id']
        load = one(db, 'SELECT * FROM door_load_profile WHERE id=? AND door_id=?',
                   (e['profile_id'], e['door_id']))
        missing = _missing_evidence(db, trial, sv, door, cal, load, half)
        if missing:
            continue
        edge = json.loads(sv['edge_points_json'])
        docks = [d for d in dock_rows(db, e['profile_id']) if d['door_id'] == e['door_id']]
        samples = _sample_offsets(edge, door['door_x_cm'], docks, half)
        if all(_check_offset(sv, door, board, cal, load, edge, docks, s) is None for s in samples):
            return [('replace_hardware', 'required', 'qualified_board:' + board['id'])]
    return [('door_unavailable_marking', 'required', 'door:' + e['door_id']),
            ('hardware_procurement', 'required', 'full_envelope_board_for:' + e['door_id'])]


def _issue(db, e, sv, cal, state, reason, intervals, rows, required_precision, control, extra_actions):
    profile = one(db, 'SELECT * FROM platform_profile WHERE id=?', (e['profile_id'],))
    door = one(db, 'SELECT * FROM train_door WHERE id=?', (e['door_id'],))
    db.execute('INSERT INTO fit_certificate VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (e['cert_id'], profile['station'], e['profile_id'], door['train_model'], e['door_id'],
                e['board_id'], sv['id'], cal['id'], e['requested_half_width_cm'], e['mode'],
                state, reason, required_precision, e['id'], None, None))
    for i, (lo, hi) in enumerate(intervals):
        db.execute('INSERT INTO safe_stop_interval(certificate_id,position,lower_cm,upper_cm) VALUES(?,?,?,?)',
                   (e['cert_id'], i, lo, hi))
    for i, (s, check, gap, h, bearing, slope) in enumerate(rows):
        db.execute('INSERT INTO fit_counterexample(certificate_id,position,stop_offset_cm,failed_check,gap_cm,height_diff_cm,effective_bearing_cm,slope_permille) VALUES(?,?,?,?,?,?,?,?)',
                   (e['cert_id'], i, s, check, gap, h, bearing, slope))
    if control is not None and state == 'issued_restricted':
        db.execute('INSERT INTO fit_action VALUES(?,?,?,?,?,?,?)',
                   ('act-' + e['cert_id'] + '-stop', e['cert_id'], 'stop_control_window', 'active',
                    'control:' + control['id'], e['id'], None))
    for kind, action_state, detail in extra_actions:
        db.execute('INSERT INTO fit_action VALUES(?,?,?,?,?,?,?)',
                   ('act-' + e['cert_id'] + '-' + kind, e['cert_id'], kind, action_state, detail, e['id'], None))
    log(db, e['id'], 'boarding_fit', e['cert_id'], state, reason)
    return state


def revoke(db, e):
    cert = one(db, 'SELECT * FROM fit_certificate WHERE id=?', (e['cert_id'],))
    if not cert:
        raise ValueError('unknown_certificate')
    if cert['revoked_at'] is not None:
        return 'stale'
    db.execute('UPDATE fit_certificate SET revoked_at=?,revoked_reason=? WHERE id=?',
               (e['revoked_at'], e['reason'], e['cert_id']))
    db.execute("UPDATE fit_action SET state='closed',closed_at=? WHERE certificate_id=? AND state='active'",
               (e['revoked_at'], e['cert_id']))
    log(db, e['id'], 'boarding_fit', e['cert_id'], 'revoked', e['reason'])
    return 'revoked'


def _revoke_for_survey(db, survey_id, at, reason):
    for cert in many(db, "SELECT * FROM fit_certificate WHERE survey_id=? AND revoked_at IS NULL AND state IN ('issued_full','issued_restricted')",
                     (survey_id,)):
        db.execute('UPDATE fit_certificate SET revoked_at=?,revoked_reason=? WHERE id=?', (at, reason, cert['id']))
        db.execute("UPDATE fit_action SET state='closed',closed_at=? WHERE certificate_id=? AND state='active'", (at, cert['id']))
        db.execute('INSERT INTO decision(event_id,system,object_id,outcome,reason) VALUES(?,?,?,?,?)',
                   (cert['event_id'], 'boarding_fit', cert['id'], 'revoked', reason))


def _revoke_for_calibration(db, calibration_id, at, reason):
    for cert in many(db, "SELECT * FROM fit_certificate WHERE calibration_id=? AND revoked_at IS NULL AND state IN ('issued_full','issued_restricted')",
                     (calibration_id,)):
        db.execute('UPDATE fit_certificate SET revoked_at=?,revoked_reason=? WHERE id=?', (at, reason, cert['id']))
        db.execute("UPDATE fit_action SET state='closed',closed_at=? WHERE certificate_id=? AND state='active'", (at, cert['id']))
        db.execute('INSERT INTO decision(event_id,system,object_id,outcome,reason) VALUES(?,?,?,?,?)',
                   (cert['event_id'], 'boarding_fit', cert['id'], 'revoked', reason))


def _cm(value):
    return str(int(value)) if float(value).is_integer() else str(value)

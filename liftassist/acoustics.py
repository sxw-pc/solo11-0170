import json
from .store import one,log

def microphone(db,e):
    db.execute('INSERT INTO microphone VALUES(?,?,?,?,?)',(e['microphone_id'],e['station'],e['x_m'],e['y_m'],e['calibration_id']))
    log(db,e['id'],'acoustics',e['microphone_id'],'captured','microphone')
    return 'captured'
def trace(db,e):
    if not one(db,'SELECT 1 FROM microphone WHERE id=?',(e['microphone_id'],)):raise ValueError('unknown_microphone')
    if e['sample_rate_hz']<=0 or not e['bands']:raise ValueError('invalid_trace')
    db.execute('INSERT INTO acoustic_trace VALUES(?,?,?,?,?)',(e['trace_id'],e['microphone_id'],e['started_at'],e['sample_rate_hz'],json.dumps(e['bands'])))
    log(db,e['id'],'acoustics',e['trace_id'],'captured','acoustic_trace')
    return 'captured'
def fan(db,e):
    db.execute('INSERT INTO fan_run VALUES(?,?,?,?,?,?)',(e['run_id'],e['station'],e['fan_id'],e['started_at'],e['stopped_at'],e['speed_pct']))
    log(db,e['id'],'acoustics',e['run_id'],'captured','fan_run')
    return 'captured'
def train(db,e):
    db.execute('INSERT INTO train_pass VALUES(?,?,?,?,?)',(e['pass_id'],e['station'],e['train_id'],e['passed_at'],e['speed_kph']))
    log(db,e['id'],'acoustics',e['pass_id'],'captured','train_pass')
    return 'captured'

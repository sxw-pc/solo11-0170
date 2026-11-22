import json
from .store import one,log

def profile(db,e):
    if not e['edge_points'] or not e['station']:raise ValueError('invalid_platform_profile')
    db.execute('INSERT INTO platform_profile VALUES(?,?,?,?,?)',(e['profile_id'],e['station'],e['track'],json.dumps(e['edge_points']),e['survey_at']))
    log(db,e['id'],'boarding',e['profile_id'],'captured','platform_profile')
    return 'captured'
def door(db,e):
    if e['door_width_cm']<=0:raise ValueError('invalid_door')
    db.execute('INSERT INTO train_door VALUES(?,?,?,?,?,?)',(e['door_id'],e['train_model'],e['car_no'],e['door_x_cm'],e['floor_height_cm'],e['door_width_cm']))
    log(db,e['id'],'boarding',e['door_id'],'captured','train_door')
    return 'captured'
def board(db,e):
    if min(e['length_cm'],e['width_cm'],e['max_slope_permille'])<=0:raise ValueError('invalid_board')
    db.execute('INSERT INTO bridge_board VALUES(?,?,?,?,?)',(e['board_id'],e['station'],e['length_cm'],e['width_cm'],e['max_slope_permille']))
    log(db,e['id'],'boarding',e['board_id'],'captured','bridge_board')
    return 'captured'
def dock(db,e):
    if not one(db,'SELECT 1 FROM platform_profile WHERE id=?',(e['profile_id'],)):raise ValueError('unknown_platform_profile')
    if not one(db,'SELECT 1 FROM train_door WHERE id=?',(e['door_id'],)):raise ValueError('unknown_train_door')
    db.execute('INSERT INTO dock_measurement VALUES(?,?,?,?,?,?,?)',(e['measurement_id'],e['profile_id'],e['door_id'],e['stop_offset_cm'],e['gap_cm'],e['height_diff_cm'],e['measured_at']))
    log(db,e['id'],'boarding',e['measurement_id'],'captured','dock_measurement')
    return 'captured'

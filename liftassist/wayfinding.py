import json
from .store import one,log

def passage(db,e):
    if not e['centerline'] or e['width_cm']<=0:raise ValueError('invalid_passage')
    db.execute('INSERT INTO passage VALUES(?,?,?,?,?)',(e['passage_id'],e['station'],json.dumps(e['centerline']),e['width_cm'],e['survey_at']))
    log(db,e['id'],'wayfinding',e['passage_id'],'captured','passage')
    return 'captured'
def sign(db,e):
    if not one(db,'SELECT 1 FROM passage WHERE id=?',(e['passage_id'],)):raise ValueError('unknown_passage')
    db.execute('INSERT INTO guide_sign VALUES(?,?,?,?,?,?,?)',(e['sign_id'],e['passage_id'],e['x_cm'],e['y_cm'],e['z_cm'],e['face_deg'],e['text']))
    log(db,e['id'],'wayfinding',e['sign_id'],'captured','sign')
    return 'captured'
def obstacle(db,e):
    if not one(db,'SELECT 1 FROM passage WHERE id=?',(e['passage_id'],)):raise ValueError('unknown_passage')
    db.execute('INSERT INTO obstacle VALUES(?,?,?,?,?,?)',(e['obstacle_id'],e['passage_id'],json.dumps(e['polygon']),e['height_cm'],e['valid_from'],e['valid_to']))
    log(db,e['id'],'wayfinding',e['obstacle_id'],'captured','obstacle')
    return 'captured'

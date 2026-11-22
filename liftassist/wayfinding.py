from .store import one,log,jdumps

def passage(db,e):
    if not e['centerline'] or e['width_cm']<=0:raise ValueError('invalid_passage')
    db.execute('INSERT INTO passage(id,station,centerline_json,width_cm,survey_at,layer_offset_cm) VALUES(?,?,?,?,?,?)',
               (e['passage_id'],e['station'],jdumps(e['centerline']),e['width_cm'],e['survey_at'],e.get('layer_offset_cm')))
    log(db,e['id'],'wayfinding',e['passage_id'],'captured','passage')
    return 'captured'
def sign(db,e):
    if not one(db,'SELECT 1 FROM passage WHERE id=?',(e['passage_id'],)):raise ValueError('unknown_passage')
    db.execute('INSERT INTO guide_sign(id,passage_id,x_cm,y_cm,z_cm,face_deg,text,text_height_cm) VALUES(?,?,?,?,?,?,?,?)',
               (e['sign_id'],e['passage_id'],e['x_cm'],e['y_cm'],e.get('z_cm'),e['face_deg'],e['text'],e.get('text_height_cm')))
    log(db,e['id'],'wayfinding',e['sign_id'],'captured','sign')
    return 'captured'
def obstacle(db,e):
    if not one(db,'SELECT 1 FROM passage WHERE id=?',(e['passage_id'],)):raise ValueError('unknown_passage')
    db.execute('INSERT INTO obstacle(id,passage_id,polygon_json,height_cm,valid_from,valid_to) VALUES(?,?,?,?,?,?)',
               (e['obstacle_id'],e['passage_id'],jdumps(e['polygon']),e.get('height_cm'),e['valid_from'],e['valid_to']))
    log(db,e['id'],'wayfinding',e['obstacle_id'],'captured','obstacle')
    from .guide_visibility import revoke_for_obstacle
    revoke_for_obstacle(db,e)
    return 'captured'

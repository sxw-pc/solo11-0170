from .store import one,log

def edge(db,e):
    if min(e['capacity_per_min'],e['travel_min'])<=0:raise ValueError('invalid_egress_edge')
    db.execute('INSERT INTO egress_edge VALUES(?,?,?,?,?,?,?)',(e['edge_id'],e['station'],e['from_node'],e['to_node'],e['capacity_per_min'],e['travel_min'],int(e['accessible'])))
    log(db,e['id'],'evacuation',e['edge_id'],'captured','egress_edge')
    return 'captured'
def zone(db,e):
    if e['occupants']<0:raise ValueError('invalid_occupants')
    db.execute('INSERT INTO evac_zone VALUES(?,?,?,?,?)',(e['zone_id'],e['station'],e['node'],e['occupants'],e['observed_at']))
    log(db,e['id'],'evacuation',e['zone_id'],'captured','evac_zone')
    return 'captured'
def smoke(db,e):
    if not 0<=e['opacity_pct']<=100:raise ValueError('invalid_opacity')
    db.execute('INSERT INTO smoke_reading VALUES(?,?,?,?,?)',(e['reading_id'],e['station'],e['node'],e['opacity_pct'],e['sampled_at']))
    log(db,e['id'],'evacuation',e['reading_id'],'captured','smoke_reading')
    return 'captured'
def door(db,e):
    if not one(db,'SELECT 1 FROM egress_edge WHERE id=?',(e['edge_id'],)):raise ValueError('unknown_egress_edge')
    db.execute('INSERT INTO fire_door VALUES(?,?,?,?)',(e['door_id'],e['edge_id'],e['state'],e['observed_at']))
    log(db,e['id'],'evacuation',e['door_id'],'captured','fire_door')
    return 'captured'

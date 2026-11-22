from .store import one,log
from .asset_custody import reconcile_asset,assets_for_epc

def tag(db,e):
    if not one(db,'SELECT 1 FROM mobility_asset WHERE id=?',(e['asset_id'],)):raise ValueError('unknown_mobility_asset')
    db.execute('INSERT INTO mobility_tag VALUES(?,?,?,?)',(e['tag_id'],e['asset_id'],e['epc'],e['installed_at']))
    log(db,e['id'],'custody',e['asset_id'],'captured','tag')
    reconcile_asset(db,e['asset_id'],e['id'])
    return 'captured'
def scan(db,e):
    db.execute('INSERT INTO rfid_scan VALUES(?,?,?,?,?)',(e['scan_id'],e['epc'],e['reader'],e['observed_at'],e['rssi']))
    log(db,e['id'],'custody',e['scan_id'],'captured','rfid_scan')
    affected=set(assets_for_epc(db,e['epc']))|set(assets_for_epc(db,e['epc'],e['observed_at']))
    for asset_id in affected:reconcile_asset(db,asset_id,e['id'])
    return 'captured'
def receipt(db,e):
    if not one(db,'SELECT 1 FROM mobility_asset WHERE id=?',(e['asset_id'],)):raise ValueError('unknown_mobility_asset')
    db.execute('INSERT INTO custody_receipt VALUES(?,?,?,?,?,?)',(e['receipt_id'],e['asset_id'],e['holder'],e['location'],e['observed_at'],e['witness']))
    log(db,e['id'],'custody',e['asset_id'],'captured','custody_receipt')
    reconcile_asset(db,e['asset_id'],e['id'])
    return 'captured'

from .store import one,log

def screen(db,e):
    db.execute('INSERT INTO display_screen VALUES(?,?,?,?)',(e['screen_id'],e['station'],e['zone'],e['device_serial']))
    log(db,e['id'],'display',e['screen_id'],'captured','screen')
    return 'captured'
def campaign(db,e):
    db.execute('INSERT INTO ad_campaign VALUES(?,?,?,?,?,?)',(e['campaign_id'],e['advertiser'],e['asset_hash'],e['guaranteed_seconds'],e['valid_from'],e['valid_to']))
    log(db,e['id'],'display',e['campaign_id'],'captured','campaign')
    return 'captured'
def command(db,e):
    if not one(db,'SELECT 1 FROM display_screen WHERE id=?',(e['screen_id'],)):raise ValueError('unknown_screen')
    if not one(db,'SELECT 1 FROM ad_campaign WHERE id=?',(e['campaign_id'],)):raise ValueError('unknown_campaign')
    db.execute('INSERT INTO play_command VALUES(?,?,?,?,?,?)',(e['command_id'],e['screen_id'],e['campaign_id'],e['starts'],e['ends'],e['priority']))
    log(db,e['id'],'display',e['command_id'],'captured','play_command')
    return 'captured'
def ack(db,e):
    if not one(db,'SELECT 1 FROM play_command WHERE id=?',(e['command_id'],)):raise ValueError('unknown_command')
    db.execute('INSERT INTO screen_ack VALUES(?,?,?,?,?,?)',(e['ack_id'],e['screen_id'],e['command_id'],e['asset_hash'],e['observed_at'],e['state']))
    log(db,e['id'],'display',e['ack_id'],'captured','ack')
    return 'captured'
def frame(db,e):
    if not one(db,'SELECT 1 FROM display_screen WHERE id=?',(e['screen_id'],)):raise ValueError('unknown_screen')
    db.execute('INSERT INTO camera_frame VALUES(?,?,?,?,?)',(e['frame_id'],e['screen_id'],e['image_hash'],e['captured_at'],e['visible_asset_hash']))
    log(db,e['id'],'display',e['frame_id'],'captured','camera_frame')
    return 'captured'

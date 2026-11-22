from .store import one,log
from . import fare_reconcile

def token(db,e):
    db.execute('INSERT INTO fare_token VALUES(?,?,?,?,?)',(e['token_id'],e['issuer'],e['token_hash'],e['valid_from'],e['valid_to']))
    log(db,e['id'],'fare',e['token_id'],'captured','token')
    fare_reconcile.reconcile(db,e['id'])
    return 'captured'
def tap(db,e):
    if not one(db,'SELECT 1 FROM fare_token WHERE id=?',(e['token_id'],)):raise ValueError('unknown_token')
    if e['direction'] not in ('in','out'):raise ValueError('invalid_direction')
    db.execute('INSERT INTO gate_tap(id,token_id,station,gate,direction,observed_at,offline) VALUES(?,?,?,?,?,?,?)',
               (e['tap_id'],e['token_id'],e['station'],e['gate'],e['direction'],e['observed_at'],int(bool(e.get('offline')))))
    log(db,e['id'],'fare',e['tap_id'],'captured','gate_tap_offline' if e.get('offline') else 'gate_tap')
    fare_reconcile.reconcile(db,e['id'])
    return 'captured'
def product(db,e):
    db.execute('INSERT INTO fare_product VALUES(?,?,?,?,?)',(e['product_id'],e['product_type'],e['cap_cents'],e['valid_from'],e['valid_to']))
    log(db,e['id'],'fare',e['product_id'],'captured','fare_product')
    fare_reconcile.reconcile(db,e['id'])
    return 'captured'
def debit(db,e):
    if not one(db,'SELECT 1 FROM fare_token WHERE id=?',(e['token_id'],)):raise ValueError('unknown_token')
    db.execute('INSERT INTO fare_debit VALUES(?,?,?,?,?)',(e['debit_id'],e['token_id'],e['cents'],e['charged_at'],e['operator']))
    log(db,e['id'],'fare',e['debit_id'],'captured','fare_debit')
    fare_reconcile.reconcile(db,e['id'])
    return 'captured'

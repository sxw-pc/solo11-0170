"""Cross-token journey/daily-cap clearing under privacy-limited identity credentials.

Two tokens are treated as one paying subject only when the token issuer has
lodged a one-time blinded rotation witness proving the equivalence class.
Geographic or temporal proximity of gate taps is never a substitute for that
proof, and a pure anonymous per-ride product forbids any cross-token merge.

Money events are append-only: refunds never overwrite the original debits and a
late witness revocation produces a backcharge *todo* instead of deleting an
already issued refund. Every projection here is rebuilt deterministically from
the event ledger so :func:`liftassist.engine.audit` can verify it by replay.
"""
from datetime import datetime,timezone
from .store import one,many,log

def _ts(value):
    return datetime.fromisoformat(value.replace('Z','+00:00'))

def _day(value):
    return _ts(value).astimezone(timezone.utc).date().isoformat()

# --------------------------------------------------------------------------
# Event intake: product enrollment, anti-replay counters, blinded witnesses
# --------------------------------------------------------------------------
def enrollment(db,e):
    """Effective history binding a token to a fare product (per token period)."""
    if not one(db,'SELECT 1 FROM fare_token WHERE id=?',(e['token_id'],)):raise ValueError('unknown_token')
    if not one(db,'SELECT 1 FROM fare_product WHERE id=?',(e['product_id'],)):raise ValueError('unknown_product')
    if e['effective_from']>=e['effective_to']:raise ValueError('invalid_enrollment_window')
    db.execute('INSERT INTO fare_enrollment VALUES(?,?,?,?,?)',
               (e['enrollment_id'],e['token_id'],e['product_id'],e['effective_from'],e['effective_to']))
    log(db,e['id'],'fare_reconcile',e['enrollment_id'],'enrolled','fare_product_effective_history')
    reconcile(db,e['id'])
    return 'enrolled'

def counter(db,e):
    """Card anti-replay counter; a repeated counter value is a replay attack."""
    if not one(db,'SELECT 1 FROM fare_token WHERE id=?',(e['token_id'],)):raise ValueError('unknown_token')
    if int(e['counter'])<0:raise ValueError('invalid_counter')
    if one(db,'SELECT 1 FROM tap_counter WHERE token_id=? AND counter=?',(e['token_id'],e['counter'])):
        raise ValueError('replayed_counter')
    db.execute('INSERT INTO tap_counter VALUES(?,?,?)',(e['token_id'],int(e['counter']),e['observed_at']))
    log(db,e['id'],'fare_reconcile',e['token_id'],'counter_accepted','anti_replay_counter')
    reconcile(db,e['id'])
    return 'counter_accepted'

def witness(db,e):
    """One-time blinded issuer link: only an equivalence class is revealed."""
    link_id=e['link_id'];members=list(e['tokens'])
    if len(set(members))<2:raise ValueError('link_needs_two_distinct_tokens')
    if one(db,'SELECT 1 FROM rotation_witness WHERE link_id=? AND state=?',(link_id,'revoked')):
        raise ValueError('link_id_revoked_cannot_reuse')
    if one(db,'SELECT 1 FROM rotation_witness WHERE link_id=?',(link_id,)):
        raise ValueError('link_id_already_spent')
    token_rows={r['id']:r for r in many(db,'SELECT * FROM fare_token')}
    for token_id in members:
        if token_id not in token_rows:raise ValueError('unknown_token')
        other=one(db,'SELECT w.link_id FROM witness_member m JOIN rotation_witness w ON w.link_id=m.link_id '
                  "WHERE m.token_id=? AND w.state='active' LIMIT 1",(token_id,))
        if other:raise ValueError('token_already_linked_elsewhere')
    # Anonymous per-ride products explicitly forbid any cross-token merge;
    # a blind link may not span different paying product families either.
    kinds=set()
    for token_id in members:
        prod=_product_at(db,token_id,e['issued_at'])
        if prod and prod['product_type']=='per-ride':
            raise ValueError('anonymous_product_forbids_link')
        if prod:kinds.add(prod['product_type'])
    if len(kinds)>1:raise ValueError('link_spans_conflicting_products')
    db.execute('INSERT INTO rotation_witness VALUES(?,?,?,?,NULL,?,?)',
               (link_id,e['issuer'],e['blind_proof_hash'],e['issued_at'],'active','issuer_blind_witness'))
    db.executemany('INSERT INTO witness_member(link_id,token_id) VALUES(?,?)',[(link_id,t) for t in members])
    log(db,e['id'],'fare_reconcile',link_id,'link_accepted','blind_equivalence_class')
    reconcile(db,e['id'])
    return 'link_accepted'

def revoke_witness(db,e):
    """Late revocation: history stays on the books; backcharges become todos."""
    row=one(db,'SELECT * FROM rotation_witness WHERE link_id=?',(e['link_id'],))
    if not row:raise ValueError('unknown_link')
    if row['state']=='revoked':
        if row['revoked_at']!=e['revoked_at']:raise ValueError('revocation_identity_conflict')
        return 'stale'
    if e['revoked_at']<=row['issued_at']:raise ValueError('invalid_revocation_time')
    db.execute("UPDATE rotation_witness SET state='revoked',revoked_at=?,reason=? WHERE link_id=?",
               (e['revoked_at'],'revoked_by_issuer',e['link_id']))
    log(db,e['id'],'fare_reconcile',e['link_id'],'link_revoked','blind_equivalence_class_withdrawn')
    reconcile(db,e['id'])
    return 'link_revoked'

def refund(db,e):
    """Refund receipt; append-only and idempotent, never overwrites a debit."""
    if int(e['cents'])<=0:raise ValueError('invalid_refund_amount')
    if one(db,'SELECT 1 FROM fare_refund WHERE refund_id=?',(e['refund_id'],)):return 'stale'
    link=e.get('link_id')
    if link:
        w=one(db,'SELECT * FROM rotation_witness WHERE link_id=?',(link,))
        if not w:raise ValueError('unknown_link')
        if w['state']!='active':raise ValueError('link_not_active_for_refund')
    account=one(db,'SELECT * FROM fare_account WHERE account_id=? AND day=?',(e['account_id'],e['day']))
    if not account:raise ValueError('no_clearing_account_for_refund')
    if link and not account['account_id'].startswith('link:'):raise ValueError('refund_link_account_mismatch')
    if account['refundable_cents']-account['refunded_cents']<int(e['cents']):
        raise ValueError('refund_exceeds_proven_balance')
    db.execute('INSERT INTO fare_refund VALUES(?,?,?,?,?,?,?)',
               (e['refund_id'],e['account_id'],e['day'],int(e['cents']),e['refunded_at'],e.get('reason','cap_settlement'),link))
    log(db,e['id'],'fare_reconcile',e['refund_id'],'refund_recorded','append_only_refund_receipt')
    reconcile(db,e['id'])
    return 'refund_recorded'

# --------------------------------------------------------------------------
# Deterministic rebuilding of the clearing projections
# --------------------------------------------------------------------------
def _active_links(db):
    links={r['link_id']:[m['token_id'] for m in many(db,'SELECT token_id FROM witness_member WHERE link_id=? ORDER BY token_id',(r['link_id'],))]
           for r in many(db,"SELECT * FROM rotation_witness WHERE state='active'")}
    token_to_link={}
    for link_id,tokens in links.items():
        for token_id in tokens:token_to_link[token_id]=link_id
    return links,token_to_link

def _product_at(db,token_id,when):
    return one(db,"SELECT p.* FROM fare_enrollment n JOIN fare_product p ON p.id=n.product_id "
               'WHERE n.token_id=? AND n.effective_from<=? AND n.effective_to>? ORDER BY n.effective_from DESC LIMIT 1',
               (token_id,when,when))

def _build_journeys(db,account_id,member_tokens):
    """Greedy in/out pairing over business time; every tap is consumed once."""
    taps=many(db,'SELECT * FROM gate_tap WHERE token_id IN (%s) ORDER BY observed_at,id'%','.join('?'*len(member_tokens)),
              tuple(member_tokens)) if member_tokens else []
    open_in=None;journeys=[];anomalous=set()
    for tap in taps:
        if tap['direction']=='in':
            if open_in is not None:
                # A second entry with no exit leaves the first one open.
                journeys.append((open_in,None,'open_in','entry_without_exit'))
                anomalous.add(open_in['token_id'])
            open_in=tap
        else:
            if open_in is None:
                journeys.append((None,tap,'open_out','exit_without_entry'))
                anomalous.add(tap['token_id'])
            else:
                cross=_day(open_in['observed_at'])!=_day(tap['observed_at'])
                journeys.append((open_in,tap,'closed_cross_day' if cross else 'closed','cross_day_pair' if cross else 'paired'))
                open_in=None
    if open_in is not None:
        journeys.append((open_in,None,'open_in','entry_without_exit'))
        anomalous.add(open_in['token_id'])
    rows=[]
    for i,(tin,tout,state,reason) in enumerate(journeys):
        anchor=tin or tout
        rows.append(('%s|%s|%d'%(account_id,_day(anchor['observed_at']),i),account_id,_day(anchor['observed_at']),
                     tin['id'] if tin else None,tout['id'] if tout else None,
                     tin['station'] if tin else None,tout['station'] if tout else None,
                     state,reason))
    return rows,anomalous

def reconcile(db,event_id):
    db.execute('DELETE FROM fare_account')
    db.execute('DELETE FROM candidate_journey')
    db.execute('DELETE FROM reconcile_todo')
    links,token_to_link=_active_links(db)
    # Refunds bound to a withdrawn link can no longer rely on a cross-token cap:
    # the full amount becomes a backcharge work item (the receipt is preserved).
    swept_refunds=set()
    revoked=many(db,"SELECT * FROM rotation_witness WHERE state='revoked' ORDER BY link_id")
    tokens=[r['id'] for r in many(db,'SELECT id FROM fare_token ORDER BY id')]
    # Linked class accounts first, then singleton token accounts, deterministically.
    accounts=[('link:%s'%link_id,links[link_id]) for link_id in sorted(links)]
    accounts += [('token:%s'%t,[t]) for t in tokens if t not in token_to_link]
    todo_seq=0;account_rows=[];journey_rows=[];todos=[]
    for account_id,members in accounts:
        debits=many(db,'SELECT * FROM fare_debit WHERE token_id IN (%s) ORDER BY charged_at,id'%','.join('?'*len(members)),
                    tuple(members)) if members else []
        all_journeys,anomalous=_build_journeys(db,account_id,members)
        journey_rows.extend(all_journeys)
        offline_by_day={}
        for tap in many(db,'SELECT * FROM gate_tap WHERE offline=1 AND token_id IN (%s)'%','.join('?'*len(members)),tuple(members)):
            offline_by_day.setdefault(_day(tap['observed_at']),set()).add(tap['token_id'])
        days=sorted({_day(d['charged_at']) for d in debits} |
                    {_day(t['observed_at']) for t in many(db,'SELECT observed_at FROM gate_tap WHERE token_id IN (%s)'%','.join('?'*len(members)),tuple(members))})
        for day in days:
            day_debits=[d for d in debits if _day(d['charged_at'])==day]
            gross=sum(int(d['cents']) for d in day_debits)
            # Effective product: same family for every member on a given day.
            probe=day_debits[0]['charged_at'] if day_debits else day+'T00:00:00Z'
            products={_product_at(db,t,probe)['product_type'] for t in members if _product_at(db,t,probe)}
            cap=None
            if 'daily-cap' in products:
                cap_rows=[_product_at(db,t,probe) for t in members if _product_at(db,t,probe) and _product_at(db,t,probe)['product_type']=='daily-cap']
                cap=min(int(p['cap_cents']) for p in cap_rows)
            linked=account_id.startswith('link:')
            jr=[j for j in all_journeys if j[2]==day]
            if cap is None:
                # Pure anonymous per-ride: every original debit stands, no merge, no cap.
                lower=upper=receivable=gross
                state,reason=('per_ride_held','anonymous_no_cross_token_merge')
            elif linked:
                # Proven equivalence class: the purchased daily cap is honored.
                receivable=min(gross,cap)
                lower=upper=receivable
                state,reason=('cap_settled','blind_link_proven_cap') if gross>cap else ('within_cap','blind_link_proven_cap')
            else:
                # Proof not yet (or no longer) available: keep explainable bounds,
                # never refund into an account that cannot prove the merge.
                per_token={}
                for d in day_debits:per_token[d['token_id']]=per_token.get(d['token_id'],0)+int(d['cents'])
                # Lower receivable: each token still keeps its own purchased cap.
                lower=min(sum(per_token.values()),sum(min(v,cap) for v in per_token.values()))
                upper=min(gross,cap)
                receivable=upper
                state,reason=('proof_pending','cap_bounds_without_cross_token_proof')
            refunds=many(db,'SELECT * FROM fare_refund WHERE account_id=? AND day=?',(account_id,day))
            refunded=sum(int(r['cents']) for r in refunds)
            refundable=max(gross-receivable,0)
            account_rows.append((account_id,day,'linked_class' if linked else 'singleton_token',cap,gross,receivable,
                                 refunded,refundable,lower,upper,state,reason))
            for d in day_debits:
                if d['token_id'] in anomalous:
                    todo_seq+=1
                    todos.append((todo_seq,account_id,day,'gate_dispute',int(d['cents']),'abnormal_gate_debit_single_sided',d['id']))
            for j in jr:
                if j[7]=='open_in':
                    todo_seq+=1;todos.append((todo_seq,account_id,day,'open_in_review',0,j[8],j[0]))
                elif j[7]=='open_out':
                    todo_seq+=1;todos.append((todo_seq,account_id,day,'open_out_review',0,j[8],j[0]))
                elif j[7]=='closed_cross_day':
                    todo_seq+=1;todos.append((todo_seq,account_id,day,'cross_day_review',0,j[8],j[0]))
            for token_id in offline_by_day.get(day,()):
                todo_seq+=1;todos.append((todo_seq,account_id,day,'offline_buffer_review',0,'offline_gate_buffer_uploaded',token_id))
    # Withdrawn-link refunds are swept after the loop against the former members.
    for w in revoked:
        members=[m['token_id'] for m in many(db,'SELECT token_id FROM witness_member WHERE link_id=? ORDER BY token_id',(w['link_id'],))]
        for r in many(db,'SELECT * FROM fare_refund WHERE link_id=? ORDER BY refund_id',(w['link_id'],)):
            if r['refund_id'] in swept_refunds:continue
            swept_refunds.add(r['refund_id'])
            for token_id in members:
                todo_seq+=1
                todos.append((todo_seq,'token:%s'%token_id,r['day'],'backcharge',int(r['cents']),
                              'link_revoked_cross_token_refund_unsupported',r['refund_id']))
    db.executemany('INSERT INTO fare_account VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',account_rows)
    db.executemany('INSERT INTO candidate_journey VALUES(?,?,?,?,?,?,?,?,?)',journey_rows)
    db.executemany('INSERT INTO reconcile_todo VALUES(?,?,?,?,?,?,?)',todos)
    log(db,event_id,'fare_reconcile','fare_account',
        'reconciled','accounts=%d journeys=%d todos=%d'%(len(account_rows),len(journey_rows),len(todos)))
    return {'accounts':len(account_rows),'journeys':len(journey_rows),'todos':len(todos)}

def clearing_report(db):
    """Privacy-limited view: equivalence classes and evidence digests only."""
    return {
        'fare_account':[dict(r) for r in many(db,'SELECT * FROM fare_account ORDER BY account_id,day')],
        'candidate_journey':[dict(r) for r in many(db,'SELECT * FROM candidate_journey ORDER BY journey_key')],
        'reconcile_todo':[dict(r) for r in many(db,'SELECT * FROM reconcile_todo ORDER BY id')],
        'fare_refund':[dict(r) for r in many(db,'SELECT refund_id,account_id,day,cents,refunded_at,reason,link_id FROM fare_refund ORDER BY refund_id')],
        'blind_witness':[{'link_id':r['link_id'],'issuer':r['issuer'],'blind_proof_hash':r['blind_proof_hash'],
                          'state':r['state'],'issued_at':r['issued_at'],'revoked_at':r['revoked_at'],
                          'members':[m['token_id'] for m in many(db,'SELECT token_id FROM witness_member WHERE link_id=? ORDER BY token_id',(r['link_id'],))]}
                         for r in many(db,'SELECT * FROM rotation_witness ORDER BY link_id')],
    }

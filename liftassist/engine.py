"""Transactional source import and replayable three-ledger decisions."""
import hashlib,json,sqlite3
from contextlib import closing
from pathlib import Path
from . import lifts,journeys,assists,mobility
from .store import connect,one,many

DISPATCH={'lift':lifts.register,'fault':lifts.fault,'repair':lifts.repair,'retest':lifts.retest,'journey':journeys.request,
          'staff':assists.qualify,'shift':assists.schedule,'staff_revoke':assists.revoke,'terms':assists.terms,'assist':assists.record,
          'mobility_asset':mobility.register,'mobility_reserve':mobility.reserve,
          'mobility_loan':mobility.loan,'mobility_return':mobility.return_asset}
GROUPS={'lift':{'lift','fault','repair','retest'},'journey':{'journey'},'assist':{'staff','shift','staff_revoke','terms','assist'},
        'mobility':{'mobility_asset','mobility_reserve','mobility_loan','mobility_return'}}
PROJECTIONS=('lift','fault','retest','journey','route_segment','route','staff_qualification','staff_shift','assistance','obligation','compensation_contract','compensation','mobility_asset','mobility_reservation','mobility_loan','decision')

def apply(db,event):
    kind=event.get('kind')
    if kind not in DISPATCH:raise ValueError('unknown_event_kind')
    return DISPATCH[kind](db,event)

def import_file(path,file,expected=None):
    raw=Path(file).read_bytes();doc=json.loads(raw)
    if not isinstance(doc,dict) or not isinstance(doc.get('events'),list):raise ValueError('events_required')
    with closing(connect(path)) as db,db:
        digest=hashlib.sha256(raw).hexdigest()
        old=one(db,'SELECT id,command FROM source_file WHERE sha=?',(digest,))
        if old and old['command']!=(expected or 'mixed'):raise ValueError('source_command_conflict')
        source_id=old['id'] if old else db.execute('INSERT INTO source_file(command,sha,raw) VALUES(?,?,?)',(expected or 'mixed',digest,raw)).lastrowid
        results=[]
        for e in doc['events']:
            if expected and e['kind'] not in GROUPS[expected]:raise ValueError('wrong_subsystem_fixture')
            normalized=json.dumps(e,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
            sha=hashlib.sha256(normalized).hexdigest()
            old_event=one(db,'SELECT sha FROM event WHERE id=?',(e['id'],))
            if old_event:
                if old_event['sha']!=sha:raise ValueError('immutable_event_conflict')
                results.append({'id':e['id'],'replayed':True});continue
            db.execute('INSERT INTO event VALUES(?,?,?,?,?,?)',(e['id'],e['kind'],sha,normalized,e.get('recorded_at',e.get('requested_at',e.get('opened_at','2026-01-01T00:00:00Z'))),source_id))
            outcome=apply(db,e);results.append({'id':e['id'],'outcome':outcome,'replayed':False})
        return {'source_sha':digest,'events':results}

def report(path):
    with closing(connect(path)) as db:
        return {table:[dict(r) for r in many(db,f'SELECT * FROM {table}')] for table in PROJECTIONS}

def audit(path):
    with closing(connect(path)) as db:
        source_rows=many(db,'SELECT sha,raw FROM source_file')
        event_rows=many(db,'SELECT sha,raw FROM event ORDER BY rowid')
        source_integrity=all(hashlib.sha256(x['raw']).hexdigest()==x['sha'] for x in source_rows)
        event_integrity=all(hashlib.sha256(x['raw']).hexdigest()==x['sha'] for x in event_rows)
        if not source_integrity or not event_integrity:
            return {'event_count':len(event_rows),'replay_ok':False,'source_integrity':source_integrity,
                    'event_integrity':event_integrity,'mismatched_tables':['evidence_digest']}
        events=[json.loads(x['raw']) for x in event_rows]
        original={t:[tuple(x) for x in many(db,f'SELECT * FROM {t} ORDER BY rowid')] for t in PROJECTIONS}
    scratch=sqlite3.connect(':memory:');scratch.row_factory=sqlite3.Row
    from .store import DDL
    scratch.executescript(DDL)
    try:
        for event in events:apply(scratch,event)
        rebuilt={t:[tuple(x) for x in many(scratch,f'SELECT * FROM {t} ORDER BY rowid')] for t in PROJECTIONS}
    finally:scratch.close()
    return {'event_count':len(events),'replay_ok':original==rebuilt,'source_integrity':True,
            'event_integrity':True,'mismatched_tables':[t for t in PROJECTIONS if original[t]!=rebuilt[t]]}

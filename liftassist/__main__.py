"""Local CLI for three independently importable evidence ledgers."""
import argparse,json
from contextlib import closing
from pathlib import Path
from .engine import import_file,report,audit
from .store import connect,many
from .asset_custody import location_report

def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in ('lift-import','journey-import','assist-import','mobility-import','custody-import','asset-custody-import'):
        a=s.add_parser(name);a.add_argument('--db',required=True);a.add_argument('--file',required=True)
    for name in ('report','audit','custody-report'):
        a=s.add_parser(name);a.add_argument('--db',required=True)
    d=s.add_parser('demo');d.add_argument('--db',required=True)
    x=p.parse_args()
    if x.cmd=='demo':
        staff=Path(__file__).resolve().parent.parent/'fixtures'/'staff-ok.json'
        import_file(x.db,staff,'assist')
        import_file(x.db,Path(__file__).resolve().parent.parent/'fixtures'/'terms-ok.json','assist')
        file=Path(__file__).resolve().parent.parent/'fixtures'/'demo.json'
        result=import_file(x.db,file)
        result['audit']=audit(x.db)
    elif x.cmd=='report':result=report(x.db)
    elif x.cmd=='audit':result=audit(x.db)
    elif x.cmd=='custody-report':
        with closing(connect(x.db)) as db:
            result={'locations':location_report(db),
                    'custody':[dict(r) for r in many(db,'SELECT * FROM asset_custody ORDER BY asset_id')],
                    'conflicts':[dict(r) for r in many(db,'SELECT * FROM asset_conflict ORDER BY id')]}
    else:result=import_file(x.db,x.file,{'lift-import':'lift','journey-import':'journey','assist-import':'assist','mobility-import':'mobility','custody-import':'custody','asset-custody-import':'asset_custody'}[x.cmd])
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

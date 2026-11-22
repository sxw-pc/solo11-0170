"""Local CLI for three independently importable evidence ledgers."""
import argparse,json
from contextlib import closing
from pathlib import Path
from .engine import import_file,report,audit
from .store import connect
from . import fare_reconcile

def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in ('lift-import','journey-import','assist-import','fare-import'):
        a=s.add_parser(name);a.add_argument('--db',required=True);a.add_argument('--file',required=True)
    for name in ('report','audit','fare-reconcile'):
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
    elif x.cmd=='fare-reconcile':
        with closing(connect(x.db)) as db:result=fare_reconcile.clearing_report(db)
    else:result=import_file(x.db,x.file,{'lift-import':'lift','journey-import':'journey','assist-import':'assist','fare-import':'fare'}[x.cmd])
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

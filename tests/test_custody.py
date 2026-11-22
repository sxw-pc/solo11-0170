import tempfile,unittest,json
from pathlib import Path
from liftassist.engine import import_file,report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class CustodyTests(unittest.TestCase):
    def test_capture_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            asset={'events':[{'id':'asset-1','kind':'mobility_asset','asset_id':'WC1','station':'Central','width_cm':85}]}
            f=Path(tmp)/'asset.json';f.write_text(json.dumps(asset))
            import_file(db,f,'mobility')
            result=import_file(db,FIX/'custody-ok.json','custody')
            self.assertEqual(len(result['events']),3)
            self.assertEqual(report(db)['rfid_scan'][0]['epc'],'EPC-1')
            self.assertTrue(audit(db)['replay_ok'])
    def test_duplicate_custody_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            asset={'events':[{'id':'asset-1','kind':'mobility_asset','asset_id':'WC1','station':'Central','width_cm':85}]}
            f=Path(tmp)/'asset.json';f.write_text(json.dumps(asset))
            import_file(db,f,'mobility');import_file(db,FIX/'custody-ok.json','custody')
            result=import_file(db,FIX/'custody-ok.json','custody')
            self.assertTrue(all(e['replayed'] for e in result['events']))

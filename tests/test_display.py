import tempfile,unittest
from pathlib import Path
from liftassist.engine import import_file,report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class DisplayTests(unittest.TestCase):
    def test_capture_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            result=import_file(db,FIX/'display-ok.json','display')
            self.assertEqual(len(result['events']),5)
            self.assertEqual(report(db)['camera_frame'][0]['visible_asset_hash'],'H-AD1')
            self.assertTrue(audit(db)['replay_ok'])
    def test_duplicate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            import_file(db,FIX/'display-ok.json','display')
            result=import_file(db,FIX/'display-ok.json','display')
            self.assertTrue(all(e['replayed'] for e in result['events']))

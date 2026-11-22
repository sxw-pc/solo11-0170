import tempfile,unittest
from pathlib import Path
from liftassist.engine import import_file,report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class BoardingTests(unittest.TestCase):
    def test_capture_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            result=import_file(db,FIX/'boarding-ok.json','boarding')
            self.assertEqual(len(result['events']),4)
            self.assertEqual(report(db)['dock_measurement'][0]['gap_cm'],60)
            self.assertTrue(audit(db)['replay_ok'])
    def test_duplicate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            import_file(db,FIX/'boarding-ok.json','boarding')
            result=import_file(db,FIX/'boarding-ok.json','boarding')
            self.assertTrue(all(e['replayed'] for e in result['events']))

import tempfile,unittest
from pathlib import Path
from liftassist.engine import import_file,report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class FareTests(unittest.TestCase):
    def test_capture_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            result=import_file(db,FIX/'fare-ok.json','fare')
            self.assertEqual(len(result['events']),4)
            self.assertEqual(report(db)['fare_debit'][0]['cents'],300)
            self.assertTrue(audit(db)['replay_ok'])
    def test_duplicate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            import_file(db,FIX/'fare-ok.json','fare')
            result=import_file(db,FIX/'fare-ok.json','fare')
            self.assertTrue(all(e['replayed'] for e in result['events']))

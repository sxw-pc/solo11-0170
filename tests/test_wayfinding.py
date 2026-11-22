import tempfile,unittest
from pathlib import Path
from liftassist.engine import import_file,report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class WayfindingTests(unittest.TestCase):
    def test_capture_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            result=import_file(db,FIX/'wayfinding-ok.json','wayfinding')
            self.assertEqual(len(result['events']),3)
            self.assertEqual(report(db)['guide_sign'][0]['text'],'Platform')
            self.assertTrue(audit(db)['replay_ok'])
    def test_duplicate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            import_file(db,FIX/'wayfinding-ok.json','wayfinding')
            result=import_file(db,FIX/'wayfinding-ok.json','wayfinding')
            self.assertTrue(all(e['replayed'] for e in result['events']))

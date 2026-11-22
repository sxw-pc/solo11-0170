import tempfile,unittest
from pathlib import Path
from liftassist.engine import import_file,report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class EvacuationTests(unittest.TestCase):
    def test_capture_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            result=import_file(db,FIX/'evacuation-ok.json','evacuation')
            self.assertEqual(len(result['events']),4)
            self.assertEqual(report(db)['evac_zone'][0]['occupants'],80)
            self.assertTrue(audit(db)['replay_ok'])
    def test_duplicate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            import_file(db,FIX/'evacuation-ok.json','evacuation')
            result=import_file(db,FIX/'evacuation-ok.json','evacuation')
            self.assertTrue(all(e['replayed'] for e in result['events']))

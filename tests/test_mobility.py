import json
import tempfile
import unittest
from pathlib import Path
from liftassist.engine import import_file, report, audit

FIX = Path(__file__).resolve().parents[1] / 'fixtures'


class MobilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'state.sqlite'
        for name, group in [('lift-ok.json', 'lift'), ('staff-ok.json', 'assist'),
                            ('journey-ok.json', 'journey')]:
            import_file(self.db, FIX / name, group)

    def test_loan_return_and_replay(self):
        import_file(self.db, FIX / 'mobility-ok.json', 'mobility')
        state = report(self.db)
        self.assertEqual(state['mobility_asset'][0]['state'], 'ready')
        self.assertEqual(state['mobility_loan'][0]['state'], 'returned')
        self.assertTrue(audit(self.db)['replay_ok'])
        result = import_file(self.db, FIX / 'mobility-ok.json', 'mobility')
        self.assertTrue(all(item['replayed'] for item in result['events']))

    def test_unwitnessed_return_is_quarantined(self):
        doc = json.loads((FIX / 'mobility-ok.json').read_text())
        doc['events'][-1]['witness'] = ''
        file = Path(self.temp.name) / 'return-dirty.json'
        file.write_text(json.dumps(doc))
        import_file(self.db, file, 'mobility')
        self.assertEqual(report(self.db)['mobility_asset'][0]['state'], 'quarantine')
        self.assertTrue(audit(self.db)['replay_ok'])


if __name__ == '__main__':
    unittest.main()

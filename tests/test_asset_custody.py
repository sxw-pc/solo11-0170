import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from liftassist.engine import import_file, report, audit
from liftassist.asset_custody import location_report
from liftassist.store import connect

FIX = Path(__file__).resolve().parents[1] / 'fixtures'
ROOT = Path(__file__).resolve().parents[1]


class AssetCustodyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'state.sqlite'

    def ingest(self, name, group=None):
        return import_file(self.db, FIX / name, group)

    def import_events(self, name, events):
        path = self.root / name
        path.write_text(json.dumps({'events': events}))
        return import_file(self.db, path)

    def base(self):
        self.ingest('asset-custody-base.json')

    def custody(self, asset='WC1'):
        return next(r for r in report(self.db)['asset_custody'] if r['asset_id'] == asset)

    def conflict_kinds(self, asset='WC1'):
        return {r['kind'] for r in report(self.db)['asset_conflict'] if r['asset_id'] == asset}

    def location(self, name):
        with connect(self.db) as db:
            for row in location_report(db):
                if row['location'] == name:
                    return row
        return None

    def setup_journey_a(self):
        self.import_events('lift-a.json', [{'id': 'lift-a', 'kind': 'lift', 'lift_id': 'LA', 'station': 'A',
                                            'width_cm': 95, 'capacity': 500, 'revision': 1}])
        self.import_events('staff-a.json', [
            {'id': 'staff-a', 'kind': 'staff', 'qualification_id': 'QA', 'staff_id': 'SA',
             'role': 'accessible_assist', 'issued_at': '2026-11-01T00:00:00Z', 'expires_at': '2026-12-01T00:00:00Z'},
            {'id': 'shift-a', 'kind': 'shift', 'shift_id': 'SHA', 'staff_id': 'SA', 'station': 'A',
             'starts': '2026-11-22T07:00:00Z', 'ends': '2026-11-22T20:00:00Z'}])
        self.import_events('journey-a.json', [{'id': 'journey-a', 'kind': 'journey', 'journey_id': 'JA',
                                               'passenger': 'PA', 'lift_id': 'LA', 'wheelchair_width': 80,
                                               'requested_at': '2026-11-22T09:30:00Z',
                                               'need_by': '2026-11-22T10:30:00Z', 'route_minutes': 10,
                                               'staff_id': 'SA'}])

    def reserve(self):
        return self.import_events('reserve.json', [{'id': 'res-1', 'kind': 'mobility_reserve',
                                                    'reservation_id': 'MR-A', 'asset_id': 'WC1',
                                                    'journey_id': 'JA', 'needed_at': '2026-11-22T09:45:00Z'}])

    def test_tag_scan_and_receipt_alone_stay_pending(self):
        self.base()
        c = self.custody()
        self.assertEqual(c['physical_state'], 'pending_verification')
        self.assertEqual((c['location'], c['holder']), ('A-LOCKER', 'STAFF-A'))
        self.assertEqual((c['tag_epc'], c['tag_state'], c['tag_location']), ('EPC-1', 'bound', 'A-LOCKER'))
        locker = self.location('A-LOCKER')
        self.assertEqual((locker['available'], locker['pending_verification']), (0, 1))
        self.setup_journey_a()
        with self.assertRaisesRegex(ValueError, 'mobility_asset_pending_verification'):
            self.reserve()
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_world_a_physical_return_verifies(self):
        self.base()
        self.ingest('asset-custody-world-a.json', 'asset_custody')
        c = self.custody()
        self.assertEqual(c['physical_state'], 'verified')
        self.assertEqual((c['location'], c['holder']), ('A-LOCKER', 'STAFF-A'))
        self.assertEqual(self.conflict_kinds(), set())
        self.assertEqual(self.location('A-LOCKER')['available'], 1)
        self.setup_journey_a()
        self.assertEqual(self.reserve()['events'][0]['outcome'], 'reserved')
        replay = self.ingest('asset-custody-world-a.json', 'asset_custody')
        self.assertTrue(all(e['replayed'] for e in replay['events']))
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_world_b_tag_on_tray_is_not_physical_presence(self):
        self.base()
        self.ingest('asset-custody-world-b.json', 'asset_custody')
        c = self.custody()
        # the tag was left on a tray at A-LOCKER; the frame was seen at B-DOCK
        self.assertEqual(c['physical_state'], 'conflict')
        self.assertEqual((c['location'], c['holder']), ('B-DOCK', 'unknown'))
        self.assertEqual((c['tag_state'], c['tag_location']), ('detached', 'A-LOCKER'))
        self.assertIn('claim_contradicted', self.conflict_kinds())
        self.assertIsNone(self.location('A-LOCKER'))
        self.assertEqual(self.location('B-DOCK')['available'], 0)
        # the wrongly entered scans and the manual receipt are preserved, not rewritten
        scans = report(self.db)['rfid_scan']
        self.assertEqual(len(scans), 2)
        self.assertEqual(scans[1]['reader'], 'A-LOCKER-READER')
        receipt = report(self.db)['custody_receipt'][0]
        self.assertEqual((receipt['id'], receipt['holder'], receipt['location']), ('CR1', 'STAFF-A', 'A-LOCKER'))
        self.setup_journey_a()
        with self.assertRaisesRegex(ValueError, 'mobility_asset_conflict'):
            self.reserve()
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_plan1_sealed_inventory_found(self):
        self.base()
        self.ingest('asset-custody-hold.json', 'asset_custody')
        c = self.custody()
        self.assertEqual((c['physical_state'], c['location']), ('sealed', 'A-LOCKER'))
        self.setup_journey_a()
        with self.assertRaisesRegex(ValueError, 'mobility_asset_sealed'):
            self.reserve()
        self.ingest('asset-custody-check-found.json', 'asset_custody')
        c = self.custody()
        self.assertEqual(c['physical_state'], 'verified')
        self.assertEqual(report(self.db)['custody_hold'][0]['state'], 'released')
        self.assertEqual(self.location('A-LOCKER')['available'], 1)
        self.assertEqual(self.reserve()['events'][0]['outcome'], 'reserved')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_plan1_sealed_inventory_missing_escalates(self):
        self.base()
        self.ingest('asset-custody-hold.json', 'asset_custody')
        self.ingest('asset-custody-check-missing.json', 'asset_custody')
        c = self.custody()
        self.assertEqual((c['physical_state'], c['reason']), ('missing', 'missing_investigation'))
        self.assertEqual(report(self.db)['custody_hold'][0]['state'], 'escalated')
        self.assertEqual(self.location('A-LOCKER')['missing'], 1)
        self.setup_journey_a()
        with self.assertRaisesRegex(ValueError, 'mobility_asset_missing'):
            self.reserve()
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_plan2_transfer_completes_with_frame_receipt(self):
        self.base()
        self.ingest('asset-custody-transfer.json', 'asset_custody')
        c = self.custody()
        self.assertEqual((c['physical_state'], c['location'], c['holder']), ('in_transit', 'IN_TRANSIT', 'CARRIER-1'))
        self.assertIsNone(self.location('A-LOCKER'))  # A no longer counts WC1
        self.setup_journey_a()
        with self.assertRaisesRegex(ValueError, 'mobility_asset_in_transit'):
            self.reserve()
        self.ingest('asset-custody-receive.json', 'asset_custody')
        c = self.custody()
        self.assertEqual((c['physical_state'], c['location'], c['holder']), ('verified', 'B-DOCK', 'STAFF-B'))
        self.assertEqual(report(self.db)['asset_transfer'][0]['state'], 'completed')
        self.assertEqual(self.conflict_kinds(), set())  # signed transfer explains the movement
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_plan2_overdue_carrier_claim(self):
        self.base()
        self.ingest('asset-custody-transfer.json', 'asset_custody')
        early = [{'id': 'cl-early', 'kind': 'carrier_claim', 'claim_id': 'CL0', 'transfer_id': 'TR1',
                  'claimed_at': '2026-11-22T11:00:00Z', 'claimant': 'OWNER-1'}]
        with self.assertRaisesRegex(ValueError, 'carrier_claim_before_due'):
            self.import_events('claim-early.json', early)
        self.ingest('asset-custody-claim.json', 'asset_custody')
        c = self.custody()
        self.assertEqual((c['physical_state'], c['reason'], c['holder']), ('missing', 'carrier_overdue', 'CARRIER-1'))
        self.assertEqual(report(self.db)['asset_transfer'][0]['state'], 'overdue_claim')
        self.assertEqual(report(self.db)['carrier_claim'][0]['state'], 'open')
        late = [{'id': 'rcv-late', 'kind': 'transfer_receive', 'receive_id': 'TRR2', 'transfer_id': 'TR1',
                 'asset_id': 'WC1', 'frame_serial': 'FS-WC1', 'location': 'B-DOCK',
                 'received_at': '2026-11-22T13:30:00Z', 'receiver': 'STAFF-B', 'seal_intact': True}]
        self.import_events('receive-late.json', late)
        c = self.custody()
        self.assertEqual((c['physical_state'], c['location']), ('verified', 'B-DOCK'))
        self.assertEqual(report(self.db)['asset_transfer'][0]['state'], 'recovered_after_claim')
        self.assertEqual(report(self.db)['carrier_claim'][0]['state'], 'closed')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_tag_rewrite_shares_one_epc_on_two_assets(self):
        self.base()
        self.ingest('asset-custody-rewrite.json')
        for asset in ('WC1', 'WC2'):
            c = self.custody(asset)
            self.assertEqual(c['physical_state'], 'conflict')
            self.assertEqual(c['tag_state'], 'shared')
            self.assertIn('epc_shared', self.conflict_kinds(asset))
        self.setup_journey_a()
        with self.assertRaisesRegex(ValueError, 'mobility_asset_conflict'):
            self.reserve()
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_same_frame_witnessed_in_two_places(self):
        self.base()
        self.ingest('asset-custody-two-frames.json', 'asset_custody')
        self.assertIn('frame_in_two_places', self.conflict_kinds())
        self.assertEqual(self.custody()['physical_state'], 'conflict')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_late_detach_recomputes_without_rewriting_history(self):
        self.base()
        self.assertEqual(self.custody()['tag_state'], 'bound')
        detach = [{'id': 'ld-1', 'kind': 'tag_certificate', 'cert_id': 'CERT-D1', 'epc': 'EPC-1',
                   'asset_id': 'WC1', 'action': 'detach', 'actor': 'VENDOR-9',
                   'effective_at': '2026-11-22T08:05:00Z', 'certificate': 'WC-77'}]
        self.import_events('detach.json', detach)
        c = self.custody()
        # scans after the detach no longer count as asset claims; the manual receipt remains a mere claim
        self.assertEqual((c['physical_state'], c['tag_state']), ('pending_verification', 'detached'))
        tray = [{'id': 'ld-2', 'kind': 'location_observation', 'obs_id': 'LO-X', 'location': 'A-LOCKER',
                 'observed_at': '2026-11-22T09:06:00Z', 'weight_kg': 3, 'silhouette': 'tray', 'observer': 'SCALE-1'}]
        self.import_events('tray.json', tray)
        c = self.custody()
        self.assertEqual((c['physical_state'], c['reason']), ('missing', 'absent_at_claimed_location'))
        self.assertIn('absent_at_claimed_location', self.conflict_kinds())
        self.assertEqual(len(report(self.db)['rfid_scan']), 2)
        self.assertEqual(report(self.db)['custody_receipt'][0]['location'], 'A-LOCKER')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_completed_loan_facts_survive_late_contradiction(self):
        self.import_events('asset.json', [
            {'id': 'la-asset', 'kind': 'mobility_asset', 'asset_id': 'WC1', 'station': 'A', 'width_cm': 85},
            {'id': 'la-frame', 'kind': 'asset_frame', 'asset_id': 'WC1', 'frame_serial': 'FS-WC1',
             'enrolled_at': '2026-11-22T08:01:00Z', 'certificate': 'FRAME-CERT-1'}])
        self.import_events('lift-a.json', [{'id': 'lift-a', 'kind': 'lift', 'lift_id': 'LA', 'station': 'A',
                                            'width_cm': 95, 'capacity': 500, 'revision': 1}])
        self.import_events('staff-a.json', [
            {'id': 'staff-a', 'kind': 'staff', 'qualification_id': 'QA', 'staff_id': 'SA',
             'role': 'accessible_assist', 'issued_at': '2026-11-01T00:00:00Z', 'expires_at': '2026-12-01T00:00:00Z'},
            {'id': 'shift-a', 'kind': 'shift', 'shift_id': 'SHA', 'staff_id': 'SA', 'station': 'A',
             'starts': '2026-11-22T07:00:00Z', 'ends': '2026-11-22T20:00:00Z'}])
        self.import_events('journey-a.json', [{'id': 'journey-a', 'kind': 'journey', 'journey_id': 'JA',
                                               'passenger': 'PA', 'lift_id': 'LA', 'wheelchair_width': 80,
                                               'requested_at': '2026-11-22T08:05:00Z',
                                               'need_by': '2026-11-22T09:00:00Z', 'route_minutes': 10,
                                               'staff_id': 'SA'}])
        self.import_events('loan.json', [
            {'id': 'la-reserve', 'kind': 'mobility_reserve', 'reservation_id': 'MR-L', 'asset_id': 'WC1',
             'journey_id': 'JA', 'needed_at': '2026-11-22T08:20:00Z'},
            {'id': 'la-loan', 'kind': 'mobility_loan', 'loan_id': 'ML-L', 'reservation_id': 'MR-L',
             'loaned_at': '2026-11-22T08:10:00Z'},
            {'id': 'la-return', 'kind': 'mobility_return', 'loan_id': 'ML-L',
             'returned_at': '2026-11-22T08:50:00Z', 'condition': 'clean', 'witness': 'I-1'}])
        state = report(self.db)
        self.assertEqual(state['mobility_loan'][0]['state'], 'returned')
        self.assertEqual(state['mobility_reservation'][0]['state'], 'completed')
        self.assertEqual(self.custody()['physical_state'], 'pending_verification')
        late = [{'id': 'la-fw', 'kind': 'frame_witness', 'witness_id': 'FWX', 'asset_id': 'WC1',
                 'frame_serial': 'FS-WC1', 'location': 'B-DOCK', 'observed_at': '2026-11-22T09:30:00Z',
                 'observer': 'CLERK-B'}]
        self.import_events('late-frame.json', late)
        # the late contradiction recomputes current custody but never the fulfilled loan
        self.assertEqual(self.custody()['physical_state'], 'conflict')
        state = report(self.db)
        self.assertEqual(state['mobility_loan'][0]['state'], 'returned')
        self.assertEqual(state['mobility_reservation'][0]['state'], 'completed')
        self.assertEqual(state['mobility_asset'][0]['state'], 'ready')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_cli_asset_custody_import_and_report(self):
        db = str(self.db)
        def run(*args):
            p = subprocess.run([sys.executable, '-m', 'liftassist', *args], cwd=ROOT,
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout)
        run('mobility-import', '--db', db, '--file', str(FIX / 'asset-custody-asset.json'))
        run('asset-custody-import', '--db', db, '--file', str(FIX / 'asset-custody-world-a.json'))
        out = run('custody-report', '--db', db)
        self.assertEqual(out['custody'][0]['physical_state'], 'verified')
        self.assertEqual(out['locations'][0]['available'], 1)
        self.assertTrue(run('audit', '--db', db)['replay_ok'])


if __name__ == '__main__':
    unittest.main()

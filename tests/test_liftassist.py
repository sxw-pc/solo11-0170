import json,subprocess,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import liftassist
from liftassist.engine import import_file,report,audit

FIX=ROOT/'fixtures'

def put(folder,name,events):
    path=folder/name
    path.write_text(json.dumps({'events':events}),encoding='utf-8')
    return path

def event(kind,**kwargs):
    return {'id':kwargs.pop('id',kind+'-x'),'kind':kind,**kwargs}

class LiftAssistTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'data.sqlite'
        print(liftassist.__file__)
    def ingest(self,name):return import_file(self.db,FIX/name)
    def setup_journey(self):
        self.ingest('lift-ok.json');self.ingest('staff-ok.json');self.ingest('journey-ok.json')
    def test_demo_replay_crosses_three_business_ledgers(self):
        self.ingest('staff-ok.json')
        out=self.ingest('demo.json')
        self.assertEqual([x['outcome'] for x in out['events']],['open','committed','blocked','fulfilled','awaiting_retest','open'])
        self.assertTrue(audit(self.db)['replay_ok'])
        r=report(self.db);self.assertEqual(r['lift'][0]['state'],'open')
        self.assertEqual(r['assistance'][0]['state'],'fulfilled')
        self.assertEqual(r['route'][-1]['state'],'served')
    def test_fault_changes_existing_commitment_and_records_replacement(self):
        self.setup_journey();self.ingest('lift-bad.json')
        r=report(self.db)
        self.assertEqual(r['journey'][0]['state'],'reroute')
        self.assertEqual(r['obligation'][0]['kind'],'replacement')
    def test_repair_alone_does_not_reopen_lift(self):
        self.setup_journey();self.ingest('lift-bad.json')
        self.import_events('repair.json',[event('repair',fault_id='F1',closed_at='2026-11-22T08:40:00Z',witness='I1')])
        self.assertEqual(report(self.db)['lift'][0]['state'],'awaiting_retest')
        self.assertEqual(report(self.db)['journey'][0]['state'],'reroute')
    def test_load_retest_restores_capacity_and_route(self):
        self.test_repair_alone_does_not_reopen_lift()
        self.import_events('retest.json',[event('retest',lift_id='L1',at='2026-11-22T08:41:00Z',load_kg=500,inspector='I2',passed=True)])
        self.assertEqual(report(self.db)['journey'][0]['state'],'committed')
        self.assertEqual(report(self.db)['obligation'][0]['resolved'],1)
    def test_narrow_door_refuses_unreachable_journey(self):
        self.ingest('lift-ok.json');self.ingest('staff-ok.json');self.ingest('journey-bad.json')
        self.assertEqual(report(self.db)['journey'][0]['reason'],'door_too_narrow')
    def test_route_time_must_fit_connection(self):
        self.ingest('lift-ok.json');self.ingest('staff-ok.json')
        self.import_events('slow.json',[event('journey',journey_id='J3',passenger='P3',lift_id='L1',wheelchair_width=80,requested_at='2026-11-22T08:05:00Z',need_by='2026-11-22T08:10:00Z',route_minutes=10,staff_id='S3')])
        self.assertEqual(report(self.db)['journey'][0]['state'],'missed_connection')
    def test_staff_cannot_take_overlapping_commitment(self):
        self.setup_journey()
        self.import_events('second.json',[event('journey',journey_id='J2',passenger='P2',lift_id='L1',wheelchair_width=80,requested_at='2026-11-22T08:06:00Z',need_by='2026-11-22T08:34:00Z',route_minutes=10,staff_id='S1')])
        self.assertEqual({x['id']:x['state'] for x in report(self.db)['journey']}['J2'],'waitlist')
    def test_single_party_assist_cannot_prove_delivery(self):
        self.setup_journey();self.ingest('assist-bad.json')
        self.assertEqual(report(self.db)['assistance'][0]['state'],'unverified')
    def test_late_delivery_creates_compensation(self):
        self.setup_journey()
        self.import_events('late.json',[event('assist',assist_id='A3',journey_id='J1',staff_id='S1',arrived_at='2026-11-22T08:12:00Z',delivered_at='2026-11-22T08:45:00Z',witness='P1',passenger_confirmed=True)])
        self.assertEqual(report(self.db)['assistance'][0]['state'],'remedy')
        self.assertEqual(report(self.db)['obligation'][0]['kind'],'delay_compensation')
    def test_event_id_conflict_never_overwrites_original(self):
        self.ingest('lift-ok.json')
        changed=event('lift',id='lift-1',lift_id='L1',station='Other',width_cm=95,capacity=500,revision=1)
        with self.assertRaisesRegex(ValueError,'immutable_event_conflict'):self.import_events('changed.json',[changed])
        self.assertEqual(report(self.db)['lift'][0]['station'],'Central')
    def test_bad_second_event_rolls_back_first(self):
        self.ingest('lift-ok.json')
        good=event('journey',id='J-new',journey_id='J-new',passenger='P4',lift_id='L1',wheelchair_width=80,requested_at='2026-11-22T08:05:00Z',need_by='2026-11-22T08:30:00Z',route_minutes=10,staff_id='S4')
        bad=event('journey',id='J-bad',journey_id='J-bad',passenger='P5',lift_id='UNKNOWN',wheelchair_width=80,requested_at='2026-11-22T08:05:00Z',need_by='2026-11-22T08:30:00Z',route_minutes=10,staff_id='S5')
        with self.assertRaisesRegex(ValueError,'unknown_route_lift'):self.import_events('batch.json',[good,bad])
        self.assertEqual(report(self.db)['journey'],[])
    def test_parallel_fault_identity_freezes_lift(self):
        self.ingest('lift-ok.json');self.ingest('lift-bad.json')
        self.import_events('conflict.json',[event('fault',fault_id='F1',lift_id='L1',opened_at='2026-11-22T08:11:00Z')])
        self.assertEqual(report(self.db)['lift'][0]['state'],'frozen')
    def test_public_cli_and_local_module(self):
        self.assertTrue(str(Path(liftassist.__file__).resolve()).startswith(str(ROOT)))
        p=subprocess.run([sys.executable,'-m','liftassist','demo','--db',str(self.db)],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertTrue(json.loads(p.stdout)['audit']['replay_ok'])
    def test_identical_file_replay_does_not_duplicate_obligations(self):
        self.setup_journey();self.ingest('lift-bad.json')
        prior=report(self.db)
        replay=self.ingest('lift-bad.json')
        self.assertTrue(replay['events'][0]['replayed'])
        self.assertEqual(len(report(self.db)['obligation']),len(prior['obligation']))
    def test_source_corruption_is_visible_to_audit(self):
        import sqlite3
        self.ingest('lift-ok.json')
        with closing(sqlite3.connect(self.db)) as db,db:db.execute("UPDATE source_file SET raw=? WHERE id=1",(b'corrupt',))
        self.assertFalse(audit(self.db)['source_integrity'])
    def test_outcome_projection_tamper_is_visible_to_audit(self):
        import sqlite3
        self.setup_journey()
        with closing(sqlite3.connect(self.db)) as db,db:db.execute("UPDATE journey SET state='fulfilled' WHERE id='J1'")
        self.assertIn('journey',audit(self.db)['mismatched_tables'])
    def test_missing_staff_qualification_blocks_commitment(self):
        self.ingest('lift-ok.json');self.ingest('journey-ok.json')
        self.assertEqual(report(self.db)['journey'][0]['state'],'awaiting_staff')
    def test_late_revocation_retracts_unserved_commitment(self):
        self.setup_journey()
        self.import_events('revoke.json',[event('staff_revoke',qualification_id='Q1',revoked_at='2026-11-22T08:04:00Z')])
        self.assertEqual(report(self.db)['journey'][0]['state'],'awaiting_staff')
        self.assertEqual(report(self.db)['obligation'][0]['kind'],'replacement')
    def test_delivery_after_shift_cannot_claim_fulfilled(self):
        self.setup_journey()
        self.import_events('offshift.json',[event('assist',assist_id='A-off',journey_id='J1',staff_id='S1',arrived_at='2026-11-22T08:12:00Z',delivered_at='2026-11-22T09:05:00Z',witness='P1',passenger_confirmed=True)])
        self.assertEqual(report(self.db)['assistance'][0]['state'],'disputed')
    def test_new_fault_after_confirmed_delivery_keeps_historical_service(self):
        self.setup_journey();self.ingest('assist-ok.json');self.ingest('lift-bad.json')
        self.assertEqual(report(self.db)['journey'][0]['state'],'served')
        self.assertEqual(report(self.db)['obligation'],[])
    def import_events(self,name,events):
        return import_file(self.db,put(self.root,name,events))

if __name__=='__main__':unittest.main()

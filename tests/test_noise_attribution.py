import json,math,subprocess,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from liftassist.engine import import_file,report,audit
from liftassist.store import connect
from liftassist import noise_attribution

FIX=ROOT/'fixtures'

def put(folder,name,events):
    path=folder/name
    path.write_text(json.dumps({'events':events}),encoding='utf-8')
    return path

def event(kind,**kwargs):
    return {'id':kwargs.pop('id',kind+'-x'),'kind':kind,**kwargs}

class NoiseAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'noise.sqlite'
        import_file(self.db,FIX/'noise-complaint.json','noise')
    def dossier(self):
        with closing(connect(self.db)) as db:return noise_attribution.dossier(db,'ST1',125)
    def import_events(self,name,events):
        return import_file(self.db,put(self.root,name,events))

    def test_complaint_capture_and_replay(self):
        out=import_file(self.db,FIX/'noise-complaint.json','noise')
        self.assertEqual(len(out['events']),15)
        self.assertTrue(all(e['replayed'] for e in out['events']))
        r=report(self.db)
        self.assertEqual([c['level_db'] for c in r['noise_complaint']],[70.0,70.0,70.0])
        self.assertTrue(all(c['saturated']==0 for c in r['noise_complaint']))
        self.assertEqual({m['id']:m['status'] for m in r['interim_measure']},{'M1':'issued','M2':'issued','M3':'issued'})
        self.assertTrue(all(m['basis']=='attribution_unproven' for m in r['interim_measure']))
        self.assertTrue(audit(self.db)['replay_ok'])
        # noise attribution never alters passenger journeys, lifts or evacuation ledgers
        self.assertEqual(r['journey'],[]);self.assertEqual(r['lift'],[]);self.assertEqual(r['obligation'],[])

    def test_histories_a_and_b_are_indistinguishable(self):
        # history A: fan 0.8 / train 0.2; history B: fan 0.2 / train 0.8.
        # Both sum to the same normalized energy 1.0 (70 dB) at MIC1.
        for fan_share in (0.8,0.2):
            self.assertAlmostEqual(70+10*math.log10(fan_share+(1.0-fan_share)),70.0)
        # The ledger built from the complaint evidence is byte-identical in both worlds.
        other=self.root/'world-b.sqlite'
        import_file(other,FIX/'noise-complaint.json','noise')
        self.assertEqual(report(self.db),report(other))
        d=self.dossier()
        self.assertEqual(d['design_matrix'],[[1.0,1.0],[1.0,1.0],[1.0,1.0]])
        self.assertEqual(d['identifiability']['rank'],1)
        self.assertFalse(d['identifiability']['identifiable'])
        # no point split is fabricated: shares stay unproven instead of 0.5/0.5
        self.assertIsNone(d['shares'])
        self.assertIsNone(d['final_attribution'])
        self.assertEqual(d['pending_shares'],['fan','train'])
        self.assertIn('refused',d['identifiability']['explanation'])
        self.assertEqual(d['experiments']['proposals']['min_additional_windows'],1)
        # more identical [1,1] complaints do not raise the rank either
        self.import_events('more.json',[
            event('acoustic_trace',id='AE9',trace_id='AT4',microphone_id='MIC1',started_at='2026-06-01T08:10:00Z',sample_rate_hz=1000,bands={'125':70}),
            event('train_pass',id='AE10',pass_id='TP4',station='ST1',train_id='TRAIN4',passed_at='2026-06-01T08:10:10Z',speed_kph=50),
            event('noise_complaint',id='NC4',complaint_id='C4',station='ST1',microphone_id='MIC1',trace_id='AT4',band_hz=125,started_at='2026-06-01T08:10:00Z',ended_at='2026-06-01T08:10:40Z')])
        d=self.dossier()
        self.assertEqual(len(d['design_matrix']),4)
        self.assertEqual(d['identifiability']['rank'],1)
        self.assertIsNone(d['shares'])

    def test_interim_measures_are_time_limited_and_not_final_fault(self):
        with self.assertRaisesRegex(ValueError,'measure_party_mismatch'):
            self.import_events('wrong-party.json',[event('interim_measure',id='IMX',measure_id='MX',station='ST1',party='vehicle',measure='fan_muffler',issued_at='2026-06-02T09:00:00Z',expires_at='2026-07-02T09:00:00Z')])
        with self.assertRaisesRegex(ValueError,'time_limited'):
            self.import_events('no-expiry.json',[event('interim_measure',id='IMY',measure_id='MY',station='ST1',party='station',measure='fan_muffler',issued_at='2026-06-02T09:00:00Z')])
        with self.assertRaisesRegex(ValueError,'unknown_interim_measure'):
            self.import_events('unknown.json',[event('interim_measure',id='IMZ',measure_id='MZ',station='ST1',party='station',measure='demolish_station',issued_at='2026-06-02T09:00:00Z',expires_at='2026-07-02T09:00:00Z')])
        d=self.dossier()
        measures={m['id']:m for m in d['interim_measures']}
        self.assertEqual(measures['M1']['party'],'station')
        self.assertIn('ventilation_capacity_verification',measures['M1']['obligations'])
        self.assertIn('independent_airflow_logging',measures['M1']['requires'])
        self.assertEqual(measures['M2']['party'],'vehicle')
        self.assertIn('schedule_time_loss',measures['M2']['obligations'])
        self.assertIn('track_work',measures['M3']['obligations'])
        # every interim measure is explicitly not a final fault determination
        self.assertTrue(all(not m['final_fault'] for m in measures.values()))
        self.assertTrue(all(m['basis']=='attribution_unproven' for m in measures.values()))
        self.assertIsNone(d['final_attribution'])

    def test_control_experiment_identifies_history_a(self):
        # world A: fan-only window measures 0.8 of the complaint energy
        level=round(70+10*math.log10(0.8),2)
        out=self.import_events('exp-a.json',[
            event('acoustic_trace',id='AE20',trace_id='ATX',microphone_id='MIC1',started_at='2026-06-01T08:30:00Z',sample_rate_hz=1000,bands={'125':level}),
            event('control_experiment',id='EX1',experiment_id='X1',station='ST1',design='no_train',started_at='2026-06-01T08:30:00Z',ended_at='2026-06-01T08:30:30Z',microphone_id='MIC1',trace_id='ATX',band_hz=125)])
        self.assertEqual(out['events'][1]['outcome'],'observed')
        d=self.dossier()
        self.assertTrue(d['identifiability']['identifiable'])
        self.assertEqual(d['design_matrix'][-1],[1.0,0.0])
        fan,train=d['shares']['fan'],d['shares']['train']
        self.assertGreater(fan[0],0.7);self.assertLess(fan[1],0.91)
        self.assertLess(train[1],0.42)
        self.assertGreater(fan[0],train[1])
        self.assertEqual(d['final_attribution']['dominant'],'fan_dominant')
        recs={r['measure_id']:r['action'] for r in d['final_attribution']['measure_recommendations']}
        self.assertEqual(recs,{'M1':'keep','M2':'cancel','M3':'cancel'})
        # the now-irrelevant vehicle todos are cancelled; the station measure is executed
        self.import_events('done-a.json',[
            event('measure_update',id='MU1',measure_id='M1',at='2026-06-03T09:00:00Z',action='executed',note='muffler installed, airflow independently logged'),
            event('measure_update',id='MU2',measure_id='M2',at='2026-06-03T09:00:00Z',action='cancelled'),
            event('measure_update',id='MU3',measure_id='M3',at='2026-06-03T09:00:00Z',action='cancelled')])
        status={m['id']:m['status'] for m in report(self.db)['interim_measure']}
        self.assertEqual(status,{'M1':'executed','M2':'cancelled','M3':'cancelled'})
        # executed work (timetable changes, purchases) can never be deleted
        with self.assertRaisesRegex(ValueError,'executed_measure_immutable'):
            self.import_events('bad-cancel.json',[event('measure_update',id='MU4',measure_id='M1',at='2026-06-04T09:00:00Z',action='cancelled')])
        d=self.dossier()
        self.assertEqual([m for m in d['interim_measures'] if m['id']=='M1'][0]['executed_at'],'2026-06-03T09:00:00Z')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_control_experiment_identifies_history_b(self):
        # world B: fan-only window measures 0.2 of the complaint energy
        level=round(70+10*math.log10(0.2),2)
        self.import_events('exp-b.json',[
            event('acoustic_trace',id='AE21',trace_id='ATB',microphone_id='MIC1',started_at='2026-06-01T08:30:00Z',sample_rate_hz=1000,bands={'125':level}),
            event('control_experiment',id='EX2',experiment_id='X2',station='ST1',design='no_train',started_at='2026-06-01T08:30:00Z',ended_at='2026-06-01T08:30:30Z',microphone_id='MIC1',trace_id='ATB',band_hz=125)])
        d=self.dossier()
        self.assertTrue(d['identifiability']['identifiable'])
        fan,train=d['shares']['fan'],d['shares']['train']
        self.assertLess(fan[1],0.25);self.assertGreater(train[0],0.6)
        self.assertEqual(d['final_attribution']['dominant'],'train_dominant')
        recs={r['measure_id']:r['action'] for r in d['final_attribution']['measure_recommendations']}
        self.assertEqual(recs,{'M1':'cancel','M2':'keep','M3':'keep'})
        # the vehicle speed limit already ran as an interim measure: the incurred
        # timetable loss stays in the ledger and cannot be cancelled away
        self.import_events('exec-b.json',[event('measure_update',id='MU1',measure_id='M2',at='2026-06-03T09:00:00Z',action='executed',note='speed limit ran, schedule time loss incurred')])
        with self.assertRaisesRegex(ValueError,'executed_measure_immutable'):
            self.import_events('cancel-executed.json',[event('measure_update',id='MU2',measure_id='M2',at='2026-06-04T09:00:00Z',action='cancelled')])
        # the ineffective fan muffler plan is still pending and is cancelled
        self.import_events('cancel-b.json',[event('measure_update',id='MU3',measure_id='M1',at='2026-06-04T09:00:00Z',action='cancelled')])
        status={m['id']:m['status'] for m in report(self.db)['interim_measure']}
        self.assertEqual(status,{'M1':'cancelled','M2':'executed','M3':'issued'})
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_reverb_tail_shapes_train_gain(self):
        self.assertAlmostEqual(noise_attribution.reverb_fraction(10.0,11.0,10.0,2.0),1-math.exp(-0.5))
        self.assertEqual(noise_attribution.reverb_fraction(0.0,5.0,10.0,2.0),0.0)
        self.assertAlmostEqual(noise_attribution.reverb_fraction(11.0,21.0,10.0,2.0),math.exp(-0.5)-math.exp(-5.5))
        # a 1s complaint window right at the pass captures only part of the train energy
        level=round(70+10*math.log10(0.8+0.2*(1-math.exp(-0.5))),2)
        self.import_events('reverb.json',[
            event('acoustic_trace',id='AE30',trace_id='ATS',microphone_id='MIC1',started_at='2026-06-01T08:00:10Z',sample_rate_hz=1000,bands={'125':level}),
            event('noise_complaint',id='NC9',complaint_id='C9',station='ST1',microphone_id='MIC1',trace_id='ATS',band_hz=125,started_at='2026-06-01T08:00:10Z',ended_at='2026-06-01T08:00:11Z')])
        d=self.dossier()
        obs=[o for o in d['observations'] if o['id']=='C9'][0]
        self.assertAlmostEqual(obs['gains']['train'],1-math.exp(-0.5),places=5)
        self.assertEqual(obs['gains']['fan'],1.0)
        # the reverb-shaped row is independent from [1,1] and identifies the shares
        self.assertTrue(d['identifiability']['identifiable'])
        self.assertLessEqual(d['shares']['fan'][0],0.8);self.assertGreaterEqual(d['shares']['fan'][1],0.8)
        self.assertLessEqual(d['shares']['train'][0],0.2);self.assertGreaterEqual(d['shares']['train'][1],0.2)

    def test_saturation_bounds_not_point_split(self):
        # MIC2 clips at 70 dB: the observation is only a lower bound on energy
        self.import_events('saturation.json',[
            event('microphone',id='AE40',microphone_id='MIC2',station='ST1',x_m=10,y_m=10,calibration_id='CAL2',saturation_db=70),
            event('acoustic_trace',id='AE41',trace_id='ATC',microphone_id='MIC2',started_at='2026-06-01T08:10:00Z',sample_rate_hz=1000,bands={'125':70}),
            event('train_pass',id='AE42',pass_id='TP4',station='ST1',train_id='TRAIN4',passed_at='2026-06-01T08:10:10Z',speed_kph=50),
            event('noise_complaint',id='NC5',complaint_id='C5',station='ST1',microphone_id='MIC2',trace_id='ATC',band_hz=125,started_at='2026-06-01T08:10:00Z',ended_at='2026-06-01T08:10:40Z')])
        self.assertEqual([c['saturated'] for c in report(self.db)['noise_complaint'] if c['id']=='C5'],[1])
        d=self.dossier()
        obs=[o for o in d['observations'] if o['id']=='C5'][0]
        self.assertTrue(obs['saturated'])
        self.assertIsNone(obs['energy_hi'])
        self.assertEqual([obs['gains']['fan'],obs['gains']['train']],[0.2,1.0])
        # the lower-bound row still cuts the feasible set: ranges, never a point split
        self.assertTrue(d['identifiability']['identifiable'])
        self.assertLess(d['shares']['fan'][1],0.3)
        self.assertGreater(d['shares']['train'][0],0.8)
        self.assertEqual(d['final_attribution']['dominant'],'train_dominant')

    def test_unsafe_experiment_rejected_and_alternative_offered(self):
        with self.assertRaisesRegex(ValueError,'below_minimum_ventilation'):
            self.import_events('unsafe.json',[event('control_experiment',id='EXU',experiment_id='XU',station='ST1',design='fan_speed',fan_speed_pct=20,started_at='2026-06-01T08:40:00Z',ended_at='2026-06-01T08:40:30Z',microphone_id='MIC1',trace_id='AT1',band_hz=125)])
        out=self.import_events('safe-plan.json',[event('control_experiment',id='EXP',experiment_id='XP',station='ST1',design='fan_speed',fan_speed_pct=40,started_at='2026-06-01T08:40:00Z',ended_at='2026-06-01T08:40:30Z')])
        self.assertEqual(out['events'][0]['outcome'],'planned')
        d=self.dossier()
        self.assertEqual(d['safety']['min_airflow_pct'],40)
        options={o['design']+str(o.get('fan_speed_pct','')):o for o in d['experiments']['proposals']['options']}
        self.assertFalse(options['fan_speed0']['safe'])
        self.assertEqual(options['fan_speed0']['reason'],'below_minimum_ventilation')
        self.assertEqual(options['fan_speed0']['alternative'],'reposition')
        self.assertTrue(options['no_train']['safe'])
        self.assertEqual(options['fan_speed40']['added_row'],[0.25,0.0])
        self.assertEqual(d['review_at'],'2026-06-01T08:40:00Z')

    def test_reposition_breaks_collinearity(self):
        # a second microphone at a different fan/track distance ratio: world A reads 0.36
        level=round(70+10*math.log10(0.2*0.8+1.0*0.2),2)
        self.import_events('reposition.json',[
            event('microphone',id='AE50',microphone_id='MIC2',station='ST1',x_m=10,y_m=10,calibration_id='CAL2'),
            event('acoustic_trace',id='AE51',trace_id='ATR',microphone_id='MIC2',started_at='2026-06-01T08:10:00Z',sample_rate_hz=1000,bands={'125':level}),
            event('train_pass',id='AE52',pass_id='TP4',station='ST1',train_id='TRAIN4',passed_at='2026-06-01T08:10:10Z',speed_kph=50),
            event('control_experiment',id='EXR',experiment_id='XR',station='ST1',design='reposition',started_at='2026-06-01T08:10:00Z',ended_at='2026-06-01T08:10:40Z',microphone_id='MIC2',trace_id='ATR',band_hz=125)])
        d=self.dossier()
        obs=[o for o in d['observations'] if o['id']=='XR'][0]
        self.assertEqual([obs['gains']['fan'],obs['gains']['train']],[0.2,1.0])
        self.assertTrue(d['identifiability']['identifiable'])
        self.assertEqual(d['final_attribution']['dominant'],'fan_dominant')

    def test_complaint_requires_known_evidence(self):
        with self.assertRaisesRegex(ValueError,'unknown_microphone'):
            self.import_events('bad-mic.json',[event('noise_complaint',id='NCB',complaint_id='CB',station='ST1',microphone_id='NOPE',trace_id='AT1',band_hz=125,started_at='2026-06-01T08:00:00Z',ended_at='2026-06-01T08:00:40Z')])
        with self.assertRaisesRegex(ValueError,'band_not_in_trace'):
            self.import_events('bad-band.json',[event('noise_complaint',id='NCC',complaint_id='CC',station='ST1',microphone_id='MIC1',trace_id='AT1',band_hz=63,started_at='2026-06-01T08:00:00Z',ended_at='2026-06-01T08:00:40Z')])
        self.assertEqual(len(report(self.db)['noise_complaint']),3)

    def test_cli_noise_import_and_dossier(self):
        db=self.root/'cli.sqlite'
        p=subprocess.run([sys.executable,'-m','liftassist','noise-import','--db',str(db),'--file',str(FIX/'noise-complaint.json')],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(len(json.loads(p.stdout)['events']),15)
        p=subprocess.run([sys.executable,'-m','liftassist','noise-dossier','--db',str(db),'--station','ST1','--band','125'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        d=json.loads(p.stdout)
        self.assertFalse(d['identifiability']['identifiable'])
        self.assertEqual(d['pending_shares'],['fan','train'])
        self.assertEqual(len(d['interim_measures']),3)

if __name__=='__main__':unittest.main()

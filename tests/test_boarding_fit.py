import json,subprocess,sys,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from liftassist.engine import import_file,report,audit

FIX=ROOT/'fixtures'


def put(folder,name,events):
    path=folder/name
    path.write_text(json.dumps({'events':events}),encoding='utf-8')
    return path


def event(kind,**kwargs):
    return {'id':kwargs.pop('id',kind+'-x'),'kind':kind,**kwargs}


class BoardingFitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'fit.sqlite'

    def ingest(self,name):
        return import_file(self.db,FIX/name,'boarding')

    def import_events(self,name,events):
        return import_file(self.db,put(self.root,name,events))

    def cert(self,cert_id):
        return next(c for c in report(self.db)['fit_certificate'] if c['id']==cert_id)

    def counterexamples(self,cert_id):
        r=report(self.db)
        return [x for x in r['fit_counterexample'] if x['certificate_id']==cert_id]

    def intervals(self,cert_id):
        r=report(self.db)
        return [(i['lower_cm'],i['upper_cm']) for i in r['safe_stop_interval'] if i['certificate_id']==cert_id]

    def actions(self,cert_id):
        r=report(self.db)
        return [(a['kind'],a['state'],a['detail']) for a in r['fit_action'] if a['certificate_id']==cert_id]

    # --- scenario 1: zero offset fits but the full envelope fails ----------
    def test_zero_offset_passes_but_full_envelope_denied(self):
        self.ingest('boarding-fit-ok.json')
        c1=self.cert('C1')
        self.assertEqual(c1['state'],'denied_full_envelope')
        self.assertIn('slope_exceeded',c1['reason'])
        # the zero-offset point itself is inside the safe sub-interval
        self.assertTrue(any(lo<=0<=hi for lo,hi in self.intervals('C1')))
        # the minimal geometric counterexample is recorded with real numbers
        ce=self.counterexamples('C1')
        self.assertTrue(ce)
        worst=max(ce,key=lambda x:x['slope_permille'])
        self.assertGreater(worst['slope_permille'],83)
        self.assertGreaterEqual(worst['effective_bearing_cm'],0)
        # a full-envelope denial must not be silently interpolated away
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_counterexample_carries_minimal_geometry(self):
        self.ingest('boarding-fit-ok.json')
        ce=self.counterexamples('C1')
        row=ce[0]
        for field in ('stop_offset_cm','failed_check','gap_cm','height_diff_cm','effective_bearing_cm','slope_permille'):
            self.assertIn(field,row.keys())
        # the positive (forward) stop end is where the slope limit breaks
        self.assertGreater(max(x['stop_offset_cm'] for x in ce),0)

    # --- scenario 2: the two approved operating modes differ ---------------
    def test_controlled_window_issues_restricted_hardware_certificate(self):
        self.ingest('boarding-fit-ok.json')
        c2=self.cert('C2')
        self.assertEqual(c2['state'],'issued_restricted')
        self.assertEqual(c2['required_precision_cm'],10.0)
        for lo,hi in self.intervals('C2'):
            self.assertGreaterEqual(lo,-10.0);self.assertLessEqual(hi,10.0)
        self.assertTrue(any(lo<=0<=hi for lo,hi in self.intervals('C2')))
        self.assertIn(('stop_control_window','active','control:SC1'),self.actions('C2'))

    def test_controlled_window_without_stop_control_is_retest_pending(self):
        self.ingest('boarding-fit-ok.json')
        # a separate platform on another track has no stop-control evidence
        self.import_events('other-track.json',[
            event('platform_profile',id='pp-pl9',profile_id='PL9',station='ST1',track='T9',
                  edge_points=[[400,60],[620,60]],survey_at='2026-01-01T08:00:00Z'),
            event('fit_survey',id='sv-pl9',survey_id='SV9',profile_id='PL9',survey_version=1,
                  edge_points=[[400,60],[620,60]],horizontal_precision_cm=2,
                  vertical_precision_cm=1,yaw_margin_cm=3,survey_at='2026-01-01T10:00:00Z'),
            event('door_load_profile',id='dlp-pl9',profile_id='PL9',door_id='D1',floor_height_cm=110,load_variation_cm=1),
            event('dock_measurement',id='dm90',measurement_id='DM90',profile_id='PL9',door_id='D1',
                  stop_offset_cm=0,gap_cm=60,height_diff_cm=7,measured_at='2026-01-01T09:00:00Z'),
            event('dock_measurement',id='dm9p',measurement_id='DM9P',profile_id='PL9',door_id='D1',
                  stop_offset_cm=35,gap_cm=90,height_diff_cm=9,measured_at='2026-01-01T09:10:00Z'),
            event('dock_measurement',id='dm9n',measurement_id='DM9N',profile_id='PL9',door_id='D1',
                  stop_offset_cm=-35,gap_cm=55,height_diff_cm=6,measured_at='2026-01-01T09:05:00Z'),
            event('fit_certification',id='fc-c2b',cert_id='C2B',profile_id='PL9',door_id='D1',board_id='BB1',
                  survey_id='SV9',calibration_id='CAL1',requested_half_width_cm=35,
                  mode='controlled_window',wheelchair_width_cm=75)])
        c=self.cert('C2B')
        self.assertEqual(c['state'],'retest_pending')
        self.assertEqual(c['reason'],'stop_control_evidence_missing')
        # safe geometry is still recorded even though the certificate cannot issue
        self.assertTrue(any(lo<=0<=hi for lo,hi in self.intervals('C2B')))

    def test_full_envelope_mode_keeps_passenger_and_hardware_ledgers_separate(self):
        self.ingest('boarding-fit-ok.json')
        # the hardware certificate never writes journey/route state
        r=report(self.db)
        self.assertEqual(r['journey'],[])
        self.assertEqual(r['route'],[])
        # full-envelope failure on BB1 names the validated long board as the remedy
        kinds=[a['kind'] for a in r['fit_action'] if a['certificate_id']=='C1']
        self.assertIn('replace_hardware',kinds)
    def test_long_board_validated_over_whole_envelope_issues_full_certificate(self):
        self.ingest('boarding-fit-ok.json')
        c3=self.cert('C3')
        self.assertEqual(c3['state'],'issued_full')
        iv=self.intervals('C3')
        # full envelope covered; reported bounds stay within the requested window
        self.assertEqual(len(iv),1)
        self.assertGreaterEqual(iv[0][0],-35.0);self.assertLessEqual(iv[0][1],35.0)
        self.assertEqual(self.counterexamples('C3'),[])

    # --- scenario 3: the interior notch defeats endpoint-only checks -------
    def test_interior_notch_fails_at_non_endpoint_offset(self):
        self.ingest('boarding-notch-ok.json')
        cn=self.cert('CN1')
        self.assertEqual(cn['state'],'denied_full_envelope')
        offsets=sorted({x['stop_offset_cm'] for x in self.counterexamples('CN1')
                        if x['failed_check']=='corner_overhang'})
        # the first failure is strictly inside the envelope, not at +/-35
        self.assertGreater(offsets[0],-35.0);self.assertLess(offsets[0],35.0)
        # zero remains geometrically safe; the envelope endpoints alone would pass
        self.assertTrue(any(lo<=0<=hi for lo,hi in self.intervals('CN1')))
        # no replacement board exists, so the door must be sealed and hardware procured
        kinds=[kind for kind,_,_ in self.actions('CN1')]
        self.assertIn('door_unavailable_marking',kinds)
        self.assertIn('hardware_procurement',kinds)

    # --- scenario 4: certificate revocation keeps historical evidence ------
    def test_revocation_removes_future_use_but_keeps_records(self):
        self.ingest('boarding-fit-ok.json')
        self.assertEqual(self.cert('C3')['state'],'issued_full')
        revoked=self.cert('C3')
        self.assertIsNotNone(revoked['revoked_at'])
        self.assertEqual(revoked['revoked_reason'],'late_deflection_recalibration')
        # the certificate row, counterexamples and decisions are retained
        r=report(self.db)
        self.assertTrue(any(d['object_id']=='C3' and d['outcome']=='revoked' for d in r['decision']))
        # active operational actions are closed, not deleted
        self.assertTrue(all(a['state']=='closed' for a in r['fit_action'] if a['certificate_id']=='C3'))

    def test_late_survey_revision_auto_revokes_dependent_certificates(self):
        self.ingest('boarding-fit-ok.json')
        self.import_events('new-survey.json',[
            event('fit_survey',survey_id='SV1B',profile_id='PL1',survey_version=2,
                  edge_points=[[400,60],[620,60]],horizontal_precision_cm=2,
                  vertical_precision_cm=1,yaw_margin_cm=3,survey_at='2026-02-10T00:00:00Z')])
        # C2 was the only still-valid certificate on the old survey; it is revoked
        c2=self.cert('C2')
        self.assertIsNotNone(c2['revoked_at'])
        self.assertIn('superseded_by_survey',c2['revoked_reason'])
        old=next(s for s in report(self.db)['fit_survey'] if s['id']=='SV1')
        self.assertIsNotNone(old['superseded_at'])
        # a new certificate request against the old survey cannot be issued
        self.import_events('stale-cert.json',[
            event('fit_certification',cert_id='COLD',profile_id='PL1',door_id='D1',board_id='BB1',
                  survey_id='SV1',calibration_id='CAL1',requested_half_width_cm=35,
                  mode='full_envelope',wheelchair_width_cm=75)])
        self.assertEqual(self.cert('COLD')['state'],'retest_pending')

    # --- scenario 5: data gaps force retest, never interpolation guesses ---
    def test_height_data_gap_outside_measured_offsets_is_retest_pending(self):
        self.ingest('boarding-fit-ok.json')
        self.import_events('wide-envelope.json',[
            event('fit_certification',cert_id='CWIDE',profile_id='PL1',door_id='D1',board_id='BB1',
                  survey_id='SV1',calibration_id='CAL1',requested_half_width_cm=50,
                  mode='full_envelope',wheelchair_width_cm=75)])
        c=self.cert('CWIDE')
        self.assertEqual(c['state'],'retest_pending')
        self.assertEqual(c['reason'],'height_data_gap')

    def test_missing_zero_offset_dock_blocks_certificate(self):
        events=json.loads((FIX/'boarding-fit-ok.json').read_text())['events']
        events=[e for e in events if e.get('measurement_id')!='DM0' and e.get('id') not in ('FE13','FE14','FE15','FE16')]
        events.append(event('fit_certification',cert_id='CNOZERO',profile_id='PL1',door_id='D1',
                            board_id='BB1',survey_id='SV1',calibration_id='CAL1',
                            requested_half_width_cm=35,mode='full_envelope',wheelchair_width_cm=75))
        self.import_events('no-zero.json',events)
        self.assertEqual(self.cert('CNOZERO')['state'],'retest_pending')
        self.assertEqual(self.cert('CNOZERO')['reason'],'missing_zero_offset_dock')

    def test_survey_measurement_conflict_blocks_all_certificates(self):
        events=json.loads((FIX/'boarding-fit-ok.json').read_text())['events']
        for e in events:
            if e.get('survey_id')=='SV1':
                e['edge_points']=[[400,90],[620,90]]  # contradicts the 60cm zero dock
        self.import_events('conflict.json',events)
        sv=next(s for s in report(self.db)['fit_survey'] if s['id']=='SV1')
        self.assertEqual(sv['conflict_state'],'conflicting')
        for cert_id in ('C1','C2','C3'):
            self.assertEqual(self.cert(cert_id)['state'],'retest_pending')
            self.assertEqual(self.cert(cert_id)['reason'],'survey_conflict')

    def test_width_failure_is_denied_not_pending(self):
        self.ingest('boarding-fit-ok.json')
        self.import_events('wide-wheelchair.json',[
            event('fit_certification',cert_id='CW',profile_id='PL1',door_id='D1',board_id='BB1',
                  survey_id='SV1',calibration_id='CAL1',requested_half_width_cm=35,
                  mode='full_envelope',wheelchair_width_cm=85)])  # board width 80 < 85
        c=self.cert('CW')
        self.assertEqual(c['state'],'denied_full_envelope')
        self.assertEqual(c['reason'],'width_insufficient')

    def test_board_deflection_recalibration_auto_revokes_its_certificates(self):
        self.ingest('boarding-fit-ok.json')
        self.import_events('recal.json',[
            event('board_calibration',calibration_id='CAL2R',board_id='BB2',
                  bearing_end_cm=4,flex_cm=2,calibrated_at='2026-02-11T00:00:00Z')])
        # C3 had already been manually revoked; the recalibration leaves it revoked
        c3=self.cert('C3')
        self.assertIsNotNone(c3['revoked_at'])
        old_cal=next(c for c in report(self.db)['board_calibration'] if c['id']=='CAL2')
        self.assertIsNotNone(old_cal['superseded_at'])

    def test_unknown_certification_evidence_is_rejected(self):
        self.ingest('boarding-fit-ok.json')
        with self.assertRaisesRegex(ValueError,'unknown_certification_evidence'):
            self.import_events('ghost.json',[
                event('fit_certification',cert_id='CGHOST',profile_id='PL1',door_id='D1',board_id='BB1',
                      survey_id='NO-SUCH',calibration_id='CAL1',requested_half_width_cm=35,
                      mode='full_envelope',wheelchair_width_cm=75)])
        self.assertEqual([c for c in report(self.db)['fit_certificate'] if c['id']=='CGHOST'],[])

    def test_survey_coverage_gap_is_retest_pending(self):
        self.ingest('boarding-fit-ok.json')
        # survey whose polyline stops before the board footprint at the positive end
        self.import_events('short-survey.json',[
            event('fit_survey',id='sv-short',survey_id='SVSHORT',profile_id='PL1',survey_version=2,
                  edge_points=[[400,60],[505,60]],horizontal_precision_cm=2,
                  vertical_precision_cm=1,yaw_margin_cm=3,survey_at='2026-02-12T00:00:00Z'),
            event('fit_certification',id='fc-short',cert_id='CSHORT',profile_id='PL1',door_id='D1',
                  board_id='BB1',survey_id='SVSHORT',calibration_id='CAL1',
                  requested_half_width_cm=35,mode='full_envelope',wheelchair_width_cm=75)])
        self.assertEqual(self.cert('CSHORT')['state'],'retest_pending')
        self.assertEqual(self.cert('CSHORT')['reason'],'survey_coverage_gap')

    # --- plumbing: CLI, audit and boarding group integration ---------------
    def test_fit_import_cli_and_audit(self):
        p=subprocess.run([sys.executable,'-m','liftassist','fit-import','--db',str(self.db),
                          '--file',str(FIX/'boarding-fit-ok.json')],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        out=json.loads(p.stdout)
        self.assertIn('FE13',[e['id'] for e in out['events']])
        self.assertEqual(next(e['outcome'] for e in out['events'] if e['id']=='FE13'),'denied_full_envelope')
        a=subprocess.run([sys.executable,'-m','liftassist','audit','--db',str(self.db)],
                         cwd=ROOT,capture_output=True,text=True)
        self.assertTrue(json.loads(a.stdout)['replay_ok'])

    def test_replay_is_idempotent_and_audit_covers_new_tables(self):
        self.ingest('boarding-fit-ok.json')
        again=self.ingest('boarding-fit-ok.json')
        self.assertTrue(all(e['replayed'] for e in again['events']))
        r=report(self.db)
        # re-import must not duplicate certificate-derived rows
        self.assertEqual(len(r['fit_certificate']),3)
        self.assertEqual(len(r['safe_stop_interval']),
                         len({(i['certificate_id'],i['position']) for i in r['safe_stop_interval']}))
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_boarding_group_rejects_foreign_events(self):
        with self.assertRaisesRegex(ValueError,'wrong_subsystem_fixture'):
            import_file(self.db,FIX/'lift-ok.json','boarding')

    def test_committed_journey_cannot_back_into_a_hardware_certificate(self):
        # bring up a full passenger journey that ends committed in the same db
        import_file(self.db,FIX/'lift-ok.json')
        import_file(self.db,FIX/'staff-ok.json')
        import_file(self.db,FIX/'journey-ok.json')
        self.assertEqual(report(self.db)['journey'][0]['state'],'committed')
        # the fit ledger has no boarding evidence yet: certification fails closed
        with self.assertRaisesRegex(ValueError,'unknown_certification_evidence'):
            self.import_events('no-evidence-cert.json',[
                event('fit_certification',cert_id='CISOLATED',profile_id='PL1',door_id='D1',board_id='BB1',
                      survey_id='SV1',calibration_id='CAL1',requested_half_width_cm=35,
                      mode='full_envelope',wheelchair_width_cm=75)])
        self.assertEqual([c for c in report(self.db)['fit_certificate'] if c['id']=='CISOLATED'],[])
        # importing the real fit evidence leaves the journey commitment untouched
        self.ingest('boarding-fit-ok.json')
        self.assertEqual(report(self.db)['journey'][0]['state'],'committed')
        self.assertEqual(self.cert('C1')['state'],'denied_full_envelope')


if __name__=='__main__':unittest.main()

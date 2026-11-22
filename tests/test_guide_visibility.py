import json,subprocess,sys,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from liftassist.engine import import_file,report,audit
from liftassist.store import connect,one,many
from liftassist.guide_visibility import evaluate_route,junction_distance_ok

FIX=ROOT/'fixtures'
LIT=[{'from':'2026-01-01T00:00:00Z','to':'2026-12-31T23:59:59Z'}]
DURING='2026-01-15T10:00:00Z'
AFTER='2026-03-01T10:00:00Z'

def put(folder,name,events):
    path=folder/name;path.write_text(json.dumps({'events':events}),encoding='utf-8');return path

def event(kind,**kw):
    return {'id':kw.pop('id',kind+'-x'),'kind':kind,**kw}

def cert_event(cert_id='CERT1',scheme_id=None,visual=True,eval_at=DURING,eye=(110,130),passages=('PA1',),**kw):
    e={'id':'CE-'+cert_id,'kind':'guide_cert','cert_id':cert_id,'passage_ids':list(passages),
       'map_version':'2026-01-01T08:00:00Z','eye_low_cm':eye[0],'eye_high_cm':eye[1],
       'eval_at':eval_at,'lit_windows':LIT,'visual_claimed':visual}
    if scheme_id:e['scheme_id']=scheme_id
    e.update(kw);return e

class GuideVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'guide.sqlite'
    def load_base(self):
        return import_file(self.db,FIX/'wayfinding-ok.json','wayfinding')
    def import_events(self,name,events,group='guide'):
        return import_file(self.db,put(self.root,name,events),group)
    def import_wayfinding(self,name,events):
        return import_file(self.db,put(self.root,name,events),'wayfinding')
    def evaluate(self,scheme_id=None,eval_at=DURING,eye=(110,130),passages=('PA1',),**kw):
        db=connect(self.db)
        try:
            sch=one(db,'SELECT * FROM guide_scheme WHERE id=?',(scheme_id,)) if scheme_id else None
            return evaluate_route(db,list(passages),eye[0],eye[1],eval_at,LIT,scheme=sch,**kw)
        finally:db.close()

    def test_distance_check_passes_but_continuous_sightline_chain_refused(self):
        self.load_base()
        db=connect(self.db)
        try:
            self.assertTrue(junction_distance_ok(db,['PA1']))
        finally:db.close()
        r=self.evaluate()
        self.assertEqual(r['status'],'refused')
        self.assertEqual(r['reason'],'visual_chain_broken')
        self.assertTrue(r['breaks'])
        # the entrance leg cannot see the end-of-straight sign: break begins at the first samples
        self.assertLessEqual(r['breaks'][0]['start_cm'],100.0)
        actions=[a['action'] for a in r['advice']]
        self.assertIn('move_sign',actions)

    def test_obstruction_ray_height_matches_147_153cm_band(self):
        self.load_base()
        r=self.evaluate(eye=(110,130))
        db=connect(self.db)
        try:
            blocked=[s for s in many(db,'SELECT * FROM cert_sample')] if False else []
        finally:db.close()
        # samples persisted only via cert events; check the geometry directly
        from liftassist.guide_visibility import segment_polygon_t
        poly=[[400,-20],[500,-20],[500,20],[400,20]]
        # eye (0,0,120) -> sign SI1 (900,0,180): ray z at board entry/exit
        t=segment_polygon_t((0,0),(900,0),poly)
        self.assertIsNotNone(t)
        z_lo=120+t[0]*(180-120);z_hi=120+t[1]*(180-120)
        # board spans x 400-500 on a 780 cm sightline: ray height about 146.7-153.3 cm
        self.assertTrue(146.0<=z_lo<=154.0,(z_lo,z_hi))
        self.assertTrue(146.0<=z_hi<=154.0,(z_lo,z_hi))
        self.assertLessEqual(z_lo,160.0)  # board top at 160 cm blocks the ray
        # and every walkable viewpoint in the shadowed section is unreadable
        for smp in r['samples']:
            if smp['station']<400.0 and not smp['covered']:
                self.assertIn(smp['reason'],('obstruction:OB1','face_angle_exceeded','out_of_readable_distance','no_sign_in_sight'))

    def test_static_scheme_relay_issues_visual_cert(self):
        self.load_base()
        import_file(self.db,FIX/'guide-static-ok.json','guide')
        r=self.evaluate(scheme_id='SCH-STATIC')
        self.assertEqual((r['status'],r['reason']),('issued','visual_continuous'))
        self.assertEqual(r['breaks'],[])
        out=self.import_events('cert.json',[cert_event('CERT-OK','SCH-STATIC')])
        self.assertEqual(out['events'][0]['outcome'],'issued')
        row=one(connect(self.db),"SELECT * FROM guide_cert WHERE id='CERT-OK'")
        self.assertEqual(row['state'],'issued');self.assertEqual(row['visual_claimed'],1)
        self.assertEqual(row['map_version'],'2026-01-01T08:00:00Z')

    def test_moving_only_si1_in_front_of_board_still_refuses(self):
        self.load_base()
        self.import_events('half.json',[
            event('guide_scheme',scheme_id='SCH-HALF',passage_ids=['PA1'],scheme_kind='static_visual',
                  map_version='2026-01-01T08:00:00Z',approved_at='2026-01-10T09:00:00Z',
                  signs=[{'sign_id':'SI1','passage_id':'PA1','x_cm':300,'y_cm':0,'z_cm':180,
                          'face_deg':180,'text':'Platform','text_height_cm':10}],aids=[])])
        r=self.evaluate(scheme_id='SCH-HALF')
        self.assertEqual(r['status'],'refused')
        # moving SI1 in front of the board restores the first leg only;
        # the long stretch behind the board and the bend remain broken
        self.assertTrue(any(b['end_cm']>500.0 for b in r['breaks']))

    def test_bend_internal_break_between_sampled_nodes(self):
        self.load_base()
        self.import_wayfinding('column.json',[
            event('obstacle',id='OB2E',obstacle_id='OB2',passage_id='PA1',
                  polygon=[[980,300],[1020,300],[1020,400],[980,400]],height_cm=200,
                  valid_from='2026-03-01T00:00:00Z',valid_to='2026-12-31T00:00:00Z')])
        import_file(self.db,FIX/'guide-static-ok.json','guide')
        r=self.evaluate(scheme_id='SCH-STATIC',eval_at=AFTER)
        self.assertEqual(r['status'],'refused')
        bend=[b for b in r['breaks'] if b['blocked_by']=='OB2']
        self.assertTrue(bend)
        # break lies inside the second leg, between junction samples
        self.assertTrue(any(1000.0<=b['start_cm']<1400.0 for b in bend))

    def test_assistive_scheme_issues_non_visual_cert_without_claiming_sight(self):
        self.load_base()
        import_file(self.db,FIX/'guide-assist-ok.json','guide')
        r=self.evaluate(scheme_id='SCH-ASSIST')
        self.assertEqual((r['status'],r['reason']),('issued','non_visual_assist'))
        self.assertTrue(r['breaks'])
        self.assertTrue(all(b['covered_by_aid'] for b in r['breaks']))
        out=self.import_events('aidcert1.json',[cert_event('CERT-AID','SCH-ASSIST',visual=False)])
        self.assertEqual(out['events'][0]['outcome'],'issued')
        row=one(connect(self.db),"SELECT * FROM guide_cert WHERE id='CERT-AID'")
        self.assertEqual(row['kind'],'tactile_voice');self.assertEqual(row['visual_claimed'],0)
        with self.assertRaises(ValueError):
            self.import_events('badclaim.json',[cert_event('CERT-BAD2','SCH-ASSIST',visual=True)])

    def test_late_obstacle_revokes_visual_cert_but_keeps_history(self):
        self.load_base()
        import_file(self.db,FIX/'guide-static-ok.json','guide')
        self.import_events('cert.json',[cert_event('CERT-R','SCH-STATIC')])
        # a late obstacle record covering the certified evaluation window
        self.import_wayfinding('late.json',[
            event('obstacle',id='OBLATE',obstacle_id='OB3',passage_id='PA1',
                  polygon=[[600,-30],[700,-30],[700,30],[600,30]],height_cm=170,
                  valid_from='2026-01-10T00:00:00Z',valid_to='2026-01-31T00:00:00Z')])
        rows=many(connect(self.db),"SELECT * FROM guide_cert WHERE id='CERT-R' ORDER BY revision")
        self.assertEqual([r['state'] for r in rows],['issued','revoked'])
        self.assertEqual(rows[1]['reason'],'late_obstacle_record')
        # original revision rows and samples stay untouched
        self.assertEqual(len(many(connect(self.db),"SELECT * FROM visibility_break WHERE cert_id='CERT-R' AND revision=1")),0)

    def test_obstacle_removal_allows_new_visual_cert_and_keeps_assist_history(self):
        self.load_base()
        import_file(self.db,FIX/'guide-assist-ok.json','guide')
        self.import_events('aidcert.json',[cert_event('CERT-A','SCH-ASSIST',visual=False)])
        import_file(self.db,FIX/'guide-static-ok.json','guide')
        r=self.evaluate(scheme_id='SCH-STATIC',eval_at=AFTER)
        self.assertEqual((r['status'],r['reason']),('issued','visual_continuous'))
        self.import_events('visualcert.json',[cert_event('CERT-V','SCH-STATIC',eval_at=AFTER)])
        db=connect(self.db)
        try:
            self.assertEqual(one(db,"SELECT state FROM guide_cert WHERE id='CERT-A'")['state'],'issued')
            self.assertEqual(one(db,"SELECT kind,state FROM guide_cert WHERE id='CERT-V'")['kind'],'static_visual')
        finally:db.close()

    def test_unknown_measurements_and_overlapping_windows_are_pending(self):
        cases=[
            ([event('passage',id='c1p',passage_id='P1',station='S',centerline=[[0,0],[1000,0]],width_cm=200,
                    survey_at='2026-01-01T08:00:00Z',layer_offset_cm=50)],'map_layer_offset_unresolved'),
            ([event('passage',id='c2p',passage_id='P2',station='S',centerline=[[0,0],[1000,0]],width_cm=200,survey_at='2026-01-01T08:00:00Z'),
              event('guide_sign',id='c2s',sign_id='S1',passage_id='P2',x_cm=900,y_cm=0,z_cm=None,face_deg=180,text='X',text_height_cm=10)],'unknown_sign_height'),
            ([event('passage',id='c3p',passage_id='P3',station='S',centerline=[[0,0],[1000,0]],width_cm=200,survey_at='2026-01-01T08:00:00Z'),
              event('guide_sign',id='c3s',sign_id='S2',passage_id='P3',x_cm=900,y_cm=0,z_cm=180,face_deg=180,text='X',text_height_cm=10),
              event('obstacle',id='c3o',obstacle_id='O1',passage_id='P3',polygon=[[400,-20],[500,-20],[500,20],[400,20]],height_cm=None,
                    valid_from='2026-01-01T00:00:00Z',valid_to='2026-02-01T00:00:00Z')],'unknown_obstacle_height'),
            ([event('passage',id='c4p',passage_id='P4',station='S',centerline=[[0,0],[1000,0]],width_cm=200,survey_at='2026-01-01T08:00:00Z'),
              event('guide_sign',id='c4s',sign_id='S3',passage_id='P4',x_cm=900,y_cm=0,z_cm=180,face_deg=180,text='X',text_height_cm=10),
              event('obstacle',id='c4a',obstacle_id='OA',passage_id='P4',polygon=[[400,-20],[500,-20],[500,20],[400,20]],height_cm=160,
                    valid_from='2026-01-01T00:00:00Z',valid_to='2026-02-01T00:00:00Z'),
              event('obstacle',id='c4b',obstacle_id='OB',passage_id='P4',polygon=[[400,-20],[500,-20],[500,20],[400,20]],height_cm=160,
                    valid_from='2026-01-15T00:00:00Z',valid_to='2026-03-01T00:00:00Z')],'obstacle_window_overlap'),
            ([event('passage',id='c5p',passage_id='P5',station='S',centerline=[[0,0],[1000,0]],width_cm=200,survey_at='2026-01-01T08:00:00Z'),
              event('guide_sign',id='c5s',sign_id='S5',passage_id='P5',x_cm=900,y_cm=0,z_cm=180,face_deg=180,text='X')],'unknown_text_height'),
        ]
        for i,(events,reason) in enumerate(cases):
            dbp=self.root/f'case{i}.sqlite'
            import_file(dbp,put(self.root,f'case{i}.json',events),'wayfinding')
            db=connect(dbp)
            try:
                r=evaluate_route(db,[events[0]['passage_id']],120,120,DURING,LIT)
            finally:db.close()
            self.assertEqual(r['status'],'pending',reason)
            self.assertEqual(r['reason'],reason)

    def test_pending_cert_is_persisted_and_map_version_bound(self):
        self.load_base()
        self.import_events('pend.json',[cert_event('CERT-P',map_version='2020-01-01T00:00:00Z')])
        row=one(connect(self.db),"SELECT * FROM guide_cert WHERE id='CERT-P'")
        self.assertEqual(row['state'],'pending');self.assertEqual(row['reason'],'map_version_mismatch')

    def test_shared_sign_move_requires_revalidation_of_both_chains(self):
        self.load_base()
        self.import_wayfinding('branch.json',[
            event('passage',id='PA3E',passage_id='PA3',station='ST1',centerline=[[0,0],[0,-800]],width_cm=200,survey_at='2026-01-01T08:00:00Z'),
            event('guide_sign',id='SI9E',sign_id='SI9',passage_id='PA3',x_cm=0,y_cm=-700,z_cm=180,face_deg=90,text='Exit',text_height_cm=10)])
        self.import_events('shared.json',[
            event('guide_scheme',scheme_id='SCH9',passage_ids=['PA1','PA3'],scheme_kind='static_visual',
                  map_version='2026-01-01T08:00:00Z',approved_at='2026-01-10T09:00:00Z',
                  signs=[{'sign_id':'SI9','passage_id':'PA1','x_cm':300,'y_cm':0,'z_cm':180,'face_deg':180,'text':'Exit','text_height_cm':10},
                         {'sign_id':'SI9','passage_id':'PA3','x_cm':0,'y_cm':-700,'z_cm':180,'face_deg':90,'text':'Exit','text_height_cm':10}],aids=[])])
        r=self.evaluate(scheme_id='SCH9',passages=('PA1','PA3'))
        reval=[a for a in r['advice'] if a['action']=='revalidate_chains' and a['sign_id']=='SI9']
        self.assertTrue(reval)
        self.assertEqual(sorted(reval[0]['passage_ids']),['PA1','PA3'])

    def test_committed_journey_does_not_create_guide_cert(self):
        # the journey ledger stays independent: no guide_cert rows appear from journeys
        import_file(self.db,FIX/'lift-ok.json','lift')
        import_file(self.db,FIX/'staff-ok.json','assist')
        import_file(self.db,FIX/'journey-ok.json','journey')
        self.assertEqual(report(self.db)['journey'][0]['state'],'committed')
        self.assertEqual(report(self.db)['guide_cert'],[])

    def test_audit_replays_guide_tables_and_cli_guide_import(self):
        self.load_base()
        import_file(self.db,FIX/'guide-static-ok.json','guide')
        self.import_events('cert.json',[cert_event('CERT-AUD','SCH-STATIC')])
        self.assertTrue(audit(self.db)['replay_ok'])
        p=subprocess.run([sys.executable,'-m','liftassist','guide-import','--db',str(self.db),
                          '--file',str(FIX/'guide-assist-ok.json')],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('captured',p.stdout)

if __name__=='__main__':unittest.main()

import json,subprocess,sys,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from liftassist.engine import import_file,report,audit
from liftassist.evac_plan import static_throughput_estimate,_Rehearsal,_parse
from liftassist.store import connect,one,many

FIX=ROOT/'fixtures'
T0='2026-06-01T08:{:02d}:00Z'


def put(folder,name,events):
    path=folder/name
    path.write_text(json.dumps({'events':events}),encoding='utf-8')
    return path


def ev(kind,**kw):
    return {'id':kw.pop('id',kind+'-x'),'kind':kind,**kw}


def base_scene(smoke=10,fd1='closed',fd2='closed',occupants=80,station='ST1',smoke_min=1):
    return [
        ev('egress_edge',id='e1',edge_id='E1',station=station,from_node='P',to_node='H',
           capacity_per_min=20,travel_min=2,accessible=True),
        ev('egress_edge',id='e2',edge_id='E2',station=station,from_node='P',to_node='W',
           capacity_per_min=10,travel_min=2,accessible=True),
        ev('egress_edge',id='e3',edge_id='E3',station=station,from_node='W',to_node='STREET',
           capacity_per_min=10,travel_min=1,accessible=True),
        ev('egress_edge',id='er',edge_id='ER',station=station,from_node='P',to_node='R',
           capacity_per_min=40,travel_min=1,accessible=True),
        ev('evac_zone',id='z1',zone_id='Z1',station=station,node='P',occupants=occupants,observed_at=T0.format(0)),
        ev('smoke_reading',id='sr1',reading_id='SR1',station=station,node='H',opacity_pct=smoke,sampled_at=T0.format(smoke_min)),
        ev('fire_door',id='fd1',door_id='FD1',edge_id='E1',state=fd1,observed_at=T0.format(0)),
        ev('fire_door',id='fd2',door_id='FD2',edge_id='E2',state=fd2,observed_at=T0.format(0)),
    ]


class EvacPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'state.sqlite'

    def load(self,name,events,group=None):
        return import_file(self.db,put(self.root,name,events),group)

    def outcomes(self,plan_id):
        db=connect(self.db)
        try:
            return {r['metric']:r['value'] for r in
                    many(db,'SELECT * FROM plan_outcome WHERE plan_id=?',(plan_id,))}
        finally:db.close()

    # ---------------------------------------------------------- requirement 1

    def test_static_max_flow_overstates_evacuation_against_coupled_model(self):
        self.load('scene.json',base_scene(smoke=20,fd1='open'))
        with closing_db(self.db) as db:
            naive=static_throughput_estimate(db,'ST1','Z1',['E1'])
        # conventional answer: 80 people / 20 per min + 2 min walk = 6 min, all clear
        self.assertTrue(naive['all_clear_claim'])
        self.assertEqual(naive['minutes_to_clear'],6)
        self.load('direct.json',[ev('evac_plan',plan_id='PL-DIRECT',station='ST1',zone_id='Z1',
                                    plan_type='direct',issued_at=T0.format(1),horizon_min=3,
                                    route_edges=['E1'],target_node='H')],'evac_plan')
        out=self.outcomes('PL-DIRECT')
        # the coupled rehearsal cannot certify the later batches; by the
        # third minute at least 40 people (two forced batches) are inside
        # the smoke-invaded edge, matching the narrative's lower bound
        self.assertGreaterEqual(int(out['exposed_out']),40)
        self.assertLess(int(out['safe_out']),80)
        exposures=report(self.db)['exposure_record']
        self.assertGreaterEqual(sum(r['count'] for r in exposures),40)

    # ---------------------------------------------------------- requirement 2

    def test_door_action_feeds_back_into_next_minute_smoke_boundary(self):
        # smoke above threshold at H, sampled at 08:02; same evidence, two door policies
        self.load('scene.json',base_scene(smoke=20,fd1='closed',smoke_min=2))
        with closing_db(self.db) as db:
            sim=_Rehearsal(db,'ST1',_parse(T0.format(1)),6,['E1'])
            closed=_Rehearsal(db,'ST1',_parse(T0.format(1)),6,['E1'])
            # opening the door in the rehearsal also moves the smoke boundary
            sim.assumed['E1']='open'
            open_p,_=sim.smoke_bounds(3)
            shut_p,_=closed.smoke_bounds(3)
        import math
        # open pressure boundary lets smoke walk upstream to P; sealed door blocks it
        self.assertTrue(math.isfinite(open_p['P']))
        self.assertTrue(math.isinf(shut_p['P']))
        # capacity is not fixed: E1 certifies at minute 0 (no reading yet)
        # but fails at minute 2 once the reading plus the open door are known
        self.assertEqual(sim.edge_certificate('E1',0,sim.smoke_bounds(0)[0])[0],True)
        self.assertEqual(sim.edge_certificate('E1',2,sim.smoke_bounds(2)[0])[0],False)

    def test_unexecuted_door_command_does_not_change_confirmed_state(self):
        self.load('scene.json',base_scene(smoke=20,fd1='closed'))
        # an open command arrives but no receipt ever confirms execution
        self.load('cmd.json',[ev('access_command',order_id='CMD-9',station='ST1',door_id='FD1',
                                 edge_id='E1',state='open',issued_at=T0.format(1))],'evac_plan')
        with closing_db(self.db) as db:
            sim=_Rehearsal(db,'ST1',_parse(T0.format(1)),6,['E1'])
            self.assertEqual(sim._door_state('E1',2),'closed')
        # once the receipt lands, the confirmed state follows it
        self.load('rcpt.json',[ev('access_receipt',receipt_id='RC-9',door_id='FD1',state='open',
                                  observed_at=T0.format(2),command_id='CMD-9')],'evac_plan')
        with closing_db(self.db) as db:
            sim=_Rehearsal(db,'ST1',_parse(T0.format(1)),6,['E1'])
            self.assertEqual(sim._door_state('E1',2),'open')

    # ---------------------------------------------------------- requirement 3

    def test_shelter_plan_seals_doors_and_hands_full_zone_to_fire_brigade(self):
        import_file(self.db,FIX/'evac-scene-ok.json','evacuation')
        import_file(self.db,FIX/'evac-plan-ok.json','evac_plan')
        r=report(self.db)
        out={x['id']:{m['metric']:m['value'] for m in r['plan_outcome'] if m['plan_id']==x['id']}
             for x in r['evac_plan']}
        shelter=out['PL-SHELTER']
        self.assertEqual(int(shelter['sheltered']),80)
        self.assertEqual(int(shelter['safe_out']),0)
        self.assertEqual(int(shelter['rescue_stranded']),0)
        tasks=[t for t in r['plan_task'] if t['plan_id']=='PL-SHELTER']
        kinds={t['kind'] for t in tasks}
        self.assertEqual(kinds,{'rescue_handoff','refuge_timer'})
        handoff=next(t for t in tasks if t['kind']=='rescue_handoff')
        self.assertEqual(handoff['headcount'],80)
        self.assertEqual(handoff['due_at'],'2026-06-01T08:21:00Z')  # 20 min protection
        # FD1 and FD2 stay sealed: the plan emits no opening command and
        # the only door orders it does emit are 'closed'
        orders=[o for o in r['plan_order'] if o['plan_id']=='PL-SHELTER' and o['door_id']]
        self.assertTrue(all(o['door_state']=='closed' for o in orders))
        cmds={c['door_id']:c['state'] for c in r['access_command'] if c['id'].startswith('PL-SHELTER')}
        self.assertEqual(cmds.get('FD1','closed'),'closed')
        self.assertEqual(cmds.get('FD2','closed'),'closed')
        self.assertEqual([x for x in r['exposure_record'] if x['plan_id']=='PL-SHELTER'],[])

    def test_stairs_plan_phases_eighty_people_at_ten_per_minute_via_smoke_free_route(self):
        import_file(self.db,FIX/'evac-scene-ok.json','evacuation')
        import_file(self.db,FIX/'evac-plan-ok.json','evac_plan')
        r=report(self.db)
        stairs={m['metric']:m['value'] for m in r['plan_outcome'] if m['plan_id']=='PL-STAIRS'}
        self.assertEqual(int(stairs['safe_out']),80)
        self.assertEqual(int(stairs['rescue_stranded']),0)
        minutes=[x for x in r['plan_minute'] if x['plan_id']=='PL-STAIRS' and x['node']=='P']
        first_drop=next(x for x in minutes if x['occupants']<80)
        # first batch leaves at minute 0; last of 80 leaves at minute 7 (10/min)
        self.assertEqual(first_drop['minute'],0)
        self.assertEqual(minutes[-1]['occupants'],0)
        tasks={t['kind']:t for t in r['plan_task'] if t['plan_id']=='PL-STAIRS'}
        self.assertEqual(tasks['stairhead_guidance']['headcount'],80)
        self.assertEqual(tasks['accessible_transfer']['headcount'],6)
        # FD1 stays sealed (already closed in the ledger), FD2 is opened:
        # the independent smoke-free stair is the only route door released
        door_orders={o['door_id']:o['door_state'] for o in r['plan_order']
                     if o['plan_id']=='PL-STAIRS' and o['door_id']}
        self.assertEqual(door_orders,{'FD2':'open'})
        cmds={c['door_id']:c['state'] for c in r['access_command'] if c['id'].startswith('PL-STAIRS')}
        self.assertEqual(cmds,{'FD2':'open'})
        fd1=next(x for x in r['fire_door'] if x['id']=='FD1')
        self.assertEqual(fd1['state'],'closed')

    # ---------------------------------------------------------- requirement 4

    def test_late_sensor_evidence_annotates_exposure_but_never_rewrites_history(self):
        # the reading's existence itself is only knowable when it is received
        self.load('scene.json',base_scene(smoke=20,fd1='open'))
        self.load('cal.json',[ev('sensor_calibration',calibration_id='CAL1',reading_id='SR1',
                                 lag_min=2,clock_drift_min=1,received_at=T0.format(6))],'evac_plan')
        self.load('direct.json',[ev('evac_plan',plan_id='PL-LATE',station='ST1',zone_id='Z1',
                                    plan_type='direct',issued_at=T0.format(1),horizon_min=4,
                                    route_edges=['E1'],target_node='H')],'evac_plan')
        # at plan time the reading has not been received: the rehearsal's
        # knowable evidence cannot prove any smoke, so no exposure is recorded
        before=report(self.db)
        self.assertEqual([x for x in before['exposure_record'] if x['plan_id']=='PL-LATE'],[])
        # independent flow evidence: two batches really entered E1
        moves=[ev('movement_reading',id=f'mv{i}',reading_id=f'MV{i}',station='ST1',edge_id='E1',
                  batch_id=f'P-B{i}',count=20,direction='with_edge',observed_at=T0.format(1+i))
               for i in range(2)]
        self.load('moves.json',moves,'evac_plan')
        # now the late reading is knowable; review annotates actual exposure
        res=self.load('review.json',[ev('plan_review',id='rv1',plan_id='PL-LATE')],'evac_plan')
        self.assertGreaterEqual(res['events'][0]['outcome']['exposure_rows_added'],1)
        after=report(self.db)
        rows=[x for x in after['exposure_record'] if x['plan_id']=='PL-LATE']
        self.assertTrue(rows)
        self.assertTrue(all(x['reason']=='late_smoke_evidence_annotates_actual_exposure' for x in rows))
        # original orders, minute rows and safe-out figures are untouched
        self.assertEqual(len([x for x in after['plan_order'] if x['plan_id']=='PL-LATE']),
                         len([x for x in before['plan_order'] if x['plan_id']=='PL-LATE']))
        # re-review is idempotent: append-only, no duplicate exposure rows
        self.load('review2.json',[ev('plan_review',id='rv2',plan_id='PL-LATE')],'evac_plan')
        again=report(self.db)
        self.assertEqual(len([x for x in again['exposure_record'] if x['plan_id']=='PL-LATE']),len(rows))

    def test_duplicate_movement_counts_keep_first_evidence_only(self):
        self.load('scene.json',base_scene(smoke=20))
        dup=[ev('movement_reading',id='mv1',reading_id='MV1',station='ST1',edge_id='E1',
                batch_id='B1',count=20,direction='with_edge',observed_at=T0.format(2)),
             ev('movement_reading',id='mv2',reading_id='MV2',station='ST1',edge_id='E1',
                batch_id='B1',count=20,direction='with_edge',observed_at=T0.format(2))]
        self.load('moves.json',dup,'evac_plan')
        with closing_db(self.db) as db:
            sim=_Rehearsal(db,'ST1',_parse(T0.format(0)),6,['E1'])
            bounds=sim.reconcile_movement()
        self.assertEqual(len(bounds),1)
        self.assertEqual(bounds[0]['count_high'],20)

    def test_running_exhaust_fan_clears_node_from_smoke_bounds(self):
        import math
        self.load('scene.json',base_scene(smoke=20,fd1='open'))
        with closing_db(self.db) as db:
            without=_Rehearsal(db,'ST1',_parse(T0.format(1)),6,['E1'])
            self.assertTrue(math.isfinite(without.smoke_bounds(2)[0]['H']))
        self.load('fan.json',[ev('fan_reading',reading_id='FAN1',station='ST1',node='H',
                                 running=True,observed_at=T0.format(2))],'evac_plan')
        with closing_db(self.db) as db:
            with_fan=_Rehearsal(db,'ST1',_parse(T0.format(1)),6,['E1'])
            # once the running fan is knowable evidence, H is no longer a smoke seed
            self.assertTrue(math.isinf(with_fan.smoke_bounds(2)[0]['H']))

    def test_validation_rejects_unknown_refuge_bad_horizon_and_unknown_plan_type(self):
        self.load('scene.json',base_scene())
        bad=[ev('evac_plan',id='p-bad-type',plan_id='PL-BAD',station='ST1',zone_id='Z1',
                plan_type='improvise',issued_at=T0.format(1),horizon_min=4,route_edges=['E1'],target_node='H')]
        with self.assertRaisesRegex(ValueError,'unknown_evac_plan_type'):
            self.load('bad1.json',bad,'evac_plan')
        bad=[ev('evac_plan',id='p-bad-horizon',plan_id='PL-BAD2',station='ST1',zone_id='Z1',
                plan_type='shelter',issued_at=T0.format(1),horizon_min=0,route_edges=['ER'],refuge_node='R')]
        with self.assertRaisesRegex(ValueError,'invalid_plan_horizon'):
            self.load('bad2.json',bad,'evac_plan')
        bad=[ev('evac_plan',id='p-bad-refuge',plan_id='PL-BAD3',station='ST1',zone_id='Z1',
                plan_type='shelter',issued_at=T0.format(1),horizon_min=4,route_edges=['ER'],refuge_node='R')]
        with self.assertRaisesRegex(ValueError,'unknown_refuge_area'):
            self.load('bad3.json',bad,'evac_plan')

    # ---------------------------------------------------------- requirement 5

    def test_independent_smoke_free_accessible_exit_stays_certified(self):
        # heavy smoke on the H side; sealed FD1 must keep E2's stair clean
        self.load('scene.json',base_scene(smoke=80,fd1='closed',fd2='open'))
        with closing_db(self.db) as db:
            sim=_Rehearsal(db,'ST1',_parse(T0.format(1)),12,['E2','E3'])
            smoke_p,_=sim.smoke_bounds(10)
        import math
        self.assertTrue(math.isinf(smoke_p['W']))
        self.assertTrue(math.isinf(smoke_p['STREET']))
        ok,_=sim.edge_certificate('E2',0,sim.smoke_bounds(0)[0])
        self.assertTrue(ok)
        self.load('stairs.json',[ev('evac_plan',plan_id='PL-WEST',station='ST1',zone_id='Z1',
                                    plan_type='stairs',issued_at=T0.format(1),horizon_min=12,
                                    route_edges=['E2','E3'],target_node='STREET',
                                    accessible_occupants=6)],'evac_plan')
        out=self.outcomes('PL-WEST')
        self.assertEqual(int(out['safe_out']),80)
        edge=one(connect(self.db),'SELECT accessible FROM egress_edge WHERE id=?',('E2',))
        self.assertEqual(edge['accessible'],1)

    # ---------------------------------------------------------- audit & cli

    def test_full_fixture_replays_through_audit(self):
        import_file(self.db,FIX/'evac-scene-ok.json','evacuation')
        import_file(self.db,FIX/'evac-plan-ok.json','evac_plan')
        a=audit(self.db)
        self.assertTrue(a['replay_ok'],a['mismatched_tables'])
        self.assertEqual(a['mismatched_tables'],[])

    def test_cli_evac_plan_import_and_audit(self):
        p=subprocess.run([sys.executable,'-m','liftassist','evacuation-import','--db',str(self.db),
                          '--file',str(FIX/'evac-scene-ok.json')],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        p=subprocess.run([sys.executable,'-m','liftassist','evac-plan-import','--db',str(self.db),
                          '--file',str(FIX/'evac-plan-ok.json')],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        out=json.loads(p.stdout)
        plan_types={e['outcome']['plan_type'] for e in out['events'] if isinstance(e['outcome'],dict)}
        self.assertEqual(plan_types,{'shelter','stairs'})
        p=subprocess.run([sys.executable,'-m','liftassist','audit','--db',str(self.db)],
                         cwd=ROOT,capture_output=True,text=True)
        self.assertTrue(json.loads(p.stdout)['replay_ok'])


class closing_db:
    def __init__(self,path):self.path=path
    def __enter__(self):
        from liftassist.store import connect
        self.db=connect(self.path);return self.db
    def __exit__(self,*exc):self.db.close()


if __name__=='__main__':unittest.main()

import json,tempfile,unittest
from pathlib import Path
from liftassist.engine import import_file,report,audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def ev(kind,**kw):return {'id':kw.pop('id'),'kind':kind,**kw}

# Shared base batch (identical in every history): schedule PC1 08:00-08:01 for AD1,
# ack SA1 only says "received" at 08:00:05, camera frames CF1@08:00:10 and CF2@08:00:50.
def switch(id,seq,at,to):return ev('screen_switch',id=id,switch_id=id,screen_id='SC1',seq=seq,at=at,to_asset_hash=to)
def segment(id,start,end,asset,screen='SC1'):
    return ev('av_segment',id=id,segment_id=id,screen_id=screen,starts=start,ends=end,visible_asset_hash=asset,media_hash='M-'+id)
def override(id,start,end,prio=9,category='safety'):
    return ev('safety_override',id=id,override_id=id,screen_id='SC1',starts=start,ends=end,priority=prio,category=category)

HIST_A=[switch('SW1',1,'2026-06-01T08:00:00Z','H-AD1'),switch('SW2',2,'2026-06-01T08:01:00Z','H-NEXT')]
HIST_B=[override('OV1','2026-06-01T08:00:20Z','2026-06-01T08:00:40Z'),
        segment('AV1','2026-06-01T08:00:00Z','2026-06-01T08:00:20Z','H-AD1'),
        segment('AV2','2026-06-01T08:00:20Z','2026-06-01T08:00:40Z','H-SAFE'),
        segment('AV3','2026-06-01T08:00:40Z','2026-06-01T08:01:00Z','H-AD1')]

class AdFulfillmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'ad.sqlite'
    def import_events(self,name,events,group='display'):
        path=self.root/name;path.write_text(json.dumps({'events':events}),encoding='utf-8')
        return import_file(self.db,path,group)
    def base(self):
        import_file(self.db,FIX/'display-ok.json','display')
        import_file(self.db,FIX/'ad-fulfillment.json','display')
    def fulfill(self,cid):return {r['contract_id']:r for r in report(self.db)['fulfillment']}[cid]
    def bills(self,cid,kind=None):
        rows=[r for r in report(self.db)['ad_bill'] if r['contract_id']==cid]
        return [r for r in rows if kind is None or r['kind']==kind]
    def makeups(self,cid):return [r for r in report(self.db)['makeup'] if r['contract_id']==cid]

    def test_two_frames_do_not_prove_continuous_minute(self):
        # Same PC1+SA1+CF1+CF2 a naive implementation would bill as 60s: window length,
        # ack linked by command_id, both frames visible. None of that proves one second.
        self.base()
        f=self.fulfill('C-CONT')
        self.assertEqual((f['state'],f['proven_seconds'],f['max_seconds'],f['unproven_seconds']),('unproven',0,60,60))
        held=self.bills('C-CONT','continuous')
        self.assertEqual([(b['state'],b['seconds'],b['amount_cents']) for b in held],[('suspended',60,6000)])
        mk=self.makeups('C-CONT')
        self.assertEqual([(m['owed_seconds'],m['window_start'],m['window_end']) for m in mk],
                         [(60,'2026-06-01T08:01:00Z','2026-06-01T08:02:00Z')])
        s=self.fulfill('C-SAMP')
        self.assertEqual((s['state'],s['samples']),('fulfilled',2))
        sb=self.bills('C-SAMP','sample')
        self.assertEqual([(b['state'],b['seconds'],b['samples'],b['amount_cents']) for b in sb],[('issued',0,2,500)])
        self.assertNotEqual(sb[0]['seconds'],60)  # sample proof is never a continuous-minute bill
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_history_a_switch_log_completes_minute(self):
        self.base();self.import_events('hist-a.json',HIST_A)
        f=self.fulfill('C-CONT')
        self.assertEqual((f['state'],f['proven_seconds'],f['unproven_seconds']),('fulfilled',60,0))
        issued=[b for b in self.bills('C-CONT','continuous') if b['state']=='issued']
        self.assertEqual([(b['seconds'],b['amount_cents']) for b in issued],[(60,6000)])
        self.assertEqual(self.makeups('C-CONT')[-1]['owed_seconds'],0)
        self.assertEqual(self.bills('C-CONT','reversal'),[])

    def test_history_a_player_counter_completes_minute(self):
        self.base()
        self.import_events('counter.json',[
            ev('player_counter',id='CT1',counter_id='CT1',screen_id='SC1',asset_hash='H-AD1',at='2026-06-01T08:00:00Z',total_seconds=1000),
            ev('player_counter',id='CT2',counter_id='CT2',screen_id='SC1',asset_hash='H-AD1',at='2026-06-01T08:01:00Z',total_seconds=1060)])
        self.assertEqual(self.fulfill('C-CONT')['state'],'fulfilled')
        self.assertEqual(self.fulfill('C-CONT')['proven_seconds'],60)

    def test_history_b_safety_preemption_leaves_shortfall(self):
        # Identical PC1/SA1/CF1/CF2 as history A, but the safety alert covered 08:00:20-08:00:40.
        self.base();self.import_events('hist-b.json',HIST_B)
        f=self.fulfill('C-CONT')
        self.assertEqual((f['state'],f['proven_seconds'],f['max_seconds'],f['unproven_seconds']),('shortfall',40,40,0))
        self.assertEqual(self.bills('C-CONT','reversal'),[])  # nothing was issued yet, so nothing to reverse
        mk=self.makeups('C-CONT')[-1]
        self.assertEqual((mk['owed_seconds'],mk['window_start'],mk['window_end']),(20,'2026-06-01T08:01:00Z','2026-06-01T08:01:20Z'))
        self.assertEqual(self.fulfill('C-SAMP')['state'],'fulfilled')  # sample contract untouched

    def test_late_segments_reverse_issued_bill_and_keep_history(self):
        self.base()
        self.import_events('confirm.json',[ev('playback_confirmation',id='CFM1',receipt_id='R1',command_id='PC1',
                                              confirmed_seconds=60,confirmed_at='2026-06-01T08:05:00Z',confirmer='AUDITOR-1')])
        self.assertEqual(self.fulfill('C-CONT')['state'],'fulfilled')
        self.assertEqual(self.makeups('C-CONT')[-1]['owed_seconds'],0)
        before=report(self.db)['ad_bill']
        out=self.import_events('confirm-retry.json',[ev('playback_confirmation',id='CFM1B',receipt_id='R1',command_id='PC1',
                                                        confirmed_seconds=60,confirmed_at='2026-06-01T08:05:00Z',confirmer='AUDITOR-1')])
        self.assertEqual(out['events'][0]['outcome'],'replayed')  # resume by receipt id, no double count
        self.assertEqual(report(self.db)['ad_bill'],before)
        self.import_events('late.json',HIST_B)
        f=self.fulfill('C-CONT')
        self.assertEqual((f['state'],f['proven_seconds'],f['max_seconds']),('shortfall',40,40))
        bills=self.bills('C-CONT')
        self.assertIn(('continuous','issued',60,6000),[(b['kind'],b['state'],b['seconds'],b['amount_cents']) for b in bills])
        rev=self.bills('C-CONT','reversal')
        self.assertEqual([(b['seconds'],b['amount_cents'],b['reason']) for b in rev],[(-20,-2000,'late_evidence_shortfall')])
        self.assertEqual(self.makeups('C-CONT')[-1]['owed_seconds'],20)
        sb=self.bills('C-SAMP','sample')  # issued sample bill is retained, not reversed
        self.assertEqual([(b['state'],b['samples'],b['amount_cents']) for b in sb],[('issued',2,500)])
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_cross_screen_dedup_and_mirror_screen_excluded(self):
        import_file(self.db,FIX/'display-ok.json','display')
        self.import_events('screens.json',[
            ev('display_screen',id='DS2',screen_id='SC2',station='ST1',zone='HALL',device_serial='SER-2'),
            ev('display_screen',id='DS3',screen_id='SC3',station='ST1',zone='HALL',device_serial='SER-3',mirror_of='SC1'),
            ev('ad_contract',id='CTC',contract_id='C-CONT2',campaign_id='AD1',product='continuous',guaranteed_seconds=60,
               required_samples=0,rate_per_second_cents=100,sample_amount_cents=0,signed_at='2026-05-31T12:00:00Z'),
            ev('ad_contract',id='CTS',contract_id='C-S2',campaign_id='AD1',product='sample',guaranteed_seconds=0,
               required_samples=2,rate_per_second_cents=0,sample_amount_cents=500,signed_at='2026-05-31T12:00:00Z'),
            ev('play_command',id='PC2',command_id='PC2',screen_id='SC2',campaign_id='AD1',starts='2026-06-01T08:00:00Z',ends='2026-06-01T08:01:00Z',priority=1),
            ev('play_command',id='PC3',command_id='PC3',screen_id='SC3',campaign_id='AD1',starts='2026-06-01T08:00:00Z',ends='2026-06-01T08:01:00Z',priority=1),
            ev('camera_frame',id='F2',frame_id='F2',screen_id='SC2',image_hash='H-IMG2',captured_at='2026-06-01T08:00:50Z',visible_asset_hash='H-AD1'),
            ev('camera_frame',id='DUP',frame_id='DUP',screen_id='SC2',image_hash='H-FRAME1',captured_at='2026-06-01T08:00:10Z',visible_asset_hash='H-AD1'),
            ev('camera_frame',id='MIR',frame_id='MIR',screen_id='SC3',image_hash='H-IMG3',captured_at='2026-06-01T08:00:30Z',visible_asset_hash='H-AD1')])
        s=self.fulfill('C-S2')  # CF1+F2 count; the resubmitted same-second image and the mirror frame do not
        self.assertEqual((s['state'],s['samples']),('fulfilled',2))
        self.assertEqual(self.bills('C-S2','sample')[0]['samples'],2)
        self.import_events('mirror-seg.json',[segment('MIRSEG','2026-06-01T08:00:00Z','2026-06-01T08:01:00Z','H-AD1',screen='SC3')])
        c=self.fulfill('C-CONT2')  # a full minute proven only on the back-side mirror screen counts for nothing
        self.assertEqual((c['state'],c['proven_seconds']),('unproven',0))

    def test_makeup_window_never_covers_fire_override(self):
        self.base()
        self.import_events('fire.json',[override('OVF','2026-06-01T08:01:00Z','2026-06-01T08:01:30Z',category='fire')])
        mk=self.makeups('C-CONT')[-1]
        self.assertEqual((mk['owed_seconds'],mk['window_start'],mk['window_end'],mk['reason']),
                         (60,'2026-06-01T08:01:30Z','2026-06-01T08:02:30Z','window_rescheduled'))

    def test_camera_calibration_moves_frame_into_window(self):
        import_file(self.db,FIX/'display-ok.json','display')
        self.import_events('c-s3.json',[
            ev('ad_contract',id='CTS3',contract_id='C-S3',campaign_id='AD1',product='sample',guaranteed_seconds=0,
               required_samples=2,rate_per_second_cents=0,sample_amount_cents=500,signed_at='2026-05-31T12:00:00Z'),
            ev('camera_frame',id='CF9',frame_id='CF9',screen_id='SC1',image_hash='H-IMG9',captured_at='2026-06-01T08:01:02Z',visible_asset_hash='H-AD1')])
        self.assertEqual(self.fulfill('C-S3')['state'],'unproven')  # CF9 reads outside the window
        self.import_events('cal.json',[ev('camera_calibration',id='CAL1',calibration_id='CAL1',screen_id='SC1',
                                          at='2026-06-01T00:00:00Z',offset_ms=-5000)])
        self.assertEqual(self.fulfill('C-S3')['state'],'fulfilled')  # calibrated to 08:00:57, inside

    def test_conflicting_player_and_camera_evidence_freezes_billing(self):
        self.base()
        self.import_events('conflict.json',[segment('AVX','2026-06-01T08:00:20Z','2026-06-01T08:00:40Z','H-SAFE')]+HIST_A)
        self.assertEqual(self.fulfill('C-CONT')['state'],'evidence_conflict')
        self.assertEqual([b for b in self.bills('C-CONT','continuous') if b['state']=='issued'],[])
        self.assertEqual(self.makeups('C-CONT')[-1]['owed_seconds'],60)  # positions frozen while in conflict
        self.import_events('override.json',[override('OVX','2026-06-01T08:00:20Z','2026-06-01T08:00:40Z')])
        f=self.fulfill('C-CONT')  # declared preemption priority resolves the same evidence to history B
        self.assertEqual((f['state'],f['proven_seconds']),('shortfall',40))

    def test_power_off_shrinks_provable_maximum(self):
        self.base()
        self.import_events('power.json',[
            ev('screen_power',id='PW1',power_id='PW1',screen_id='SC1',at='2026-06-01T08:00:10Z',state='off'),
            ev('screen_power',id='PW2',power_id='PW2',screen_id='SC1',at='2026-06-01T08:00:30Z',state='on')])
        f=self.fulfill('C-CONT')
        self.assertEqual((f['state'],f['proven_seconds'],f['max_seconds'],f['unproven_seconds']),('unproven',0,40,40))

if __name__=='__main__':unittest.main()

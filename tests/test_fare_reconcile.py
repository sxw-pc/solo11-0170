import json,subprocess,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from liftassist.engine import import_file,report,audit
from liftassist.store import connect
from liftassist import fare_reconcile

FIX=ROOT/'fixtures'
DAY='2026-06-01'

def put(folder,name,events):
    path=folder/name
    path.write_text(json.dumps({'events':events}),encoding='utf-8')
    return path

def ev(kind,**kw):
    return {'id':kw.pop('id',kind+'-x'),'kind':kind,**kw}

class FareReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'fare.sqlite'
    def ingest(self,name):return import_file(self.db,FIX/name,'fare')
    def load(self,events,name='batch.json'):return import_file(self.db,put(self.root,name,events),'fare')
    def accounts(self):return {a['account_id']:a for a in report(self.db)['fare_account']}
    def todos(self,kind=None):
        rows=report(self.db)['reconcile_todo']
        return [r for r in rows if kind is None or r['kind']==kind]
    def clear(self):
        with closing(connect(self.db)) as db:return fare_reconcile.clearing_report(db)

    # World A: one rotating card, named daily-cap product, blind link present.
    def test_world_a_blind_link_merges_only_proven_tokens_and_refunds_100(self):
        self.ingest('fare-reconcile-common.json');self.ingest('fare-reconcile-world-a.json')
        acc=self.accounts()
        linked=acc['link:LK1']
        self.assertEqual((linked['gross_cents'],linked['cap_cents'],linked['receivable_cents']),(600,500,500))
        self.assertEqual((linked['refundable_cents'],linked['bound_lower'],linked['bound_upper']),(100,500,500))
        self.assertEqual(linked['state'],'cap_settled')
        # The co-located passenger keeps an independent singleton account.
        self.assertIn('token:TK-C',acc)
        self.assertEqual(acc['token:TK-C']['gross_cents'],300)
        # The proven class closes into one paired journey; no single-sided dispute
        # is attached to any of its two original 300-cent debits.
        journeys=report(self.db)['candidate_journey']
        link_j=[j for j in journeys if j['account_id']=='link:LK1']
        self.assertEqual(len(link_j),1)
        self.assertEqual((link_j[0]['in_tap'],link_j[0]['out_tap'],link_j[0]['state']),('GTA','GTB','closed'))
        self.assertEqual([t for t in self.todos('gate_dispute') if t['ref_id'] in ('FDA','FDB')],[])
        # The co-located passenger stays an independent account with its own open entry.
        tc_disputes=[t for t in self.todos('gate_dispute') if t['account_id']=='token:TK-C']
        self.assertEqual([t['ref_id'] for t in tc_disputes],['FDC'])
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_world_a_refund_receipt_is_append_only_and_idempotent(self):
        self.ingest('fare-reconcile-common.json');self.ingest('fare-reconcile-world-a.json')
        receipt=[ev('fare_refund',id='RF1',refund_id='RF1',account_id='link:LK1',day=DAY,cents=100,
                    refunded_at='2026-06-01T09:10:00Z',reason='daily_cap_settlement',link_id='LK1')]
        self.assertEqual(self.load(receipt,'refund.json')['events'][0]['outcome'],'refund_recorded')
        replay=import_file(self.db,self.root/'refund.json','fare')
        self.assertTrue(replay['events'][0]['replayed'])
        r=report(self.db)
        self.assertEqual(len(r['fare_refund']),1)
        self.assertEqual(self.accounts()['link:LK1']['refunded_cents'],100)
        # Original two 300-cent debits are untouched.
        self.assertEqual(sorted(d['cents'] for d in r['fare_debit']),[300,300,300])
        self.assertTrue(audit(self.db)['replay_ok'])

    # World B: two distinct cards, anonymous per-ride product, identical gate bytes.
    def test_world_b_keeps_singleton_accounts_and_flags_both_single_sided_journeys(self):
        self.ingest('fare-reconcile-common.json');self.ingest('fare-reconcile-world-b.json')
        acc=self.accounts()
        self.assertEqual(set(acc),{'token:TK-A','token:TK-B','token:TK-C'})
        for key in ('token:TK-A','token:TK-B','token:TK-C'):
            self.assertEqual((acc[key]['gross_cents'],acc[key]['receivable_cents'],acc[key]['refundable_cents']),(300,300,0))
            self.assertIsNone(acc[key]['cap_cents'])
        # Both rotating tokens are single-sided; the co-located passenger keeps its own dispute.
        disputes=self.todos('gate_dispute')
        self.assertEqual(sorted(d['ref_id'] for d in disputes),['FDA','FDB','FDC'])
        kinds={t['kind'] for t in self.todos()}
        self.assertIn('open_in_review',kinds);self.assertIn('open_out_review',kinds)
        # No refund may be paid to either account without a proven equivalence class.
        self.assertTrue(all(a['refundable_cents']==0 for a in acc.values()))
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_anonymous_per_ride_product_rejects_borrowed_blind_link(self):
        self.ingest('fare-reconcile-common.json');self.ingest('fare-reconcile-world-b.json')
        link=[ev('rotation_witness',id='FB-LINK',link_id='LK-ILLEGAL',issuer='CITY',
                 blind_proof_hash='blind-X',issued_at='2026-06-01T09:05:00Z',tokens=['TK-A','TK-B'])]
        with self.assertRaisesRegex(ValueError,'anonymous_product_forbids_link'):
            self.load(link,'illegal-link.json')
        # The rejected link leaves no trace and accounts stay singleton.
        self.assertEqual(report(self.db)['rotation_witness'],[])
        self.assertEqual(set(self.accounts()),{'token:TK-A','token:TK-B','token:TK-C'})

    def test_blind_proof_late_arrival_replaces_bounds_with_settlement(self):
        # Before the witness: explainable bounds, zero payable refund.
        self.ingest('fare-reconcile-common.json')
        self.load([ev('fare_enrollment',enrollment_id='ENA',token_id='TK-A',product_id='FP-CAP',
                      effective_from='2026-06-01T00:00:00Z',effective_to='2026-06-02T00:00:00Z'),
                   ev('fare_enrollment',id='ENB',enrollment_id='ENB',token_id='TK-B',product_id='FP-CAP',
                      effective_from='2026-06-01T00:00:00Z',effective_to='2026-06-02T00:00:00Z'),
                   ev('fare_enrollment',id='ENC',enrollment_id='ENC',token_id='TK-C',product_id='FP-CAP',
                      effective_from='2026-06-01T00:00:00Z',effective_to='2026-06-02T00:00:00Z')],'enroll.json')
        before=self.accounts()
        self.assertEqual(before['token:TK-A']['state'],'proof_pending')
        self.assertEqual(before['token:TK-A']['refundable_cents'],0)
        with self.assertRaisesRegex(ValueError,'refund_exceeds_proven_balance'):
            self.load([ev('fare_refund',id='RF-EARLY',refund_id='RF-EARLY',account_id='token:TK-A',day=DAY,
                          cents=100,refunded_at='2026-06-01T09:01:00Z')],'early-refund.json')
        # Late witness then proves the equivalence class and unlocks the 100-cent balance.
        self.load([ev('rotation_witness',id='LATE-LINK',link_id='LK1',issuer='CITY',
                      blind_proof_hash='blind-proof-LK1',issued_at='2026-06-01T10:00:00Z',tokens=['TK-A','TK-B'])],
                 'late-link.json')
        linked=self.accounts()['link:LK1']
        self.assertEqual((linked['state'],linked['refundable_cents']),('cap_settled',100))
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_proof_pending_bounds_are_explainable_on_single_overspent_token(self):
        self.load([ev('fare_token',token_id='TK-X',issuer='CITY',token_hash='hx',
                      valid_from='2026-06-01T00:00:00Z',valid_to='2026-06-02T00:00:00Z'),
                   ev('fare_product',product_id='FP-CAP',product_type='daily-cap',cap_cents=500,
                      valid_from='2026-01-01T00:00:00Z',valid_to='2026-12-31T00:00:00Z'),
                   ev('fare_enrollment',enrollment_id='ENX',token_id='TK-X',product_id='FP-CAP',
                      effective_from='2026-06-01T00:00:00Z',effective_to='2026-06-02T00:00:00Z'),
                   ev('gate_tap',tap_id='GX',token_id='TK-X',station='ST1',gate='G1',direction='in',
                      observed_at='2026-06-01T08:00:00Z'),
                   ev('fare_debit',id='D1',debit_id='D1',token_id='TK-X',cents=300,charged_at='2026-06-01T08:01:00Z',operator='CITY'),
                   ev('fare_debit',id='D2',debit_id='D2',token_id='TK-X',cents=300,charged_at='2026-06-01T08:31:00Z',operator='CITY')],
                 'overspend.json')
        a=self.accounts()['token:TK-X']
        self.assertEqual((a['gross_cents'],a['bound_lower'],a['bound_upper']),(600,500,500))
        self.assertEqual(a['refundable_cents'],100)

    def test_late_revocation_keeps_refund_receipt_and_raises_backcharge_todo(self):
        self.ingest('fare-reconcile-common.json');self.ingest('fare-reconcile-world-a.json')
        self.load([ev('fare_refund',id='RF1',refund_id='RF1',account_id='link:LK1',day=DAY,cents=100,
                      refunded_at='2026-06-01T09:10:00Z',link_id='LK1')],'refund.json')
        self.load([ev('witness_revoke',id='REV1',link_id='LK1',revoked_at='2026-06-01T12:00:00Z')],'revoke.json')
        r=report(self.db)
        # Refund receipt and original debits remain on the ledger.
        self.assertEqual(len(r['fare_refund']),1)
        self.assertEqual(len(r['fare_debit']),3)
        backcharges=self.todos('backcharge')
        # The withdrawn 100-cent cross-token refund is chased against each former member.
        self.assertEqual(sorted((t['account_id'],t['cents'],t['ref_id']) for t in backcharges),
                         [('token:TK-A',100,'RF1'),('token:TK-B',100,'RF1')])
        # The class is dissolved; each token is singleton again with no payable balance.
        acc=self.accounts()
        self.assertNotIn('link:LK1',acc)
        self.assertTrue(all(a['refundable_cents']==0 for a in acc.values()))
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_cross_day_debits_settle_per_utc_day_and_cross_day_pair_is_reviewed(self):
        self.load([ev('fare_token',id='CD-T',token_id='TK-D',issuer='CITY',token_hash='hd',
                      valid_from='2026-06-01T00:00:00Z',valid_to='2026-06-03T00:00:00Z'),
                   ev('fare_product',id='CD-P',product_id='FP-CAP',product_type='daily-cap',cap_cents=500,
                      valid_from='2026-01-01T00:00:00Z',valid_to='2026-12-31T00:00:00Z'),
                   ev('fare_enrollment',id='CD-EN',enrollment_id='END',token_id='TK-D',product_id='FP-CAP',
                      effective_from='2026-06-01T00:00:00Z',effective_to='2026-06-03T00:00:00Z'),
                   ev('gate_tap',id='CD-GI',tap_id='GIN',token_id='TK-D',station='ST1',gate='G1',direction='in',
                      observed_at='2026-06-01T23:40:00Z'),
                   ev('gate_tap',id='CD-GO',tap_id='GOUT',token_id='TK-D',station='ST2',gate='G1',direction='out',
                      observed_at='2026-06-02T00:20:00Z'),
                   ev('fare_debit',id='CD-D1',debit_id='DD1',token_id='TK-D',cents=400,charged_at='2026-06-01T23:41:00Z',operator='CITY'),
                   ev('fare_debit',id='CD-D2',debit_id='DD2',token_id='TK-D',cents=400,charged_at='2026-06-02T00:21:00Z',operator='CITY')],
                 'crossday.json')
        acc=report(self.db)['fare_account']
        days={(a['account_id'],a['day']) for a in acc}
        self.assertEqual(days,{('token:TK-D','2026-06-01'),('token:TK-D','2026-06-02')})
        self.assertTrue(all(a['receivable_cents']==400 and a['refundable_cents']==0 for a in acc))
        cross=[j for j in report(self.db)['candidate_journey'] if j['state']=='closed_cross_day']
        self.assertEqual(len(cross),1)
        self.assertEqual(len(self.todos('cross_day_review')),1)

    def test_anti_replay_counter_rejects_replayed_value(self):
        self.ingest('fare-reconcile-common.json')
        c=[ev('tap_counter',id='C1',token_id='TK-A',counter=7,observed_at='2026-06-01T08:00:00Z')]
        self.assertEqual(self.load(c,'counter.json')['events'][0]['outcome'],'counter_accepted')
        with self.assertRaisesRegex(ValueError,'replayed_counter'):
            self.load([ev('tap_counter',id='C2',token_id='TK-A',counter=7,observed_at='2026-06-01T08:05:00Z')],
                      'replay.json')

    def test_offline_gate_buffer_creates_review_todo_without_losing_tap(self):
        self.ingest('fare-reconcile-common.json')
        self.load([ev('gate_tap',id='GOFF',tap_id='GOFF',token_id='TK-A',station='ST9',gate='G9',direction='out',
                      observed_at='2026-06-01T07:30:00Z',offline=True)],'offline.json')
        taps={t['id']:t['offline'] for t in report(self.db)['gate_tap']}
        self.assertEqual(taps['GOFF'],1)
        self.assertTrue(any(t['kind']=='offline_buffer_review' for t in self.todos()))

    def test_one_tap_cannot_close_two_accounts_and_token_hash_dedupes_replay(self):
        self.ingest('fare-reconcile-common.json')
        # Re-importing the identical source file replays rather than duplicating taps.
        replay=self.ingest('fare-reconcile-common.json')
        self.assertTrue(all(e['replayed'] for e in replay['events']))
        taps=report(self.db)['gate_tap']
        self.assertEqual(len(taps),3)
        # Each tap belongs to exactly one candidate journey of one account.
        used=[j['in_tap'] for j in report(self.db)['candidate_journey'] if j['in_tap']]
        used += [j['out_tap'] for j in report(self.db)['candidate_journey'] if j['out_tap']]
        self.assertEqual(len(used),len(set(used)))

    def test_blind_link_is_single_use_and_tokens_cannot_join_two_classes(self):
        self.ingest('fare-reconcile-common.json');self.ingest('fare-reconcile-world-a.json')
        with self.assertRaisesRegex(ValueError,'link_id_already_spent'):
            self.load([ev('rotation_witness',id='LK-DUP',link_id='LK1',issuer='CITY',blind_proof_hash='again',
                          issued_at='2026-06-01T09:30:00Z',tokens=['TK-A','TK-B'])],'dup-link.json')
        with self.assertRaisesRegex(ValueError,'token_already_linked_elsewhere'):
            self.load([ev('rotation_witness',id='LK2',link_id='LK2',issuer='CITY',blind_proof_hash='other',
                          issued_at='2026-06-01T09:30:00Z',tokens=['TK-A','TK-C'])],'cross-link.json')

    def test_clearing_report_exposes_equivalence_classes_but_no_identity_material(self):
        self.ingest('fare-reconcile-common.json');self.ingest('fare-reconcile-world-a.json')
        view=self.clear()
        self.assertEqual(set(view),{'fare_account','candidate_journey','reconcile_todo','fare_refund','blind_witness'})
        w=view['blind_witness'][0]
        self.assertEqual((w['link_id'],w['members'],w['state']),('LK1',['TK-A','TK-B'],'active'))
        # Only a proof digest is exportable; no raw blinded material or real-name field exists.
        self.assertEqual(set(w),{'link_id','issuer','blind_proof_hash','state','issued_at','revoked_at','members'})

    def test_cli_fare_reconcile_command(self):
        self.ingest('fare-reconcile-common.json');self.ingest('fare-reconcile-world-a.json')
        p=subprocess.run([sys.executable,'-m','liftassist','fare-reconcile','--db',str(self.db)],
                         cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        out=json.loads(p.stdout)
        ids={a['account_id'] for a in out['fare_account']}
        self.assertIn('link:LK1',ids);self.assertIn('token:TK-C',ids)

if __name__=='__main__':unittest.main()

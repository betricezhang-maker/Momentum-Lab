"""Confirmation lifecycle using isolated financial CSV fixtures."""
import copy
import unittest
import pandas as pd
import test_completeness as fixtures
from momentumlab.data_integrity import save_acknowledgement, revoke_acknowledgement, load_acknowledgements, clear_integrity_cache, staged_dataset, validate_dataset

class IssueResolutionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CompletenessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.drop('raw_price','A',[16]);self.f.drop('adjusted_price','A',[16])

    def confirm(self):
        report=self.f.report(mode='full')
        raw=next(r for r in report['completeness']['findings'] if r['affected_component']=='raw_price' and r['severity']=='FAIL')
        return save_acknowledgement(self.f.paths,raw,'','Checked chart; cause remains unverified.','user reference',findings=report['completeness']['findings'])

    def test_persistence_propagation_warning_and_original_finding(self):
        self.assertFalse(self.f.report()['research_allowed'])
        parent=self.confirm();clear_integrity_cache()
        r=self.f.report();self.assertTrue(r['research_allowed']);self.assertEqual(r['status'],'WARNING')
        self.assertEqual(r['completeness']['status'],'FAIL')
        confirmed=[x for x in r['completeness']['findings'] if x['resolution_status']=='CONFIRMED']
        self.assertEqual({x['affected_component'] for x in confirmed},{'raw_price','adjusted_price'})
        child=next(x for x in confirmed if x['affected_component']=='adjusted_price')
        self.assertEqual(child['inherited_from'],parent['confirmation_id'])
        self.assertEqual(child['explanation'],parent['explanation'])
        self.assertEqual(child['classification'],'UNEXPLAINED_MISSING')
        self.assertEqual(len(load_acknowledgements(self.f.paths)),2)
        ranked=self.f.ranked();row=ranked[ranked.ts_code.eq('A') & ranked.trade_date.eq(self.f.days[20])].iloc[0]
        self.assertTrue(pd.isna(row.momentum_score))

    def test_reopen_cascades_and_preserves_prior_evidence(self):
        parent=self.confirm();snapshot=copy.deepcopy(self.f.report())
        self.assertTrue(revoke_acknowledgement(self.f.paths,parent['issue_id']))
        r=self.f.report();self.assertFalse(r['research_allowed'])
        self.assertEqual(r['completeness']['confirmed_count'],0)
        self.assertTrue(all(not x['active'] for x in load_acknowledgements(self.f.paths)))
        self.assertEqual(snapshot['completeness']['confirmed_count'],2)

    def test_staged_validation_preserves_confirmations(self):
        self.confirm()
        with staged_dataset(self.f.paths) as working:
            r=validate_dataset(working,universe_id='CSI300',lookback=10,mode='full')
            self.assertEqual(r['completeness']['confirmed_count'],2);self.assertTrue(r['research_allowed'])

    def test_partial_overlap_and_new_dates_require_review(self):
        self.f.drop('adjusted_price','A',[17]);self.confirm()
        r=self.f.report();parts=[x for x in r['completeness']['findings'] if x['affected_component']=='adjusted_price' and x['severity']=='FAIL']
        self.assertEqual([(x['date'],x['resolution_status']) for x in parts],[(self.f.days[16].strftime('%Y%m%d'),'CONFIRMED'),(self.f.days[17].strftime('%Y%m%d'),'UNRESOLVED')])
        self.assertFalse(r['research_allowed'])
        self.f.drop('raw_price','A',[17]);r=self.f.report()
        self.assertFalse(r['research_allowed']);self.assertEqual(r['completeness']['confirmed_count'],2)

    def test_material_evidence_change_requires_review(self):
        self.confirm();self.f.life.loc[0,'list_date']=self.f.days[1]
        self.f.life.to_csv(self.f.root/'securities.csv',index=False)
        r=self.f.report();self.assertFalse(r['research_allowed'])
        self.assertTrue(any(x['resolution_status']=='REVIEW_REQUIRED' for x in r['completeness']['findings']))

    def test_unrelated_new_day_keeps_confirmations(self):
        self.confirm();future=self.f.days[-1]+pd.offsets.BDay()
        for key in ('raw_price','adjusted_price','adj_factor'):
            p=self.f.paths[key+'_csv'];frame=pd.read_csv(p);new=frame.groupby('ts_code').tail(1).copy();new['trade_date']=future.strftime('%Y-%m-%d')
            pd.concat([frame,new]).to_csv(p,index=False)
        p=self.f.paths['trade_calendar_csv'];frame=pd.read_csv(p)
        pd.concat([frame,pd.DataFrame([dict(trade_date=future.strftime('%Y-%m-%d'),is_open=1)])]).to_csv(p,index=False)
        r=self.f.report();self.assertTrue(r['research_allowed']);self.assertEqual(r['completeness']['confirmed_count'],2)

    def test_real_rows_resolve_history_without_erasing_it(self):
        self.confirm()
        self.f.raw.to_csv(self.f.paths['raw_price_csv'],index=False);self.f.adj.to_csv(self.f.paths['adjusted_price_csv'],index=False)
        r=self.f.report();self.assertTrue(r['research_allowed'])
        self.assertTrue(all(x['resolution_status']=='RESOLVED' for x in r['completeness']['confirmation_history']))
        self.assertEqual(len(load_acknowledgements(self.f.paths)),2)

    def test_factor_and_structural_errors_not_inherited(self):
        self.f.drop('adj_factor','A',[16]);self.confirm()
        r=self.f.report();self.assertFalse(r['research_allowed'])
        fac=next(x for x in r['completeness']['findings'] if x['affected_component']=='adjustment_factor' and x['severity']=='FAIL')
        self.assertEqual(fac['resolution_status'],'UNRESOLVED')
        with self.assertRaisesRegex(ValueError,'not overridable'):
            save_acknowledgement(self.f.paths,dict(severity='FAIL',classification='duplicate_keys'),'','accept')
        p=self.f.paths['raw_price_csv'];frame=pd.read_csv(p);pd.concat([frame,frame.head(1)]).to_csv(p,index=False)
        r=self.f.report();self.assertEqual(r['structural_status'],'FAIL');self.assertFalse(r['research_allowed'])

    def test_empty_reason_and_cache_refresh(self):
        r=self.f.report();raw=next(x for x in r['completeness']['findings'] if x['affected_component']=='raw_price')
        with self.assertRaisesRegex(ValueError,'non-empty'):
            save_acknowledgement(self.f.paths,raw,'',' ')
        self.confirm();self.assertTrue(self.f.report()['research_allowed'])
        import json
        p=self.f.root/'completeness_acknowledgments.json';items=json.loads(p.read_text())
        for x in items:x['active']=False
        p.write_text(json.dumps(items));self.assertFalse(self.f.report()['research_allowed'])

    def test_research_and_grid_share_gate_and_save_confirmations(self):
        import json
        import test_regressions
        fixture=test_regressions.RegressionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        params=fixture.grid_fixture();ns=fixture.ns
        blocked=self.f.report()
        ns['validate_research_files']=lambda *a,**kw:blocked
        for name in ('run_research','run_strategy_grid'):
            with self.assertRaisesRegex(ValueError,'not ready'):ns[name](params)
        parent=self.confirm();accepted=self.f.report()
        ns.update(validate_research_files=lambda *a,**kw:accepted,save_heatmap=lambda *a,**kw:None,save_line=lambda *a,**kw:None)
        research=ns['run_research'](dict(universe='CSI300',lookback=10,display_spans=[10],selection='Top 5',rebalance_days=10))
        grid=ns['run_strategy_grid'](params)
        self.assertEqual(research['integrity']['status'],'WARNING');self.assertEqual(grid['integrity']['status'],'WARNING')
        metadata=list(fixture.root.rglob('run_metadata.json'))
        self.assertGreaterEqual(len(metadata),2)
        saved=[(p,p.read_text(encoding='utf-8')) for p in metadata]
        self.assertTrue(all(parent['confirmation_id'] in text for p,text in saved))
        revoke_acknowledgement(self.f.paths,parent['issue_id'])
        self.assertTrue(all(p.read_text(encoding='utf-8')==text for p,text in saved))

if __name__=='__main__':unittest.main()

"""Integrity tests use isolated CSV fixtures; never repair the user's dataset."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from momentumlab.data_integrity import (validate_dataset,require_valid,canonical_upsert,repair_duplicate_keys,
    staged_dataset,publish_dataset,recover_publication,validate_signal_context,provenance)


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.paths={'root':str(self.root)}
        for name in ['trade_calendar','raw_price','adjusted_price','weights','adj_factor','classification']:
            self.paths[name+'_csv']=str(self.root/(name+'.csv'))
        self.raw=pd.DataFrame(dict(ts_code=['A','A'],trade_date=['20260102','20260105'],open=[10.,11.],high=[10.,11.],low=[10.,11.],close=[10.,11.]))
        self.raw.to_csv(self.paths['raw_price_csv'],index=False)
        adjusted=self.raw.copy()
        for c in ['open','high','low','close']: adjusted['adj_'+c]=adjusted[c]
        adjusted.to_csv(self.paths['adjusted_price_csv'],index=False)
        pd.DataFrame(dict(trade_date=['20260102','20260103','20260104','20260105','20260106'],is_open=[1,0,0,1,1])).to_csv(self.paths['trade_calendar_csv'],index=False)
        self.raw[['ts_code','trade_date']].assign(adj_factor=1.).to_csv(self.paths['adj_factor_csv'],index=False)
        pd.DataFrame(dict(con_code=['A'],trade_date=['20260102'],weight=[None],market_cap_rmb=[300000000])).to_csv(self.paths['weights_csv'],index=False)
        pd.DataFrame(dict(ts_code=['A'],name=['Equity A'],fund_type=['Equity'])).to_csv(self.paths['classification_csv'],index=False)

    def report(self,**kwargs): return validate_dataset(self.paths,universe_id='ETF_250M',**kwargs)
    def hashes(self):
        import hashlib
        return {k:hashlib.sha256(Path(v).read_bytes()).hexdigest() for k,v in self.paths.items() if k!='root'}

    def test_snapshot_never_starts_validation_and_rejects_changed_files(self):
        with patch('momentumlab.data_integrity.audit_event') as audit:
            with self.assertRaisesRegex(ValueError,'No current validation'):
                self.report(mode='snapshot')
            audit.assert_not_called()
        checked=self.report(mode='full')
        with patch('momentumlab.data_integrity.audit_event') as audit:
            self.assertEqual(self.report(mode='snapshot')['status'],checked['status'])
            audit.assert_not_called()
        self.raw.iloc[:1].to_csv(self.paths['raw_price_csv'],index=False)
        with patch('momentumlab.data_integrity.audit_event') as audit:
            with self.assertRaisesRegex(ValueError,'No current validation'):
                self.report(mode='snapshot')
            audit.assert_not_called()
        # Research still validates a changed dataset and enforces its findings.
        self.assertFalse(self.report(mode='cached').get('cached',False))

    def test_duplicate_variants_detect_and_safe_repair(self):
        for key in ['raw_price_csv','adjusted_price_csv']:
            original=Path(self.paths[key]).read_bytes()
            for conflict in [False,True]:
                frame=pd.read_csv(self.paths[key]);row=frame.iloc[[0]].copy()
                if conflict: row['close' if key=='raw_price_csv' else 'adj_close']=99
                pd.concat([frame,row]).to_csv(self.paths[key],index=False)
                report=self.report(mode='full')
                self.assertEqual(report['status'],'FAIL')
                info=report['components']['raw_price' if key=='raw_price_csv' else 'adjusted_price']
                self.assertEqual(info['conflicting_keys'],int(conflict))
                if conflict:
                    before=self.hashes()
                    with self.assertRaisesRegex(ValueError,'Conflicting'):repair_duplicate_keys(self.paths,'ETF_250M')
                    self.assertEqual(before,self.hashes())
                else:
                    result=repair_duplicate_keys(self.paths,'ETF_250M')
                    self.assertTrue((self.root/result['backup']).is_dir())
                    self.assertNotEqual(result['report']['status'],'FAIL')
                Path(self.paths[key]).write_bytes(original)

    def test_current_calendar_older_prices_warning_and_absent_session_fail(self):
        report=self.report(through='2026-01-06')
        self.assertEqual(report['status'],'WARNING') # Trailing omissions are outside internal coverage.
        self.assertEqual(report['completeness']['status'],'WARNING')
        raw=pd.read_csv(self.paths['raw_price_csv']);raw.loc[0,'trade_date']=20260103
        raw.to_csv(self.paths['raw_price_csv'],index=False)
        self.assertEqual(self.report()['status'],'FAIL')

    def test_equity_filter_lists_bond(self):
        pd.DataFrame(dict(ts_code=['A'],name=['Bond A'],fund_type=['Bond'])).to_csv(self.paths['classification_csv'],index=False)
        report=self.report()
        check=next(x for x in report['checks'] if x['name']=='etf_asset_filter')
        self.assertEqual(check['status'],'FAIL');self.assertEqual(check['details']['offenders'][0]['ts_code'],'A')

    def test_future_eligibility_and_wrong_execution_fail(self):
        weights=pd.DataFrame(dict(con_code=['A'],trade_date=['20260105']))
        with self.assertRaisesRegex(ValueError,'future snapshot'):
            validate_signal_context(['2026-01-02','2026-01-05'],weights,'2026-01-02','2026-01-05')
        weights.trade_date='20260102'
        with self.assertRaisesRegex(ValueError,'next open'):
            validate_signal_context(['2026-01-02','2026-01-05'],weights,'2026-01-02','2026-01-03')

    def test_upsert_idempotent_fingerprint_and_move(self):
        for _ in range(2):
            old=pd.read_csv(self.paths['raw_price_csv'])
            canonical_upsert(old,self.raw,['ts_code','trade_date']).to_csv(self.paths['raw_price_csv'],index=False)
            report=self.report();fingerprint=report['manifest']['dataset_fingerprint']
            if hasattr(self,'first'):self.assertEqual(self.first,fingerprint)
            self.first=fingerprint
        moved=self.root/'moved';moved.mkdir()
        paths={'root':str(moved)}
        for k,v in self.paths.items():
            if k=='root':continue
            dest=moved/Path(v).name;shutil.copy2(v,dest);paths[k]=str(dest)
        self.assertEqual(validate_dataset(paths,universe_id='ETF_250M')['manifest']['dataset_fingerprint'],fingerprint)

    def test_failed_staging_keeps_manifest_and_production(self):
        manifest=self.report()['manifest'];(self.root/'dataset_manifest.json').write_text(json.dumps(manifest))
        before=self.hashes()
        with self.assertRaisesRegex(ValueError,'synthetic'):
            with staged_dataset(self.paths,resume_id='abc') as work:
                Path(work['raw_price_csv']).write_text('corrupted')
                raise ValueError('synthetic stage failure')
        self.assertEqual(before,self.hashes())
        self.assertEqual(json.loads((self.root/'dataset_manifest.json').read_text()),manifest)

    def test_publish_rollback_and_crash_recovery(self):
        before=self.hashes()
        with staged_dataset(self.paths) as work:
            report=self.report()
            real_replace=Path.replace
            def fail(source,target):
                if str(target)==self.paths['weights_csv']:raise OSError('disk failure')
                return real_replace(source,target)
            with patch.object(Path,'replace',fail):
                with self.assertRaisesRegex(OSError,'disk failure'):publish_dataset(work,self.paths,report['manifest'])
        self.assertEqual(before,self.hashes())
        backup=self.root/'update_backups'/'crash';backup.mkdir()
        shutil.copy2(self.paths['raw_price_csv'],backup/'raw_price_csv')
        Path(self.paths['raw_price_csv']).write_text('partial publish')
        (self.root/'.publish_pending.json').write_text(json.dumps(dict(backup='update_backups/crash',keys=['raw_price_csv'],existed={'raw_price_csv':True})))
        self.assertTrue(recover_publication(self.paths));self.assertEqual(before,self.hashes())

    def test_provenance_immutable_and_cache_invalidates(self):
        report=self.report();source=provenance(report,{'lookback':10},'run-original')
        saved=copy.deepcopy(source)
        self.raw.loc[0,'close']=9;self.raw.to_csv(self.paths['raw_price_csv'],index=False)
        changed=self.report()
        self.assertNotEqual(changed['manifest']['dataset_fingerprint'],source['dataset_fingerprint'])
        self.assertEqual(source,saved)
        self.assertEqual(changed['status'],'FAIL')

    def test_invalid_numeric_and_date_fail(self):
        for column,value in [('close',float('inf')),('trade_date','bad date')]:
            frame=self.raw.copy();frame[column]=frame[column].astype(object);frame.loc[0,column]=value
            frame.to_csv(self.paths['raw_price_csv'],index=False)
            self.assertEqual(self.report()['status'],'FAIL')

if __name__=='__main__':unittest.main()

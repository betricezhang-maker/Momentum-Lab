"""Day-valid integrity check tag: persistence, reuse by research gates, staleness."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from momentumlab.data_integrity import (validate_dataset, clear_integrity_cache,
                                        inspect_check_tag, _tag_paths)


class CheckTagTests(unittest.TestCase):
    def setUp(self):
        clear_integrity_cache();self.addCleanup(clear_integrity_cache)
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
    def tag_path(self): return _tag_paths(self.root)[0]
    def rewrite_tag(self,**changes):
        tag=json.loads(self.tag_path().read_text(encoding='utf-8'));tag.update(changes)
        self.tag_path().write_text(json.dumps(tag),encoding='utf-8')
    def bump_mtime(self,key):
        stat=os.stat(self.paths[key]);os.utime(self.paths[key],ns=((stat.st_mtime_ns+10**9),)*2)

    def test_full_check_writes_day_tag_and_cached_runs_reuse_it(self):
        checked=self.report(mode='full')
        tag_path,report_path=_tag_paths(self.root)
        self.assertTrue(tag_path.is_file() and report_path.is_file())
        stamp=checked['check_tag']
        self.assertEqual(stamp['valid_through'],stamp['shanghai_date'])
        self.assertEqual(stamp['dataset_fingerprint'],checked['manifest']['dataset_fingerprint'])
        self.assertNotIn('publication_phase',stamp)
        self.assertNotIn('publication_cutoff',stamp)
        cached=self.report(mode='cached')
        self.assertTrue(cached['cached']);self.assertNotIn('reused_check_tag',cached)
        clear_integrity_cache()
        reused=self.report(mode='cached')
        self.assertTrue(reused['cached'] and reused['reused_check_tag'])
        self.assertEqual(reused['status'],checked['status'])
        self.assertEqual(reused['manifest']['dataset_fingerprint'],checked['manifest']['dataset_fingerprint'])
        self.assertEqual(reused['check_tag']['checked_at'],stamp['checked_at'])
        clear_integrity_cache()
        self.assertEqual(self.report(mode='snapshot')['status'],checked['status'])

    def test_tag_expires_when_files_decisions_or_day_change(self):
        first=self.report(mode='full')['check_tag']['checked_at']
        self.bump_mtime('raw_price_csv');clear_integrity_cache()
        fresh=self.report(mode='cached')
        self.assertFalse(fresh.get('reused_check_tag',False));self.assertNotIn('cached',fresh)
        self.assertNotEqual(fresh['check_tag']['checked_at'],first)
        (self.root/'completeness_acknowledgments.json').write_text('[]',encoding='utf-8');clear_integrity_cache()
        self.assertFalse(self.report(mode='cached').get('reused_check_tag',False))
        (self.root/'completeness_acknowledgments.json').unlink();clear_integrity_cache()
        self.report(mode='cached')
        self.rewrite_tag(shanghai_date='2020-01-01');clear_integrity_cache()
        self.assertFalse(self.report(mode='cached').get('reused_check_tag',False))
        self.report(mode='cached')
        self.rewrite_tag(tag_version='9');clear_integrity_cache()
        self.assertFalse(self.report(mode='cached').get('reused_check_tag',False))

    def test_tag_valid_for_whole_day_until_midnight(self):
        import hashlib
        from datetime import datetime, timedelta
        from momentumlab import data_integrity as di
        self.report(mode='full')
        clear_integrity_cache()
        tag=json.loads(self.tag_path().read_text(encoding='utf-8'))
        signature=di.dataset_identity(di.dataset_files(self.paths,'ETF_250M'),self.root)
        ack=hashlib.sha256(di._ack_path(self.paths).read_bytes()).hexdigest() if di._ack_path(self.paths).exists() else None
        params=di._params_signature(None,None,None,None)
        morning=datetime.fromisoformat(tag['shanghai_date']+'T09:30+08:00')
        evening=datetime.fromisoformat(tag['shanghai_date']+'T21:30+08:00')
        next_day=evening+timedelta(days=1)
        self.assertIsNotNone(di._load_check_tag(self.root,'ETF_250M',signature,ack,params,morning))
        self.assertIsNotNone(di._load_check_tag(self.root,'ETF_250M',signature,ack,params,evening))
        self.assertIsNone(di._load_check_tag(self.root,'ETF_250M',signature,ack,params,next_day))

    def test_research_scope_reuses_tag_without_revalidation_but_explicit_scope_does_not(self):
        with patch('momentumlab.data_integrity.audit_event'):
            self.report(mode='full')
        clear_integrity_cache()
        with patch('momentumlab.data_integrity.audit_event') as audit:
            reused=self.report(mode='cached')
            self.assertTrue(reused['reused_check_tag'])
            events=[c.args[1] for c in audit.call_args_list if len(c.args)>=2]
            self.assertNotIn('DATA_VALIDATION_STARTED',events)
            self.assertIn('DATA_VALIDATION_REUSED_CHECK_TAG',events)
        with patch('momentumlab.data_integrity.audit_event'):
            scoped=self.report(mode='cached',through='2026-01-06')
            self.assertFalse(scoped.get('reused_check_tag',False))

    def test_inspect_check_tag_reports_presence_freshness_and_reasons(self):
        self.assertEqual(inspect_check_tag(self.paths,'ETF_250M')['present'],False)
        self.report(mode='full')
        expected=self.report(mode='cached')['status']
        info=inspect_check_tag(self.paths,'ETF_250M')
        self.assertTrue(info['present'] and info['fresh'])
        self.assertEqual(info['valid_through'],info['shanghai_date'])
        self.assertEqual(info['status'],expected)
        self.bump_mtime('adjusted_price_csv')
        stale=inspect_check_tag(self.paths,'ETF_250M')
        self.assertFalse(stale['fresh'])
        self.assertTrue(any('files changed' in r for r in stale['stale_reasons']))
        (self.root/'completeness_acknowledgments.json').write_text('[]',encoding='utf-8')
        acked=inspect_check_tag(self.paths,'ETF_250M')
        self.assertTrue(any('review decisions changed' in r for r in acked['stale_reasons']))

    def test_app_check_tag_status_serves_workspace(self):
        import MomentumLabV2 as app
        with patch.object(app,'integrity_review_paths',return_value=self.paths):
            missing=app.check_tag_status('ETF_250M')
            self.assertTrue(missing['ok']);self.assertFalse(missing['tag']['present'])
            self.report(mode='full')
            served=app.check_tag_status('ETF_250M')
            self.assertTrue(served['tag']['fresh'])
            self.assertEqual(served['tag']['dataset_id'],self.report(mode='cached')['check_tag']['dataset_id'])


if __name__ == '__main__':
    unittest.main()

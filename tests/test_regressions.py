"""Offline regression checks. No application config or real datasets are modified."""
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'MomentumLabV2.py').read_text(encoding='utf-8'))


def functions():
    ns = dict(pd=pd, np=np, Path=Path, time=time, uuid=uuid, json=json,
              APP_VERSION='test', BUILD_TAG='test')
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'MomentumLabV2.py', 'exec'), ns)
    return ns


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='momentum-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ns = functions()

    def test_mathematical_functions_unchanged(self):
        expected = {
            'calculate_signal': '9c35087f75c4b2f4a855c365c12094e9c72ac5ac98c0b17f4cd92e734ae61f0c',
            'merge_membership_and_rank': '2979fefac82e116035671432b34210418d1c747e3f6df4918b0770c626c464ce',
            'select_mask': '629a6e5c2ec1fa32e44baf26cb302d0b9f28ac6e185b7bdabb706cb277408840',
            'signal_decay_table': '4f737e30f4cc8c8485ff6a1a4f88cb17a37e9ba084585e620ded360d738f4704',
            'selection_forward_returns': 'e192aa2eb79fe090bdbf1a44f2066b00773239addfa14e1c7397ea9c6ddc7dbb',
            'backtest': '5bf65ad7e53edcdb21071d64d4b41835574c4f90719b83536d30bf31b0139b9f',
        }
        for node in TREE.body:
            if isinstance(node, ast.FunctionDef) and node.name in expected:
                self.assertEqual(hashlib.sha256(ast.dump(node).encode()).hexdigest(), expected[node.name])

    def grid_fixture(self):
        dates = pd.bdate_range('2024-01-01', periods=280)
        prices = pd.DataFrame([dict(trade_date=d.strftime('%Y%m%d'), ts_code=f'T{i}',
            adj_close=100*np.exp(.0002*(i+1)*k+.02*np.sin(k/(i+2))))
            for i in range(6) for k, d in enumerate(dates)])
        weights = pd.DataFrame([dict(trade_date='20240101', con_code=f'T{i}', weight=1/6) for i in range(6)])
        prices.to_csv(self.root/'prices.csv', index=False)
        weights.to_csv(self.root/'weights.csv', index=False)
        factors = prices[['ts_code','trade_date']].assign(adj_factor=1)
        factors.to_csv(self.root/'factors.csv', index=False)
        paths = dict(adjusted_price_csv=str(self.root/'prices.csv'),
                     weights_csv=str(self.root/'weights.csv'),adj_factor_csv=str(self.root/'factors.csv'))
        self.ns.update(load_config=lambda:dict(active_universe='CSI300',results_folder=str(self.root)),
                       UNIVERSES={'CSI300':{'label':'CSI 300'}},
                       selected_universe_paths=lambda *args:paths)
        return dict(universe='CSI300',selections=['Top 5','Q5'],lookbacks=[10,20],
                    rebalances=[5,10],years=[2024,2025],include_full=True)

    def test_grid_to_csv_and_json_single_multi_and_legacy(self):
        params = self.grid_fixture()
        for selections in [['Top 5'], ['Top 5','Q5']]:
            params['selections'] = selections
            result = self.ns['run_strategy_grid'](params)
            self.assertEqual(result['failed_combinations'], 0)
            self.assertEqual(len(result['rows']), 12*len(selections))
            self.assertEqual(result['selection_rules'], selections)
            self.assertEqual(result['selection'], selections)
            exported = pd.read_csv(result['files']['csv'])
            self.assertEqual(set(exported.selection), set(selections))
            json.loads(json.dumps(result, allow_nan=False))
            self.assertTrue((Path(result['files']['folder'])/'run_metadata.json').exists())
        params.pop('selections')
        params['selection_rule'] = 'Q5'
        self.assertEqual(self.ns['run_strategy_grid'](params)['selection_rules'], ['Q5'])

    def test_grid_error_rows_keep_selection(self):
        params = self.grid_fixture()
        def fail(*args):
            raise ValueError('synthetic failure')
        self.ns['backtest'] = fail
        result = self.ns['run_strategy_grid'](params)
        self.assertEqual(result['failed_combinations'], 24)
        self.assertEqual({r['selection'] for r in result['rows']}, {'Top 5','Q5'})

    def test_snapshot_replacement_removes_old_constituents(self):
        path = self.root/'members.csv'
        old = pd.DataFrame({'trade_date':[20240101,20240101,20240201], 'con_code':['A','B','C']})
        new = pd.DataFrame({'trade_date':[20240101], 'con_code':['D']})
        old.to_csv(path,index=False)
        out = self.ns['append_dedupe'](path,new,['trade_date','con_code'],replace_snapshots=True)
        self.assertEqual(set(out.con_code), {'C','D'})

    def test_cancellation_does_not_checkpoint_unwritten_rows(self):
        calls, marked = [], []
        def check():
            if len(calls)==2:
                raise InterruptedError('cancel')
        def fetch(api, params, fields):
            calls.append(params['ts_code'])
            return pd.DataFrame([dict(ts_code=params['ts_code'],trade_date=20240101,close=1)])
        self.ns.update(completed_checkpoint=lambda stage:set(),job_check_cancel=check,
                       load_checkpoint=lambda:{},
                       job_update=lambda **kw:None,tushare_call=fetch,
                       mark_checkpoint=lambda stage,ticker:marked.append(ticker))
        path = self.root/'download.csv'
        with self.assertRaises(InterruptedError):
            self.ns['fetch_stock_history']('daily',['A','B','C'],'2024-01-01','2024-01-02','',path,
                                           ['ts_code','trade_date'],'raw','Raw')
        self.assertEqual(marked, [])
        self.assertFalse(path.exists())
        self.ns['job_check_cancel'] = lambda:None
        self.ns['fetch_stock_history']('daily',['A','B','C'],'2024-01-01','2024-01-02','',path,
                                      ['ts_code','trade_date'],'raw','Raw')
        self.assertEqual(set(marked), {'A','B','C'})
        self.assertEqual(set(pd.read_csv(path).ts_code),set(marked))

    def test_missing_factors_do_not_overwrite_adjusted_data(self):
        raw=self.root/'raw.csv'; fac=self.root/'fac.csv'; out=self.root/'adjusted.csv'
        pd.DataFrame([dict(ts_code='A',trade_date=20240101,open=1,high=1,low=1,close=1)]).to_csv(raw,index=False)
        pd.DataFrame([dict(ts_code='A',trade_date=20240102,adj_factor=1)]).to_csv(fac,index=False)
        out.write_text('existing output',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'factors missing'):
            self.ns['build_adjusted_prices'](raw,fac,out)
        self.assertEqual(out.read_text(), 'existing output')

    def test_incremental_windows_repair_each_security(self):
        raw = pd.DataFrame({'ts_code':['A','A','B'], 'trade_date':[20240102,20240104,20240102]})
        raw.to_csv(self.root/'raw.csv', index=False)
        pd.DataFrame({'trade_date':[20240102,20240103,20240104], 'is_open':[1,1,1]}).to_csv(self.root/'cal.csv',index=False)
        paths={'raw_price_csv':str(self.root/'raw.csv'),'trade_calendar_csv':str(self.root/'cal.csv')}
        starts=self.ns['incremental_ticker_starts']('daily',raw,['A','B','C'],'2024-01-05',paths)
        self.assertEqual(starts['A'],pd.Timestamp('2024-01-03'))
        self.assertEqual(starts['B'],pd.Timestamp('2024-01-03'))
        self.assertEqual(starts['C'],pd.Timestamp('2024-01-02'))
        factors=pd.DataFrame({'ts_code':['A'],'trade_date':[20240104]})
        starts=self.ns['incremental_ticker_starts']('adj_factor',factors,['A'],'2024-01-05',paths)
        self.assertEqual(starts['A'],pd.Timestamp('2024-01-02'))

    def test_duplicate_prices_fail_validation(self):
        self.grid_fixture()
        path=self.root/'prices.csv'
        frame=pd.read_csv(path)
        pd.concat([frame,frame.iloc[:1]]).to_csv(path,index=False)
        result=self.ns['validate_research_files']('CSI300')
        self.assertFalse(result['ok'])
        self.assertTrue(any('duplicate' in e for e in result['errors']))


if __name__ == '__main__':
    unittest.main()

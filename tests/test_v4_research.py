import ast
import unittest
import numpy as np
import pandas as pd
from test_regressions import ROOT, functions
from momentumlab.turnover import compare_weights
from momentumlab.costs import apply_costs
from momentumlab.universe_validation import equity_classification, validate_etf_weights


class TurnoverTests(unittest.TestCase):
    def test_replacement_and_drift(self):
        old = {str(i): .1 for i in range(10)}
        self.assertEqual(compare_weights(old, old)['one_way_turnover'], 0)
        self.assertAlmostEqual(compare_weights(old, {str(i): .1 for i in range(3, 13)})['one_way_turnover'], .3)
        self.assertAlmostEqual(compare_weights(old, {str(i): .1 for i in range(10, 20)})['one_way_turnover'], 1)
        self.assertAlmostEqual(compare_weights({'A': .6, 'B': .4}, {'A': .5, 'B': .5})['one_way_turnover'], .1)

    def test_zero_cost_matches_committed_v3(self):
        source = (ROOT/'tests'/'fixtures'/'v3_backtest.py').read_text(encoding='utf-8')
        ns = functions()
        baseline = dict(ns)
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'backtest')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'v3_baseline', 'exec'), baseline)
        dates = pd.bdate_range('2023-10-01', periods=90)
        prices = pd.DataFrame([{'ts_code': str(i), 'trade_date': date, 'adj_close': 100*np.exp(.001*j+.03*np.sin(j/(i+2)))} for i in range(25) for j, date in enumerate(dates)])
        weights = pd.DataFrame([{'con_code': str(i), 'trade_date': dates[0], 'weight': .04} for i in range(25)])
        ranked = ns['merge_membership_and_rank'](ns['calculate_signal'](prices, 10, calendar=dates), weights)
        for selection in ['Top 5', 'Top 10', 'Top 20', 'Q1', 'Q2', 'Q3', 'Q4', 'Q5']:
            for rb in [5,10,20,40,60]:
                old = baseline['backtest'](ranked, selection, rb, cost_bps=0)
                new = ns['backtest'](ranked, selection, rb, cost_bps=0)
                np.testing.assert_allclose(old[0], new[0], rtol=1e-12, atol=1e-12)
                self.assertEqual(old[3], new[3])
                self.assertIn('old_weights', new[2].columns)
                net0=apply_costs(new[0],new[2],new[3],0)
                np.testing.assert_allclose(net0[0],old[0],rtol=1e-12)
                net=apply_costs(new[0],new[2],new[3],5)
                self.assertTrue((net[0] <= new[0]+1e-12).all())
                self.assertAlmostEqual(net[0].iloc[0],1-.5*.0005)

    def test_equity_and_historical_size(self):
        metadata=pd.DataFrame({'ts_code':['A','B','C','D','E','F'], 'fund_type':['股票型','债券型','货币型','商品型','fixed income','股票型'], 'name':['Stock','Bond','Cash','Gold','Income','有色金属股票ETF']})
        codes=set(equity_classification(metadata).ts_code)
        self.assertEqual(codes,{'A','F'})
        weights=pd.DataFrame({'con_code':['A','A','B','F'],'trade_date':[20240101,20240201,20240101,20240101],'market_cap_rmb':[249e6,250e6,1e9,300e6]})
        eligible=validate_etf_weights(weights,codes)
        self.assertEqual(list(eligible.con_code),['A','F'])
        with self.assertRaises(ValueError): validate_etf_weights(weights.drop(columns='market_cap_rmb'),codes)


if __name__ == '__main__':
    unittest.main()

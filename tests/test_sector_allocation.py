"""Sector allocation view for the Research Lab chart. Offline; no real datasets modified."""
import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_regressions import functions, TREE


def sector_functions():
    ns = functions()
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'SECTOR_KEYWORDS' for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'MomentumLabV2.py', 'exec'), ns)
    return ns


class SectorClassificationTests(unittest.TestCase):
    def setUp(self):
        self.ns = sector_functions()

    def test_classify_sector_keyword_rules(self):
        classify = self.ns['classify_sector']
        self.assertEqual(classify('华夏国证半导体芯片ETF'), 'Semiconductors')
        self.assertEqual(classify('汇添富国证航天航空行业ETF'), 'Defense & Aerospace')
        self.assertEqual(classify('万家中证金融科技主题ETF'), 'Fintech')
        self.assertEqual(classify('富国中证红利低波ETF'), 'Dividend & Value')
        self.assertEqual(classify('华泰柏瑞沪深300ETF'), 'Broad Market')
        self.assertEqual(classify('国泰纳斯达克100ETF'), 'Hong Kong & Overseas')
        self.assertEqual(classify('富国中证智能汽车ETF'), 'Auto')
        self.assertEqual(classify('华夏国证新能源车ETF'), 'New Energy')
        self.assertEqual(classify('某某稳健收益混合'), 'Other / Unclassified')
        self.assertEqual(classify(''), 'Other / Unclassified')
        self.assertEqual(classify(None), 'Other / Unclassified')

    def test_sector_allocation_table_aggregates_target_weights(self):
        log = pd.DataFrame([
            {'entry_date': '2024-01-02', 'tickers': 'A,B,C,D'},
            {'entry_date': '2024-01-12', 'tickers': 'A,B,E'},
        ])
        names = {'A': '半导体ETF', 'B': '半导体ETF', 'C': '军工ETF', 'D': '沪深300ETF', 'E': '军工ETF'}
        table, latest = self.ns['sector_allocation_table'](log, names)
        self.assertIsNotNone(table)
        self.assertEqual(list(table.index), list(pd.to_datetime(['2024-01-02', '2024-01-12'])))
        first = table.iloc[0]
        self.assertAlmostEqual(first['Semiconductors'], 0.5)
        self.assertAlmostEqual(first['Defense & Aerospace'], 0.25)
        self.assertAlmostEqual(first['Broad Market'], 0.25)
        for _, row in table.iterrows():
            self.assertAlmostEqual(row.sum(), 1.0)
        self.assertEqual(len(latest), 3)
        self.assertTrue(all(item['weight'] == 1.0 / 3 for item in latest))
        self.assertEqual(latest[0]['sector'], 'Semiconductors')
        self.assertEqual({item['ticker'] for item in latest}, {'A', 'B', 'E'})

    def test_sector_allocation_table_handles_empty_inputs(self):
        self.assertEqual(self.ns['sector_allocation_table'](None, {}), (None, None))
        empty = pd.DataFrame({'entry_date': [], 'tickers': []})
        self.assertEqual(self.ns['sector_allocation_table'](empty, {}), (None, None))

    def test_save_sector_chart_renders_png(self):
        eq = pd.Series(1.0, index=pd.bdate_range('2024-01-01', periods=30))
        table, _ = self.ns['sector_allocation_table'](
            pd.DataFrame([{'entry_date': '2024-01-02', 'tickers': 'A,B'}]),
            {'A': '半导体ETF', 'B': '军工ETF'})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sector_allocation.png'
            self.ns['save_sector_chart'](eq, table, path, 'title')
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 0)


class ResearchSectorViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='momentum-sector-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ns = sector_functions()

    def research_fixture(self, classification_names=None):
        dates = pd.bdate_range('2024-01-01', periods=280)
        prices = pd.DataFrame([dict(trade_date=d.strftime('%Y%m%d'), ts_code=f'T{i}',
            adj_close=100 * np.exp(.0002 * (i + 1) * k + .02 * np.sin(k / (i + 2))))
            for i in range(6) for k, d in enumerate(dates)])
        weights = pd.DataFrame([dict(trade_date='20240101', con_code=f'T{i}', weight=1 / 6) for i in range(6)])
        for c in ['open', 'high', 'low', 'close', 'adj_open', 'adj_high', 'adj_low']:
            prices[c] = prices.adj_close
        prices.to_csv(self.root / 'prices.csv', index=False)
        pd.DataFrame({'trade_date': dates, 'is_open': 1}).to_csv(self.root / 'calendar.csv', index=False)
        weights.to_csv(self.root / 'weights.csv', index=False)
        factors = prices[['ts_code', 'trade_date']].assign(adj_factor=1)
        factors.to_csv(self.root / 'factors.csv', index=False)
        paths = dict(root=str(self.root), raw_price_csv=str(self.root / 'prices.csv'),
                     trade_calendar_csv=str(self.root / 'calendar.csv'),
                     adjusted_price_csv=str(self.root / 'prices.csv'),
                     weights_csv=str(self.root / 'weights.csv'),
                     adj_factor_csv=str(self.root / 'factors.csv'))
        if classification_names is not None:
            frame = pd.DataFrame([{'ts_code': code, 'name': name} for code, name in classification_names.items()])
            frame.to_csv(self.root / 'fund_classification.csv', index=False, encoding='utf-8-sig')
            paths['classification_csv'] = str(self.root / 'fund_classification.csv')
        self.ns.update(load_config=lambda: dict(active_universe='CSI300', results_folder=str(self.root)),
                       UNIVERSES={'CSI300': {'label': 'CSI 300'}},
                       selected_universe_paths=lambda *args: paths,
                       save_heatmap=lambda *a, **kw: None)
        return paths

    def run_params(self):
        return dict(universe='CSI300', lookback=10, display_spans=[10, 20], selection='Top 5',
                    rebalance_days=10)

    def test_research_sector_view_available_with_classification(self):
        self.research_fixture(classification_names={
            'T0': '华夏国证半导体芯片ETF', 'T1': '易方达中证军工ETF', 'T2': '华泰柏瑞沪深300ETF',
            'T3': '南方中证新能源电池ETF', 'T4': '富国中证红利低波ETF', 'T5': '银华MSCI中国A股国际通ETF'})
        result = self.ns['run_research'](self.run_params())
        view = result['sector_allocation']
        self.assertEqual(view['status'], 'available')
        chart = Path(view['chart'])
        self.assertTrue(chart.is_file())
        self.assertGreater(chart.stat().st_size, 0)
        exported = pd.read_csv(view['csv'])
        self.assertTrue((exported.drop(columns=['entry_date']).sum(axis=1).sub(1).abs() < 1e-9).all())
        self.assertEqual(len(view['latest']), 5)
        self.assertAlmostEqual(sum(item['weight'] for item in view['latest']), 1.0)
        for item in view['latest']:
            self.assertEqual(item['sector'], self.ns['classify_sector'](item['name']))
        metadata = json.loads((Path(result['files']['folder']) / 'run_metadata.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['sector_allocation']['status'], 'available')

    def test_research_sector_view_unavailable_without_classification(self):
        self.research_fixture(classification_names=None)
        result = self.ns['run_research'](self.run_params())
        view = result['sector_allocation']
        self.assertEqual(view['status'], 'unavailable')
        self.assertNotIn('chart', view)

    def test_sector_failure_does_not_fail_research(self):
        self.research_fixture(classification_names={'T0': '半导体ETF'})
        def boom(*a, **kw):
            raise RuntimeError('synthetic sector failure')
        self.ns['sector_allocation_table'] = boom
        result = self.ns['run_research'](self.run_params())
        self.assertEqual(result['sector_allocation']['status'], 'unavailable')
        self.assertIn('synthetic sector failure', result['sector_allocation']['error'])
        self.assertIn('total_return', result['stats'])


if __name__ == '__main__':
    unittest.main()

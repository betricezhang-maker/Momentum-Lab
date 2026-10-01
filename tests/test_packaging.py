"""Packaging-only regression tests. All mutable files stay in temporary folders."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import portable_launcher as launcher


class PackagingTests(unittest.TestCase):
    def test_frozen_root_is_executable_parent_not_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / 'MomentumLab.exe'
            with patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(exe)):
                self.assertEqual(launcher.runtime_root(), exe.parent.resolve())

    def test_prepare_preserves_files_and_uses_local_font_cache(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ):
            root = Path(temp)
            (root / 'live').mkdir()
            db = root / 'live' / 'momentumlab.sqlite3'; db.write_bytes(b'untouched')
            launcher.prepare_root(root)
            self.assertEqual(db.read_bytes(), b'untouched')
            self.assertTrue(all((root / name).is_dir() for name in launcher.FOLDERS))
            self.assertEqual(os.environ['MPLCONFIGDIR'], str(root / 'logs' / 'matplotlib'))

    def test_invalid_settings_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ):
            root = Path(temp); (root / 'config').mkdir()
            settings = root / 'config' / 'settings.json'; settings.write_text('{broken')
            with self.assertRaises(json.JSONDecodeError): launcher.prepare_root(root)
            self.assertEqual(settings.read_text(), '{broken')

    @unittest.skipUnless(os.name == 'nt', 'Windows kernel instance handles')
    def test_duplicate_instance_quit_and_stale_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'logs').mkdir()
            (root / 'logs' / 'instance.json').write_text('{"pid": 999999, "port": 8765}')
            first, second, third = [launcher.Instance(root) for _ in range(3)]
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                self.assertTrue(second.request_quit())
                self.assertTrue(first.quit_requested())
                self.assertFalse(first.quit_requested())
                second.close(); first.close()
                self.assertTrue(third.acquire())
            finally:
                first.close(); second.close(); third.close()

    def test_moved_grid_uses_history_inside_verified_run(self):
        import MomentumLabV2 as app
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run = root / 'backtests' / 'saved-grid'; run.mkdir(parents=True)
            history = run / 'target_rebalances.json'; history.write_text('[]')
            (run / 'run_metadata.json').write_text(json.dumps(dict(
                universe='ETF_250M', trading_cost_bps=5,
                rows=[dict(lookback=10, rebalance_days=10, selection='Top 10',
                           history_file=str(root / 'old-development-folder' / history.name))])))
            with patch.object(app, 'load_config', return_value={'results_folder': str(root)}):
                result = app.verified_strategy_source(dict(run_id='saved-grid', lookback=10, rebalance_days=10, selection='Top 10'))
                self.assertEqual(result['history_files'], [str(history.resolve())])
                with self.assertRaises(ValueError):
                    app.verified_strategy_source(dict(run_id='../elsewhere', lookback=10, rebalance_days=10, selection='Top 10'))


if __name__ == '__main__': unittest.main()

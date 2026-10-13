"""Benchmark index data management: windowed fetch, incremental upsert, strict validation, status."""
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request, urlopen

import pandas as pd

import MomentumLabV2 as app


def index_frame(code, dates, closes=None):
    if closes is None:
        closes = [100.0 + i for i in range(len(dates))]
    return pd.DataFrame({"ts_code": code,
                         "trade_date": [d.strftime("%Y%m%d") for d in dates],
                         "close": closes})


class BenchmarkDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = app.Path(self.tmp.name)
        self.config = {"data_folder": str(self.data_dir), "active_universe": "CSI300",
                       "benchmark_csv": "", "benchmark_ticker": "", "benchmark_name": "",
                       "benchmark_basis": "price_return"}
        self.calls = []
        app._JOB_CANCEL.clear()

        def fake_tushare(api_name, params, fields):
            self.assertEqual(api_name, "index_daily")
            self.assertEqual(fields, "ts_code,trade_date,close")
            self.calls.append((params["ts_code"], params["start_date"], params["end_date"]))
            dates = pd.bdate_range(pd.Timestamp(params["start_date"]), pd.Timestamp(params["end_date"]))
            return index_frame(params["ts_code"], dates)

        self.fake_tushare = fake_tushare

    def tearDown(self):
        app._JOB_CANCEL.clear()

    def paths(self):
        with patch.object(app, "load_config", return_value=dict(self.config)):
            return app.benchmark_paths()

    def run_update(self, request=None):
        request = request or {}
        with patch.object(app, "load_config", return_value=dict(self.config)), \
             patch.object(app, "tushare_call", side_effect=self.fake_tushare):
            return app.update_benchmark_data(request)

    def test_full_build_downloads_all_indexes_to_separate_files(self):
        result = self.run_update({"end_date": "2024-06-28"})
        p = self.paths()
        self.assertEqual(sorted(p), ["CSI300", "CSI300_TR", "CSI500", "CSI500_TR"])
        for name in ("CSI300", "CSI300_TR", "CSI500", "CSI500_TR"):
            f = p[name]
            self.assertTrue(f.is_file(), name)
            frame = pd.read_csv(f, dtype={"trade_date": str})
            self.assertEqual(list(frame.columns), ["ts_code", "trade_date", "close"])
            self.assertTrue(frame["ts_code"].eq(app.BENCHMARK_INDEXES[name]["ts_code"]).all())
        by_index = {x["index"]: x for x in result["benchmarks"]}
        self.assertEqual(by_index["CSI300"]["status"], "PASS")
        self.assertEqual(by_index["CSI300"]["start"], "2005-01-03")
        self.assertEqual(by_index["CSI300"]["end"], "2024-06-28")
        self.assertEqual(by_index["CSI300"]["rows"], by_index["CSI500"]["rows"])

    def test_total_return_indexes_build_to_h0_csvs(self):
        result = self.run_update({"benchmarks": ["CSI300_TR", "CSI500_TR"], "end_date": "2024-06-28"})
        p = self.paths()
        for name, code in (("CSI300_TR", "H00300.CSI"), ("CSI500_TR", "H00905.CSI")):
            f = p[name]
            self.assertTrue(f.is_file(), name)
            self.assertEqual(f.name, code + ".csv")
            frame = pd.read_csv(f, dtype={"trade_date": str})
            self.assertTrue(frame["ts_code"].eq(code).all())
            item = {x["index"]: x for x in result["benchmarks"]}[name]
            self.assertEqual(item["status"], "PASS")
            self.assertEqual(item["code"], code)
        with patch.object(app, "load_config", return_value=dict(self.config)):
            status = app.benchmark_status()
        self.assertEqual(status["indexes"]["CSI300_TR"]["ts_code"], "H00300.CSI")
        self.assertEqual(status["indexes"]["CSI500_TR"]["ts_code"], "H00905.CSI")

    def test_windowed_full_build_chunks_long_ranges(self):
        self.run_update({"end_date": "2024-06-28"})
        csi300_windows = [c for c in self.calls if c[0] == "000300.SH"]
        starts = [pd.Timestamp(c[1]) for c in csi300_windows]
        self.assertGreater(len(csi300_windows), 1)
        self.assertEqual(starts[0], app.BENCHMARK_EARLIEST)
        self.assertEqual(csi300_windows[-1][2], "20240628")
        for earlier, later in zip(csi300_windows, csi300_windows[1:]):
            self.assertEqual(pd.Timestamp(later[1]) - pd.Timestamp(earlier[2]), pd.Timedelta(days=1))

    def test_incremental_update_requests_only_missing_days(self):
        self.run_update({"end_date": "2024-06-28"})
        seen = len(self.calls)
        result = self.run_update({"end_date": "2024-07-31"})
        new_calls = self.calls[seen:]
        self.assertEqual(len(new_calls), 4)
        for code, start, end in new_calls:
            self.assertEqual(start, "20240629")
            self.assertEqual(end, "20240731")
        by_index = {x["index"]: x for x in result["benchmarks"]}
        self.assertEqual(by_index["CSI300"]["status"], "PASS")
        self.assertGreater(by_index["CSI300"]["rows_fetched"], 0)
        self.assertEqual(by_index["CSI300"]["end"], "2024-07-31")

    def test_explicit_from_to_window_overrides_incremental_start(self):
        first = self.run_update({"end_date": "2024-07-31"})
        before = {x["index"]: x["rows"] for x in first["benchmarks"]}
        seen = len(self.calls)
        result = self.run_update({"start_date": "2024-01-01", "end_date": "2024-03-31"})
        new_calls = self.calls[seen:]
        self.assertEqual(len(new_calls), 4)
        for code, start, end in new_calls:
            self.assertEqual(start, "20240101")
            self.assertEqual(end, "20240331")
        by_index = {x["index"]: x for x in result["benchmarks"]}
        self.assertEqual(by_index["CSI300"]["status"], "PASS")
        self.assertEqual(by_index["CSI300"]["rows"], before["CSI300"])
        frame = pd.read_csv(self.paths()["CSI300"], dtype={"trade_date": str})
        self.assertFalse(frame["trade_date"].duplicated().any())

    def test_from_date_is_clamped_to_earliest_allowed(self):
        self.run_update({"start_date": "2000-01-01", "end_date": "2005-06-30"})
        self.assertTrue(self.calls)
        for code, start, end in self.calls:
            self.assertEqual(start, "20050101")

    def test_invalid_or_reversed_from_to_dates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Update start date is invalid"):
            self.run_update({"start_date": "not-a-date", "end_date": "2024-06-28"})
        with self.assertRaisesRegex(ValueError, "after the end date"):
            self.run_update({"start_date": "2024-07-01", "end_date": "2024-06-28"})
        self.assertFalse(self.paths()["CSI300"].exists())

    def test_already_current_makes_no_requests(self):
        self.run_update({"end_date": "2024-06-28"})
        seen = len(self.calls)
        result = self.run_update({"end_date": "2024-06-28"})
        self.assertEqual(len(self.calls), seen)
        for item in result["benchmarks"]:
            self.assertEqual(item["status"], "SKIPPED / ALREADY CURRENT")
            self.assertEqual(item["requests"], 0)

    def test_repeat_upsert_does_not_duplicate_rows(self):
        first = self.run_update({"end_date": "2024-06-28"})
        second = self.run_update({"end_date": "2024-07-31"})
        expected_new = len(pd.bdate_range("2024-06-29", "2024-07-31"))
        self.assertEqual(first["benchmarks"][0]["rows"] + expected_new, second["benchmarks"][0]["rows"])
        frame = pd.read_csv(self.paths()["CSI300"], dtype={"trade_date": str})
        self.assertFalse(frame["trade_date"].duplicated().any())

    def test_invalid_provider_rows_raise_before_any_write(self):
        def bad_tushare(api_name, params, fields):
            dates = pd.bdate_range(pd.Timestamp(params["start_date"]), pd.Timestamp(params["end_date"]))
            return index_frame(params["ts_code"], dates, closes=[0.0] * len(dates))

        with patch.object(app, "load_config", return_value=dict(self.config)), \
             patch.object(app, "tushare_call", side_effect=bad_tushare):
            with self.assertRaisesRegex(ValueError, "invalid/non-positive closes"):
                app.update_benchmark_data({"benchmarks": ["CSI300"], "end_date": "2024-06-28"})
        self.assertFalse(self.paths()["CSI300"].exists())

    def test_duplicate_provider_dates_raise_before_any_write(self):
        def dup_tushare(api_name, params, fields):
            dates = pd.bdate_range(pd.Timestamp(params["start_date"]), pd.Timestamp(params["end_date"]))
            frame = index_frame(params["ts_code"], dates)
            return pd.concat([frame, frame.iloc[[-1]]], ignore_index=True)

        with patch.object(app, "load_config", return_value=dict(self.config)), \
             patch.object(app, "tushare_call", side_effect=dup_tushare):
            with self.assertRaisesRegex(ValueError, "duplicate trading dates"):
                app.update_benchmark_data({"benchmarks": ["CSI300"], "end_date": "2024-06-28"})
        self.assertFalse(self.paths()["CSI300"].exists())

    def test_valid_earlier_window_is_preserved_when_later_window_is_invalid(self):
        def mixed_tushare(api_name, params, fields):
            dates = pd.bdate_range(pd.Timestamp(params["start_date"]), pd.Timestamp(params["end_date"]))
            if pd.Timestamp(params["end_date"]).year >= 2010:
                return index_frame(params["ts_code"], dates, closes=[float("nan")] * len(dates))
            return index_frame(params["ts_code"], dates)

        with patch.object(app, "load_config", return_value=dict(self.config)), \
             patch.object(app, "tushare_call", side_effect=mixed_tushare):
            with self.assertRaisesRegex(ValueError, "invalid/non-positive closes"):
                app.update_benchmark_data({"benchmarks": ["CSI300"], "end_date": "2024-06-28"})
        frame = pd.read_csv(self.paths()["CSI300"], dtype={"trade_date": str})
        parsed = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
        self.assertTrue((parsed < pd.Timestamp("2010-01-01")).all())
        self.assertTrue((frame["close"] > 0).all())

    def test_unknown_index_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown benchmark index"):
            self.run_update({"benchmarks": ["HSI"], "end_date": "2024-06-28"})

    def test_empty_benchmarks_defaults_to_all_indexes(self):
        result = self.run_update({"end_date": "2024-01-31"})
        self.assertEqual(sorted(x["index"] for x in result["benchmarks"]),
                         ["CSI300", "CSI300_TR", "CSI500", "CSI500_TR"])

    def test_cancel_is_honored_between_requests(self):
        app._JOB_CANCEL.set()
        try:
            with patch.object(app, "load_config", return_value=dict(self.config)), \
                 patch.object(app, "tushare_call", side_effect=self.fake_tushare):
                with self.assertRaises(app.JobCancelled):
                    app.update_benchmark_data({"end_date": "2024-06-28"})
        finally:
            app._JOB_CANCEL.clear()

    def test_status_reports_coverage_and_active_flag(self):
        self.run_update({"end_date": "2024-06-28"})
        with patch.object(app, "load_config", return_value=dict(self.config)):
            status = app.benchmark_status()
        self.assertEqual(sorted(status["indexes"]), ["CSI300", "CSI300_TR", "CSI500", "CSI500_TR"])
        for name in ("CSI300", "CSI300_TR", "CSI500", "CSI500_TR"):
            info = status["indexes"][name]
            self.assertTrue(info["exists"])
            self.assertEqual(info["rows"], len(pd.bdate_range("2005-01-01", "2024-06-28")))
            self.assertFalse(info["is_active"])
        active = dict(self.config, benchmark_ticker="000905.SH", benchmark_csv=str(self.paths()["CSI500"]))
        with patch.object(app, "load_config", return_value=active):
            status = app.benchmark_status()
        self.assertTrue(status["indexes"]["CSI500"]["is_active"])
        self.assertFalse(status["indexes"]["CSI300"]["is_active"])
        mismatch = dict(self.config, benchmark_ticker="000300.SH", benchmark_csv=str(self.paths()["CSI500"]))
        with patch.object(app, "load_config", return_value=mismatch):
            status = app.benchmark_status()
        self.assertFalse(status["indexes"]["CSI300"]["is_active"])

    def test_benchmark_job_kind_dispatches_and_completes(self):
        cfg = dict(self.config, tushare_token="token", proxy_url="http://proxy")
        seen = {}

        def fake_update(request):
            seen["request"] = request
            return {"ok": True, "benchmarks": []}

        try:
            with patch.object(app, "load_config", return_value=cfg), \
                 patch.object(app, "update_benchmark_data", side_effect=fake_update):
                app.start_data_job("benchmark", {"benchmarks": ["CSI300"], "end_date": "2024-12-31"})
                app._JOB_THREAD.join(10)
            self.assertEqual(seen["request"], {"benchmarks": ["CSI300"], "end_date": "2024-12-31"})
            snapshot = app.job_snapshot()
            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual(snapshot["kind"], "benchmark")
        finally:
            app._JOB_CANCEL.clear()

    def test_benchmark_endpoints_served(self):
        class Handler(app.Handler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        try:
            with patch.object(app, "load_config", return_value=dict(self.config)), \
                 patch.object(app, "start_data_job", return_value={"kind": "benchmark"}) as starter:
                with urlopen(base + "/api/benchmark-status", timeout=5) as r:
                    payload = json.loads(r.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertEqual(sorted(payload["benchmarks"]["indexes"]), ["CSI300", "CSI300_TR", "CSI500", "CSI500_TR"])
                with urlopen(Request(base + "/api/benchmark-build",
                                     data=json.dumps({"benchmarks": ["CSI500"]}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}), timeout=5) as r:
                    payload = json.loads(r.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                starter.assert_called_once_with("benchmark", {"benchmarks": ["CSI500"]})
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

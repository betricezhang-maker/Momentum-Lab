# Momentum Lab V3.3.3 - Multi-Universe
# Portable local research application.
# V2 focus: Data Manager + local browsing + Tushare full build / incremental refresh.

import sys, os, json, time, webbrowser, threading, subprocess, traceback, socket, urllib.request, collections, uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import numpy as np
import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

APP_VERSION = "3.3.3"
BUILD_TAG = "strategy-grid-selection-labels-2"
HOST = "127.0.0.1"
PORT = 8765

def app_dir():
    """Writable portable folder: where MomentumLabV2.exe lives."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def resource_dir():
    """Read-only bundled resources. PyInstaller stores these under _MEIPASS."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent

ROOT = app_dir()
RESOURCE_ROOT = resource_dir()
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "settings.json"
LOG_DIR = ROOT / "logs"
UI_FILE = RESOURCE_ROOT / "ui" / "index.html"

def write_log(message):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "momentumlab.log").open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass

def show_windows_error(title, message):
    write_log(f"{title}: {message}")
    if os.name == "nt":
        safe = str(message).replace("'", "''")
        safe_title = str(title).replace("'", "''")
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command",
                 f"Add-Type -AssemblyName PresentationFramework; "
                 f"[System.Windows.MessageBox]::Show('{safe}','{safe_title}','OK','Error') | Out-Null"],
                timeout=30
            )
        except Exception:
            pass

def find_free_port(start=8765, attempts=30):
    for port in range(start, start + attempts):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((HOST, port))
            return port
        except OSError:
            pass
        finally:
            s.close()
    raise RuntimeError(f"No free local port found between {start} and {start+attempts-1}.")

DEFAULTS = {
    "data_folder": str(ROOT / "data"),
    "results_folder": str(ROOT / "results"),
    "proxy_url": "https://fast.xiaodefa.cn",
    "tushare_token": "",
    "raw_price_csv": "",
    "adj_factor_csv": "",
    "weights_csv": "",
    "adjusted_price_csv": "",
    "trade_calendar_csv": "",
    "price_limits_csv": "",
    "api_requests_per_minute": 240,
    "api_retry_count": 3,
    "api_cooldown_seconds": 370,
    "active_universe": "CSI300",
    "universe_file_paths": {},
    "etf_min_market_cap_rmb": 250000000
}

def ensure_dirs(cfg=None):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if cfg:
        data = Path(cfg["data_folder"])
        results = Path(cfg["results_folder"])
        for p in [
            data, data/"raw", data/"adjusted", data/"membership", data/"calendar",
            data/"limits", results, results/"backtests", results/"reports",
            results/"heatmaps"
        ]:
            p.mkdir(parents=True, exist_ok=True)

def load_config():
    ensure_dirs()
    cfg = DEFAULTS.copy()
    if CONFIG_FILE.exists():
        try:
            x = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(x, dict):
                cfg.update(x)
        except Exception:
            pass
    ensure_dirs(cfg)
    return cfg

def save_config(cfg):
    ensure_dirs(cfg)
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)

def parse_dates(s):
    raw = s.astype(str).str.replace(".0","",regex=False).str.strip()
    d = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
    bad = d.isna()
    if bad.any():
        d.loc[bad] = pd.to_datetime(raw.loc[bad], errors="coerce")
    return d

def read_csv(path):
    if not path:
        raise FileNotFoundError("No file has been selected.")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return pd.read_csv(p, low_memory=False)

def write_csv_atomic(df, path, **kwargs):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        df.to_csv(tmp, **kwargs)
        tmp.replace(p)
    finally:
        if tmp.exists():
            tmp.unlink()


def append_dedupe(existing_path, new_df, keys, replace_snapshots=False):
    p = Path(existing_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    if p.exists():
        old = pd.read_csv(p, low_memory=False)
        if replace_snapshots and new_df is not None and len(new_df):
            old = old[~parse_dates(old['trade_date']).isin(parse_dates(new_df['trade_date']))]
        frames.append(old)
    if new_df is not None and len(new_df):
        frames.append(new_df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(keys, keep="last")
    write_csv_atomic(out, p, index=False, encoding="utf-8-sig")
    return out

# -------------------- WINDOWS DIALOGS --------------------

def powershell_dialog(script):
    if os.name != "nt":
        raise RuntimeError("Native Windows file browser is available in the Windows build.")
    p = subprocess.run(
        ["powershell","-NoProfile","-STA","-Command",script],
        capture_output=True, text=True, timeout=180
    )
    return p.stdout.strip()

def browse_file(initial="", title="Select CSV file"):
    initial = str(initial or "").replace("'","''")
    title = title.replace("'","''")
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.TopMost = $true
$form.ShowInTaskbar = $false
$form.WindowState = 'Minimized'
$form.Opacity = 0
$form.Show()
$dlg = New-Object System.Windows.Forms.OpenFileDialog
$dlg.Title = '{title}'
$dlg.Filter = 'CSV files (*.csv)|*.csv|All files (*.*)|*.*'
$dlg.CheckFileExists = $true
$dlg.Multiselect = $false
$dlg.RestoreDirectory = $true
if ('{initial}' -ne '') {{
    if (Test-Path '{initial}') {{
        $item = Get-Item '{initial}'
        if ($item.PSIsContainer) {{ $dlg.InitialDirectory = $item.FullName }}
        else {{ $dlg.InitialDirectory = $item.DirectoryName }}
    }}
}}
$result = $dlg.ShowDialog($form)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
    Write-Output $dlg.FileName
}}
$form.Close()
"""
    try:
        result = powershell_dialog(script)
        if not result:
            return ""
        return result
    except Exception as e:
        write_log("Native file browse failed:\n" + traceback.format_exc())
        raise RuntimeError(f"Windows file browser failed: {e}")

def browse_folder(initial=""):
    initial = str(initial or "").replace("'","''")
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
$dlg.Description = 'Select Momentum Lab folder'
if ('{initial}' -ne '' -and (Test-Path '{initial}')) {{
    $dlg.SelectedPath = '{initial}'
}}
if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    Write-Output $dlg.SelectedPath
}}
"""
    return powershell_dialog(script)



# -------------------- BACKGROUND DATA JOB ENGINE --------------------

class JobCancelled(Exception):
    pass

_JOB_LOCK = threading.Lock()
_REQUEST_LOCK = threading.RLock()
_JOB_CANCEL = threading.Event()
_JOB_THREAD = None
_JOB = {
    "status": "idle",          # idle/running/cancelling/completed/cancelled/error
    "kind": None,              # full_build / refresh
    "stage": "Idle",
    "stage_index": 0,
    "stage_total": 0,
    "current": 0,
    "total": 0,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "elapsed_seconds": 0,
    "cooldown_remaining": 0,
    "last_error": None,
    "result": None,
    "request": None,
}

def job_snapshot():
    with _JOB_LOCK:
        j = dict(_JOB)
    if j.get("started_at") and j.get("status") in ("running","cancelling"):
        j["elapsed_seconds"] = max(0, int(time.time() - j["started_at"]))
    # Approximate live request count in the last minute
    with _API_LOCK:
        now = time.monotonic()
        recent = [x for x in _API_TIMES if now - x < 60.0]
    j["api_requests_last_minute"] = len(recent)
    j['can_resume'] = j.get('status') not in ('running', 'cancelling') and bool(
        j.get('request') or load_checkpoint().get('signature'))
    return j

def job_update(**kwargs):
    with _JOB_LOCK:
        _JOB.update(kwargs)

def job_check_cancel():
    if _JOB_CANCEL.is_set():
        raise JobCancelled("Cancelled by user.")

def interruptible_sleep(seconds, cooldown=False):
    remaining = int(max(0, seconds))
    while remaining > 0:
        job_check_cancel()
        if cooldown:
            job_update(cooldown_remaining=remaining,
                       message=f"API cooldown: {remaining}s remaining")
        time.sleep(min(1, remaining))
        remaining -= 1
    if cooldown:
        job_update(cooldown_remaining=0)

def preflight_data_job(kind, request):
    cfg = load_config()
    errors = []
    if not cfg.get("tushare_token","").strip():
        errors.append("Tushare token is empty. Save it in Settings first.")
    if not cfg.get("proxy_url","").strip():
        errors.append("Proxy/API URL is empty.")
    for key in ("data_folder","results_folder"):
        p = Path(cfg.get(key,""))
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Cannot use {key}: {p} ({e})")
    u=request.get("universe") or cfg.get("active_universe","CSI300")
    if u not in UNIVERSES:
        errors.append("Unknown dataset universe.")
    if kind == "full_build":
        if not request.get("start_date") or not request.get("end_date"):
            errors.append("Build start/end date is missing.")
        elif pd.Timestamp(request["start_date"]) > pd.Timestamp(request["end_date"]):
            errors.append("Build start date is after end date.")
    elif kind == "refresh":
        if not request.get("end_date"):
            errors.append("Update end date is missing.")
    return errors

def start_data_job(kind, request):
    global _JOB_THREAD
    with _JOB_LOCK:
        if _JOB.get("status") in ("running","cancelling"):
            raise RuntimeError("A data job is already running. Cancel it or wait for it to finish.")
    errors = preflight_data_job(kind, request)
    if errors:
        raise ValueError(" ".join(errors))

    _JOB_CANCEL.clear()
    job_update(
        status="running", kind=kind, stage="Starting", stage_index=0, stage_total=6,
        current=0, total=0, message="Starting background data job...",
        started_at=time.time(), finished_at=None, elapsed_seconds=0,
        cooldown_remaining=0, last_error=None, result=None, request=dict(request)
    )

    def runner():
        try:
            if kind == "full_build":
                result = full_build(
                    request["start_date"], request["end_date"],
                    bool(request.get("include_limits", True)),
                    request.get("universe")
                )
            else:
                result = refresh_data(request["end_date"], request.get("universe"))
            job_update(
                status="completed", stage="Complete", current=1, total=1,
                message="Data job completed successfully.",
                finished_at=time.time(), result=result
            )
        except JobCancelled:
            job_update(
                status="cancelled", stage="Cancelled",
                message="Cancelled by user. Downloaded checkpoints were preserved.",
                finished_at=time.time(), cooldown_remaining=0
            )
        except Exception as e:
            write_log("Background data job failed:\n" + traceback.format_exc())
            job_update(
                status="error", stage="Error", message=str(e),
                last_error=f"{type(e).__name__}: {e}", finished_at=time.time(),
                cooldown_remaining=0
            )

    _JOB_THREAD = threading.Thread(target=runner, daemon=True, name="MomentumLabDataJob")
    _JOB_THREAD.start()
    return job_snapshot()

def cancel_data_job():
    st = job_snapshot()
    if st["status"] not in ("running","cancelling"):
        return {"ok": True, "message": "No active data job.", "job": st}
    _JOB_CANCEL.set()
    job_update(status="cancelling", message="Cancellation requested. Finishing the current API request safely...")
    return {"ok": True, "message": "Cancellation requested.", "job": job_snapshot()}

def resume_data_job():
    st = job_snapshot()
    req = st.get("request")
    kind = st.get("kind")
    if not req or not kind:
        signature = dict(load_checkpoint().get('signature') or {})
        kind = signature.pop('kind', None)
        req = signature
    if not req or kind not in ("full_build","refresh"):
        raise ValueError("No previous data job is available to resume.")
    if st["status"] in ("running","cancelling"):
        raise RuntimeError("The data job is still active.")
    return start_data_job(kind, req)


def reset_stale_job_state():
    global _JOB_THREAD
    alive = bool(_JOB_THREAD is not None and _JOB_THREAD.is_alive())
    st = job_snapshot()
    if alive and st.get("status") in ("running", "cancelling"):
        raise RuntimeError("The data worker is still alive. Use Cancel / Interrupt first.")
    _JOB_CANCEL.clear()
    _JOB_THREAD = None
    job_update(
        status="idle", kind=None, stage="Idle", stage_index=0, stage_total=0,
        current=0, total=0, message="Job state reset. No active data worker.",
        started_at=None, finished_at=None, elapsed_seconds=0,
        cooldown_remaining=0, last_error=None, result=None, request=None
    )
    return {"ok": True, "job": job_snapshot()}


# -------------------- API RATE LIMITER --------------------

_API_TIMES = collections.deque()
_API_LOCK = threading.Lock()

def throttle_api():
    """
    Central request governor. Cancellation is checked while waiting.
    """
    cfg = load_config()
    rpm = max(30, int(cfg.get("api_requests_per_minute", 240) or 240))
    window = 60.0

    while True:
        job_check_cancel()
        wait_for = 0.0
        with _API_LOCK:
            now = time.monotonic()
            while _API_TIMES and now - _API_TIMES[0] >= window:
                _API_TIMES.popleft()

            if len(_API_TIMES) < rpm:
                _API_TIMES.append(now)
                return

            wait_for = window - (now - _API_TIMES[0]) + 0.05

        if wait_for > 0:
            interruptible_sleep(min(wait_for, 2))

def is_frequency_error(obj):
    code = obj.get("code")
    msg = str(obj.get("msg", ""))
    return code == 40201 or "访问频率" in msg or "频率" in msg or "too many" in msg.lower()


# -------------------- TUSHARE API --------------------

def tushare_call(api_name, params=None, fields=""):
    cfg = load_config()
    token = cfg.get("tushare_token","").strip()
    url = cfg.get("proxy_url","").strip() or "https://api.tushare.pro"
    if not token:
        raise ValueError("Tushare token is empty. Save it in Settings first.")

    payload = {
        "api_name": api_name,
        "token": token,
        "params": params or {},
        "fields": fields or ""
    }

    retries = max(0, int(cfg.get("api_retry_count", 3) or 3))
    cooldown = max(60, int(cfg.get("api_cooldown_seconds", 370) or 370))

    for attempt in range(retries + 1):
        throttle_api()
        try:
            r = requests.post(url, json=payload, timeout=60)
            r.raise_for_status()
            obj = r.json()
        except requests.RequestException as e:
            if attempt >= retries:
                raise RuntimeError(f"Network/API request failed after {attempt+1} attempts: {e}")
            delay = min(30, 2 ** attempt)
            write_log(f"API network retry {attempt+1}/{retries}: sleeping {delay}s. {e}")
            interruptible_sleep(delay)
            continue

        if obj.get("code") in (0, None):
            data = obj.get("data", {}) or {}
            fields_out = data.get("fields", []) or []
            items = data.get("items", []) or []
            return pd.DataFrame(items, columns=fields_out)

        if is_frequency_error(obj):
            write_log(
                f"Tushare frequency limit hit for {api_name}. "
                f"Attempt {attempt+1}/{retries+1}. Message: {obj.get('msg')}"
            )
            if attempt >= retries:
                raise RuntimeError(
                    "Tushare API frequency limit was reached. "
                    "Momentum Lab has preserved downloaded CSV progress. "
                    f"Please wait about {cooldown//60} minutes and resume the build. "
                    "V2.4 preserves downloaded checkpoints."
                )
            interruptible_sleep(cooldown, cooldown=True)
            continue

        raise RuntimeError(f"Tushare error {obj.get('code')}: {obj.get('msg')}")

    raise RuntimeError("Unexpected API retry state.")

def test_tushare():
    df = tushare_call(
        "trade_cal",
        {"exchange":"SSE","start_date":"20260101","end_date":"20260110"},
        "exchange,cal_date,is_open"
    )
    return {"ok": True, "message": "Connection successful.", "rows": int(len(df))}


def checkpoint_path():
    cfg = load_config()
    p = Path(cfg["data_folder"]) / ".momentumlab_checkpoint.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def load_checkpoint():
    p = checkpoint_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_checkpoint(cp):
    p = checkpoint_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

def init_checkpoint(kind, request):
    cp = load_checkpoint()
    sig = {"kind":kind, **request}
    if cp.get("signature") != sig:
        cp = {"signature":sig, "completed":{}}
        save_checkpoint(cp)
    return cp

def mark_checkpoint(stage, item):
    cp = load_checkpoint()
    comp = cp.setdefault("completed", {})
    vals = comp.setdefault(stage, [])
    if item not in vals:
        vals.append(item)
        save_checkpoint(cp)

def completed_checkpoint(stage):
    cp = load_checkpoint()
    return set(cp.get("completed", {}).get(stage, []))

def date_chunks(start, end, months=3):
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    cur = s
    while cur <= e:
        nxt = min(cur + pd.DateOffset(months=months) - pd.Timedelta(days=1), e)
        yield cur, nxt
        cur = nxt + pd.Timedelta(days=1)

def fetch_trade_calendar(start_date, end_date, output):
    job_check_cancel()
    job_update(stage="Trading calendar", current=0, total=1,
               message="Downloading SSE trading calendar...")
    df = tushare_call(
        "trade_cal",
        {"exchange":"SSE",
         "start_date":pd.Timestamp(start_date).strftime("%Y%m%d"),
         "end_date":pd.Timestamp(end_date).strftime("%Y%m%d")},
        "exchange,cal_date,is_open,pretrade_date"
    )
    if len(df):
        df = df.rename(columns={"cal_date":"trade_date"})
    out = append_dedupe(output, df, ["trade_date"])
    job_update(current=1, total=1, message=f"Trading calendar saved: {len(out):,} rows")
    return {"rows":int(len(out)), "output":str(output)}

def fetch_index_weights(start_date, end_date, output):
    chunks = list(date_chunks(start_date,end_date,months=3))
    parts = []
    job_update(stage="CSI 300 historical weights", current=0, total=len(chunks),
               message="Downloading CSI 300 membership/weights...")
    for i,(s,e) in enumerate(chunks,1):
        job_check_cancel()
        key = f"{s.strftime('%Y%m%d')}-{e.strftime('%Y%m%d')}"
        if key in completed_checkpoint("weights_chunks"):
            job_update(current=i, total=len(chunks),
                       message=f"Weights: checkpoint {i}/{len(chunks)}")
            continue
        x = tushare_call(
            "index_weight",
            {"index_code":"000300.SH",
             "start_date":s.strftime("%Y%m%d"),
             "end_date":e.strftime("%Y%m%d")},
            "index_code,con_code,trade_date,weight"
        )
        if len(x):
            append_dedupe(output, x, ["trade_date","con_code"], replace_snapshots=True)
        mark_checkpoint("weights_chunks", key)
        job_update(current=i, total=len(chunks),
                   message=f"Weights: {i}/{len(chunks)} date chunks")
    out = read_csv(output) if Path(output).exists() else pd.DataFrame()
    return {"rows":int(len(out)),
            "snapshots":int(out["trade_date"].nunique()) if len(out) and "trade_date" in out.columns else 0,
            "output":str(output)}

def infer_tickers_from_weights(weights_path):
    w = read_csv(weights_path)
    if "con_code" not in w.columns:
        raise ValueError("Weights CSV must contain con_code.")
    return sorted(w["con_code"].astype(str).str.strip().dropna().unique())

def incremental_ticker_starts(api_name, old, tickers, start_date, paths):
    """Conservative per-security repair windows; exchange closures are not price gaps."""
    raw_path = paths['raw_price_csv']
    if not Path(raw_path).exists():
        raise ValueError(f'Raw price file missing: {raw_path}. Locate/import it before incremental update.')
    raw = pd.read_csv(raw_path, usecols=['ts_code','trade_date'])
    raw['trade_date'] = parse_dates(raw['trade_date'])
    raw = raw.dropna(subset=['trade_date'])
    history_start = raw['trade_date'].min()
    if pd.isna(history_start):
        raise ValueError('Raw prices have no usable dates; run a full build.')
    existing = {} if old is None or old.empty else {
        str(code): set(parse_dates(group['trade_date']).dropna()) for code, group in old.groupby('ts_code')}
    raw_dates = {str(code): set(group['trade_date']) for code, group in raw.groupby('ts_code')}
    cal = read_csv(paths['trade_calendar_csv'])
    open_dates = set(parse_dates(cal.loc[pd.to_numeric(cal['is_open'],errors='coerce') == 1,'trade_date']).dropna())
    starts = {}
    for ticker in tickers:
        have = existing.get(str(ticker), set())
        if not have:
            starts[ticker] = history_start
            continue
        first = min(pd.Timestamp(start_date), max(have) + pd.Timedelta(days=1))
        if api_name in ('daily','fund_daily'):
            expected = {d for d in open_dates if min(have) <= d <= max(have)}
        else:
            expected = raw_dates.get(str(ticker), set())
        missing = expected - have
        starts[ticker] = min(first, min(missing)) if missing else first
    return starts


def fetch_stock_history(api_name, tickers, start_date, end_date, fields, output, keys, checkpoint_stage, stage_name):
    p = Path(output)
    old = None
    if p.exists():
        try:
            old = pd.read_csv(p, low_memory=False)
        except Exception:
            old = None

    done = completed_checkpoint(checkpoint_stage)
    starts = {}
    signature = load_checkpoint().get('signature', {})
    if signature.get('kind') == 'refresh':
        starts = incremental_ticker_starts(api_name, old, tickers, start_date,
                                          selected_universe_paths(load_config(), signature['universe']))
    parts = []
    pending = []
    total = len(tickers)
    job_update(stage=stage_name, current=0, total=total,
               message=f"{stage_name}: preparing {total} stocks")

    for i,ticker in enumerate(tickers,1):
        job_check_cancel()
        if ticker in done:
            job_update(current=i, total=total,
                       message=f"{stage_name}: {i}/{total} (checkpoint)")
            continue

        x = tushare_call(
            api_name,
            {"ts_code":ticker,
             "start_date":pd.Timestamp(starts.get(ticker, start_date)).strftime("%Y%m%d"),
             "end_date":pd.Timestamp(end_date).strftime("%Y%m%d")},
            fields
        )
        if len(x):
            parts.append(x)
        pending.append(ticker)

        # Flush frequently so cancel/force-close loses very little work.
        if len(parts) >= 5:
            temp = pd.concat(parts, ignore_index=True)
            old = append_dedupe(output, temp, keys)
            parts = []
            for completed in pending:
                mark_checkpoint(checkpoint_stage, completed)
            pending = []

        job_update(current=i, total=total,
                   message=f"{stage_name}: {i}/{total} · {ticker}")

    if parts:
        temp = pd.concat(parts, ignore_index=True)
        old = append_dedupe(output, temp, keys)
    for completed in pending:
        mark_checkpoint(checkpoint_stage, completed)

    if old is None:
        old = read_csv(output) if Path(output).exists() else pd.DataFrame()
    return {"rows":int(len(old)),
            "stocks":int(old["ts_code"].nunique()) if len(old) and "ts_code" in old.columns else 0,
            "output":str(output)}

def fetch_raw_prices(tickers, start_date, end_date, output):
    fields = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
    return fetch_stock_history("daily", tickers, start_date, end_date, fields, output,
                               ["ts_code","trade_date"], "raw_prices", "Daily prices")

def fetch_adj_factors(tickers, start_date, end_date, output):
    return fetch_stock_history("adj_factor", tickers, start_date, end_date,
                               "ts_code,trade_date,adj_factor", output,
                               ["ts_code","trade_date"], "adj_factors", "Adjustment factors")

def fetch_price_limits(tickers, start_date, end_date, output):
    fields = "trade_date,ts_code,pre_close,up_limit,down_limit"
    return fetch_stock_history("stk_limit", tickers, start_date, end_date, fields, output,
                               ["ts_code","trade_date"], "price_limits", "Price limits")

def build_adjusted_prices(raw_path, factor_path, output_path):
    raw = read_csv(raw_path)
    fac = read_csv(factor_path)
    need_raw = {"ts_code","trade_date","open","high","low","close"}
    need_fac = {"ts_code","trade_date","adj_factor"}
    if not need_raw.issubset(raw.columns):
        raise ValueError(f"Raw price CSV missing: {sorted(need_raw - set(raw.columns))}")
    if not need_fac.issubset(fac.columns):
        raise ValueError(f"Adj-factor CSV missing: {sorted(need_fac - set(fac.columns))}")

    raw["ts_code"] = raw["ts_code"].astype(str).str.strip()
    fac["ts_code"] = fac["ts_code"].astype(str).str.strip()
    raw["trade_date"] = parse_dates(raw["trade_date"])
    fac["trade_date"] = parse_dates(fac["trade_date"])
    fac["adj_factor"] = pd.to_numeric(fac["adj_factor"], errors="coerce")

    fac = fac.dropna(subset=["ts_code","trade_date","adj_factor"]).drop_duplicates(
        ["ts_code","trade_date"], keep="last"
    )
    x = raw.merge(fac[["ts_code","trade_date","adj_factor"]],
                  on=["ts_code","trade_date"], how="left")
    x = x.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    missing = x['adj_factor'].isna() | (x['adj_factor'] <= 0)
    if missing.any():
        raise ValueError(f"Adjustment factors missing/invalid for {int(missing.sum()):,} price rows. Download historical factors before rebuilding; existing adjusted data was not overwritten.")
    x["adj_factor"] = x.groupby("ts_code")["adj_factor"].transform(lambda s:s.ffill().bfill())
    x["latest_adj_factor"] = x.groupby("ts_code")["adj_factor"].transform("last")
    x["adj_ratio"] = x["adj_factor"] / x["latest_adj_factor"]

    for c in ["open","high","low","close","pre_close"]:
        if c in x.columns:
            x[c] = pd.to_numeric(x[c], errors="coerce")
            x["adj_"+c] = x[c] * x["adj_ratio"]
    x["adj_daily_return"] = x.groupby("ts_code")["adj_close"].pct_change(fill_method=None)

    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    y = x.copy()
    y["trade_date"] = y["trade_date"].dt.strftime("%Y%m%d")
    write_csv_atomic(y,p,index=False,encoding="utf-8-sig")
    return {"rows":int(len(y)), "stocks":int(y["ts_code"].nunique()), "output":str(p)}

def default_data_paths(cfg, universe=None):
    universe = universe or cfg.get("active_universe","CSI300")
    return selected_universe_paths(cfg,universe)

def full_build(start_date, end_date, include_limits=True, universe=None):
    cfg=load_config()
    universe=universe or cfg.get("active_universe","CSI300")
    if universe not in UNIVERSES:
        raise ValueError("Unknown universe.")
    if cfg.get("active_universe") != universe:
        set_active_universe(universe)
        cfg=load_config()
    paths=default_data_paths(cfg,universe)
    init_checkpoint("full_build",{
        "universe":universe,"start_date":str(start_date),"end_date":str(end_date),
        "include_limits":bool(include_limits)
    })

    # Stage 1: shared trading calendar
    job_update(stage_total=6,stage_index=1)
    cal=fetch_trade_calendar(start_date,end_date,paths["trade_calendar_csv"])

    # Stage 2: point-in-time membership / eligibility
    job_check_cancel(); job_update(stage_index=2)
    if universe=="CSI300":
        membership=fetch_index_weights(start_date,end_date,paths["weights_csv"])
    elif universe in ("CHINEXT_TOP100","STAR_TOP100"):
        membership=build_top100_membership(universe,start_date,end_date,paths["weights_csv"])
    else:
        membership=build_etf_eligibility(start_date,end_date,paths["weights_csv"],
                                         int(cfg.get("etf_min_market_cap_rmb",250000000)))

    tickers=infer_tickers_from_weights(paths["weights_csv"])
    if not tickers:
        raise RuntimeError(f"No eligible securities were found for {UNIVERSES[universe]['label']}.")

    # Stage 3: raw prices
    job_check_cancel(); job_update(stage_index=3)
    if universe=="ETF_250M":
        raw=fetch_etf_raw_prices(tickers,start_date,end_date,paths["raw_price_csv"])
    else:
        raw=fetch_raw_prices(tickers,start_date,end_date,paths["raw_price_csv"])

    # Stage 4: adjustment factors
    job_check_cancel(); job_update(stage_index=4)
    if universe=="ETF_250M":
        fac=fetch_etf_adj_factors(tickers,start_date,end_date,paths["adj_factor_csv"])
    else:
        fac=fetch_adj_factors(tickers,start_date,end_date,paths["adj_factor_csv"])

    # Stage 5: stock price limits (not used for ETF research mode)
    lim=None
    if universe!="ETF_250M" and include_limits:
        job_check_cancel(); job_update(stage_index=5)
        lim=fetch_price_limits(tickers,start_date,end_date,paths["price_limits_csv"])
    else:
        job_update(stage_index=5,stage="Price limits",current=1,total=1,
                   message="Price limits not required for this dataset build.")

    # Stage 6: adjusted prices
    job_check_cancel()
    job_update(stage_index=6,stage="Adjusted prices",current=0,total=1,
               message="Building adjusted prices...")
    adj=build_adjusted_prices(paths["raw_price_csv"],paths["adj_factor_csv"],paths["adjusted_price_csv"])
    job_update(current=1,total=1,message=f"Adjusted prices built: {adj['rows']:,} rows")

    cfg=load_config()
    cfg=_remember_current_paths(cfg)
    save_config(cfg)
    return {"ok":True,"universe":universe,"calendar":cal,"membership":membership,
            "raw":raw,"factors":fac,"limits":lim,"adjusted":adj,
            "tickers":len(tickers),"paths":paths}

def file_max_date(path,col="trade_date"):
    if not path or not Path(path).exists():
        return None
    try:
        d=pd.read_csv(path,usecols=[col],low_memory=False)
        x=parse_dates(d[col]).max()
        return None if pd.isna(x) else x
    except Exception:
        return None

def refresh_data(end_date, universe=None):
    cfg=load_config()
    universe=universe or cfg.get("active_universe","CSI300")
    if cfg.get("active_universe") != universe:
        set_active_universe(universe)
        cfg=load_config()
    paths=default_data_paths(cfg,universe)

    candidates=[file_max_date(paths["raw_price_csv"]),
                file_max_date(paths["adj_factor_csv"]),
                file_max_date(paths["trade_calendar_csv"])]
    candidates=[x for x in candidates if x is not None]
    if not candidates:
        raise ValueError(f"No local {UNIVERSES[universe]['label']} dataset was found. Use BUILD / REBUILD first.")

    start=min(candidates)+pd.Timedelta(days=1)
    end=pd.Timestamp(end_date)
    # Membership needs overlap so a latest month-end / index snapshot is refreshed.
    member_start=max(pd.Timestamp("2005-01-01"),start-pd.Timedelta(days=70))

    init_checkpoint("refresh",{"universe":universe,"start_date":start.strftime("%Y-%m-%d"),
                               "end_date":end.strftime("%Y-%m-%d")})

    job_update(stage_total=6,stage_index=1)
    if start <= end:
        fetch_trade_calendar(start,end,paths["trade_calendar_csv"])

    job_check_cancel(); job_update(stage_index=2)
    if universe=="CSI300":
        fetch_index_weights(member_start,end,paths["weights_csv"])
    elif universe in ("CHINEXT_TOP100","STAR_TOP100"):
        build_top100_membership(universe,member_start,end,paths["weights_csv"])
    else:
        build_etf_eligibility(member_start,end,paths["weights_csv"],
                              int(cfg.get("etf_min_market_cap_rmb",250000000)))

    tickers=infer_tickers_from_weights(paths["weights_csv"])

    # Repair per-security gaps even if another security already reached the end date.
    if tickers:
        job_check_cancel(); job_update(stage_index=3)
        if universe=="ETF_250M":
            fetch_etf_raw_prices(tickers,start,end,paths["raw_price_csv"])
        else:
            fetch_raw_prices(tickers,start,end,paths["raw_price_csv"])

        job_check_cancel(); job_update(stage_index=4)
        if universe=="ETF_250M":
            fetch_etf_adj_factors(tickers,start,end,paths["adj_factor_csv"])
        else:
            fetch_adj_factors(tickers,start,end,paths["adj_factor_csv"])

        job_check_cancel(); job_update(stage_index=5)
        if universe!="ETF_250M":
            fetch_price_limits(tickers,start,end,paths["price_limits_csv"])
        else:
            job_update(stage="Price limits",current=1,total=1,message="ETF price limits skipped.")

    job_check_cancel(); job_update(stage_index=6,stage="Adjusted prices",current=0,total=1,
                                   message="Rebuilding adjusted prices...")
    adj=build_adjusted_prices(paths["raw_price_csv"],paths["adj_factor_csv"],paths["adjusted_price_csv"])
    job_update(current=1,total=1,message=f"Adjusted prices rebuilt: {adj['rows']:,} rows")
    cfg=load_config(); cfg=_remember_current_paths(cfg); save_config(cfg)
    return {"ok":True,"universe":universe,"message":"Refresh completed.",
            "start":start.strftime("%Y-%m-%d"),"end":end.strftime("%Y-%m-%d"),
            "adjusted":adj,"tickers":len(tickers)}

# -------------------- BROWSER FILE IMPORT --------------------

IMPORT_FIELD_MAP = {
    "raw_price_csv": "raw",
    "adjusted_price_csv": "adjusted",
    "weights_csv": "membership",
    "adj_factor_csv": "adjusted",
    "trade_calendar_csv": "calendar",
    "price_limits_csv": "limits",
}

def import_uploaded_csv(field, filename, content):
    if field not in IMPORT_FIELD_MAP:
        raise ValueError("Unknown data field.")
    if not filename.lower().endswith(".csv"):
        raise ValueError("Please select a CSV file.")
    cfg = load_config()
    sub = IMPORT_FIELD_MAP[field]
    target_dir = Path(cfg["data_folder"]) / cfg.get('active_universe','CSI300') / sub / "imported"
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    target = target_dir / safe_name
    target.write_bytes(content)
    # quick readability check
    pd.read_csv(target, nrows=5)
    cfg[field] = str(target)
    cfg = _remember_current_paths(cfg)
    save_config(cfg)
    write_log(f"Imported browser CSV for {field}: {target}")
    return str(target)


def top_list_persistence(ranked, ticker, signal_date, window, top_n=20):
    """
    Count how many of the last `window` available ranking dates ending on
    signal_date the ticker appeared in Top N, plus current consecutive streak.
    """
    valid = ranked[
        (ranked["csi300_member"] == 1) &
        ranked["momentum_rank"].notna() &
        (ranked["trade_date"] <= signal_date)
    ].copy()

    dates = pd.DatetimeIndex(sorted(valid["trade_date"].dropna().unique()))
    if len(dates) == 0:
        return 0, 0.0, 0, 0

    dates = dates[-int(window):]
    sub = valid[(valid["trade_date"].isin(dates)) & (valid["ts_code"] == ticker)].copy()
    top_flags = {}
    for d in dates:
        r = sub[sub["trade_date"] == d]
        top_flags[d] = bool(len(r) and float(r.iloc[0]["momentum_rank"]) <= top_n)

    count = sum(top_flags.values())
    denom = len(dates)
    persistence = count / denom if denom else 0.0

    streak = 0
    for d in reversed(dates):
        if top_flags.get(d, False):
            streak += 1
        else:
            break
    return int(count), float(persistence), int(streak), int(denom)


def multi_span_monitor(prices, weights, spans, primary_span, end_date=None, top_n=20):
    """
    Primary span determines which stocks are displayed.
    Other spans are diagnostic only: score/rank are shown independently and are
    NOT blended into the backtest signal.
    """
    spans = sorted({int(x) for x in spans if int(x) > 1})
    primary_span = int(primary_span)
    if primary_span not in spans:
        spans.append(primary_span)
        spans = sorted(set(spans))

    ranked_by_span = {}
    latest_dates = []

    for span in spans:
        sig = calculate_signal(prices, span)
        ranked = merge_membership_and_rank(sig, weights)
        if end_date:
            ranked = ranked[ranked["trade_date"] <= pd.Timestamp(end_date)].copy()
        valid = ranked[(ranked["csi300_member"] == 1) & ranked["momentum_rank"].notna()]
        if valid.empty:
            continue
        ranked_by_span[span] = ranked
        latest_dates.append(valid["trade_date"].max())

    if primary_span not in ranked_by_span:
        return {"date": None, "primary_span": primary_span, "spans": spans, "rows": []}

    primary = ranked_by_span[primary_span]
    valid_primary = primary[
        (primary["csi300_member"] == 1) &
        primary["momentum_rank"].notna()
    ].copy()
    signal_date = valid_primary["trade_date"].max()

    # Use a common date: latest date available to the primary span.
    top = valid_primary[valid_primary["trade_date"] == signal_date] \
        .sort_values("momentum_rank").head(top_n).copy()

    px = prices.copy()
    px["trade_date"] = parse_dates(px["trade_date"])
    px["ts_code"] = px["ts_code"].astype(str).str.strip()
    px["adj_close"] = pd.to_numeric(px["adj_close"], errors="coerce")
    px = px[px["trade_date"] <= signal_date][["trade_date","ts_code","adj_close"]] \
        .dropna(subset=["trade_date","ts_code","adj_close"]) \
        .sort_values(["ts_code","trade_date"])

    rows = []
    for _, r in top.iterrows():
        ticker = r["ts_code"]
        hist = px[px["ts_code"] == ticker].tail(10)
        prices10 = [float(x) for x in hist["adj_close"].tolist()]
        dates10 = [pd.Timestamp(x).strftime("%m-%d") for x in hist["trade_date"].tolist()]
        trend10 = None
        if len(prices10) >= 2 and prices10[0] != 0:
            trend10 = prices10[-1] / prices10[0] - 1

        # Persistence based on PRIMARY span ranking over PRIMARY lookback.
        p_count, p_pct, p_streak, p_denom = top_list_persistence(
            primary, ticker, signal_date, primary_span, top_n=top_n
        )

        span_data = {}
        consensus = 0
        for span in spans:
            ranked = ranked_by_span.get(span)
            item = {"score": None, "rank": None, "top20": False}
            if ranked is not None:
                rr = ranked[(ranked["trade_date"] == signal_date) & (ranked["ts_code"] == ticker)]
                if len(rr):
                    rr = rr.iloc[0]
                    if pd.notna(rr.get("momentum_score")):
                        item["score"] = float(rr["momentum_score"])
                    if pd.notna(rr.get("momentum_rank")):
                        item["rank"] = int(rr["momentum_rank"])
                        item["top20"] = item["rank"] <= top_n
                        if item["top20"]:
                            consensus += 1
            span_data[str(span)] = item

        rows.append({
            "rank": int(r["momentum_rank"]),
            "ticker": ticker,
            "momentum_score": None if pd.isna(r["momentum_score"]) else float(r["momentum_score"]),
            "signal_return": None if pd.isna(r["signal_return"]) else float(r["signal_return"]),
            "signal_volatility": None if pd.isna(r["signal_volatility"]) else float(r["signal_volatility"]),
            "top20_count": p_count,
            "top20_window": p_denom,
            "persistence": p_pct,
            "top20_streak": p_streak,
            "span_consensus": consensus,
            "span_total": len(spans),
            "span_data": span_data,
            "trend_dates": dates10,
            "trend_prices": prices10,
            "trend_10obs_return": trend10,
        })

    return {
        "date": pd.Timestamp(signal_date).strftime("%Y-%m-%d"),
        "primary_span": primary_span,
        "spans": spans,
        "rows": rows
    }



# -------------------- V3.1 MULTI-UNIVERSE --------------------
UNIVERSES = {
    "CSI300": {"label":"CSI 300", "folder":"CSI300", "kind":"csi300"},
    "CHINEXT_TOP100": {"label":"ChiNext Top 100", "folder":"CHINEXT_TOP100", "kind":"equity_top100"},
    "STAR_TOP100": {"label":"STAR Top 100", "folder":"STAR_TOP100", "kind":"equity_top100"},
    "ETF_250M": {"label":"ETF ≥ RMB 250M", "folder":"ETF_250M", "kind":"etf"},
}

FILE_KEYS = ("raw_price_csv","adjusted_price_csv","weights_csv","adj_factor_csv","trade_calendar_csv","price_limits_csv")

def default_universe_paths(cfg, u):
    root = Path(cfg["data_folder"]) / UNIVERSES[u]["folder"]
    root.mkdir(parents=True, exist_ok=True)
    return {
        "root": str(root),
        "raw_price_csv": str(root/"prices.csv"),
        "adjusted_price_csv": str(root/"adjusted_prices.csv"),
        "weights_csv": str(root/("eligibility.csv" if u=="ETF_250M" else "membership.csv")),
        "adj_factor_csv": str(root/"adj_factors.csv"),
        "trade_calendar_csv": str(root/"calendar.csv"),
        "price_limits_csv": str(root/"price_limits.csv"),
        "daily_basic_csv": str(root/"daily_basic.csv"),
        "fund_size_csv": str(root/"fund_size.csv"),
    }

def _stored_universe_paths(cfg, u):
    allp = cfg.get("universe_file_paths") or {}
    p = dict(default_universe_paths(cfg,u))
    saved = allp.get(u) or {}
    for k,v in saved.items():
        if v:
            candidate = Path(v)
            if not candidate.exists():
                # Relocate only an exact old data-relative suffix, never guess a dataset.
                parts = candidate.parts
                data_indices = [i for i, part in enumerate(parts) if part.lower() == 'data']
                if data_indices:
                    relocated = Path(cfg['data_folder']).joinpath(*parts[data_indices[-1]+1:])
                    if relocated.is_file():
                        candidate = relocated
            p[k] = str(candidate)
    return p

def _remember_current_paths(cfg):
    u = cfg.get("active_universe","CSI300")
    if u not in UNIVERSES:
        u = "CSI300"
    allp = dict(cfg.get("universe_file_paths") or {})
    cur = dict(allp.get(u) or {})
    for k in FILE_KEYS:
        if cfg.get(k):
            cur[k] = cfg[k]
    allp[u] = cur
    cfg["universe_file_paths"] = allp
    return cfg

def set_active_universe(u):
    if u not in UNIVERSES:
        raise ValueError("Unknown universe")
    cfg = load_config()
    cfg = _remember_current_paths(cfg)
    cfg["active_universe"] = u
    p = _stored_universe_paths(cfg,u)
    for k in FILE_KEYS:
        cfg[k] = p[k]
    save_config(cfg)
    return {"universe":u,"label":UNIVERSES[u]["label"],"paths":p}

def universe_status():
    cfg = load_config()
    cfg = _remember_current_paths(cfg)
    out = {}
    for u,m in UNIVERSES.items():
        p = _stored_universe_paths(cfg,u)
        out[u] = {"label":m["label"],"paths":p}
        for k in ("raw_price_csv","adjusted_price_csv","weights_csv"):
            f = Path(p[k])
            out[u][k] = {"exists":f.exists(),"path":str(f),"size":f.stat().st_size if f.exists() else 0}
    return out

def selected_universe_paths(cfg=None, u=None):
    cfg = cfg or load_config()
    u = u or cfg.get("active_universe","CSI300")
    return _stored_universe_paths(cfg,u)

def fetch_stock_basic_all():
    parts = [tushare_call("stock_basic",{"exchange":"","list_status":status},
                        "ts_code,symbol,name,market,exchange,list_date") for status in ("L", "D", "P")]
    return pd.concat(parts, ignore_index=True).drop_duplicates('ts_code')

def board_codes(universe):
    b = fetch_stock_basic_all()
    code = b["ts_code"].astype(str)
    market = b["market"].astype(str) if "market" in b else pd.Series("",index=b.index)
    if universe == "CHINEXT_TOP100":
        mask = code.str.match(r"30\d{4}\.SZ$") | market.str.contains("创业板",na=False)
    elif universe == "STAR_TOP100":
        mask = code.str.match(r"688\d{3}\.SH$") | market.str.contains("科创板",na=False)
    else:
        raise ValueError("Top100 board builder supports ChiNext or STAR only.")
    return set(b.loc[mask,"ts_code"].astype(str))

def open_trade_dates(start_date,end_date):
    cal = tushare_call("trade_cal",
        {"exchange":"SSE","start_date":pd.Timestamp(start_date).strftime("%Y%m%d"),
         "end_date":pd.Timestamp(end_date).strftime("%Y%m%d"),"is_open":"1"},
        "cal_date,is_open")
    d = pd.to_datetime(cal["cal_date"],format="%Y%m%d",errors="coerce").dropna().sort_values()
    return pd.DatetimeIndex(d)

def month_end_trade_dates(start_date,end_date):
    d = open_trade_dates(start_date,end_date)
    if len(d)==0:
        return []
    s = pd.Series(d,index=d)
    return list(s.groupby([d.year,d.month]).max().sort_values())

def build_top100_membership(universe,start_date,end_date,output):
    """Monthly point-in-time Top100 snapshots by total market value."""
    eligible = board_codes(universe)
    dates = month_end_trade_dates(start_date,end_date)
    total = len(dates)
    rows = []
    done = completed_checkpoint(f"{universe}_membership_dates")
    job_update(stage=UNIVERSES[universe]["label"]+" membership",current=0,total=total,
               message="Building month-end point-in-time Top100 by market cap...")
    for i,d in enumerate(dates,1):
        job_check_cancel()
        ds = pd.Timestamp(d).strftime("%Y%m%d")
        if ds in done:
            job_update(current=i,total=total,message=f"Membership snapshot {i}/{total} (checkpoint)")
            continue
        x = tushare_call("daily_basic",{"trade_date":ds},
                         "ts_code,trade_date,total_mv,circ_mv")
        if len(x):
            x = x[x["ts_code"].astype(str).isin(eligible)].copy()
            x["total_mv"] = pd.to_numeric(x["total_mv"],errors="coerce")
            x = x.dropna(subset=["total_mv"]).nlargest(100,"total_mv")
            x["con_code"] = x["ts_code"].astype(str)
            x["weight"] = pd.NA
            append_dedupe(output,x[["trade_date","con_code","weight","total_mv"]],
                          ["trade_date","con_code"], replace_snapshots=True)
        mark_checkpoint(f"{universe}_membership_dates",ds)
        job_update(current=i,total=total,message=f"Membership snapshot {i}/{total} · {ds}")
    out = read_csv(output) if Path(output).exists() else pd.DataFrame()
    return {"rows":int(len(out)),
            "snapshots":int(out["trade_date"].nunique()) if len(out) else 0,
            "output":str(output)}

def fetch_etf_basic_history():
    parts=[]
    for st in ("L","D"):
        try:
            x=tushare_call("etf_basic",{"list_status":st},
                           "ts_code,csname,extname,cname,index_code,index_name,list_date,list_status,exchange,etf_type")
            if len(x): parts.append(x)
        except Exception:
            if st=="L":
                raise
    if not parts:
        raise RuntimeError("No ETF basic data returned.")
    x=pd.concat(parts,ignore_index=True).drop_duplicates("ts_code",keep="first")
    txt=(x.get("csname",pd.Series("",index=x.index)).fillna("").astype(str)+" "+
         x.get("extname",pd.Series("",index=x.index)).fillna("").astype(str)+" "+
         x.get("cname",pd.Series("",index=x.index)).fillna("").astype(str))
    # Exclude cash/money-market-like products from momentum universe.
    bad=txt.str.contains("货币|现金|理财|保证金",regex=True,na=False)
    return x[~bad].copy()



def fetch_equity_etf_codes():
    """Return ETF codes classified as equity funds by Tushare fund_basic.

    etf_basic.etf_type describes the investment channel (domestic/QDII), not the
    asset class, so fund_basic.fund_type is used for the equity/bond distinction.
    """
    parts=[]
    for st in ("L","D"):
        try:
            x=tushare_call("fund_basic",{"market":"E","status":st},
                           "ts_code,name,fund_type,status,market")
            if len(x): parts.append(x)
        except Exception:
            if st=="L": raise
    if not parts:
        raise RuntimeError("No exchange-traded fund classification data returned from Tushare fund_basic.")
    f=pd.concat(parts,ignore_index=True).drop_duplicates("ts_code",keep="first")
    f["fund_type"]=f.get("fund_type",pd.Series("",index=f.index)).fillna("").astype(str)
    f["name"]=f.get("name",pd.Series("",index=f.index)).fillna("").astype(str)
    # Primary rule: Tushare fund_type must identify the product as equity/stock.
    equity=f["fund_type"].str.contains("股票|equity|stock",case=False,regex=True,na=False)
    # Defensive exclusions for mixed metadata / names.
    bad=(f["fund_type"]+" "+f["name"]).str.contains(
        "债券|货币|现金|商品|黄金|白银|原油|豆粕|有色|REIT|理财|保证金|bond|money|commodity|gold",
        case=False,regex=True,na=False)
    f=f[equity & ~bad].copy()
    return set(f["ts_code"].astype(str)), f

def filter_equity_etf_weights(weights):
    """Filter an existing ETF eligibility file to equity ETFs without rebuilding price history."""
    codes,_=fetch_equity_etf_codes()
    w=weights.copy()
    code_col="con_code" if "con_code" in w.columns else "ts_code"
    before=len(w)
    w=w[w[code_col].astype(str).isin(codes)].copy()
    if w.empty:
        raise RuntimeError("ETF equity filter removed all rows. Check Tushare fund_basic permission/data.")
    w.attrs["equity_filter_before_rows"]=before
    w.attrs["equity_filter_after_rows"]=len(w)
    return w

def build_etf_eligibility(start_date,end_date,output,min_cap_rmb=250_000_000):
    """
    Monthly point-in-time eligibility using Tushare etf_share_size.total_size.
    total_size is in RMB 10,000; RMB 250m == 25,000 (10k RMB units).
    """
    basic=fetch_etf_basic_history()
    allowed=set(basic["ts_code"].astype(str))
    equity_codes,_equity_meta=fetch_equity_etf_codes()
    allowed &= equity_codes
    dates=month_end_trade_dates(start_date,end_date)
    threshold_10k=float(min_cap_rmb)/10000.0
    total=len(dates)
    done=completed_checkpoint("ETF_250M_eligibility_dates")
    job_update(stage="ETF ≥ RMB 250M eligibility",current=0,total=total,
               message="Building month-end ETF eligibility from historical fund size...")
    for i,d in enumerate(dates,1):
        job_check_cancel()
        ds=pd.Timestamp(d).strftime("%Y%m%d")
        if ds in done:
            job_update(current=i,total=total,message=f"ETF eligibility {i}/{total} (checkpoint)")
            continue
        parts=[]
        for exch in ("SSE","SZSE"):
            x=tushare_call("etf_share_size",{"trade_date":ds,"exchange":exch},
                           "trade_date,ts_code,etf_name,total_share,total_size,nav,close,exchange")
            if len(x): parts.append(x)
        if parts:
            x=pd.concat(parts,ignore_index=True)
            x=x[x["ts_code"].astype(str).isin(allowed)].copy()
            x["total_size"]=pd.to_numeric(x["total_size"],errors="coerce")
            x=x[x["total_size"]>=threshold_10k].copy()
            x["con_code"]=x["ts_code"].astype(str)
            x["weight"]=pd.NA
            x["market_cap_rmb"]=x["total_size"]*10000.0
            append_dedupe(output,x[["trade_date","con_code","weight","market_cap_rmb","total_size"]],
                          ["trade_date","con_code"], replace_snapshots=True)
        mark_checkpoint("ETF_250M_eligibility_dates",ds)
        job_update(current=i,total=total,message=f"ETF eligibility {i}/{total} · {ds}")
    out=read_csv(output) if Path(output).exists() else pd.DataFrame()
    return {"rows":int(len(out)),
            "snapshots":int(out["trade_date"].nunique()) if len(out) else 0,
            "etfs":int(out["con_code"].nunique()) if len(out) else 0,
            "threshold_rmb":float(min_cap_rmb),"output":str(output)}

def fetch_etf_raw_prices(tickers,start_date,end_date,output):
    fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
    return fetch_stock_history("fund_daily",tickers,start_date,end_date,fields,output,
                               ["ts_code","trade_date"],"etf_raw_prices","ETF daily prices")

def fetch_etf_adj_factors(tickers,start_date,end_date,output):
    return fetch_stock_history("fund_adj",tickers,start_date,end_date,
                               "ts_code,trade_date,adj_factor",output,
                               ["ts_code","trade_date"],"etf_adj_factors","ETF adjustment factors")

# -------------------- DATA STATUS --------------------

def inspect_csv(path, kind):
    info = {"path":path or "", "exists":False, "rows":0, "start":None, "end":None, "extra":""}
    if not path or not Path(path).exists():
        return info
    info["exists"] = True
    try:
        df = pd.read_csv(path, low_memory=False)
        info["rows"] = int(len(df))
        if "trade_date" in df.columns:
            d = parse_dates(df["trade_date"])
            if d.notna().any():
                info["start"] = d.min().strftime("%Y-%m-%d")
                info["end"] = d.max().strftime("%Y-%m-%d")
        if kind == "weights" and "trade_date" in df.columns:
            info["extra"] = f"{df['trade_date'].nunique()} snapshots"
        elif "ts_code" in df.columns:
            info["extra"] = f"{df['ts_code'].nunique()} stocks"
    except Exception as e:
        info["extra"] = f"Read error: {e}"
    return info

def data_status():
    cfg = load_config()
    return {
        "raw":inspect_csv(cfg.get("raw_price_csv"),"raw"),
        "adjusted":inspect_csv(cfg.get("adjusted_price_csv"),"adjusted"),
        "weights":inspect_csv(cfg.get("weights_csv"),"weights"),
        "factors":inspect_csv(cfg.get("adj_factor_csv"),"factors"),
        "calendar":inspect_csv(cfg.get("trade_calendar_csv"),"calendar"),
        "limits":inspect_csv(cfg.get("price_limits_csv"),"limits")
    }

def validate_research_files(universe=None):
    cfg = load_config()
    universe = universe or cfg.get("active_universe","CSI300")
    if universe not in UNIVERSES:
        return {"ok":False,"errors":["Unknown research universe."]}
    paths = selected_universe_paths(cfg, universe)
    errors=[]
    for key,label,required in [
        ("adjusted_price_csv","Adjusted price CSV",{"ts_code","trade_date","adj_close"}),
        ("weights_csv","Membership / eligibility CSV",{"trade_date","con_code","weight"})
    ]:
        p = paths.get(key,"")
        if not p:
            errors.append(f"{UNIVERSES[universe]['label']} {label}: no file selected.")
            continue
        if not Path(p).exists():
            errors.append(f"{UNIVERSES[universe]['label']} {label}: file not found: {p}")
            continue
        try:
            frame = pd.read_csv(p, low_memory=False)
            cols = set(frame.columns)
            missing = required-cols
            if missing:
                errors.append(f"{UNIVERSES[universe]['label']} {label}: missing columns {sorted(missing)}")
                continue
            code = 'ts_code' if key == 'adjusted_price_csv' else 'con_code'
            dates = parse_dates(frame['trade_date'])
            normalized = pd.DataFrame({'date': dates, 'code': frame[code].astype('string').str.strip()})
            if frame.empty or dates.isna().any() or normalized['code'].isna().any() or normalized['code'].eq('').any():
                errors.append(f'{label}: empty data or invalid dates/security codes.')
            if normalized.duplicated().any():
                errors.append(f'{label}: duplicate date/security keys.')
            if key == 'adjusted_price_csv':
                prices = pd.to_numeric(frame['adj_close'], errors='coerce')
                if (~np.isfinite(prices) | (prices <= 0)).any():
                    errors.append(f'{label}: adjusted close must be finite and positive.')
        except Exception as e:
            errors.append(f"{UNIVERSES[universe]['label']} {label}: cannot read file ({e})")
    return {"ok":not errors,"errors":errors,"universe":universe,"paths":paths}


def research_notes(paths):
    notes = [
        'Research execution: signal close → next dataset observation adjusted close; equal-weight rebalances; fractional shares.',
        'Missing valuation prices are carried forward; exchange calendar, suspension exit restrictions, price limits and liquidity are not enforced.',
        'Equity is normalized to first post-entry NAV: initial entry cost is excluded from reported performance.',
        'Sharpe label denotes CAGR / annualized volatility (rf=0), not mean excess return / volatility; annualization uses 252 observations.'
    ]
    factor_path = paths.get('adj_factor_csv')
    if not factor_path or not Path(factor_path).exists():
        notes.append('Historical adjustment-factor evidence is unavailable; adjusted price provenance is unverified.')
    else:
        factors = read_csv(factor_path)
        prices = pd.read_csv(paths['adjusted_price_csv'], usecols=['ts_code','trade_date'])
        if {'ts_code','trade_date','adj_factor'}.issubset(factors.columns):
            factors = factors[pd.to_numeric(factors['adj_factor'], errors='coerce') > 0].copy()
            for frame in (factors, prices):
                frame['trade_date'] = parse_dates(frame['trade_date'])
                frame['ts_code'] = frame['ts_code'].astype(str).str.strip()
            covered = pd.MultiIndex.from_frame(factors[['ts_code','trade_date']])
            missing = ~pd.MultiIndex.from_frame(prices[['ts_code','trade_date']]).isin(covered)
            if missing.any():
                notes.append(f'DATA QUALITY: {int(missing.sum()):,} adjusted-price rows lack matching historical factor evidence. Re-download factors and rebuild before trusting results.')
        else:
            notes.append('Historical adjustment-factor file has invalid columns; provenance is unverified.')
    return notes

# -------------------- RESEARCH ENGINE --------------------

def calculate_signal(df, lookback):
    x=df.copy()
    x["trade_date"]=parse_dates(x["trade_date"])
    x["ts_code"]=x["ts_code"].astype(str).str.strip()
    x["adj_close"]=pd.to_numeric(x["adj_close"],errors="coerce")
    x=x.dropna(subset=["trade_date","ts_code"]).sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    g=x.groupby("ts_code",group_keys=False)
    x["adj_daily_return_calc"]=g["adj_close"].pct_change(fill_method=None)
    x["signal_return"]=g["adj_close"].pct_change(periods=lookback,fill_method=None)
    x["signal_daily_std"]=(g["adj_daily_return_calc"].rolling(lookback,min_periods=lookback)
                            .std(ddof=1).reset_index(level=0,drop=True))
    x["signal_volatility"]=x["signal_daily_std"]*np.sqrt(lookback)
    x["momentum_score"]=(x["signal_return"]/x["signal_volatility"]).replace([np.inf,-np.inf],np.nan)
    return x

def merge_membership_and_rank(signal,w):
    w=w.copy()
    w["snapshot_date"]=parse_dates(w["trade_date"])
    w["ts_code"]=w["con_code"].astype(str).str.strip()
    w["weight"]=pd.to_numeric(w["weight"],errors="coerce")
    w=w.dropna(subset=["snapshot_date","ts_code"]).sort_values(["snapshot_date","ts_code"]).drop_duplicates(["snapshot_date","ts_code"],keep="last")
    dates=signal[["trade_date"]].drop_duplicates().sort_values("trade_date")
    snaps=w[["snapshot_date"]].drop_duplicates().sort_values("snapshot_date")
    dm=pd.merge_asof(dates,snaps,left_on="trade_date",right_on="snapshot_date",direction="backward")
    members=dm.dropna(subset=["snapshot_date"]).merge(w[["snapshot_date","ts_code","weight"]],on="snapshot_date",how="left")
    members["csi300_member"]=1
    members=members.rename(columns={"weight":"csi300_weight"})
    out=signal.merge(members[["trade_date","ts_code","snapshot_date","csi300_member","csi300_weight"]],on=["trade_date","ts_code"],how="left")
    out["csi300_member"]=out["csi300_member"].fillna(0).astype(int)
    valid=(out["csi300_member"]==1)&out["momentum_score"].notna()
    out["momentum_rank"]=np.nan
    out["momentum_percentile"]=np.nan
    out["quintile"]=np.nan
    out.loc[valid,"momentum_rank"]=out.loc[valid].groupby("trade_date")["momentum_score"].rank(ascending=False,method="first")
    out.loc[valid,"momentum_percentile"]=out.loc[valid].groupby("trade_date")["momentum_score"].rank(ascending=True,pct=True)
    temp=out.loc[valid].copy()
    def q(s):
        if s.notna().sum()<5:
            return pd.Series(np.nan,index=s.index)
        return pd.qcut(s.rank(method="first"),5,labels=[1,2,3,4,5]).astype(float)
    temp["quintile"]=temp.groupby("trade_date",group_keys=False)["momentum_score"].apply(q)
    out.loc[temp.index,"quintile"]=temp["quintile"]
    return out

def select_mask(df,selection):
    if selection.startswith("Top "):
        return df["momentum_rank"]<=int(selection.split()[1])
    return df["quintile"]==int(selection[-1])

def signal_decay_table(ranked,horizons=(1,3,5,10,20,40,60)):
    cal=pd.DatetimeIndex(sorted(ranked["trade_date"].dropna().unique()))
    px=ranked.pivot_table(index="trade_date",columns="ts_code",values="adj_close",aggfunc="last").sort_index()
    # V2 pandas-compatible stack: do not pass dropna=False.
    stacked=px.stack()
    groups=["Top 10","Top 20","Q5","Q4","Q3","Q2","Q1"]
    results=pd.DataFrame(index=groups,columns=[f"{h}D" for h in horizons],dtype=float)
    counts=results.copy()
    base=ranked[(ranked["csi300_member"]==1)&ranked["momentum_rank"].notna()].copy()
    for h in horizons:
        mapping={cal[i]:cal[i+h] for i in range(len(cal)-h)}
        target=base["trade_date"].map(mapping)
        idx=pd.MultiIndex.from_arrays([target,base["ts_code"]],names=["trade_date","ts_code"])
        future=stacked.reindex(idx).to_numpy()
        base[f"_fwd_{h}"]=future/base["adj_close"].to_numpy()-1
    for group in groups:
        sub=base[select_mask(base,group)]
        for h in horizons:
            daily=sub.groupby("trade_date")[f"_fwd_{h}"].mean()
            results.loc[group,f"{h}D"]=daily.mean()
            counts.loc[group,f"{h}D"]=daily.notna().sum()
    return results,counts

def save_heatmap(table,path,title,ylabel="Momentum group"):
    vals=table.to_numpy(dtype=float)*100
    fig_h=max(4.5, 1.0 + 0.72*max(len(table.index),1))
    fig,ax=plt.subplots(figsize=(10,fig_h))
    im=ax.imshow(vals,aspect="auto")
    ax.set_xticks(range(len(table.columns)),table.columns)
    ax.set_yticks(range(len(table.index)),table.index)
    ax.set_title(title)
    ax.set_xlabel("Forward trading-day horizon")
    ax.set_ylabel(ylabel)
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            if np.isfinite(vals[i,j]):
                ax.text(j,i,f"{vals[i,j]:.2f}%",ha="center",va="center",fontsize=8)
    fig.colorbar(im,ax=ax,label="Average forward return (%)")
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(path,dpi=160,bbox_inches="tight")
    plt.close(fig)

def selection_forward_returns(ranked, selection, horizons=(1,3,5,10,20,40,60)):
    """Average cross-sectional forward return of the selected portfolio on each signal date.

    This is deliberately a signal-diagnostic calculation, not a portfolio backtest:
    each horizon asks what happened after the ranking date without imposing a rebalance path.
    """
    cal=pd.DatetimeIndex(sorted(ranked["trade_date"].dropna().unique()))
    px=ranked.pivot_table(index="trade_date",columns="ts_code",values="adj_close",aggfunc="last").sort_index()
    stacked=px.stack()
    base=ranked[(ranked["csi300_member"]==1)&ranked["momentum_rank"].notna()].copy()
    base=base[select_mask(base,selection)].copy()
    values={}
    counts={}
    for h in horizons:
        mapping={cal[i]:cal[i+h] for i in range(len(cal)-h)}
        target=base["trade_date"].map(mapping)
        idx=pd.MultiIndex.from_arrays([target,base["ts_code"]],names=["trade_date","ts_code"])
        future=stacked.reindex(idx).to_numpy()
        fwd=future/base["adj_close"].to_numpy()-1
        temp=pd.DataFrame({"trade_date":base["trade_date"].to_numpy(),"fwd":fwd})
        daily=temp.groupby("trade_date")["fwd"].mean()
        values[f"+{h}D"]=float(daily.mean()) if daily.notna().any() else np.nan
        counts[f"+{h}D"]=int(daily.notna().sum())
    return values,counts

def lookback_comparison(prices, weights, spans, selection, start=None, end=None, horizons=(1,3,5,10,20,40,60)):
    rows=[]
    count_rows=[]
    for span in spans:
        ranked=merge_membership_and_rank(calculate_signal(prices,int(span)),weights)
        subset=ranked
        if start: subset=subset[subset["trade_date"]>=pd.Timestamp(start)]
        if end: subset=subset[subset["trade_date"]<=pd.Timestamp(end)]
        vals,cts=selection_forward_returns(subset,selection,horizons=horizons)
        rows.append(pd.Series(vals,name=f"MOM{int(span)}"))
        count_rows.append(pd.Series(cts,name=f"MOM{int(span)}"))
    if not rows:
        return pd.DataFrame(),pd.DataFrame()
    return pd.DataFrame(rows),pd.DataFrame(count_rows)

def backtest(ranked,selection,rebalance_days,start_date=None,end_date=None,cost_bps=0):
    d=ranked.sort_values(["trade_date","ts_code"]).copy()
    cal=pd.DatetimeIndex(sorted(d["trade_date"].dropna().unique()))
    px_raw=d.pivot_table(index="trade_date",columns="ts_code",values="adj_close",aggfunc="last").sort_index()
    px_val=px_raw.ffill()

    valid_dates=d.loc[(d["csi300_member"]==1)&d["momentum_rank"].notna(),"trade_date"]
    if valid_dates.empty:
        raise ValueError("No valid ranking dates. Check historical membership / eligibility coverage.")
    first=valid_dates.min()
    last=valid_dates.max()
    if start_date: first=max(first,pd.Timestamp(start_date))
    if end_date: last=min(last,pd.Timestamp(end_date))
    candidates=cal[(cal>=first)&(cal<=last)]
    if len(candidates)<2:
        raise ValueError("Backtest range is too short.")
    first_idx=cal.get_loc(candidates[0])
    sig_idx=[i for i in range(first_idx,len(cal)-1,int(rebalance_days)) if cal[i]<=last]
    sig_to_entry={cal[i+1]:cal[i] for i in sig_idx}

    cash=1.0
    shares={}
    nav=[]
    logs=[]

    def value(date):
        v=cash
        for t,sh in shares.items():
            if t in px_val.columns:
                p=px_val.at[date,t]
                if pd.notna(p): v+=sh*p
        return float(v)

    for di in range(sig_idx[0]+1,len(cal)):
        date=cal[di]
        if end_date and date>pd.Timestamp(end_date):
            break
        before=value(date)
        if date in sig_to_entry:
            sig=sig_to_entry[date]
            today=d[(d["trade_date"]==sig)&(d["csi300_member"]==1)&d["momentum_rank"].notna()].copy()
            chosen=today[select_mask(today,selection)].copy()
            if selection.startswith("Top "): chosen=chosen.sort_values("momentum_rank")
            tickers=[t for t in chosen["ts_code"] if t in px_raw.columns and pd.notna(px_raw.at[date,t])]
            if tickers:
                current={}
                for t,sh in shares.items():
                    p=px_val.at[date,t] if t in px_val.columns else np.nan
                    current[t]=sh*p if pd.notna(p) else 0.0
                target_each=before/len(tickers)
                traded=sum(abs((target_each if t in tickers else 0)-current.get(t,0)) for t in set(current)|set(tickers))
                cost=traded*(float(cost_bps)/10000.0)
                after=max(before-cost,0)
                each=after/len(tickers)
                new={}
                for t in tickers:
                    p=px_raw.at[date,t]
                    if p>0: new[t]=each/p
                shares=new
                cash=after-sum(new[t]*px_raw.at[date,t] for t in new)
                logs.append({"signal_date":sig.strftime("%Y-%m-%d"),"entry_date":date.strftime("%Y-%m-%d"),
                             "names":len(tickers),"one_way_turnover":0.5*traded/before if before>0 else np.nan,
                             "cost":cost,"tickers":",".join(tickers)})
        nav.append((date,value(date)))
        if end_date and date>=pd.Timestamp(end_date):
            break

    s=pd.Series([v for _,v in nav],index=pd.DatetimeIndex([d for d,_ in nav]),name="nav")
    eq=s/s.iloc[0]
    rets=s.pct_change(fill_method=None).fillna(0)
    dd=eq/eq.cummax()-1
    ann_ret=eq.iloc[-1]**(252/max(len(eq)-1,1))-1
    ann_vol=rets.std(ddof=1)*np.sqrt(252)
    stats={
        "total_return":float(eq.iloc[-1]-1),
        "annualized_return":float(ann_ret),
        "annualized_volatility":float(ann_vol),
        "sharpe_rf0":float(ann_ret/ann_vol) if ann_vol>0 else None,
        "max_drawdown":float(dd.min()),
        "rebalances":len(logs),
        "backtest_start":eq.index.min().strftime("%Y-%m-%d"),
        "backtest_end":eq.index.max().strftime("%Y-%m-%d"),
        "observations":int(len(eq))
    }
    return eq,dd,pd.DataFrame(logs),stats

def save_line(series,path,title,ylabel):
    fig,ax=plt.subplots(figsize=(10,5))
    ax.plot(series.index,series.values)
    ax.set_title(title); ax.set_xlabel("Date"); ax.set_ylabel(ylabel); ax.grid(True,alpha=.25)
    fig.tight_layout(); fig.savefig(path,dpi=160,bbox_inches="tight"); plt.close(fig)

def run_strategy_grid(params):
    """Run a robustness grid across calendar years, momentum lookbacks and rebalance periods.

    Signals are computed once per lookback from the full local history so each calendar-year
    test has the necessary warm-up observations. Portfolio returns are then hard-clipped to
    the requested calendar year. This is a parameter-robustness tool, not an optimizer.
    """
    cfg=load_config()
    universe=params.get("universe") or cfg.get("active_universe","CSI300")
    if universe not in UNIVERSES:
        raise ValueError("Unknown research universe.")
    paths=selected_universe_paths(cfg, universe)
    valid=validate_research_files(universe)
    if not valid["ok"]:
        raise ValueError("Research data is not ready:\n- " + "\n- ".join(valid["errors"]))

    prices=read_csv(paths["adjusted_price_csv"])
    weights=read_csv(paths["weights_csv"])
    if universe=="ETF_250M":
        weights=filter_equity_etf_weights(weights)

    selections=params.get("selections") or [params.get("selection_rule","Top 10")]
    selections=[str(s) for s in selections if s]
    allowed_selections = {f'Top {n}' for n in (5,10,20,30,50)} | {f'Q{n}' for n in range(1,6)}
    if not selections or any(s not in allowed_selections for s in selections):
        raise ValueError('Choose valid Strategy Grid portfolio selections.')
    selections = list(dict.fromkeys(selections))
    cost=float(params.get("trading_cost_bps",0) or 0)
    lookbacks=params.get("lookbacks",[10,20,40,60,120])
    rebalances=params.get("rebalances",[5,10,20,40,60])
    lookbacks=sorted({int(x) for x in lookbacks if int(x)>1})
    rebalances=sorted({int(x) for x in rebalances if int(x)>0})
    if not lookbacks:
        raise ValueError("Choose at least one MOM lookback for Strategy Grid.")
    if not rebalances:
        raise ValueError("Choose at least one rebalance period for Strategy Grid.")

    # Determine usable dataset calendar years from adjusted prices.
    price_dates=parse_dates(prices["trade_date"]).dropna()
    if price_dates.empty:
        raise ValueError("Adjusted price data has no usable trade dates.")
    data_start=price_dates.min().normalize()
    data_end=price_dates.max().normalize()
    available_years=list(range(int(data_start.year), int(data_end.year)+1))

    requested_years=params.get("years")
    if requested_years:
        years=[]
        for y in requested_years:
            try:
                yi=int(y)
            except Exception:
                continue
            if yi in available_years:
                years.append(yi)
        years=sorted(set(years))
    else:
        years=available_years
    if not years:
        raise ValueError("No selected calendar years overlap the dataset.")

    include_full=bool(params.get("include_full",True))
    ranked_by_lb={}
    for lb in lookbacks:
        ranked_by_lb[lb]=merge_membership_and_rank(calculate_signal(prices,lb),weights)

    rows=[]
    def add_period(label,start,end):
        for lb in lookbacks:
            ranked=ranked_by_lb[lb]
            for rb in rebalances:
                for selection_rule in selections:
                    try:
                        _,_,log,stats=backtest(ranked,selection_rule,rb,start,end,cost)
                        rows.append({
                            "period":label,"lookback":lb,"rebalance_days":rb,
                            "selection":selection_rule,"trading_cost_bps":cost,
                            "requested_start":start.strftime('%Y-%m-%d'),"requested_end":end.strftime('%Y-%m-%d'),
                            "total_return":stats.get("total_return"),
                            "annualized_return":stats.get("annualized_return"),
                            "annualized_volatility":stats.get("annualized_volatility"),
                            "sharpe_rf0":stats.get("sharpe_rf0"),
                            "max_drawdown":stats.get("max_drawdown"),
                            "rebalances":stats.get("rebalances"),
                            "backtest_start":stats.get("backtest_start"),
                            "backtest_end":stats.get("backtest_end"),
                            "observations":stats.get("observations")
                        })
                    except Exception as e:
                        rows.append({
                            "period":label,"lookback":lb,"rebalance_days":rb,
                            "selection":selection_rule,"trading_cost_bps":cost,
                            "error":str(e)
                        })

    for y in years:
        start=pd.Timestamp(year=y,month=1,day=1)
        end=min(pd.Timestamp(year=y,month=12,day=31),data_end)
        add_period(str(y),start,end)
    if include_full:
        add_period("Full Period",data_start,data_end)

    out=Path(cfg["results_folder"])/"backtests"/f"{universe}_strategy_grid_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out.mkdir(parents=True,exist_ok=True)
    df=pd.DataFrame(rows)
    csv_path=out/"strategy_grid_results.csv"
    df.to_csv(csv_path,index=False,encoding="utf-8-sig")
    weights.to_csv(out/'membership_used.csv',index=False,encoding='utf-8-sig')

    result = {
        "ok":True,
        "version":APP_VERSION,"build_tag":BUILD_TAG,
        "universe":universe,
        "universe_label":UNIVERSES[universe]["label"],
        "selection": selections,
        "selection_rules": selections,
        "trading_cost_bps":cost,
        "lookbacks":lookbacks,
        "rebalances":rebalances,
        "periods":[str(y) for y in years]+(["Full Period"] if include_full else []),
        "dataset_start":data_start.strftime("%Y-%m-%d"),
        "dataset_end":data_end.strftime("%Y-%m-%d"),
        "rows":rows,
        "files":{"folder":str(out),"csv":str(csv_path)}
    }
    result['notes'] = research_notes(paths) + ['Calendar-year portfolios and rebalance schedules restart independently. First/last years may be partial; Full Period includes all dataset years, independent of selected years.']
    result['failed_combinations'] = sum('error' in row for row in rows)
    (out/'run_metadata.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result

def run_research(params):
    cfg=load_config()
    universe=params.get("universe") or cfg.get("active_universe","CSI300")
    if universe not in UNIVERSES:
        raise ValueError("Unknown research universe.")

    # IMPORTANT: research uses the universe explicitly supplied by the UI.
    # It does not rely on stale top-level file paths from a previously active universe.
    paths=selected_universe_paths(cfg, universe)
    valid=validate_research_files(universe)
    if not valid["ok"]:
        raise ValueError("Research data is not ready:\n- " + "\n- ".join(valid["errors"]))
    if cfg.get("active_universe") != universe:
        set_active_universe(universe)
        cfg=load_config()

    prices=read_csv(paths["adjusted_price_csv"])
    weights=read_csv(paths["weights_csv"])
    # V3.2.2: ETF research is equity-only by default. Existing V3.1/V3.2
    # eligibility files remain reusable; classification is applied at research time.
    if universe=="ETF_250M":
        weights=filter_equity_etf_weights(weights)
    lookback=int(params.get("lookback",20))
    display_spans=params.get("display_spans",[10,20,40,60])
    if isinstance(display_spans, str):
        display_spans=[int(x.strip()) for x in display_spans.split(",") if x.strip()]
    display_spans=sorted({int(x) for x in display_spans if int(x)>1})
    if not display_spans:
        raise ValueError("Choose at least one MOM lookback to compare.")
    selection=params.get("selection","Top 10")
    rebalance=int(params.get("rebalance_days",10))
    start=params.get("start_date") or None
    end=params.get("end_date") or None
    if start:
        start=pd.Timestamp(start).normalize()
    if end:
        end=pd.Timestamp(end).normalize()
    if start is not None and end is not None and start > end:
        raise ValueError("Research start date is after end date.")
    cost=float(params.get("trading_cost_bps",0) or 0)

    signal=calculate_signal(prices,lookback)
    ranked=merge_membership_and_rank(signal,weights)

    subset=ranked.copy()
    if start: subset=subset[subset["trade_date"]>=pd.Timestamp(start)]
    if end: subset=subset[subset["trade_date"]<=pd.Timestamp(end)]
    decay,counts=signal_decay_table(subset)

    out=Path(cfg["results_folder"])/"backtests"/f"{universe}_MOM{lookback}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out.mkdir(parents=True,exist_ok=True)
    heat=out/"signal_decay.png"
    weights.to_csv(out/'membership_used.csv',index=False,encoding='utf-8-sig')
    date_label = f"{subset['trade_date'].min():%Y-%m-%d} → {subset['trade_date'].max():%Y-%m-%d}"
    diagnostic_label = f'{date_label} | signal-close forward returns, gross, no rebalance/cost model'
    save_heatmap(decay,heat,f"{UNIVERSES[universe]['label']} MOM{lookback} Rank / Signal Decay\n{diagnostic_label}",ylabel="Momentum rank group (Q5 strongest)")
    decay.to_csv(out/"signal_decay_returns.csv")
    counts.to_csv(out/"signal_decay_counts.csv")

    # V3.2: compare the checked lookback spans on the SAME universe, selection and date range.
    lookback_table,lookback_counts=lookback_comparison(prices,weights,display_spans,selection,start,end)
    lookback_heat=out/"lookback_comparison.png"
    save_heatmap(lookback_table,lookback_heat,
                 f"{UNIVERSES[universe]['label']} · {selection} · Momentum Lookback Comparison\n{diagnostic_label}",
                 ylabel="Momentum lookback")
    lookback_table.to_csv(out/"lookback_comparison_returns.csv")
    lookback_counts.to_csv(out/"lookback_comparison_counts.csv")

    eq,dd,log,stats=backtest(ranked,selection,rebalance,start,end,cost)
    ep=out/"equity.png"; dp=out/"drawdown.png"
    portfolio_label = f"{UNIVERSES[universe]['label']} | {selection} | MOM{lookback} | {rebalance}D rebalance | {cost:g} bps\n{stats['backtest_start']} → {stats['backtest_end']} | T+1 adjusted close | {stats['observations']} observations"
    save_line(eq,ep,portfolio_label + '\nEquity — normalized to first post-entry NAV',"Growth of 1.0")
    save_line(dd*100,dp,portfolio_label + '\nDrawdown from running equity peak',"Drawdown (%)")
    eq.to_csv(out/"equity.csv"); dd.to_csv(out/"drawdown.csv"); log.to_csv(out/"rebalance_log.csv",index=False)
    pd.DataFrame([stats]).to_csv(out/"summary.csv",index=False)
    notes = research_notes(paths)
    metadata = {'version': APP_VERSION, 'build_tag': BUILD_TAG, 'universe': universe,
                'lookback': lookback, 'selection': selection, 'rebalance_days': rebalance,
                'trading_cost_bps': cost, 'diagnostic_range': date_label, 'stats': stats,
                'comparison_spans': display_spans, 'notes': notes}
    (out/'run_metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')

    member=ranked[(ranked["csi300_member"]==1)&ranked["momentum_rank"].notna()]
    if member.empty:
        raise ValueError("No usable membership/ranking dates for this universe.")
    dataset_start=member["trade_date"].min()
    dataset_end=member["trade_date"].max()
    decay_json={g:{c:(None if pd.isna(decay.loc[g,c]) else float(decay.loc[g,c])) for c in decay.columns} for g in decay.index}
    lookback_json={g:{c:(None if pd.isna(lookback_table.loc[g,c]) else float(lookback_table.loc[g,c])) for c in lookback_table.columns} for g in lookback_table.index}
    # The latest monitor always needs the primary span to rank the table, even when the user
    # intentionally leaves that span unchecked in the comparison heatmap.
    monitor_spans=sorted(set(display_spans+[lookback]))
    top20 = multi_span_monitor(prices, weights, monitor_spans, lookback, end_date=end, top_n=20)
    return {"ok":True,
            "notes":notes,"diagnostic_label":diagnostic_label,"portfolio_label":portfolio_label,
            "run":{"universe":universe,"universe_label":UNIVERSES[universe]["label"],
                   "lookback":lookback,"selection":selection,"rebalance_days":rebalance,
                   "trading_cost_bps":cost,
                   "etf_asset_filter":("Equity only" if universe=="ETF_250M" else None)},
            "stats":stats,
            "usable_start":dataset_start.strftime("%Y-%m-%d"),
            "usable_end":dataset_end.strftime("%Y-%m-%d"),
            "requested_start":(start.strftime("%Y-%m-%d") if start is not None else None),
            "requested_end":(end.strftime("%Y-%m-%d") if end is not None else None),
            "backtest_start":stats.get("backtest_start"),
            "backtest_end":stats.get("backtest_end"),
            "snapshots":int(pd.to_datetime(ranked["snapshot_date"],errors="coerce").nunique()),
            "decay":decay_json,
            "lookback_comparison":lookback_json,
            "comparison_spans":display_spans,
            "comparison_counts":lookback_counts.to_dict(orient='index'),
            "decay_counts":counts.to_dict(orient='index'),
            "top20":top20,
            "files":{"folder":str(out),"heatmap":str(heat),"lookback_heatmap":str(lookback_heat),
                     "lookback_csv":str(out/"lookback_comparison_returns.csv"),
                     "equity":str(ep),"drawdown":str(dp),"summary":str(out/"summary.csv")}}

# -------------------- HTTP --------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def trusted_request(self):
        expected = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        valid = self.headers.get('Host') in expected and (not origin or origin in {'http://' + h for h in expected})
        if not valid:
            self.json({'ok':False,'error':'Only same-origin localhost requests are allowed.'},403)
        return valid

    def json(self,obj,status=200):
        b=json.dumps(obj,ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if not self.trusted_request(): return
        u=urlparse(self.path)
        if u.path=="/":
            try:
                if not UI_FILE.exists():
                    raise FileNotFoundError(
                        f"UI resource not found: {UI_FILE}. "
                        f"Executable root={ROOT}; resource root={RESOURCE_ROOT}"
                    )
                b=UI_FILE.read_bytes()
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b); return
            except Exception as e:
                write_log("GET / failed:\n" + traceback.format_exc())
                msg = f"Momentum Lab UI failed to load: {e}"
                b = msg.encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type","text/plain; charset=utf-8")
                self.send_header("Content-Length",str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
        if u.path=="/api/health":
            self.json({"ok":True,"version":APP_VERSION,"ui_exists":UI_FILE.exists(),
                       "build_tag":BUILD_TAG,
                       "app_root":str(ROOT),"resource_root":str(RESOURCE_ROOT)}); return
        if u.path=="/api/config": self.json(load_config()); return
        if u.path=="/api/status": self.json({"ok":True,"status":data_status()}); return
        if u.path=="/api/job-status": self.json({"ok":True,"job":job_snapshot()}); return
        if u.path=="/api/universes": self.json({"ok":True,"active":load_config().get("active_universe","CSI300"),"universes":universe_status()}); return
        if u.path=="/file":
            p=Path(parse_qs(u.query).get("path",[""])[0]).resolve()
            results_root = Path(load_config()['results_folder']).resolve()
            if not p.is_relative_to(results_root) or not p.is_file():
                self.json({'ok':False,'error':'Only result files can be served.'},403); return
            if not p.exists(): self.json({"ok":False,"error":"File not found."},404); return
            b=p.read_bytes(); self.send_response(200)
            self.send_header("Content-Type","image/png" if p.suffix.lower()==".png" else "application/octet-stream")
            self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b); return
        self.json({"ok":False,"error":"Not found."},404)

    def do_POST(self):
        if not self.trusted_request(): return
        # Serialize HTTP mutations and matplotlib use. Data downloads retain their worker.
        with _REQUEST_LOCK:
            path = urlparse(self.path).path
            if job_snapshot()['status'] in ('running','cancelling') and path not in {
                '/api/job-cancel','/api/job-reset','/api/browse-file','/api/browse-folder'}:
                self.json({'ok':False,'error':'Wait for or cancel the active data job before changing settings, importing, or running research.'},409)
                return
            self.handle_post()

    def handle_post(self):
        try:
            n=int(self.headers.get("Content-Length","0"))
            u=urlparse(self.path)

            if u.path=="/api/import-csv":
                q=parse_qs(u.query)
                field=q.get("field",[""])[0]
                filename=q.get("filename",[""])[0]
                content=self.rfile.read(n)
                path=import_uploaded_csv(field,filename,content)
                self.json({"ok":True,"path":path})
                return

            body=json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if u.path=="/api/config":
                cfg=load_config()
                for k in DEFAULTS:
                    if k in body: cfg[k]=body[k]
                cfg=_remember_current_paths(cfg)
                save_config(cfg); self.json({"ok":True}); return
            if u.path=="/api/browse-file":
                self.json({"ok":True,"path":browse_file(body.get("initial",""),body.get("title","Select CSV file"))}); return
            if u.path=="/api/browse-folder":
                self.json({"ok":True,"path":browse_folder(body.get("initial",""))}); return
            if u.path=="/api/test-tushare":
                self.json(test_tushare()); return
            if u.path=="/api/full-build":
                self.json({"ok":True,"job":start_data_job("full_build",body)}); return
            if u.path=="/api/refresh":
                self.json({"ok":True,"job":start_data_job("refresh",body)}); return
            if u.path=="/api/job-cancel":
                self.json(cancel_data_job()); return
            if u.path=="/api/job-resume":
                self.json({"ok":True,"job":resume_data_job()}); return
            if u.path=="/api/job-reset":
                self.json(reset_stale_job_state()); return
            if u.path=="/api/set-universe":
                self.json({"ok":True,**set_active_universe(body.get("universe","CSI300"))}); return
            if u.path=="/api/build-adjusted":
                cfg=load_config()
                r=build_adjusted_prices(cfg["raw_price_csv"],cfg["adj_factor_csv"],cfg["adjusted_price_csv"])
                self.json({"ok":True,**r}); return
            if u.path=="/api/validate":
                self.json(validate_research_files(load_config().get("active_universe","CSI300"))); return
            if u.path=="/api/run":
                self.json(run_research(body)); return
            if u.path=="/api/strategy-grid":
                self.json(run_strategy_grid(body)); return
            self.json({"ok":False,"error":"Not found."},404)
        except Exception as e:
            write_log("POST failure:\n" + traceback.format_exc())
            self.json({"ok":False,"error":f"{type(e).__name__}: {e}"},500)

def wait_until_healthy(port, timeout=15):
    deadline = time.time() + timeout
    url = f"http://{HOST}:{port}/api/health"
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data.get("ok"):
                    return data
        except Exception as e:
            last_error = e
        time.sleep(0.25)
    raise RuntimeError(f"Local server did not become healthy within {timeout}s. Last error: {last_error}")

def startup_browser(port):
    try:
        health = wait_until_healthy(port)
        write_log(f"Health check OK on port {port}: {health}")
        webbrowser.open(f"http://{HOST}:{port}/")
    except Exception as e:
        write_log("Startup health check failed:\n" + traceback.format_exc())
        show_windows_error(
            "Momentum Lab V2.4 Startup Error",
            f"Momentum Lab could not start correctly.\n\n{e}\n\n"
            f"Please check: {LOG_DIR / 'momentumlab.log'}"
        )

def main():
    try:
        cfg=load_config(); ensure_dirs(cfg)
        write_log(f"Starting Momentum Lab {APP_VERSION}")
        write_log(f"Executable/app root: {ROOT}")
        write_log(f"Bundled resource root: {RESOURCE_ROOT}")
        write_log(f"UI file: {UI_FILE} (exists={UI_FILE.exists()})")

        if not UI_FILE.exists():
            raise FileNotFoundError(
                f"Bundled UI file is missing: {UI_FILE}. "
                "The Windows build may not have included the ui folder."
            )

        port = find_free_port(PORT)
        write_log(f"Using local port {port}")
        server=ThreadingHTTPServer((HOST,port),Handler)
        threading.Thread(target=startup_browser,args=(port,),daemon=True).start()
        try:
            server.serve_forever()
        finally:
            server.server_close()
    except Exception as e:
        write_log("Fatal startup failure:\n" + traceback.format_exc())
        show_windows_error(
            "Momentum Lab V2.4 Startup Error",
            f"{type(e).__name__}: {e}\n\n"
            f"Error log: {LOG_DIR / 'momentumlab.log'}"
        )
        raise

if __name__=="__main__":
    main()

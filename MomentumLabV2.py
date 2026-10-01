from momentumlab.serialization import dumps as json_dumps
from momentumlab.dates import normalize_dates
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
from momentumlab.turnover import compare_weights, summarize_turnover
from momentumlab.costs import apply_costs
from momentumlab.universe_validation import equity_classification, validate_etf_weights, ETF_LABEL
from momentumlab.live_store import LiveStore
from momentumlab.live_service import LiveService
from momentumlab.live_export import export_portfolio
from momentumlab.reconstruction import research_bundle, compare_strategy_targets
from momentumlab.reconstruction import saved_signal_target
from momentumlab.live_model import select_target_rows, target_snapshot
from momentumlab.research_audit import build_audit, export_audit
from momentumlab.runtime_cache import RuntimeCache, STATUS, file_identity, read_small_or_direct, clear_small_caches
from momentumlab.data_integrity import normalize_keys, duplicate_diagnostics, validate_calendar, validate_dataset, staged_dataset, publish_dataset
from momentumlab.data_integrity import require_valid, provenance, repair_duplicate_keys, recover_publication, DATA_LOCK, audit_event, save_acknowledgement, revoke_acknowledgement, load_acknowledgements
from momentumlab.data_integrity import canonical_upsert, consistent_read, validate_signal_context
from momentumlab.data_integrity import history_provenance
from momentumlab.data_integrity import clear_integrity_cache
from momentumlab.benchmark import load_benchmark, compare_equity, save_comparison_chart
from momentumlab.benchmark import load_benchmark, compare_equity

plt = None

def plotting():
    global plt
    if plt is None:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as pyplot
        plt=pyplot
    return plt

APP_VERSION = "4.0.0"
BUILD_TAG = "turnover-live-validation-1"
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
    "etf_min_market_cap_rmb": 250000000,
    "benchmark_csv": "", "benchmark_ticker": "", "benchmark_name": "", "benchmark_basis": "price_return"
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
    # Resolve persisted portable paths against the runtime folder, never the CWD.
    cfg = resolve_config_paths(cfg)
    ensure_dirs(cfg)
    return cfg

def save_config(cfg):
    cfg = resolve_config_paths(cfg)
    ensure_dirs(cfg)
    stored = dict(cfg)
    for key in ('data_folder','results_folder',*FILE_KEYS):
        if stored.get(key): stored[key] = portable_path_label(stored[key])
    stored['universe_file_paths'] = {u:{k:portable_path_label(v) if v else v for k,v in paths.items()}
                                     for u,paths in (cfg.get('universe_file_paths') or {}).items()}
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json_dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)

def parse_dates(s):
    return normalize_dates(s)

def read_csv(path):
    if not path:
        raise FileNotFoundError("No file has been selected.")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return read_small_or_direct(p,pd.read_csv)

def write_csv_atomic(df, path, **kwargs):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        df.to_csv(tmp, **kwargs)
        tmp.replace(p)
        clear_small_caches()
    finally:
        if tmp.exists():
            tmp.unlink()


def append_dedupe(existing_path, new_df, keys, replace_snapshots=False):
    p = Path(existing_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    old=pd.read_csv(p,low_memory=False) if p.exists() else None
    out=canonical_upsert(old,new_df,keys,replace_snapshots)
    if out.empty: return out
    audit_event(p.parent,'CANONICAL_UPSERT',p.parent.name,file=p.name,
                message='Normalized keys; refreshed rows replace prior matching keys; identical duplicates collapsed.',rows=len(out))
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
_CALCULATION_PROGRESS={'running':False,'stage':'Idle'}

def calculation_stage(stage):
    if _CALCULATION_PROGRESS['running']:_CALCULATION_PROGRESS['stage']=stage
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
    "stage_results": [],
    "request": None,
}

def job_snapshot():
    with _JOB_LOCK:
        j = dict(_JOB)
    alive=bool(_JOB_THREAD is not None and _JOB_THREAD.is_alive())
    if j.get('status') in ('running','cancelling') and not alive and (
            j.get('stage')!='Starting' or time.time()-(j.get('started_at') or time.time())>5):
        record_job_error(RuntimeError('Stale job: registered worker is no longer alive. Use Reset or Resume.'),
                         'Worker exited without a captured Python traceback.')
        with _JOB_LOCK: j=dict(_JOB)
    if j.get('status')=='idle':
        error_path=checkpoint_path().with_name('.momentumlab_job_error.json')
        if error_path.exists():
            try: j.update(json.loads(error_path.read_text(encoding='utf-8')))
            except (ValueError,OSError): pass
    j['worker_active']=alive and j.get('status') in ('running','cancelling')
    if j.get("started_at") and j.get("status") in ("running","cancelling"):
        j["elapsed_seconds"] = max(0, int(time.time() - j["started_at"]))
    # Approximate live request count in the last minute
    with _API_LOCK:
        now = time.monotonic()
        recent = [x for x in _API_TIMES if now - x < 60.0]
    j["api_requests_last_minute"] = len(recent) if j['worker_active'] else None
    j['can_resume'] = j.get('status') not in ('running', 'cancelling') and bool(
        j.get('request') or load_checkpoint().get('signature'))
    return j

def job_update(**kwargs):
    with _JOB_LOCK:
        _JOB.update(kwargs)

def record_job_error(error, trace):
    with _JOB_LOCK:
        failed=dict(_JOB)
    prefix='UPDATE FAILED · Production dataset unchanged. ' if failed.get('kind') in ('refresh','full_build') and not failed.get('publication_committed') else ''
    failed.update(status='error',message=prefix+str(error),last_error=prefix+f'{type(error).__name__}: {error}',
                  traceback=trace,finished_at=time.time(),cooldown_remaining=0)
    job_update(**failed)
    try:
        path=checkpoint_path().with_name('.momentumlab_job_error.json')
        temp=path.with_suffix('.tmp')
        temp.write_text(json_dumps(failed,indent=2),encoding='utf-8');temp.replace(path)
    except Exception as persistence_error:
        write_log(f'Could not persist job error: {persistence_error}')

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
        cooldown_remaining=0, last_error=None, traceback=None, checkpoint_notice=None, integrity=None, retained_build_id=None, publication_committed=False, result=None, stage_results=[], request=dict(request)
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
        except BaseException as e:
            write_log("Background data job failed:\n" + traceback.format_exc())
            record_job_error(e,traceback.format_exc())

    _JOB_THREAD = threading.Thread(target=runner, daemon=True, name="MomentumLabDataJob")
    try:
        checkpoint_path().with_name('.momentumlab_job_error.json').unlink(missing_ok=True)
        _JOB_THREAD.start()
    except Exception as e:
        record_job_error(e,traceback.format_exc())
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
    checkpoint_path().with_name('.momentumlab_job_error.json').unlink(missing_ok=True)
    job_update(
        status="idle", kind=None, stage="Idle", stage_index=0, stage_total=0,
        current=0, total=0, message="Job state reset. No active data worker.",
        started_at=None, finished_at=None, elapsed_seconds=0,
        cooldown_remaining=0, last_error=None, traceback=None, stage_results=[], result=None, request=None
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
    tmp.write_text(json_dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")
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
    # Calendar coverage must extend beyond price coverage for live scheduling.
    # Only the exchange calendar request changes; all price/eligibility dates stay unchanged.
    calendar_end = max(pd.Timestamp(end_date), pd.Timestamp.today().normalize()) + pd.Timedelta(days=120)
    job_update(stage="Trading calendar", current=0, total=1,
               message="Downloading SSE trading calendar...")
    df = tushare_call(
        "trade_cal",
        {"exchange":"SSE",
         "start_date":pd.Timestamp(start_date).strftime("%Y%m%d"),
         "end_date":calendar_end.strftime("%Y%m%d")},
        "exchange,cal_date,is_open,pretrade_date"
    )
    if len(df):
        df = df.rename(columns={"cal_date":"trade_date"})
    out = append_dedupe(output, df, ["trade_date"])
    validate_calendar(read_csv(output),end_date)
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
    for index,ticker in enumerate(tickers,1):
        job_check_cancel()
        have = existing.get(str(ticker), set())
        if not have:
            starts[ticker] = history_start
            continue
        earliest, latest = min(have), max(have)
        first = min(pd.Timestamp(start_date), latest + pd.Timedelta(days=1))
        if api_name in ('daily','fund_daily'):
            expected = {d for d in open_dates if earliest <= d <= latest}
        else:
            expected = raw_dates.get(str(ticker), set())
        missing = expected - have
        starts[ticker] = min(first, min(missing)) if missing else first
        job_update(message=f'Checking local history: {index}/{len(tickers)} tickers', preparation_current=index)
    return starts


def fetch_stock_history(api_name, tickers, start_date, end_date, fields, output, keys, checkpoint_stage, stage_name, history_paths=None):
    total = len(tickers)
    job_update(stage=stage_name,current=0,total=total,preparation_current=0,
               message=f'{stage_name}: reading local history for {total} tickers')
    p = Path(output)
    old = None
    if p.exists():
        old = pd.read_csv(p, low_memory=False)

    done = completed_checkpoint(checkpoint_stage)
    starts = {}
    signature = load_checkpoint().get('signature', {})
    if signature.get('kind') == 'refresh':
        starts = incremental_ticker_starts(api_name, old, tickers, start_date,
                                          history_paths or selected_universe_paths(load_config(), signature['universe']))
    parts = []
    pending = []
    requests_made = 0
    total = len(tickers)
    job_update(stage=stage_name, current=0, total=total,
               message=f"{stage_name}: preparing {total} stocks")

    for i,ticker in enumerate(tickers,1):
        job_check_cancel()
        if ticker in done:
            job_update(current=i, total=total,
                       message=f"{stage_name}: {i}/{total} (checkpoint)")
            continue

        if pd.Timestamp(starts.get(ticker,start_date)) > pd.Timestamp(end_date):
            mark_checkpoint(checkpoint_stage,ticker)
            job_update(current=i,total=total,message=f'{stage_name}: {i}/{total} · {ticker} SKIPPED / ALREADY CURRENT')
            continue
        job_update(message=f'{stage_name}: requesting {ticker} ({i}/{total})')
        requests_made += 1
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
            "status":"PASS" if requests_made else "SKIPPED / ALREADY CURRENT OR CHECKPOINTED",
            "stocks":int(old["ts_code"].nunique()) if len(old) and "ts_code" in old.columns else 0,
            "output":str(output)}

def fetch_raw_prices(tickers, start_date, end_date, output, history_paths=None):
    fields = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
    return fetch_stock_history("daily", tickers, start_date, end_date, fields, output,
                               ["ts_code","trade_date"], "raw_prices", "Daily prices", history_paths=history_paths)

def fetch_adj_factors(tickers, start_date, end_date, output, history_paths=None):
    return fetch_stock_history("adj_factor", tickers, start_date, end_date,
                               "ts_code,trade_date,adj_factor", output,
                               ["ts_code","trade_date"], "adj_factors", "Adjustment factors", history_paths=history_paths)

def fetch_price_limits(tickers, start_date, end_date, output, history_paths=None):
    fields = "trade_date,ts_code,pre_close,up_limit,down_limit"
    return fetch_stock_history("stk_limit", tickers, start_date, end_date, fields, output,
                               ["ts_code","trade_date"], "price_limits", "Price limits", history_paths=history_paths)

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
    for label,frame in [('Raw prices',raw),('Adjustment factors',fac)]:
        unique=frame.drop_duplicates()
        if unique.duplicated(['ts_code','trade_date']).any():
            raise ValueError(f'{label} contain conflicting duplicate keys; refresh canonical rows before rebuilding.')
    raw=raw.drop_duplicates(['ts_code','trade_date'],keep='last')
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
    from momentumlab import pending_builds
    cfg=load_config();universe=universe or cfg.get('active_universe','CSI300')
    paths=default_data_paths(cfg,universe)
    state,work=pending_builds.create(paths,universe,dict(start_date=str(start_date),end_date=str(end_date),include_limits=include_limits))
    job_update(retained_build_id=state['id'])
    try:
        # A fresh staging copy cannot safely reuse per-ticker full-build markers.
        init_checkpoint('staged_full_build',{'attempt':uuid.uuid4().hex})
        result=_full_build_staged(start_date,end_date,include_limits,universe,work)
        state.update(download_complete=True,status='AWAITING_REVIEW')
        pending_builds.write(state,work['root'])
        job_update(stage='Final integrity validation', current=0, total=0,
                   message='Validating staged data before publication.')
        report=validate_dataset(work,end_date,universe_id=universe,mode='full')
        # Keep the actual findings in persisted job errors before staging is removed.
        job_update(integrity=report)
        require_valid(report)
        result['backup']=pending_builds.publish(paths,universe,state['id'])['backup']
        job_update(publication_committed=True)
        result.update(integrity=report,manifest=report['manifest'],paths=paths)
    except BaseException as exc:
        saved,_=pending_builds.load(paths,universe,state['id'])
        if saved['status']=='PUBLISHED':
            job_update(publication_committed=True)
            raise
        state.update(status='AWAITING_REVIEW' if state['download_complete'] else 'DOWNLOAD_FAILED',error=str(exc))
        pending_builds.write(state,work['root'])
        raise
    return result

def _full_build_staged(start_date, end_date, include_limits=True, universe=None, working_paths=None):
    cfg=load_config()
    universe=universe or cfg.get("active_universe","CSI300")
    if universe not in UNIVERSES:
        raise ValueError("Unknown universe.")
    if cfg.get("active_universe") != universe:
        set_active_universe(universe)
        cfg=load_config()
    paths=working_paths or default_data_paths(cfg,universe)
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
                                         int(cfg.get("etf_min_market_cap_rmb",250000000)),paths['classification_csv'])

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
                file_max_date(paths["adj_factor_csv"])]
    candidates=[x for x in candidates if x is not None]
    if not candidates:
        raise ValueError(f"No local {UNIVERSES[universe]['label']} dataset was found. Use BUILD / REBUILD first.")

    end=pd.Timestamp(end_date)
    start=min(candidates)+pd.Timedelta(days=1)
    # Membership needs overlap so a latest month-end / index snapshot is refreshed.
    member_start=max(pd.Timestamp("2005-01-01"),start-pd.Timedelta(days=70))

    previous=load_checkpoint()
    if previous.get('signature',{}).get('kind')=='refresh' and previous.get('completed') and not previous.get('resume_id'):
        notice='Legacy interrupted checkpoint has no reusable stage identity; restarting safely. Live files remain intact.'
        job_update(checkpoint_notice=notice);write_log(notice)
    cp=init_checkpoint("refresh",{"universe":universe,"start_date":start.strftime("%Y-%m-%d"),
                               "end_date":end.strftime("%Y-%m-%d")})
    resume_id=cp.get('resume_id')
    if not resume_id or not (Path(paths['root'])/('.incremental-'+resume_id)).is_dir():
        if cp.get('completed'):
            job_update(message='Stale checkpoint: staged files missing; restarting safely from live files.')
            write_log('Stale refresh checkpoint: staged files missing; discarded completed markers.')
        cp['completed']={}; cp['stage_results']=[]; cp['resume_id']=uuid.uuid4().hex
        save_checkpoint(cp)
    with staged_dataset(paths,resume_id=cp['resume_id']) as work:
        results=list(cp.get('stage_results',[]))
        completed_stages={x['stage'] for x in results}
        def record(item):
            results[:]=[x for x in results if x['stage']!=item['stage']]+[item]
            saved=load_checkpoint(); saved['stage_results']=list(results); save_checkpoint(saved)
            job_update(stage_results=list(results))
        def stage(index,name):
            job_check_cancel(); job_update(stage_total=6,stage_index=index,stage=name,current=0,total=1,message=f"{name}...")
        # Calendar is always refreshed first so weekend/closed requested dates are represented.
        stage(1,"Trading calendar")
        if 'Trading calendar' not in completed_stages:
            fetch_trade_calendar(min(start,end),end,work["trade_calendar_csv"])
        validate_calendar(read_csv(work["trade_calendar_csv"]),end)
        record({"stage":"Trading calendar","status":"PASS","through":str(end.date())})
        stage(2,"Eligibility")
        if 'Eligibility' not in completed_stages:
            if universe=="CSI300": fetch_index_weights(member_start,end,work["weights_csv"])
            elif universe in ("CHINEXT_TOP100","STAR_TOP100"): build_top100_membership(universe,member_start,end,work["weights_csv"])
            else: build_etf_eligibility(member_start,end,work["weights_csv"],int(cfg.get("etf_min_market_cap_rmb",250000000)),work.get('classification_csv'))
        if universe=='ETF_250M' and work.get('classification_csv') and not Path(work['classification_csv']).exists():
            fetch_equity_etf_codes(work['classification_csv'])
        tickers=infer_tickers_from_weights(work["weights_csv"])
        record({"stage":"Eligibility","status":"PASS","tickers":len(tickers)})
        if tickers:
            if universe == 'ETF_250M':
                raw_missing = _incremental_open_sessions(work['trade_calendar_csv'], work['raw_price_csv'], end)
                factor_missing = _incremental_open_sessions(work['trade_calendar_csv'], work['adj_factor_csv'], end)
                job_update(message=(f'Missing sessions: {len(raw_missing)} | Eligible ETFs: {len(tickers):,} | '
                    f'ETF price requests: {len(raw_missing)} date batches | Adjustment-factor requests: {len(factor_missing)} date batches | '
                    f'Expected raw-price API calls: {len(raw_missing)}'), update_plan={
                        'missing_sessions': len(raw_missing), 'eligible_tickers': len(tickers),
                        'raw_price_requests': len(raw_missing), 'adjustment_factor_requests': len(factor_missing),
                        'expected_api_calls': len(raw_missing) + len(factor_missing)})
                stage(3,"Raw prices")
                raw=fetch_etf_date_batches('fund_daily',tickers,raw_missing,work['raw_price_csv'],
                    'ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount',
                    ['ts_code','trade_date'],'ETF daily prices')
                job_update(message=(f"ETF raw prices complete · Rows fetched: {raw.get('rows_fetched',0):,} · "
                    f"Rows written: {raw.get('rows_written',0):,} · Tickers represented: {raw.get('tickers',0):,} · "
                    f"Duplicate keys after upsert: {raw.get('duplicate_keys',0)}"))
                record({'stage':'Raw prices','status':raw.get('status','PASS'),'rows':raw.get('rows',0),
                        'rows_fetched':raw.get('rows_fetched',0),'rows_written':raw.get('rows_written',0),
                        'requests':raw.get('requests',0),'duplicate_keys':raw.get('duplicate_keys',0)})
                stage(4,"Adjustment factors")
                fac=fetch_etf_date_batches('fund_adj',tickers,factor_missing,work['adj_factor_csv'],
                    'ts_code,trade_date,adj_factor',['ts_code','trade_date'],'Adjustment factors')
                job_update(message=(f"Adjustment factors complete · Rows fetched: {fac.get('rows_fetched',0):,} · "
                    f"Rows written: {fac.get('rows_written',0):,} · Tickers represented: {fac.get('tickers',0):,} · "
                    f"Duplicate keys after upsert: {fac.get('duplicate_keys',0)}"))
                record({'stage':'Adjustment factors','status':fac.get('status','PASS'),'rows':fac.get('rows',0),
                        'rows_fetched':fac.get('rows_fetched',0),'rows_written':fac.get('rows_written',0),
                        'requests':fac.get('requests',0),'duplicate_keys':fac.get('duplicate_keys',0)})
            else:
                stage(3,"Raw prices")
                raw=fetch_raw_prices(tickers,start,end,work["raw_price_csv"],history_paths=work)
                record({"stage":"Raw prices","status":raw.get('status','PASS'),"rows":raw.get("rows",0)})
                stage(4,"Adjustment factors")
                fac=fetch_adj_factors(tickers,start,end,work["adj_factor_csv"],history_paths=work)
                record({"stage":"Adjustment factors","status":"PASS","rows":fac.get("rows",0)})
            if universe!="ETF_250M":
                fetch_price_limits(tickers,start,end,work["price_limits_csv"],history_paths=work)
        else:
            raw=fac={"rows":0}
        stage(5,"Adjusted prices")
        adj=build_adjusted_prices(work["raw_price_csv"],work["adj_factor_csv"],work["adjusted_price_csv"])
        record({"stage":"Adjusted prices","status":"PASS","rows":adj["rows"]})
        stage(6,"Validation")
        validation=validate_dataset(work,end,universe_id=universe,mode='full')
        job_update(integrity=validation)
        require_valid(validation)
        record({"stage":"Validation","status":validation['status'],"calendar_last":validation['manifest'].get("calendar_last_date")})
        manifest={**validation['manifest'],"requested_through":end.strftime("%Y-%m-%d"),"stages":results}
        backup=publish_dataset(work,paths,manifest)
        job_update(publication_committed=True)
    cfg=load_config(); cfg=_remember_current_paths(cfg); save_config(cfg)
    return {"ok":True,"universe":universe,"message":"Refresh completed.","start":start.strftime("%Y-%m-%d"),
            "end":end.strftime("%Y-%m-%d"),"adjusted":adj,"tickers":len(tickers),"manifest":manifest,"integrity":validation,"backup":backup}

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
    clear_small_caches();clear_integrity_cache()
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


def multi_span_monitor(prices, weights, spans, primary_span, end_date=None, top_n=20, ranked_provider=None):
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
        ranked = ranked_provider(span) if ranked_provider else merge_membership_and_rank(calculate_signal(prices, span), weights)
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
    top = select_target_rows(primary,f"Top {top_n}",signal_date).head(top_n).copy()

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

def portable_path_label(value):
    path=Path(value)
    try: return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError: return str(path)

def resolve_config_paths(cfg):
    """Resolve one shared runtime configuration, including legacy copied settings."""
    cfg=dict(cfg)
    old_roots={key:Path(cfg.get(key) or folder) for key,folder in
               [('data_folder','data'),('results_folder','results')]}
    for key,folder in [('data_folder','data'),('results_folder','results')]:
        old=old_roots[key]
        if not old.is_absolute(): resolved=ROOT/old
        elif old.name.lower()==folder and (ROOT/folder).is_dir(): resolved=ROOT/folder
        else: resolved=old
        cfg[key]=str(resolved.resolve())
    def resolve_file(value):
        if not value: return value
        path=Path(value)
        if not path.is_absolute(): return str((ROOT/path).resolve())
        for key,old in old_roots.items():
            if old.is_absolute():
                try: return str(Path(cfg[key])/path.relative_to(old))
                except ValueError: pass
        return str(path)
    for key in FILE_KEYS:
        if cfg.get(key): cfg[key]=resolve_file(cfg[key])
    if cfg.get('benchmark_csv'): cfg['benchmark_csv']=resolve_file(cfg['benchmark_csv'])
    cfg['universe_file_paths']={u:{k:resolve_file(v) for k,v in paths.items()}
                                for u,paths in (cfg.get('universe_file_paths') or {}).items()}
    return cfg

def default_universe_paths(cfg, u):
    cfg=resolve_config_paths(cfg)
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
        "classification_csv": str(root/"fund_classification.csv"),
        "lifecycle_csv": str(root/"securities.csv"),
        "suspensions_csv": str(root/"suspensions.csv"),
    }

def _stored_universe_paths(cfg, u):
    cfg=resolve_config_paths(cfg)
    allp = cfg.get("universe_file_paths") or {}
    p = dict(default_universe_paths(cfg,u))
    saved = allp.get(u) or {}
    for k,v in saved.items():
        if v:
            candidate = Path(v)
            if not candidate.exists() or candidate.is_absolute():
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

def historical_st_codes(trade_date):
    """Risk-warning exclusions at the membership date, never today's name."""
    try:
        rows = tushare_call('stock_st', {'trade_date':trade_date},
                            'ts_code,name,trade_date,type,type_name')
    except Exception as exc:
        raise ValueError(f'Historical ST eligibility unavailable for {trade_date}. '
                         'The configured provider must support stock_st; check endpoint access. '
                         'No unfiltered membership snapshot was saved.') from exc
    if rows.empty or not {'ts_code','trade_date'}.issubset(rows.columns):
        raise ValueError(f'Historical ST eligibility unverified for {trade_date}: stock_st returned '
                         'no usable nationwide records. An empty response is not proof of no ST stocks.')
    if len(rows) >= 1000:
        raise ValueError(f'stock_st reached its 1000-row response limit for {trade_date}; '
                         'complete ST coverage cannot be verified.')
    dates = parse_dates(rows['trade_date'])
    if dates.isna().any() or not dates.eq(pd.Timestamp(trade_date)).all() or rows['ts_code'].isna().any():
        raise ValueError(f'stock_st returned invalid identities or dates for {trade_date}; eligibility blocked.')
    return set(rows['ts_code'].astype(str).str.strip())


def build_top100_membership(universe,start_date,end_date,output):
    """Monthly point-in-time Top100 snapshots by total market value."""
    eligible = board_codes(universe)
    dates = month_end_trade_dates(start_date,end_date)
    total = len(dates)
    rows = []
    checkpoint_stage = f"{universe}_membership_dates_ex_st_v1"
    done = completed_checkpoint(checkpoint_stage)
    job_update(stage=UNIVERSES[universe]["label"]+" membership",current=0,total=total,
               message="Building month-end Top100 by market cap, excluding historical ST/*ST...")
    for i,d in enumerate(dates,1):
        job_check_cancel()
        ds = pd.Timestamp(d).strftime("%Y%m%d")
        if ds in done:
            job_update(current=i,total=total,message=f"Membership snapshot {i}/{total} (checkpoint)")
            continue
        excluded = historical_st_codes(ds)
        x = tushare_call("daily_basic",{"trade_date":ds},
                         "ts_code,trade_date,total_mv,circ_mv")
        if len(x):
            x = x[x["ts_code"].astype(str).isin(eligible) & ~x["ts_code"].astype(str).isin(excluded)].copy()
            x["total_mv"] = pd.to_numeric(x["total_mv"],errors="coerce")
            x = x.dropna(subset=["total_mv"]).nlargest(100,"total_mv")
            x["con_code"] = x["ts_code"].astype(str)
            x["weight"] = pd.NA
            if x.empty:
                raise ValueError(f'No eligible non-ST board stocks returned for {ds}; snapshot not saved.')
            x['eligibility_policy'] = 'month_end_non_ST_v1'
            x['st_source'] = 'stock_st'
            x['excluded_st_count'] = len(eligible & excluded)
            append_dedupe(output,x[["trade_date","con_code","weight","total_mv",
                                  "eligibility_policy","st_source","excluded_st_count"]],
                          ["trade_date","con_code"], replace_snapshots=True)
        else:
            raise ValueError(f'daily_basic returned no rows for {ds}; membership snapshot not saved.')
        mark_checkpoint(checkpoint_stage,ds)
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



def fetch_equity_etf_codes(classification_output=None):
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
    if classification_output: write_csv_atomic(f,classification_output,index=False,encoding='utf-8-sig')
    f=equity_classification(f)
    return set(f["ts_code"].astype(str)), f

def filter_equity_etf_weights(weights):
    """Filter an existing ETF eligibility file to equity ETFs without rebuilding price history."""
    codes,_=fetch_equity_etf_codes()
    w=weights.copy()
    code_col="con_code" if "con_code" in w.columns else "ts_code"
    before=len(w)
    w=validate_etf_weights(w, codes)
    if w.empty:
        raise RuntimeError("ETF equity filter removed all rows. Check Tushare fund_basic permission/data.")
    w.attrs["equity_filter_before_rows"]=before
    w.attrs["equity_filter_after_rows"]=len(w)
    return w

def build_etf_eligibility(start_date,end_date,output,min_cap_rmb=250_000_000,classification_output=None):
    """
    Monthly point-in-time eligibility using Tushare etf_share_size.total_size.
    total_size is in RMB 10,000; RMB 250m == 25,000 (10k RMB units).
    """
    basic=fetch_etf_basic_history()
    allowed=set(basic["ts_code"].astype(str))
    equity_codes,_equity_meta=fetch_equity_etf_codes(classification_output)
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

def fetch_etf_raw_prices(tickers,start_date,end_date,output,history_paths=None):
    fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
    return fetch_stock_history("fund_daily",tickers,start_date,end_date,fields,output,
                               ["ts_code","trade_date"],"etf_raw_prices","ETF daily prices",history_paths=history_paths)

def fetch_etf_adj_factors(tickers,start_date,end_date,output,history_paths=None):
    return fetch_stock_history("fund_adj",tickers,start_date,end_date,
                               "ts_code,trade_date,adj_factor",output,
                               ["ts_code","trade_date"],"etf_adj_factors","ETF adjustment factors",history_paths=history_paths)

def _incremental_open_sessions(calendar_path, existing_path, requested_end):
    cal = read_csv(calendar_path)
    if cal.empty or 'trade_date' not in cal.columns:
        return []
    is_open = pd.to_numeric(cal['is_open'], errors='coerce').fillna(0) if 'is_open' in cal.columns else 1
    dates = sorted(parse_dates(cal.loc[is_open == 1, 'trade_date']).dropna().unique())
    end = pd.Timestamp(requested_end)
    latest = file_max_date(existing_path)
    return [d for d in dates if (latest is None or d > latest) and d <= end]

def fetch_etf_date_batches(api_name, tickers, missing_sessions, output, fields, keys, stage_name):
    wanted = {str(t).strip() for t in tickers}
    dates = [pd.Timestamp(d) for d in missing_sessions]
    if not dates:
        rows = read_csv(output) if Path(output).exists() else pd.DataFrame()
        return {'rows': int(len(rows)), 'status': 'SKIPPED / ALREADY CURRENT', 'requests': 0,
                'rows_fetched': 0, 'rows_written': 0, 'tickers': int(rows['ts_code'].nunique()) if len(rows) and 'ts_code' in rows else 0,
                'duplicate_keys': 0, 'output': str(output)}
    job_update(stage=stage_name, current=0, total=len(dates),
               message=f'{stage_name}: plan · {len(dates)} date batches · {len(wanted):,} eligible ETFs')
    before = len(read_csv(output)) if Path(output).exists() else 0
    fetched, parts = 0, []
    for index, date in enumerate(dates, 1):
        job_check_cancel()
        result = tushare_call(api_name, {'trade_date': date.strftime('%Y%m%d')}, fields)
        if len(result) and 'ts_code' in result.columns:
            result['ts_code'] = result['ts_code'].astype(str).str.strip()
            result = result[result['ts_code'].isin(wanted)].copy()
            fetched += len(result)
            if len(result): parts.append(result)
        job_update(current=index, total=len(dates), message=f'{stage_name}: {index}/{len(dates)} date batches · rows fetched {fetched:,}')
    if parts:
        append_dedupe(output, pd.concat(parts, ignore_index=True), keys)
    final = read_csv(output) if Path(output).exists() else pd.DataFrame()
    duplicates = int(final.duplicated(keys).sum()) if len(final) and all(k in final.columns for k in keys) else 0
    return {'rows': int(len(final)), 'status': 'PASS', 'requests': len(dates), 'rows_fetched': int(fetched),
            'rows_written': int(max(0, len(final)-before)), 'tickers': int(final['ts_code'].nunique()) if len(final) and 'ts_code' in final else 0,
            'duplicate_keys': duplicates, 'output': str(output)}

# -------------------- DATA STATUS --------------------

def inspect_csv(path, kind):
    info = {"path":path or "", "exists":False, "rows":0, "start":None, "end":None, "extra":""}
    if not path or not Path(path).exists():
        return info
    info["exists"] = True
    key=(file_identity(path),kind)
    cached=STATUS.get(key)
    if cached is not None:return dict(cached)
    try:
        df = pd.read_csv(path, low_memory=False,usecols=lambda c:c in {'ts_code','trade_date'})
        if not len(df.columns):df=pd.read_csv(path,low_memory=False)
        info["rows"] = int(len(df))
        if "trade_date" in df.columns:
            d = parse_dates(df["trade_date"].drop_duplicates())
            if d.notna().any():
                info["start"] = d.min().strftime("%Y-%m-%d")
                info["end"] = d.max().strftime("%Y-%m-%d")
        if kind == "weights" and "trade_date" in df.columns:
            info["extra"] = f"{df['trade_date'].nunique()} snapshots"
        elif "ts_code" in df.columns:
            info["extra"] = f"{df['ts_code'].nunique()} stocks"
    except Exception as e:
        info["extra"] = f"Read error: {e}"
    if not info['extra'].startswith('Read error:') and key[0]==file_identity(path):STATUS.put(key,dict(info),2048)
    return info

def data_status():
    cfg = load_config()
    cfg = selected_universe_paths(cfg)
    out = {
        "raw":inspect_csv(cfg.get("raw_price_csv"),"raw"),
        "adjusted":inspect_csv(cfg.get("adjusted_price_csv"),"adjusted"),
        "weights":inspect_csv(cfg.get("weights_csv"),"weights"),
        "factors":inspect_csv(cfg.get("adj_factor_csv"),"factors"),
        "calendar":inspect_csv(cfg.get("trade_calendar_csv"),"calendar"),
        "limits":inspect_csv(cfg.get("price_limits_csv"),"limits")
    }
    manifest_path = Path(cfg.get("root", ROOT)) / "dataset_manifest.json"
    if manifest_path.exists():
        try:
            out["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            out["manifest"] = {"validation": {"validation_status":"FAILED"}}
    return out

def integrity_review_paths(universe, build_id=None):
    from momentumlab.pending_builds import review_paths
    return review_paths(selected_universe_paths(load_config(),universe),universe,build_id)


def validate_research_files(universe=None, mode='cached', through=None, signal_date=None, execution_date=None, lookback=None, build_id=None):
    cfg=load_config()
    universe=universe or cfg.get('active_universe','CSI300')
    if universe not in UNIVERSES:
        return {'ok':False,'status':'FAIL','errors':['Unknown research universe.']}
    paths=integrity_review_paths(universe,build_id)
    result=validate_dataset(paths,through,universe_id=universe,mode=mode,
                            signal_date=signal_date,execution_date=execution_date,lookback=lookback)
    result['paths']=paths
    from momentumlab.issue_review import review_groups
    result['review_groups']=[dict(group_id=g['group_id'],issue_ids=g['issue_ids']) for g in review_groups(result)]
    return result

def repair_completeness_issues(universe, issue_ids):
    """Individual and batch repair share the same staged provider workflow."""
    return bulk_repair_issues(universe,issue_ids)

def bulk_repair_issues(universe, issue_ids, progress=lambda **kw:None, build_id=None):
    from momentumlab.issue_repair import repair_issues
    paths=integrity_review_paths(universe,build_id)
    return repair_issues(paths,universe,issue_ids,lambda:validate_research_files(universe,mode='full',build_id=build_id),tushare_call,build_adjusted_prices,progress)

def export_integrity_review(universe, group_ids=None, build_id=None):
    from momentumlab.issue_review import export_review
    report=validate_research_files(universe,mode='full',build_id=build_id);paths=report['paths'];names={}
    if paths.get('classification_csv') and Path(paths['classification_csv']).is_file():
        metadata=read_csv(paths['classification_csv'])
        if {'ts_code','name'}.issubset(metadata):names=dict(zip(metadata.ts_code,metadata.name.fillna('Unavailable')))
    return export_review(paths,report,names,group_ids)


def research_notes(paths, price_inputs=None):
    notes = [
        'Research execution: signal close → next dataset observation adjusted close; equal-weight rebalances; fractional shares.',
        'Missing valuation prices are carried forward; exchange calendar, suspension exit restrictions, price limits and liquidity are not enforced.',
        'Gross equity preserves V3 zero-cost normalization. Net equity includes initial funding cost. Cost = one-way turnover × bps / 10000.',
        'Recurring turnover summaries exclude initial funding (50% securities-only turnover); annualization uses 252 observations.',
        'Sharpe label denotes CAGR / annualized volatility (rf=0), not mean excess return / volatility; annualization uses 252 observations.'
    ]
    factor_path = paths.get('adj_factor_csv')
    if not factor_path or not Path(factor_path).exists():
        notes.append('Historical adjustment-factor evidence is unavailable; adjusted price provenance is unverified.')
    else:
        factors = read_csv(factor_path)
        prices = price_inputs[['ts_code','trade_date']].copy() if price_inputs is not None else pd.read_csv(paths['adjusted_price_csv'], usecols=['ts_code','trade_date'])
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

def calculate_signal(df, lookback, calendar=None):
    from momentumlab.completeness import gate_signal_windows
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
    return gate_signal_windows(x, lookback, calendar if calendar is not None else df.attrs.get('exchange_calendar'))


def research_prices(paths):
    """All production ranking paths carry the independent exchange calendar."""
    prices=read_csv(paths['adjusted_price_csv'])
    from momentumlab.completeness import open_sessions
    prices.attrs['exchange_calendar']=tuple(open_sessions(read_csv(paths['trade_calendar_csv'])).strftime('%Y-%m-%d'))
    return prices

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
    plt=plotting()
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

def lookback_comparison(prices, weights, spans, selection, start=None, end=None, horizons=(1,3,5,10,20,40,60), ranked_provider=None):
    rows=[]
    count_rows=[]
    for span in spans:
        ranked=ranked_provider(int(span)) if ranked_provider else merge_membership_and_rank(calculate_signal(prices,int(span)),weights)
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
            chosen=select_target_rows(d,selection,sig).copy()
            tickers=[t for t in chosen["ts_code"] if t in px_raw.columns and pd.notna(px_raw.at[date,t])]
            if tickers:
                current={}
                for t,sh in shares.items():
                    p=px_val.at[date,t] if t in px_val.columns else np.nan
                    current[t]=sh*p if pd.notna(p) else 0.0
                target_each=before/len(tickers)
                detail=compare_weights({t:v/before for t,v in current.items()} if before>0 else {},
                                       {t:1/len(tickers) for t in tickers}, initial=not logs)
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
                             "cost":cost,"tickers":",".join(tickers),
                             "target_details":json_dumps([{"ticker":str(r.ts_code),"rank":int(r.momentum_rank),
                                                            "score":(float(getattr(r,'momentum_score')) if pd.notna(getattr(r,'momentum_score',None)) else None),"target_weight":1/len(tickers)}
                                                           for r in chosen.itertuples() if r.ts_code in tickers],ensure_ascii=False),
                             **detail,"nav_before":before,"nav_after":after})
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

def backtest_v4(ranked, selection, rebalance_days, start_date=None, end_date=None, cost_bps=5):
    gross, _, history, stats = backtest(ranked, selection, rebalance_days, start_date, end_date, 0)
    return apply_costs(gross, history, stats, cost_bps)


def save_line(series,path,title,ylabel):
    plt=plotting()
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
    calculation_stage('Validating data')
    cfg=load_config()
    universe=params.get("universe") or cfg.get("active_universe","CSI300")
    if universe not in UNIVERSES:
        raise ValueError("Unknown research universe.")
    paths=selected_universe_paths(cfg, universe)
    benchmark_id=params.get('benchmark') or cfg.get('benchmark_ticker') or 'none'
    benchmark_meta={'selected':benchmark_id}
    benchmark_close=None
    if benchmark_id not in ('none','None',''):
        benchmark_close,benchmark_meta=load_benchmark(cfg.get('benchmark_csv'),cfg.get('benchmark_ticker'),cfg.get('benchmark_name') or cfg.get('benchmark_ticker'),cfg.get('benchmark_basis','price_return'))
    valid=validate_research_files(universe)
    if not valid.get("research_allowed", valid.get("ok", False)):
        raise ValueError("Research data is not ready:\n- " + "\n- ".join(valid["errors"]))

    calculation_stage("Loading dataset")
    prices=research_prices(paths)
    weights=read_csv(paths["weights_csv"])

    selections=params.get("selections") or [params.get("selection_rule","Top 10")]
    selections=[str(s) for s in selections if s]
    allowed_selections = {f'Top {n}' for n in (5,10,20,30,50)} | {f'Q{n}' for n in range(1,6)}
    if not selections or any(s not in allowed_selections for s in selections):
        raise ValueError('Choose valid Strategy Grid portfolio selections.')
    selections = list(dict.fromkeys(selections))
    cost=float(params.get("trading_cost_bps",5) or 0)
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
    out=Path(cfg["results_folder"])/"backtests"/f"{universe}_strategy_grid_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out.mkdir(parents=True,exist_ok=True)
    rows=[]
    source_provenance=provenance(valid,params,out.name)
    grid_total=len(lookbacks)*len(rebalances)*len(selections)*(len(years)+int(include_full))
    grid_started=time.monotonic()
    grid_failed=0
    def grid_progress(detail):
        elapsed=int(time.monotonic()-grid_started)
        calculation_stage(f'{len(rows)} / {grid_total} combinations processed ({len(rows)*100/grid_total:.0f}%) · {grid_failed} failed · {elapsed//60}m {elapsed%60:02d}s · {detail}')
    def add_period(label,start,end):
        nonlocal grid_failed
        for lb in [current_lb]:
            for rb in rebalances:
                for selection_rule in selections:
                    grid_progress(f'Running MOM{lb} · {label} · {selection_rule} · {rb}D rebalance')
                    try:
                        net,_,log,stats=backtest_v4(ranked,selection_rule,rb,start,end,cost)
                        key=f'{label.replace(" ", "_")}_MOM{lb}_{selection_rule.replace(" ", "_")}_{rb}D'
                        log.to_csv(out/(key+'_rebalances.csv'),index=False,encoding='utf-8-sig')
                        (out/(key+'_rebalances.json')).write_text(json_dumps(history_provenance(log,valid,dict(lookback=lb,rebalance_days=rb,selection=selection_rule,cost_bps=cost),out.name),ensure_ascii=False),encoding='utf-8')
                        net.to_csv(out/(key+'_net_nav.csv'))
                        rows.append({
                            **stats,"history_file":str(out/(key+'_rebalances.json')),
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
                            ,"benchmark_return": (float(benchmark_close.loc[(benchmark_close.index>=start)&(benchmark_close.index<=end)].iloc[-1]/benchmark_close.loc[(benchmark_close.index>=start)&(benchmark_close.index<=end)].iloc[0]-1) if benchmark_close is not None and len(benchmark_close.loc[(benchmark_close.index>=start)&(benchmark_close.index<=end)])>=2 else None),
                            "benchmark_basis": benchmark_meta.get('basis') if benchmark_close is not None else None
                        })
                    except Exception as e:
                        grid_failed+=1
                        rows.append({
                            "period":label,"lookback":lb,"rebalance_days":rb,
                            "selection":selection_rule,"trading_cost_bps":cost,
                            "error":str(e)
                        })
                    grid_progress(f'Processed MOM{lb} · {label} · {selection_rule} · {rb}D rebalance')

    for current_lb in lookbacks:
        grid_progress(f'Computing MOM{current_lb} scores')
        ranked=merge_membership_and_rank(calculate_signal(prices,current_lb),weights)
        ranked.loc[ranked.signal_exclusion_reason.ne(''),['ts_code','trade_date','signal_exclusion_reason']].to_csv(out/f'MOM{current_lb}_signal_exclusions.csv',index=False,encoding='utf-8-sig')
        ranked=ranked[['ts_code','trade_date','adj_close','momentum_score','csi300_member','momentum_rank','quintile']].copy()
        grid_progress(f'Preparing strategies — MOM{current_lb}')
        for y in years:
            start=pd.Timestamp(year=y,month=1,day=1)
            end=min(pd.Timestamp(year=y,month=12,day=31),data_end)
            add_period(str(y),start,end)
        if include_full:add_period("Full Period",data_start,data_end)
        del ranked
    period_order=[str(y) for y in years]+(['Full Period'] if include_full else [])
    rows.sort(key=lambda r:(period_order.index(r['period']),r['lookback'],r['rebalance_days'],selections.index(r['selection'])))

    for row in rows:
        row.update({k:source_provenance[k] for k in ('dataset_id','dataset_fingerprint','strategy_run_id')})
    grid_progress('Saving final results — not finished yet')
    df=pd.DataFrame(rows)
    csv_path=out/"strategy_grid_results.csv"
    df.to_csv(csv_path,index=False,encoding="utf-8-sig")
    weights.to_csv(out/'membership_used.csv',index=False,encoding='utf-8-sig')

    result = {
        'provenance':source_provenance,'integrity':valid,
        "ok":True,
        "run_id":out.name,
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
    result['benchmark']=benchmark_meta
    result['notes'] = research_notes(paths,prices) + ['Calendar-year portfolios and rebalance schedules restart independently. First/last years may be partial; Full Period includes all dataset years, independent of selected years.']
    result['failed_combinations'] = sum('error' in row for row in rows)
    (out/'run_metadata.json').write_text(json_dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result

def etf_momentum_observations(mom10, primary, weights, history, selection, prices=None, median_min_coverage=.8, participation_min_coverage=.8):
    """Diagnostic read of production scores/targets on recorded signal dates only."""
    w=weights.copy();w['trade_date']=parse_dates(w.trade_date)
    snapshots={date:set(group.con_code.astype(str).str.strip()) for date,group in w.groupby('trade_date')}
    dates=sorted(snapshots);rows=[]
    scores={date:g.set_index('ts_code').momentum_score for date,g in mom10.groupby('trade_date')}
    reasons={date:g.set_index('ts_code').signal_exclusion_reason for date,g in mom10.groupby('trade_date')} if 'signal_exclusion_reason' in mom10 else {}
    if not 0<median_min_coverage<=1:raise ValueError('Median coverage must be in (0,1].')
    participation_min_coverage=float(participation_min_coverage)
    if not np.isfinite(participation_min_coverage) or not 0<=participation_min_coverage<=1:raise ValueError('Participation coverage must be finite and in [0,1].')
    px=None;sessions=pd.DatetimeIndex([])
    if prices is not None:
        from momentumlab.completeness import open_sessions
        sessions=open_sessions(prices.attrs.get('exchange_calendar'))
        p=prices[['ts_code','trade_date','adj_close']].copy();p['trade_date']=parse_dates(p.trade_date)
        px=p.pivot(index='trade_date',columns='ts_code',values='adj_close').reindex(sessions)
        px=px.where(np.isfinite(px)&px.gt(0))
    for record in history.itertuples():
        signal=pd.Timestamp(record.signal_date);available=[d for d in dates if d<=signal]
        members=snapshots[available[-1]] if available else set()
        values=scores.get(signal,pd.Series(dtype=float)).reindex(sorted(members))
        valid=values[np.isfinite(values)];n=len(members)
        exclusions={}
        for ticker in values.index[~np.isfinite(values)]:
            why=reasons.get(signal,pd.Series(dtype=str)).get(ticker,'')
            if pd.isna(why) or not why:
                why='No adjusted-price observation on signal date' if ticker not in scores.get(signal,pd.Series(dtype=float)).index else 'Undefined production MOM10 despite complete window (zero/nonfinite volatility or arithmetic)'
            exclusions[ticker]=str(why)
        # Do not count an invalid MOM window as a nonpositive score.
        participation=float(valid.gt(0).mean()) if n and len(valid)>0 and len(valid)/n>=participation_min_coverage else None
        participation_status=('Available: positive / valid MOM10 scores; excluded ETFs omitted from denominator' if participation is not None else ('Unavailable: no historical eligible membership' if not n else f'Unavailable: {len(valid)}/{n} valid production MOM10; minimum coverage {participation_min_coverage:.1%}. '+json_dumps(exclusions,ensure_ascii=False)))
        returns=pd.Series(dtype=float);excluded60={};position=sessions.get_indexer([signal])[0] if len(sessions) else -1
        window60=px.iloc[position-60:position+1].reindex(columns=sorted(members)) if px is not None and position>=60 else None
        for ticker in sorted(members):
            if px is None or position<60:excluded60[ticker]='Insufficient exchange-calendar history: 61 closes required'
            else:
                window=window60[ticker]
                if window.isna().any():excluded60[ticker]=f'{int(window.isna().sum())} missing/invalid closes in required 61-session window'
                else:returns.loc[ticker]=float(window.iloc[-1]/window.iloc[0]-1)
        coverage60=len(returns)/n if n else None
        median60=float(returns.median()) if n and len(returns)>0 and coverage60>=median_min_coverage else None
        median_status='Complete' if median60 is not None else f'Unavailable: {len(returns)}/{n} valid 60-session returns; minimum coverage {median_min_coverage:.0%}. '
        actual=str(record.tickers).split(',')
        expected=list(select_target_rows(primary,selection,signal).ts_code.astype(str))
        target_scores=scores.get(signal,pd.Series(dtype=float)).reindex(actual)
        reason=''
        if str(selection).replace(' ','').lower()!='top10':reason='Selected strategy is not Top10; no substitute portfolio is constructed.'
        elif len(actual)!=10:reason=f'Only {len(actual)} actual holdings; a complete Top10 is unavailable.'
        elif set(actual)!=set(expected):reason='Executed holdings differ from signal-date targets; execution availability must not condition this indicator.'
        elif not np.isfinite(target_scores).all():reason='Selected holdings have incomplete or undefined MOM10 scores.'
        rows.append(dict(signal_date=signal,execution_date=pd.Timestamp(record.entry_date),
            membership_date=available[-1] if available else pd.NaT,eligible_count=n,valid_mom10_count=len(valid),
            excluded_count=n-len(valid),positive_count=int(valid.gt(0).sum()),coverage=len(valid)/n if n else None,
            positive_participation=participation,participation_min_coverage=participation_min_coverage,participation_status=participation_status,mom10_exclusions=json_dumps(exclusions,ensure_ascii=False),
            average_top10_mom10=float(target_scores.mean()) if not reason else None,top10_status=reason or 'Complete',
            selected_count=len(actual),selected_tickers=','.join(actual),median_return60=median60,
            valid_return60_count=len(returns),excluded_return60_count=n-len(returns),coverage_return60=coverage60,
            median_min_coverage=median_min_coverage,median_return60_status=median_status,return60_exclusions=json_dumps(excluded60,ensure_ascii=False)))
    result=pd.DataFrame(rows)
    result['holding_end_date']=pd.NaT;result['subsequent_net_return']=np.nan;result['holding_return_status']='Final holding period not complete'
    for i in range(len(result)-1):
        start=result.iloc[i].execution_date;end=result.iloc[i+1].execution_date
        result.loc[i,'holding_end_date']=end
        needed=sessions[(sessions>=start)&(sessions<=end)]
        held=result.iloc[i].selected_tickers.split(',')
        if px is None or not len(needed) or px.reindex(index=needed,columns=held).isna().any().any():
            result.loc[i,'holding_return_status']='Unavailable: missing held-position prices; saved NAV may use a fallback'
            continue
        before=float(history.iloc[i].nav_before);finish=float(history.iloc[i+1].nav_before)
        if not np.isfinite([before,finish]).all() or before<=0 or finish<=0:
            result.loc[i,'holding_return_status']='Unavailable: invalid saved pre-cost NAV';continue
        result.loc[i,'subsequent_net_return']=finish/before-1
        result.loc[i,'holding_return_status']='Complete: current execution cost included; next execution cost excluded'
    return result


def etf_visual_series(observations, column):
    """Display-only transforms; a missing rebalance resets the trailing window."""
    values=pd.to_numeric(observations[column],errors='coerce').replace([np.inf,-np.inf],np.nan)
    median=values.rolling(5,min_periods=5).median()
    returns=pd.to_numeric(observations.subsequent_net_return,errors='coerce')
    complete=observations.holding_return_status.fillna('').str.startswith('Complete:') & np.isfinite(returns)
    colors=np.where(complete & returns.gt(0),'#16834a',np.where(complete & returns.lt(0),'#c53939','#888888'))
    return values,median,returns.where(complete),colors


def save_etf_momentum_scatter(observations,path):
    fig,axes=plt.subplots(3,1,figsize=(10,11))
    for ax,column,label,scale in zip(axes,['positive_participation','average_top10_mom10','median_return60'],
            ['Positive MOM10 participation (%)','Mean selected Top10 MOM10 (dimensionless)','Median Eligible ETF 60-Session Return (%)'],[100,1,100]):
        values,_,returns,_=etf_visual_series(observations,column)
        good=values.notna() & returns.notna()
        ax.scatter(values[good]*scale,returns[good]*100,s=22,alpha=.7)
        ax.axhline(0,color='grey',linewidth=.7);ax.set_xlabel(label);ax.set_ylabel('Subsequent net holding return (%)')
        ax.set_title(f'n = {int(good.sum())} paired completed holding periods; excluded = {int((~good).sum())}')
        ax.grid(alpha=.2)
    fig.suptitle('Signal-date indicators vs subsequent realized net returns\nDescriptive only · no fitted model · exact values in table/CSV')
    fig.tight_layout();fig.savefig(path,dpi=130);plt.close(fig)


def save_etf_momentum_chart(equity, observations, path, title, median_overlay=False, color_returns=False):
    """Use the existing static chart infrastructure; no interpolation of signals."""
    fig,axes=plt.subplots(4,1,figsize=(12,12),sharex=True)
    axes[0].plot(equity.index,(equity-1)*100,label='Existing net backtest (modeled costs included)')
    axes[0].set_ylabel('Cumulative return (%)');axes[0].legend()
    for ax,column,label,scale in [(axes[1],'positive_participation','Positive MOM10 (%)',100),
                                  (axes[2],'average_top10_mom10','Mean MOM10 (dimensionless)',1),
                                  (axes[3],'median_return60','Median Eligible ETF 60-Session Return (%)',100)]:
        vals,median,returns,colors=etf_visual_series(observations,column)
        ax.plot(observations.signal_date,vals*scale,color='#286cb0',linewidth=1,label='Signal observations (gaps retained)')
        ax.scatter(observations.signal_date,vals*scale,s=16,c=colors if color_returns else '#286cb0',label='Recorded signal-date observations')
        if median_overlay:ax.plot(observations.signal_date,median*scale,color='#d18a00',linestyle='--',linewidth=1.5,label='Trailing median: 5 consecutive valid rebalances')
        missing=vals.isna()
        ax.scatter(observations.loc[missing,'signal_date'],[.03]*int(missing.sum()),transform=ax.get_xaxis_transform(),marker='x',color='grey',label='Unavailable (axis-base marker, not zero)')
        ax.set_ylabel(label);ax.legend(fontsize=8)
    axes[1].set_ylim(0,100);axes[2].axhline(0,color='grey',linewidth=.7)
    axes[3].axhline(0,color='grey',linewidth=.7)
    axes[3].set_xlabel('Signal date')
    start=min(pd.Timestamp(equity.index.min()),observations.signal_date.min())
    for ax in axes:ax.set_xlim(start,equity.index.max());ax.grid(alpha=.2)
    fig.suptitle(title+'\nDiagnostic only · '+('Green/red: subsequent gain/loss; grey: zero or unavailable outcome' if color_returns else 'Daily net returns and signal-date indicators'))
    fig.tight_layout();fig.savefig(path,dpi=130);plt.close(fig)


def run_research(params):
    calculation_stage('Validating data')
    cfg=load_config()
    universe=params.get("universe") or cfg.get("active_universe","CSI300")
    if universe not in UNIVERSES:
        raise ValueError("Unknown research universe.")

    # IMPORTANT: research uses the universe explicitly supplied by the UI.
    # It does not rely on stale top-level file paths from a previously active universe.
    paths=selected_universe_paths(cfg, universe)
    valid=validate_research_files(universe)
    if not valid.get("research_allowed", valid.get("ok", False)):
        raise ValueError("Research data is not ready:\n- " + "\n- ".join(valid["errors"]))
    if cfg.get("active_universe") != universe:
        set_active_universe(universe)
        cfg=load_config()

    calculation_stage("Loading dataset")
    prices=research_prices(paths)
    weights=read_csv(paths["weights_csv"])
    # V3.2.2: ETF research is equity-only by default. Existing V3.1/V3.2
    # eligibility files remain reusable; classification is applied at research time.
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
    cost=float(params.get("trading_cost_bps",5) or 0)
    benchmark_id=params.get('benchmark') or cfg.get('benchmark_ticker') or 'none'

    calculation_stage(f"Computing MOM{lookback}")
    ranked=merge_membership_and_rank(calculate_signal(prices,lookback),weights)
    research_columns=['ts_code','trade_date','adj_close','momentum_score','csi300_member','momentum_rank',
                      'momentum_percentile','quintile','signal_return','signal_volatility','snapshot_date',
                      'signal_window_valid','signal_exclusion_reason']
    ranked=ranked[research_columns].copy()
    # Request-local reuse: primary plus at most one secondary ranking, never global.
    secondary={}
    def ranked_for(span):
        if span==lookback:return ranked
        if span not in secondary:
            secondary.clear()
            secondary[span]=merge_membership_and_rank(calculate_signal(prices,span),weights)[research_columns].copy()
        return secondary[span]

    subset=ranked
    if start: subset=subset[subset["trade_date"]>=pd.Timestamp(start)]
    if end: subset=subset[subset["trade_date"]<=pd.Timestamp(end)]
    decay,counts=signal_decay_table(subset)

    out=Path(cfg["results_folder"])/"backtests"/f"{universe}_MOM{lookback}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out.mkdir(parents=True,exist_ok=True)
    heat=out/"signal_decay.png"
    ranked.loc[ranked.signal_exclusion_reason.ne(''),['ts_code','trade_date','signal_exclusion_reason']].to_csv(out/'signal_exclusions.csv',index=False,encoding='utf-8-sig')
    weights.to_csv(out/'membership_used.csv',index=False,encoding='utf-8-sig')
    date_label = f"{subset['trade_date'].min():%Y-%m-%d} → {subset['trade_date'].max():%Y-%m-%d}"
    diagnostic_label = f'{date_label} | signal-close forward returns, gross, no rebalance/cost model'
    save_heatmap(decay,heat,f"{UNIVERSES[universe]['label']} MOM{lookback} Rank / Signal Decay\n{diagnostic_label}",ylabel="Momentum rank group (Q5 strongest)")
    decay.to_csv(out/"signal_decay_returns.csv")
    counts.to_csv(out/"signal_decay_counts.csv")

    # V3.2: compare the checked lookback spans on the SAME universe, selection and date range.
    lookback_table,lookback_counts=lookback_comparison(prices,weights,display_spans,selection,start,end,ranked_provider=ranked_for)
    lookback_heat=out/"lookback_comparison.png"
    save_heatmap(lookback_table,lookback_heat,
                 f"{UNIVERSES[universe]['label']} · {selection} · Momentum Lookback Comparison\n{diagnostic_label}",
                 ylabel="Momentum lookback")
    lookback_table.to_csv(out/"lookback_comparison_returns.csv")
    lookback_counts.to_csv(out/"lookback_comparison_counts.csv")

    calculation_stage("Running backtest")
    eq,dd,log,stats=backtest_v4(ranked,selection,rebalance,start,end,cost)
    benchmark_result={'selected':benchmark_id}
    if benchmark_id not in ('none','None',''):
        benchmark_close,benchmark_result=load_benchmark(cfg.get('benchmark_csv'),cfg.get('benchmark_ticker'),cfg.get('benchmark_name') or cfg.get('benchmark_ticker'),cfg.get('benchmark_basis','price_return'))
        curves,benchmark_stats,coverage=compare_equity(eq,benchmark_close,start,end)
        curves.to_csv(out/"benchmark_comparison.csv")
        save_comparison_chart(curves,out/"benchmark_comparison.png",f"{UNIVERSES[universe]['label']} {selection} net strategy vs {benchmark_result['name']}")
        benchmark_result.update(stats=benchmark_stats,coverage=coverage,chart=str(out/"benchmark_comparison.png"),comparison_csv=str(out/"benchmark_comparison.csv"),benchmark_cost_assumption='0 bps buy-and-hold; no transaction costs or management-fee adjustment')
        stats['benchmark']=benchmark_result
    ep=out/"equity.png"; dp=out/"drawdown.png"
    portfolio_label = f"{UNIVERSES[universe]['label']} | {selection} | MOM{lookback} | {rebalance}D rebalance | {cost:g} bps\n{stats['backtest_start']} → {stats['backtest_end']} | T+1 adjusted close | {stats['observations']} observations"
    save_line(eq,ep,portfolio_label + '\nNet equity — costs include initial funding',"Growth of 1.0")
    save_line(dd*100,dp,portfolio_label + '\nDrawdown from running equity peak',"Drawdown (%)")
    eq.to_csv(out/"equity.csv"); dd.to_csv(out/"drawdown.csv"); log.to_csv(out/"rebalance_log.csv",index=False)
    (out/'rebalance_history.json').write_text(json_dumps(history_provenance(log,valid,params,out.name),ensure_ascii=False),encoding='utf-8')
    pd.DataFrame([stats]).to_csv(out/"summary.csv",index=False)
    notes = research_notes(paths,prices)
    metadata = {'version': APP_VERSION, 'build_tag': BUILD_TAG, 'universe': universe, 'run_id':out.name,
                'provenance':provenance(valid,params,out.name),'integrity':valid,
                'lookback': lookback, 'selection': selection, 'rebalance_days': rebalance,
                'trading_cost_bps': cost, 'diagnostic_range': date_label, 'stats': stats, 'benchmark': benchmark_result,
                'comparison_spans': display_spans, 'notes': notes}
    (out/'run_metadata.json').write_text(json_dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')

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
    top20 = multi_span_monitor(prices, weights, monitor_spans, lookback, end_date=end, top_n=20,ranked_provider=ranked_for)
    # Display metadata only; never feed names into ranking or return calculations.
    monitor_names = {}
    name_sources = [weights, prices]
    if paths.get('classification_csv') and Path(paths['classification_csv']).is_file():
        name_sources.append(read_csv(paths['classification_csv']))
    for source in name_sources:
        code_column = 'ts_code' if 'ts_code' in source else 'con_code'
        if code_column not in source:
            continue
        for name_column in ('name', 'fullname', 'full_name'):
            if name_column not in source:
                continue
            named = source[[code_column, name_column]].dropna()
            named = named[named[name_column].astype(str).str.strip().ne('')].drop_duplicates(code_column, keep='last')
            for code, name in named.itertuples(index=False, name=None):
                if str(name).strip():
                    monitor_names[str(code).strip()] = str(name).strip()
    for row in top20['rows']:
        row['name'] = monitor_names.get(str(row['ticker']).strip(), '')
    etf_monitor=None
    if universe=='ETF_250M':
        # Reuse cached production rankings and the already completed net backtest.
        # Diagnostic failure must not discard otherwise valid research results.
        try:
            participation_coverage=float(params.get('etf_participation_min_coverage',.8)) if params.get('participation_coverage_enabled',True) else 0.
            observations=etf_momentum_observations(ranked_for(10),ranked,weights,log,selection,prices,participation_min_coverage=participation_coverage)
            csv=out/'etf_momentum_observations.csv';chart=out/'etf_momentum_conditions.png'
            observations.to_csv(csv,index=False,encoding='utf-8-sig')
            save_etf_momentum_chart(eq,observations,chart,portfolio_label)
            variants={'00':str(chart)}
            for overlay,colored in [(False,True),(True,False),(True,True)]:
                key=f'{int(overlay)}{int(colored)}';variant=out/f'etf_momentum_conditions_{key}.png'
                save_etf_momentum_chart(eq,observations,variant,portfolio_label,overlay,colored);variants[key]=str(variant)
            etf_monitor={'status':'available','chart':str(chart),'csv':str(csv),'observations':len(observations),
                'chart_variants':variants,
                'unavailable_participation':int(observations.positive_participation.isna().sum()),
                'unavailable_top10':int(observations.average_top10_mom10.isna().sum()),
                'coverage_rows':observations.to_dict('records'),'participation_min_coverage':participation_coverage,
                'note':f'Participation = eligible ETFs with positive valid MOM10 / eligible ETFs with valid MOM10; minimum eligible-universe coverage {participation_coverage:.1%}. Excluded scores are not zero. Median Eligible ETF 60-Session Return requires 61 consecutive adjusted closes per ETF and at least 80% eligible coverage; it is not an investable benchmark or portfolio. Latest eligible snapshot on/before signal is reused with production equity classification. Historical classification publication vintages are unavailable, so strict point-in-time classification cannot be independently verified. No interpolation. Subsequent net returns use saved NAV before current/next execution costs (current cost included, next excluded); final incomplete periods and periods with missing held prices are unavailable. Static panels share a date axis; no hover.'}
            if weights.trade_date.nunique()<2:etf_monitor['note']+=' Only one membership snapshot exists; historical survivorship bias cannot be ruled out.'
        except Exception as exc:
            etf_monitor={'status':'unavailable','note':'ETF diagnostic unavailable: '+str(exc)}
        metadata['etf_momentum_monitor']=etf_monitor
        (out/'run_metadata.json').write_text(json_dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    participation={'status':'off'}
    if params.get('participation_threshold') not in (None,'','off'):
        try:
            from momentumlab.participation import compare as compare_participation
            participation,control_equity,control_periods=compare_participation(ranked,weights,log,eq,params['participation_threshold'],float(params.get('etf_participation_min_coverage',.8)) if params.get('participation_coverage_enabled',True) else 0.,cost)
            control_equity.to_csv(out/'participation_equity.csv')
            control_periods.to_csv(out/'participation_periods.csv',index=False,encoding='utf-8-sig')
            participation.update(lookback=lookback,selection=selection,rebalance_days=rebalance)
            from momentumlab.participation import save_comparison_chart as save_participation_chart
            participation_chart=out/'participation_comparison.png'
            save_participation_chart(control_equity,participation,participation_chart)
            participation.update(chart=str(participation_chart),equity_csv=str(out/'participation_equity.csv'))
        except Exception as exc:
            participation={'status':'unavailable','error':str(exc)}
        metadata['participation_control']=participation
        (out/'run_metadata.json').write_text(json_dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    rank_weighting={'status':'off'}
    if params.get('rank_weighting_research') is True:
        try:
            if universe!='ETF_250M' or lookback!=10 or str(selection).replace(' ','').lower()!='top10' or rebalance!=10 or cost!=5:
                raise ValueError('This predefined study requires ETF ≥ RMB250M, MOM10, Top10, 10D and 5 bps. Other research results are unchanged.')
            calculation_stage('Research-only Top10 rank and weighting comparison')
            from momentumlab.rank_weighting import save_report as save_rank_weighting
            rank_weighting=save_rank_weighting(ranked,log,eq,prices,cost,out,{'run_id':out.name,'provenance':metadata['provenance']})
        except Exception as exc:
            rank_weighting={'status':'unavailable','error':str(exc)}
        metadata['rank_weighting_research']=rank_weighting
        (out/'run_metadata.json').write_text(json_dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    return {"ok":True,
            "rank_weighting_research":rank_weighting,
            "participation_control":participation,
            "etf_momentum_monitor":etf_monitor,
            "provenance":metadata['provenance'],"integrity":valid,
            "notes":notes,"diagnostic_label":diagnostic_label,"portfolio_label":portfolio_label,
            "rebalance_history":history_provenance(log,valid,params,out.name),
            "run":{"universe":universe,"universe_label":UNIVERSES[universe]["label"],
                   "lookback":lookback,"selection":selection,"rebalance_days":rebalance,
                   "trading_cost_bps":cost,
                   "etf_asset_filter":("Equity only" if universe=="ETF_250M" else None)},
            "stats":stats,
            "benchmark":benchmark_result,
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

# -------------------- LIVE PORTFOLIO ADAPTER --------------------

_LIVE_MARKET_CACHE = RuntimeCache(max_bytes=384*1024*1024,max_entries=1,ttl=120)

def live_calendar(cfg,paths,universe):
    diagnostic=dict(app_root='.',data_root=portable_path_label(cfg['data_folder']),universe_key=universe,
                    dataset_folder=portable_path_label(paths['root']),calendar_file=portable_path_label(paths['trade_calendar_csv']),
                    calendar_loaded=False,calendar_first_date=None,calendar_last_date=None,failure_reason=None)
    calendar=[]
    try:
        cal=read_csv(paths['trade_calendar_csv'])
        column='trade_date' if 'trade_date' in cal else 'cal_date'
        if column not in cal or 'is_open' not in cal: raise ValueError('Calendar needs trade_date (or cal_date) and is_open columns.')
        dates=parse_dates(cal[column])
        calendar=sorted(dates[pd.to_numeric(cal['is_open'],errors='coerce')==1].dropna().dt.strftime('%Y-%m-%d').unique())
        if not calendar: raise ValueError('Calendar contains no valid open-session dates.')
        diagnostic.update(calendar_loaded=True,calendar_first_date=dates.min().strftime('%Y-%m-%d'),
                          calendar_last_date=dates.max().strftime('%Y-%m-%d'),last_open_session=calendar[-1])
    except (OSError,ValueError,KeyError) as exc:
        diagnostic['failure_reason']=f'Calendar could not be loaded: {exc}'
    return calendar,diagnostic


def live_market(strategy):
    cfg=load_config()
    paths=selected_universe_paths(cfg,strategy['universe'])
    valid=validate_research_files(strategy['universe'])
    if not valid['ok']:
        raise ValueError('New model signal blocked by data integrity failure. Actual portfolio state unchanged.\n'+'\n'.join(valid['errors']))
    # Raw close is required for actual-account valuation; never substitute adjusted close.
    raw_path=paths['raw_price_csv']
    if not Path(raw_path).exists(): raw_path=paths['adjusted_price_csv']
    sources=[raw_path,paths['adjusted_price_csv'],paths['weights_csv'],paths['trade_calendar_csv'],paths['adj_factor_csv'],paths.get('classification_csv','')]
    key=(strategy['universe'],int(strategy['lookback']),valid['manifest']['dataset_fingerprint'],tuple(file_identity(p) for p in sources))
    cached=_LIVE_MARKET_CACHE.get(key)
    if cached and time.monotonic()-cached[0]<300: return cached[1]
    raw=read_csv(raw_path)
    if 'close' not in raw.columns: raise ValueError('Live valuation needs raw close prices. Locate the raw price dataset.')
    prices=research_prices(paths); weights=read_csv(paths['weights_csv'])
    for frame in (raw,prices):
        frame['trade_date']=parse_dates(frame['trade_date'])
        frame['ts_code']=frame['ts_code'].astype(str).str.strip()
    calendar,calendar_diagnostics=live_calendar(cfg,paths,strategy['universe'])
    ranked=merge_membership_and_rank(calculate_signal(prices,int(strategy['lookback'])),weights)
    names=dict(zip(raw['ts_code'],raw['name'].fillna(raw['ts_code']))) if 'name' in raw else {}
    if paths.get('classification_csv') and Path(paths['classification_csv']).is_file():
        metadata=read_csv(paths['classification_csv'])
        if {'ts_code','name'}.issubset(metadata): names.update(dict(zip(metadata.ts_code,metadata.name.fillna(metadata.ts_code))))
    raw=raw[['ts_code','trade_date','close']]
    prices=prices[['ts_code','trade_date','adj_close']]
    ranked=ranked[['ts_code','trade_date','adj_close','adj_daily_return_calc','signal_return','signal_daily_std','signal_volatility',
                   'momentum_score','snapshot_date','csi300_member','csi300_weight','momentum_rank','momentum_percentile','quintile',
                   'signal_window_valid','signal_exclusion_reason']]
    result=dict(raw=raw,adjusted=prices,ranked=ranked,calendar=calendar,calendar_diagnostics=calendar_diagnostics,
                names=names,data_end=raw['trade_date'].max().strftime('%Y-%m-%d'),weights=weights,
                provenance=provenance(valid,strategy),integrity=valid)
    _LIVE_MARKET_CACHE.clear()
    _LIVE_MARKET_CACHE[key]=(time.monotonic(),result)
    return result


def historical_reconstruction(strategy,start,end,source):
    market=live_market(strategy)
    research=target_snapshot(strategy,market,start)
    if research['ranking_date']!=start:
        raise ValueError(f'NO TARGET FOUND: {start} has no eligible ranking; latest available is {research["ranking_date"]}.')
    saved_first=saved_signal_target(source,start)
    if saved_first and (not compare_strategy_targets(research,saved_first)['match'] or saved_first['execution_date']!=research['execution_date']):
        raise ValueError('STRATEGY TARGET MISMATCH: saved target differs from Research Lab for the resolved signal.')
    net,_,history,_=backtest_v4(market['ranked'],strategy['selection'],strategy['rebalance_days'],
                                pd.Timestamp(start),pd.Timestamp(end),strategy['cost_bps'])
    generated=research_bundle(history,net,source['run_id'])
    if not generated['signals']: raise ValueError('NO TARGET FOUND: price coverage does not include the resolved execution date.')
    for signal in generated['signals']:
        validate_signal_context(market['calendar'],market['weights'],signal['signal_date'],signal['execution_date'])
        signal['provenance']={**market['provenance'],'strategy_run_id':source['run_id'],
                              'signal_date':signal['signal_date'],'execution_date':signal['execution_date']}
        signal['names']={ticker:market.get('names',{}).get(ticker,ticker) for ticker in signal['weights']}
        signal.update(strategy_run_id=source['run_id'],universe=strategy['universe'],
                      momentum_lookback=strategy['lookback'],selection=strategy['selection'],
                      rebalance_frequency=strategy['rebalance_days'])
    generated['integrity']={'research_vs_backtest':compare_strategy_targets(research,generated['signals'][0]),
                            'saved_grid_vs_backtest':compare_strategy_targets(saved_first,generated['signals'][0]) if saved_first else None,
                            'target_source':'SAVED_STRATEGY_GRID_TARGET' if saved_first else 'BACKTEST_GENERATED_FOR_SIGNAL'}
    if saved_first:
        saved_first['names']=generated['signals'][0]['names']
        generated['signals'][0]={**generated['signals'][0],**saved_first}
    expected_dates=[s['signal_date'] for s in generated['signals']]
    for filename in source.get('history_files',[]):
        path=Path(filename).resolve()
        if not path.is_file(): continue
        try: records=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError): continue
        records=[r for r in records if start<=r.get('signal_date','') and r.get('entry_date','')<=end]
        if [r.get('signal_date') for r in records]!=expected_dates: continue
        saved=research_bundle(pd.DataFrame(records),net,source['run_id'])
        checks=[compare_strategy_targets(a,b) for a,b in zip(generated['signals'],saved['signals'])]
        if checks and all(c['match'] for c in checks):
            for saved_signal,generated_signal in zip(saved['signals'],generated['signals']):
                for field in ('ranks','scores','names','strategy_run_id','universe','momentum_lookback','selection','rebalance_frequency','provenance'):
                    saved_signal[field]=generated_signal[field]
            saved['integrity']={'research_vs_backtest':compare_strategy_targets(research,saved['signals'][0]),
                                'saved_grid_vs_backtest':checks[0], 'target_source':'SAVED_STRATEGY_GRID_HISTORY'}
            return saved
        if checks: generated['integrity']['saved_grid_vs_backtest']=checks[0]
    return generated


def live_service():
    return LiveService(LiveStore(ROOT/'live'/'momentumlab.sqlite3'),live_market,historical_reconstruction,historical_target_preview)


def historical_target_preview(strategy,start,end,source):
    market=live_market(strategy)
    reference=target_snapshot(strategy,market,start)
    if reference['signal_date']!=start: raise ValueError('NO TARGET FOUND: no eligible ranking on the resolved signal date.')
    execution=reference['execution_date']
    if not execution or execution>end: raise ValueError('NO TARGET FOUND: execution falls outside the selected range/calendar.')
    saved=saved_signal_target(source,start)
    if saved:
        match=compare_strategy_targets(reference,saved)
        if not match['match'] or saved['execution_date']!=execution:
            raise ValueError('STRATEGY TARGET MISMATCH: saved target differs from Research Lab.')
        saved['names']={t:market.get('names',{}).get(t,t) for t in saved['weights']}
        return dict(signals=[saved],integrity={'research_vs_backtest':match,'saved_grid_vs_backtest':match,'target_source':'SAVED_STRATEGY_GRID_TARGET'})
    # Preview only needs the first rebalance; retain the existing engine and full warm-up rankings.
    return historical_reconstruction(strategy,start,execution,source)


def run_research_audit(body):
    calculation_stage('Validating data')
    universe=str(body.get('universe','ETF_250M'))
    lookback=int(body.get('lookback',10))
    mode=str(body.get('mode','Single Ticker Calculation'))
    ticker=str(body.get('ticker','')).strip()
    if universe not in UNIVERSES or lookback not in (5,10,20,40,60,120): raise ValueError('Invalid audit universe or MOM lookback.')
    if mode not in ('Single Ticker Calculation','Top Ranking Audit','Full Signal Audit Package'): raise ValueError('Invalid audit mode.')
    if mode=='Single Ticker Calculation' and not ticker: raise ValueError('Enter a ticker for the single-ticker audit.')
    if not body.get('signal_date'): raise ValueError('Choose an exact signal date.')
    valid=validate_research_files(universe)
    if not valid.get('research_allowed', valid.get('ok', False)): return dict(blocked=True,integrity=valid,error='Dataset integrity FAIL: calculation audit blocked.')
    strategy=dict(universe=universe,lookback=lookback,selection='Top 10',rebalance_days=10,cost_bps=0)
    market=dict(live_market(strategy))
    cfg=load_config(); paths=selected_universe_paths(cfg,universe)
    if paths.get('classification_csv') and Path(paths['classification_csv']).is_file(): market['classification']=read_csv(paths['classification_csv'])
    market['source_components']={name:portable_path_label(paths[key]) for name,key in
                                  [('Adjusted Prices','adjusted_price_csv'),('Eligibility','weights_csv'),('Trading Calendar','trade_calendar_csv')]}
    calculation_stage('Building audit from production calculations')
    result=build_audit(market,strategy,body['signal_date'],ticker,mode,backtest_v4,cfg['results_folder'])
    calculation_stage('Saving audit package')
    result['export_path']=export_audit(result,cfg['results_folder'])
    # Complete, full-precision inputs remain in the immutable export. UI uses one ticker trace.
    result.pop('price_inputs'); result.pop('daily_returns')
    if mode!='Full Signal Audit Package':
        result['ranking']=result['ranking'][:20]
    return result


def verified_strategy_source(body):
    # Promotion must reference an existing successful row, not arbitrary client strategy parameters.
    run_id=str(body['run_id'])
    root=(Path(load_config()['results_folder'])/'backtests').resolve()
    run=(root/run_id).resolve()
    if run.parent!=root: raise ValueError('Invalid research run ID.')
    meta=json.loads((run/'run_metadata.json').read_text(encoding='utf-8'))
    matches=[r for r in meta.get('rows',[]) if not r.get('error') and r['lookback']==int(body['lookback'])
             and r['rebalance_days']==int(body['rebalance_days']) and r['selection']==body['selection']]
    if not matches: raise ValueError('No successful tested strategy matches this source run.')
    history_files=[]
    for row in matches:
        filename=row.get('history_file')
        if not filename: continue
        # Histories travel with their verified run, even when metadata predates a move.
        candidate=(run/Path(filename).name).resolve()
        if candidate.parent==run and candidate.is_file(): history_files.append(str(candidate))
    return dict(run_id=run_id,universe=meta['universe'],lookback=int(body['lookback']),selection=body['selection'],
                rebalance_days=int(body['rebalance_days']),cost_bps=float(meta['trading_cost_bps']),history_files=history_files,provenance=meta.get('provenance'))


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
        b=json_dumps(obj,ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if not self.trusted_request(): return
        u=urlparse(self.path)
        if u.path.startswith('/ui/'):
            asset=(RESOURCE_ROOT/u.path.lstrip('/')).resolve()
            if asset.parent!=(RESOURCE_ROOT/'ui').resolve() or asset.suffix not in {'.js','.css'} or not asset.is_file():
                self.json({'ok':False,'error':'Asset not found.'},404); return
            b=asset.read_bytes();self.send_response(200)
            self.send_header('Content-Type','text/javascript; charset=utf-8' if asset.suffix=='.js' else 'text/css; charset=utf-8')
            self.send_header('Cache-Control','no-store, max-age=0')
            self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
        if u.path=='/api/live/portfolios':
            try: self.json({'ok':True,'portfolios':live_service().store.list_portfolios(parse_qs(u.query).get('view',['active'])[0],summary=parse_qs(u.query).get('summary',['0'])[0]=='1')})
            except Exception as e: self.json({'ok':False,'error':str(e)},500)
            return
        if u.path=="/":
            try:
                if not UI_FILE.exists():
                    raise FileNotFoundError(
                        f"UI resource not found: {UI_FILE}. "
                        f"Executable root={ROOT}; resource root={RESOURCE_ROOT}"
                    )
                b=UI_FILE.read_bytes()
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header('Cache-Control','no-store, max-age=0')
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
        if u.path=='/api/pending-builds':
            from momentumlab.pending_builds import list_builds
            universe=parse_qs(u.query).get('universe',['CSI300'])[0]
            try:self.json({'ok':True,'builds':list_builds(selected_universe_paths(load_config(),universe),universe)})
            except Exception as exc:self.json({'ok':False,'error':str(exc)},400)
            return
        if u.path=='/api/completeness/bulk-status':
            from momentumlab.review_jobs import snapshot
            self.json({'ok':True,'job':snapshot()});return
        if u.path=='/api/completeness/page':
            from momentumlab.integrity_pages import page
            q=parse_qs(u.query);one=lambda k,d='':q.get(k,[d])[0]
            universe=one('universe') or load_config().get('active_universe','CSI300')
            try:
                report=validate_research_files(universe,mode='snapshot',build_id=one('build_id'))
                result=page(report,number=one('page','1'),size=one('size','50'),sort=one('sort','ticker'),
                    descending=one('descending')=='true',filters={k:one(k) for k in ('status','ticker','component','classification','group','blocking','repair_eligible')})
                result['summary']={k:report.get(k) for k in ('status','structural_status','research_allowed','timestamp','universe')}
                result['summary']['completeness']={k:report['completeness'].get(k) for k in ('status','unresolved_blocking_count','confirmed_count','scope')}
                result['summary']['manifest']={k:report['manifest'].get(k) for k in ('dataset_id','dataset_fingerprint','raw_price_last_date','adjusted_price_last_date')}
                result['summary']['freshness']=next((x.get('details',{}) for x in report['checks'] if x['name']=='latest_prices'),{})
                self.json(result)
            except Exception as exc:self.json({'ok':False,'error':str(exc)},400)
            return
        if u.path=='/api/completeness/preview':
            from momentumlab.issue_review import load_preview
            q=parse_qs(u.query);universe=q.get('universe',[''])[0]
            try:
                report=validate_research_files(universe,mode='snapshot',build_id=q.get('build_id',[None])[0])
                result=load_preview(report['paths'],report,q.get('id',[''])[0])
                size=int(q.get('size',['50'])[0]);number=max(1,int(q.get('page',['1'])[0]))
                if size not in (25,50,100,250):raise ValueError('Invalid preview page size.')
                result['total_rows']=len(result['decisions']);result['valid_count']=sum(d['status']=='VALID' for d in result['decisions'])
                result['decisions']=result['decisions'][(number-1)*size:number*size];result['page']=number;result['page_size']=size
                self.json(result)
            except Exception as exc:self.json({'ok':False,'error':str(exc)},400)
            return
        if u.path=='/api/completeness/workbook':
            from momentumlab.issue_review import safe_id
            q=parse_qs(u.query);universe=q.get('universe',[''])[0]
            try:
                path=Path(integrity_review_paths(universe,q.get('build_id',[None])[0])['root'])/'integrity_reviews'/('workbook-'+safe_id(q.get('id',[''])[0])+'.xlsx')
                b=path.read_bytes();self.send_response(200)
                self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                self.send_header('Content-Disposition','attachment; filename="MomentumLab_issues.xlsx"')
                self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
            except Exception as exc:self.json({'ok':False,'error':str(exc)},404)
            return
        if u.path=='/api/calculation-status':self.json({'ok':True,**_CALCULATION_PROGRESS});return
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
        path = urlparse(self.path).path
        if not _REQUEST_LOCK.acquire(blocking=False):
            # Do not queue stale mutations or consume an occupied worker's lock.
            # The request body is unread, so close rather than reuse this connection.
            self.close_connection = True
            # Drain the unread body first. On Windows, closing a socket that still
            # holds unread input sends a reset that can destroy this 409 before the
            # client reads it.
            try:
                remaining=min(int(self.headers.get('Content-Length','0')),64*1024*1024)
                while remaining>0:
                    chunk=self.rfile.read(min(remaining,65536))
                    if not chunk:break
                    remaining-=len(chunk)
            except (OSError,ValueError):
                pass
            self.json({'ok':False,'code':'APPLICATION_BUSY','preview_status':'ERROR',
                       'error':'Another operation is active. This request was not started or queued. Wait for it to finish, then retry.'},409)
            # Orderly response EOF even if the drain was partial (huge or stalled body).
            import socket
            self.wfile.flush()
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            return
        try:
            if getattr(self.server,'stopping',False):
                self.json({'ok':False,'error':'Momentum Lab is closing. Reopen the app to continue.'},503)
                return
            from momentumlab.review_jobs import snapshot as bulk_snapshot
            if bulk_snapshot()['status']=='running':
                self.json({'ok':False,'error':'A bulk integrity operation is running. Wait for its result before another action.'},409);return
            if job_snapshot()['status'] in ('running','cancelling') and path not in {
                '/api/job-cancel','/api/job-reset','/api/browse-file','/api/browse-folder'}:
                self.json({'ok':False,'error':'Wait for or cancel the active data job before changing settings, importing, or running research.'},409)
                return
            if path in ('/api/run','/api/strategy-grid','/api/research-audit'):_CALCULATION_PROGRESS.update(running=True,stage='Validating data')
            self.handle_post()
        finally:
            if path in ('/api/run','/api/strategy-grid','/api/research-audit'):_CALCULATION_PROGRESS.update(running=False,stage='Finished')
            _REQUEST_LOCK.release()

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
            if u.path=='/api/results-compression/preview':
                from momentumlab.result_compression import preview
                self.json({'ok':True,**preview(load_config()['results_folder'],body.get('days',30))});return
            if u.path=='/api/results-compression/apply':
                from momentumlab.result_compression import apply
                from momentumlab.review_jobs import start
                results=load_config()['results_folder'];token=body.get('token','')
                self.json({'ok':True,'job':start(lambda progress:apply(results,token,progress),_REQUEST_LOCK,kind='compress older results')});return
            if u.path=='/api/clear-runtime-cache':
                with DATA_LOCK:
                    clear_small_caches();clear_integrity_cache()
                self.json({'ok':True,'message':'Runtime caches cleared. Saved data, research results and portfolios are retained.'});return
            if u.path=='/api/research-audit':
                self.json({'ok':True,**run_research_audit(body)}); return
            if u.path.startswith('/api/live/'):
                service=live_service(); action=u.path.rsplit('/',1)[-1]
                if action=='create': result=service.create(body,verified_strategy_source(body))
                elif action=='preview': result=service.preview_historical(body,verified_strategy_source(body))
                elif action=='detail': result=service.detail(body['id'],refresh=bool(body.get('refresh',False)))
                elif action=='trade': result=service.record_trade(body['id'],body)
                elif action=='status': service.set_status(body['id'],body['status']); result={}
                elif action=='journal': result=service.journal(body['id'],body)
                elif action=='export': result={'files':export_portfolio(service.detail(body['id']),load_config()['results_folder'])}
                elif action=='archive': service.archive(body['id']); result={}
                elif action=='restore': service.restore(body['id']); result={}
                elif action=='delete': service.delete_portfolio(body['id'],body.get('confirmation')); result={}
                elif action=='promote': result=service.promote(body['id'],body)
                elif action=='rebalance-preview': result=service.preview_rebalance(body['id'],body)
                elif action=='paper-rebalance': result=service.apply_paper_rebalance(body['id'],body)
                elif action=='actual-rebalance': result=service.record_actual_rebalance(body['id'],body)
                elif action=='skip-rebalance': result=service.skip_rebalance(body['id'],body)
                else: raise ValueError('Unknown live portfolio action.')
                self.json({'ok':True,**result}); return
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
                universe=cfg.get('active_universe','CSI300');paths=selected_universe_paths(cfg,universe)
                with staged_dataset(paths) as work:
                    r=build_adjusted_prices(work['raw_price_csv'],work['adj_factor_csv'],work['adjusted_price_csv'])
                    report=require_valid(validate_dataset(work,universe_id=universe,mode='full'))
                    backup=publish_dataset(work,paths,report['manifest'])
                self.json({"ok":True,**r,'output':paths['adjusted_price_csv'],'integrity':report,'backup':backup}); return
            if u.path=="/api/validate":
                self.json(validate_research_files(body.get('universe'),mode=body.get('mode','full'),through=body.get('through'),lookback=body.get('lookback'))); return
            build_id=body.get('build_id')
            if u.path=='/api/pending-builds/publish':
                from momentumlab.pending_builds import publish
                from momentumlab.review_jobs import start
                universe=body.get('universe')
                if body.get('confirmation')!='PUBLISH':raise ValueError('Explicit publication confirmation is required.')
                paths=selected_universe_paths(load_config(),universe)
                self.json({'ok':True,'job':start(lambda progress:publish(paths,universe,build_id,progress),
                    _REQUEST_LOCK,kind='publish retained build',metadata={'universe':universe,'build_id':build_id})});return
            if u.path=='/api/completeness/check-job':
                from momentumlab.review_jobs import start
                universe=body.get('universe') or load_config().get('active_universe','CSI300')
                def operation(progress):
                    progress(stage='VALIDATING',message='Checking local files and reconciling review decisions.')
                    result=validate_research_files(universe,mode='full',build_id=build_id)
                    progress(stage='REFRESHING_REPORT',message='Validation complete; refreshing findings.')
                    return {'status':result['status'],'timestamp':result['timestamp']}
                self.json({'ok':True,'job':start(operation,_REQUEST_LOCK,kind='integrity check',metadata={'universe':universe,'build_id':build_id})});return
            if u.path=='/api/completeness/export-job':
                from momentumlab.review_jobs import start
                from momentumlab.integrity_pages import page
                import base64 as _base64
                universe=body.get('universe') or load_config().get('active_universe','CSI300')
                scope=body.get('scope','all')
                if scope not in ('all','filtered','page'):raise ValueError('Invalid export scope.')
                def operation(progress):
                    progress(stage='PREPARING_WORKBOOK',message='Preparing workbook.')
                    group_ids=None
                    if scope!='all':
                        result=page(validate_research_files(universe,mode='cached',build_id=build_id),number=body.get('page',1),size=body.get('size',50),filters=body.get('filters',{}),sort=body.get('sort','ticker'))
                        group_ids=[g['group_id'] for g in result['groups']] if scope=='page' else result['all_group_ids']
                    result=export_integrity_review(universe,group_ids,build_id=build_id)
                    path=Path(integrity_review_paths(universe,build_id)['root'])/'integrity_reviews'/('workbook-'+result['export_id']+'.xlsx')
                    path.write_bytes(_base64.b64decode(result.pop('workbook')))
                    progress(stage='DOWNLOAD_READY',current=result['findings'],total=result['findings'],message='Workbook prepared.')
                    return result
                self.json({'ok':True,'job':start(operation,_REQUEST_LOCK,kind='export',metadata={'universe':universe,'build_id':build_id,'scope':scope})});return
            if u.path=='/api/completeness/export-review':
                universe=body.get('universe') or load_config().get('active_universe','CSI300')
                self.json(export_integrity_review(universe,build_id=build_id));return
            if u.path=='/api/completeness/import-review':
                from momentumlab.issue_review import preview_review
                universe=body.get('universe') or load_config().get('active_universe','CSI300')
                report=validate_research_files(universe,mode='full',build_id=build_id)
                result=preview_review(report['paths'],report,body.get('workbook',''),body.get('filename',''))
                result['total_rows']=len(result['decisions']);result['valid_count']=sum(d['status']=='VALID' for d in result['decisions'])
                result['decisions']=result['decisions'][:50];result['page']=1;result['page_size']=50
                self.json(result);return
            if u.path=='/api/completeness/import-job':
                from momentumlab.issue_review import preview_review
                from momentumlab.review_jobs import start
                universe=body.get('universe') or load_config().get('active_universe','CSI300')
                filename=body.get('filename','')
                def operation(progress):
                    progress(stage='VALIDATING_DATASET',message='Checking current findings before parsing workbook.')
                    report=validate_research_files(universe,mode='cached',build_id=build_id)
                    result=preview_review(report['paths'],report,body.get('workbook',''),filename,progress)
                    result['total_rows']=len(result['decisions']);result['valid_count']=sum(d['status']=='VALID' for d in result['decisions'])
                    result['decisions']=result['decisions'][:50];result['page']=1;result['page_size']=50
                    return result
                self.json({'ok':True,'job':start(operation,_REQUEST_LOCK,kind='review upload',metadata={'universe':universe,'build_id':build_id,'filename':filename})});return
            if u.path=='/api/completeness/apply-review':
                from momentumlab.issue_review import apply_review
                from momentumlab.review_jobs import start
                universe=body.get('universe') or load_config().get('active_universe','CSI300');preview_id=body.get('preview_id')
                paths=integrity_review_paths(universe,build_id)
                def operation(progress):
                    result=apply_review(paths,universe,preview_id,lambda:validate_research_files(universe,mode='full',build_id=build_id),lambda ids:bulk_repair_issues(universe,ids,progress,build_id=build_id),progress)
                    result.pop('report',None)
                    if result.get('repair'):result['repair'].pop('integrity',None)
                    return result
                self.json({'ok':True,'job':start(operation,_REQUEST_LOCK,kind='review apply',metadata={'universe':universe,'build_id':build_id,'preview_id':preview_id})});return
            if u.path=='/api/completeness/repair-batch':
                from momentumlab.issue_review import repair_eligible
                from momentumlab.review_jobs import start
                universe=body.get('universe') or load_config().get('active_universe','CSI300')
                ids=body.get('issue_ids',[])
                if body.get('all_eligible'):
                    ids=[r['issue_id'] for r in validate_research_files(universe,mode='full',build_id=build_id)['completeness']['findings'] if repair_eligible(r)]
                def operation(progress):
                    result=bulk_repair_issues(universe,ids,progress,build_id=build_id)
                    result.pop('integrity',None)
                    return result
                self.json({'ok':True,'job':start(operation,_REQUEST_LOCK,kind='repair',metadata={'universe':universe,'build_id':build_id,'issues':len(ids)})});return
            if u.path=='/api/completeness/acknowledge':
                universe=body.get('universe') or load_config().get('active_universe','CSI300'); paths=integrity_review_paths(universe,build_id)
                report=validate_research_files(universe,mode='full',build_id=build_id); issue=next((x for x in report['completeness'].get('findings',[]) if x.get('issue_id')==body.get('issue_id')),None)
                if not issue: raise ValueError('Issue was not found in the current validation result; revalidate first.')
                item=save_acknowledgement(paths,issue,body.get('category',''),body.get('explanation',''),body.get('reference',''),body.get('operator','unknown'),findings=report['completeness'].get('findings',[]))
                self.json({'ok':True,'acknowledgement':item,'report':validate_research_files(universe,mode='full',build_id=build_id)}); return
            if u.path=='/api/completeness/revoke':
                universe=body.get('universe') or load_config().get('active_universe','CSI300'); paths=integrity_review_paths(universe,build_id)
                revoke_acknowledgement(paths,body.get('issue_id')); self.json({'ok':True,'report':validate_research_files(universe,mode='full',build_id=build_id)}); return
            if u.path=='/api/completeness/repair':
                universe=body.get('universe') or load_config().get('active_universe','CSI300')
                self.json(bulk_repair_issues(universe,body.get('issue_ids',[]),build_id=build_id)); return
            if u.path=='/api/repair-safe':
                universe=body.get('universe') or load_config().get('active_universe','CSI300')
                self.json({'ok':True,**repair_duplicate_keys(integrity_review_paths(universe,build_id),universe)});return
            if u.path=="/api/run":
                self.json(run_research(body)); return
            if u.path=="/api/strategy-grid":
                self.json(run_strategy_grid(body)); return
            self.json({"ok":False,"error":"Not found."},404)
        except Exception as e:
            write_log("POST failure:\n" + traceback.format_exc())
            result={"ok":False,"error":f"{type(e).__name__}: {e}"}
            if urlparse(self.path).path=='/api/live/preview':
                message=str(e)
                result['preview_status']=('DATASET BLOCKED' if 'integrity fail' in message.lower() else
                    'NO TARGET FOUND' if 'NO TARGET FOUND' in message else
                    'INVALID SIGNAL DATE' if 'date' in message.lower() and 'MISMATCH' not in message else 'ERROR')
            self.json(result,500)

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

def main(on_server=None, open_browser=True):
    try:
        cfg=load_config(); ensure_dirs(cfg)
        for universe in UNIVERSES:
            recover_publication(selected_universe_paths(cfg,universe))
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
        try:
            if on_server is not None: on_server(server)
            if open_browser:
                threading.Thread(target=startup_browser,args=(port,),daemon=True).start()
            server.serve_forever()
        finally:
            server.server_close()
    except Exception as e:
        write_log("Fatal startup failure:\n" + traceback.format_exc())
        if on_server is None:
            show_windows_error(
                "Momentum Lab Startup Error",
                f"{type(e).__name__}: {e}\n\n"
                f"Error log: {LOG_DIR / 'momentumlab.log'}"
            )
        raise

run_research=consistent_read(run_research)
run_strategy_grid=consistent_read(run_strategy_grid)
live_market=consistent_read(live_market)
historical_reconstruction=consistent_read(historical_reconstruction)
historical_target_preview=consistent_read(historical_target_preview)
run_research_audit=consistent_read(run_research_audit)

if __name__=="__main__":
    main()

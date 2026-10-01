"""Windows packaging entry point; owns startup/shutdown, never strategy calculations."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser


def runtime_root():
    return Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent


FOLDERS = ('data', 'results', 'live', 'logs', 'backups', 'config')


def prepare_root(root):
    for name in FOLDERS:
        folder = root / name
        folder.mkdir(exist_ok=True)
        probe = folder / f'.write-test-{os.getpid()}'
        try:
            with probe.open('x', encoding='utf-8') as stream:
                stream.write('')
        finally:
            probe.unlink(missing_ok=True)
    settings = root / 'config' / 'settings.json'
    if settings.exists():
        if not isinstance(json.loads(settings.read_text(encoding='utf-8')), dict):
            raise ValueError('config/settings.json must contain a JSON object.')
    # Matplotlib's writable font cache must travel with this portable app.
    os.environ['MPLCONFIGDIR'] = str(root / 'logs' / 'matplotlib')


class Instance:
    """Kernel handles vanish on process exit; stale JSON cannot hold a lock."""
    def __init__(self, root):
        key = hashlib.sha256(os.path.normcase(str(root.resolve())).encode()).hexdigest()[:32]
        self.name = 'Local\\MomentumLab-' + key
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in [
            ('CreateMutexW', [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('CreateEventW', [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('OpenEventW', [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('SetEvent', [wintypes.HANDLE], wintypes.BOOL),
            ('WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ]:
            fn = getattr(self.api, name); fn.argtypes = args; fn.restype = result
        self.mutex = self.event = None

    def acquire(self):
        self.mutex = self.api.CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not self.mutex:
            raise ctypes.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS
            return False
        self.event = self.api.CreateEventW(None, False, False, self.name + '-quit')
        if not self.event:
            raise ctypes.WinError(ctypes.get_last_error())
        return True

    def request_quit(self):
        handle = self.api.OpenEventW(2, False, self.name + '-quit')
        if not handle:
            return False
        try:
            return bool(self.api.SetEvent(handle))
        finally:
            self.api.CloseHandle(handle)

    def quit_requested(self):
        return self.api.WaitForSingleObject(self.event, 0) == 0

    def close(self):
        for handle in (self.event, self.mutex):
            if handle: self.api.CloseHandle(handle)
        self.event = self.mutex = None


def existing_url(root, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = json.loads((root / 'logs' / 'instance.json').read_text(encoding='utf-8'))
            port = int(state['port'])
            if not 8765 <= port < 8795: raise ValueError('Invalid instance port')
            url = f'http://127.0.0.1:{port}'
            with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
                health = json.load(response)
            if health.get('ok') and Path(health['app_root']).resolve() == root.resolve():
                return url + '/'
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.2)
    raise RuntimeError('Another copy is starting or closing in this folder. Wait, then try again. See logs/momentumlab.log.')


def launch(root, instance, open_browser):
    import tkinter as tk
    from tkinter import ttk, messagebox
    window = tk.Tk()
    window.title('Momentum Lab V4')
    window.geometry('500x185')
    window.resizable(False, False)
    status = tk.StringVar(value='Starting local backend…')
    ttk.Label(window, text='Momentum Lab V4', font=('Segoe UI', 15)).pack(pady=(16, 8))
    ttk.Label(window, textvariable=status, wraplength=460).pack(pady=6)
    ttk.Label(window, text='Keep this window open. Close it or choose Quit to stop the app.').pack()
    buttons = ttk.Frame(window); buttons.pack(pady=12)
    state = {'server': None, 'app': None, 'closing': False, 'error': None}
    events = queue.Queue()

    def ready(server):
        # server_close waits for requests to finish; no daemon request is abandoned.
        server.daemon_threads = False
        state['server'] = server
        (root / 'logs' / 'instance.json').write_text(
            json.dumps({'pid': os.getpid(), 'port': server.server_port}), encoding='utf-8')
        events.put('ready')

    def backend():
        try:
            import MomentumLabV2 as app
            state['app'] = app
            settings = root / 'config' / 'settings.json'
            stored = json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else {}
            cfg = app.resolve_config_paths({**app.DEFAULTS, **stored})
            # Portable builds must not silently use files on the original computer.
            paths = [cfg['data_folder'], cfg['results_folder']]
            paths += [cfg[k] for k in app.FILE_KEYS if cfg.get(k)]
            paths += [v for p in cfg.get('universe_file_paths', {}).values() for v in p.values() if v]
            outside = [str(p) for p in paths if not Path(p).resolve().is_relative_to(root)]
            if outside:
                raise ValueError('Configured paths are outside this portable folder. Copy those files here and update '
                                 'config/settings.json to relative paths:\n' + '\n'.join(outside))
            app.main(on_server=ready, open_browser=open_browser)
        except Exception:
            state['error'] = traceback.format_exc()
            with (root / 'logs' / 'launcher-error.log').open('a', encoding='utf-8') as stream:
                stream.write(state['error'] + '\n')
            events.put('error')

    worker = threading.Thread(target=backend, name='MomentumLab-backend')

    def browse():
        server = state['server']
        if server and not state['closing']:
            webbrowser.open(f'http://127.0.0.1:{server.server_port}/')

    def close():
        if state['closing']: return
        server, app = state['server'], state['app']
        if state['error'] or not worker.is_alive():
            state['closing'] = True
            window.destroy(); return
        if server is None:
            status.set('Startup is still in progress. Please wait before closing.'); return
        if not app._REQUEST_LOCK.acquire(blocking=False):
            status.set('A request is running. Wait for it to finish, then choose Quit again.'); return
        try:
            if app._JOB_THREAD is not None and app._JOB_THREAD.is_alive():
                status.set('A data job is active. Finish or cancel it in Data Manager before quitting.'); return
            server.stopping = True
            state['closing'] = True
        finally:
            app._REQUEST_LOCK.release()
        status.set('Closing backend and finishing pending requests…')
        open_button.config(state='disabled')
        threading.Thread(target=server.shutdown, name='MomentumLab-shutdown').start()

    open_button = ttk.Button(buttons, text='Open Momentum Lab', command=browse, state='disabled')
    open_button.pack(side='left', padx=8)
    ttk.Button(buttons, text='Quit', command=close).pack(side='left', padx=8)
    window.protocol('WM_DELETE_WINDOW', close)

    def poll():
        while not events.empty():
            event = events.get_nowait()
            if event == 'ready':
                status.set(f'Running at http://127.0.0.1:{state["server"].server_port}/')
                open_button.config(state='normal')
            else:
                status.set('Startup failed. Details are in logs/launcher-error.log.')
                messagebox.showerror('Momentum Lab startup failed', state['error'][-2500:], parent=window)
        if instance.quit_requested(): close()
        if state['closing'] and not worker.is_alive():
            window.destroy(); return
        if window.winfo_exists(): window.after(200, poll)

    worker.start()
    window.after(200, poll)
    window.mainloop()
    worker.join()
    (root / 'logs' / 'instance.json').unlink(missing_ok=True)
    return not bool(state['error'])


def main():
    parser = argparse.ArgumentParser(description='Momentum Lab portable Windows launcher')
    parser.add_argument('--no-browser', action='store_true', help='Start the app without opening a browser (validation).')
    parser.add_argument('--quit', action='store_true', help='Ask the instance in this folder to quit safely when idle.')
    args = parser.parse_args()
    root = runtime_root()
    instance = Instance(root)
    try:
        if args.quit:
            return 0 if instance.request_quit() else 1
        if not instance.acquire():
            url = existing_url(root)
            if not args.no_browser: webbrowser.open(url)
            return 0
        prepare_root(root)
        return 0 if launch(root, instance, not args.no_browser) else 1
    except Exception:
        error = traceback.format_exc()
        try:
            (root / 'logs').mkdir(exist_ok=True)
            (root / 'logs' / 'launcher-error.log').write_text(error, encoding='utf-8')
        except OSError: pass
        ctypes.windll.user32.MessageBoxW(None,
            'Momentum Lab could not start. Keep _internal and the data/config folders beside the EXE in a writable folder.\n\n' + error[-2500:],
            'Momentum Lab startup error', 0x10)
        return 1
    finally:
        instance.close()


if __name__ == '__main__':
    raise SystemExit(main())

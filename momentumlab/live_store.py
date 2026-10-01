"""Transactional local storage. Corrections append records; history is never deleted."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import uuid
from .serialization import dumps


def stamp():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return dumps(value, ensure_ascii=False)


class LiveStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_version VALUES(1);
                CREATE TABLE IF NOT EXISTS portfolios(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    status TEXT NOT NULL, start_date TEXT NOT NULL, capital REAL NOT NULL,
                    strategy TEXT NOT NULL, source_run_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'ACTIVE', archived INTEGER NOT NULL DEFAULT 0,
                    parent_id TEXT, activation_date TEXT);
                CREATE TABLE IF NOT EXISTS trades(id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
                    supersedes TEXT UNIQUE REFERENCES trades(id), request_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, recorded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
                    signal_date TEXT NOT NULL, payload TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    UNIQUE(portfolio_id, signal_date));
                CREATE TABLE IF NOT EXISTS rebalances(id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
                    payload TEXT NOT NULL, recorded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cash_flows(id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
                    request_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, recorded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS nav_history(id INTEGER PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
                    snapshot_id TEXT NOT NULL, date TEXT NOT NULL, actual_nav REAL NOT NULL, model_nav REAL,
                    recorded_at TEXT NOT NULL, paper_nav REAL, mode TEXT NOT NULL DEFAULT 'ACTIVE');
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
                    event_type TEXT NOT NULL, payload TEXT NOT NULL, recorded_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS nav_by_portfolio ON nav_history(portfolio_id, snapshot_id);
            ''')
            portfolio_columns={r[1] for r in db.execute('PRAGMA table_info(portfolios)')}
            for column,definition in [('mode',"TEXT NOT NULL DEFAULT 'ACTIVE'"),('archived','INTEGER NOT NULL DEFAULT 0'),('parent_id','TEXT'),('activation_date','TEXT')]:
                if column not in portfolio_columns: db.execute(f'ALTER TABLE portfolios ADD COLUMN {column} {definition}')
            nav_columns={r[1] for r in db.execute('PRAGMA table_info(nav_history)')}
            if 'paper_nav' not in nav_columns: db.execute('ALTER TABLE nav_history ADD COLUMN paper_nav REAL')
            if 'mode' not in nav_columns: db.execute("ALTER TABLE nav_history ADD COLUMN mode TEXT NOT NULL DEFAULT 'ACTIVE'")
            db.execute("UPDATE portfolios SET mode='PAPER' WHERE status='PAPER' OR json_extract(strategy,'$.mode')='PAPER'")
            db.execute("""UPDATE portfolios SET mode='PAPER' WHERE id IN
                (SELECT portfolio_id FROM events WHERE event_type='Portfolio Created'
                 AND json_extract(payload,'$.status')='PAPER')""")
            db.execute('DELETE FROM schema_version')
            db.execute('INSERT INTO schema_version VALUES(3)')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, portfolio_id, kind, payload):
        db.execute('INSERT INTO events(portfolio_id,event_type,payload,recorded_at) VALUES(?,?,?,?)',
                   (portfolio_id, kind, encode(payload), stamp()))

    @staticmethod
    def portfolio(db, portfolio_id):
        row = db.execute('SELECT * FROM portfolios WHERE id=?', (portfolio_id,)).fetchone()
        if row is None:
            raise ValueError('Portfolio not found.')
        result = dict(row)
        result['strategy'] = json.loads(result['strategy'])
        return result

    @staticmethod
    def records(db, table, portfolio_id):
        if table not in {'trades','signals','rebalances','cash_flows','events'}:
            raise ValueError('Invalid record type.')
        return [{**dict(r), 'payload': json.loads(r['payload'])} for r in db.execute(
            f'SELECT * FROM {table} WHERE portfolio_id=? ORDER BY recorded_at,id', (portfolio_id,))]

    def list_portfolios(self, view='active', summary=False):
        with self.transaction() as db:
            if view is True or view=='all': where=''
            elif view=='archived': where='WHERE archived=1'
            elif view=='closed': where="WHERE archived=0 AND status='CLOSED'"
            elif view=='active': where="WHERE archived=0 AND status<>'CLOSED'"
            else: raise ValueError('Portfolio view must be active, closed, archived or all.')
            if summary:return [dict(r) for r in db.execute(f'SELECT id,name,status,archived FROM portfolios {where} ORDER BY created_at DESC').fetchall()]
            return [self.portfolio(db, r[0]) for r in db.execute(f'SELECT id FROM portfolios {where} ORDER BY created_at DESC').fetchall()]


def identity():
    return uuid.uuid4().hex

"""SQLite access, schema migrations and transaction helper.

All writes go through ``tx()`` which opens ``BEGIN IMMEDIATE`` so that two
requests can never interleave inside one business operation (for example two
scale operators finishing weighings at the same second both asking for the
next waybill number).
"""
import sqlite3
from contextlib import contextmanager

from flask import current_app, g

SCHEMA_VERSION = 1

SCHEMA = r'''
CREATE TABLE IF NOT EXISTS brigadiers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  full_name TEXT,
  phone TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  full_name TEXT NOT NULL,
  role TEXT NOT NULL,
  brigadier_id INTEGER REFERENCES brigadiers(id),
  phone TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  telegram_id TEXT UNIQUE,
  tg_link_code TEXT UNIQUE,
  tg_link_expires TEXT,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS fields (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  area_ha REAL NOT NULL CHECK (area_ha > 0),
  brigadier_id INTEGER REFERENCES brigadiers(id),
  polygon_json TEXT,
  notes TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  full_name TEXT NOT NULL,
  name_key TEXT NOT NULL,
  phone TEXT,
  photo_id INTEGER,
  brigadier_id INTEGER REFERENCES brigadiers(id),
  active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workers_key ON workers(name_key);

CREATE TABLE IF NOT EXISTS equipment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('traktor','telashka','kombayn','mashina')),
  code TEXT NOT NULL UNIQUE COLLATE NOCASE,
  plate TEXT,
  operator_name TEXT,
  ownership TEXT NOT NULL DEFAULT 'own',
  notes TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seasons (
  year INTEGER PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'OCHIQ' CHECK (status IN ('OCHIQ','YOPILGAN')),
  planned_area_ha REAL,
  notes TEXT,
  closed_at TEXT,
  closed_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT,
  updated_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS counters (
  name TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

-- One trailer trip: opened at the field, filled (TOLDI), weighed (TORTILDI).
CREATE TABLE IF NOT EXISTS trailer_loads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  season_year INTEGER NOT NULL REFERENCES seasons(year),
  load_date TEXT NOT NULL,
  trailer_id INTEGER NOT NULL REFERENCES equipment(id),
  tractor_id INTEGER REFERENCES equipment(id),
  field_id INTEGER NOT NULL REFERENCES fields(id),
  brigadier_id INTEGER NOT NULL REFERENCES brigadiers(id),
  vehicle_plate TEXT,
  driver_name TEXT,
  status TEXT NOT NULL DEFAULT 'OCHIQ' CHECK (status IN ('OCHIQ','TOLDI','TORTILDI','BEKOR')),
  hand_kg REAL NOT NULL DEFAULT 0,
  combine_kg REAL NOT NULL DEFAULT 0,
  internal_kg REAL NOT NULL DEFAULT 0,
  note TEXT,
  client_uuid TEXT UNIQUE,
  opened_by INTEGER REFERENCES users(id),
  opened_at TEXT NOT NULL,
  full_by INTEGER REFERENCES users(id),
  full_at TEXT,
  weighed_at TEXT,
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT
);
-- A trailer can carry only one unfinished load at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_trailer_active_load
  ON trailer_loads(trailer_id) WHERE status IN ('OCHIQ','TOLDI');
CREATE INDEX IF NOT EXISTS idx_loads_season_date ON trailer_loads(season_year, load_date);

CREATE TABLE IF NOT EXISTS harvests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  season_year INTEGER NOT NULL REFERENCES seasons(year),
  work_date TEXT NOT NULL,
  load_id INTEGER NOT NULL REFERENCES trailer_loads(id),
  worker_id INTEGER REFERENCES workers(id),
  field_id INTEGER NOT NULL REFERENCES fields(id),
  brigadier_id INTEGER NOT NULL REFERENCES brigadiers(id),
  trailer_id INTEGER NOT NULL REFERENCES equipment(id),
  tractor_id INTEGER REFERENCES equipment(id),
  combine_id INTEGER REFERENCES equipment(id),
  method TEXT NOT NULL CHECK (method IN ('hand','combine')),
  kg REAL NOT NULL CHECK (kg > 0),
  note TEXT,
  source TEXT NOT NULL DEFAULT 'web',
  client_uuid TEXT UNIQUE,
  entered_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT,
  CHECK (method = 'combine' OR worker_id IS NOT NULL),
  CHECK (method = 'hand' OR combine_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_harvests_load ON harvests(load_id);
CREATE INDEX IF NOT EXISTS idx_harvests_date ON harvests(season_year, work_date);
CREATE INDEX IF NOT EXISTS idx_harvests_worker ON harvests(worker_id);

CREATE TABLE IF NOT EXISTS weighings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  load_id INTEGER NOT NULL UNIQUE REFERENCES trailer_loads(id),
  gross_kg REAL NOT NULL CHECK (gross_kg > 0),
  gross_at TEXT NOT NULL,
  gross_by INTEGER REFERENCES users(id),
  tare_kg REAL,
  tare_at TEXT,
  tare_by INTEGER REFERENCES users(id),
  net_kg REAL,
  internal_kg REAL,
  diff_kg REAL,
  diff_reason TEXT,
  scale_no TEXT,
  status TEXT NOT NULL DEFAULT 'BRUTTO' CHECK (status IN ('BRUTTO','YAKUNLANDI')),
  created_at TEXT NOT NULL,
  updated_at TEXT,
  CHECK (tare_kg IS NULL OR (tare_kg >= 0 AND tare_kg < gross_kg))
);

CREATE TABLE IF NOT EXISTS waybills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seq INTEGER NOT NULL UNIQUE,
  number TEXT NOT NULL UNIQUE,
  season_year INTEGER NOT NULL REFERENCES seasons(year),
  load_id INTEGER NOT NULL UNIQUE REFERENCES trailer_loads(id),
  net_kg REAL NOT NULL CHECK (net_kg > 0),
  document_date TEXT NOT NULL,
  destination TEXT NOT NULL DEFAULT 'Nayman',
  price_per_kg INTEGER,
  status TEXT NOT NULL DEFAULT 'YARATILDI' CHECK (status IN ('YARATILDI','QABUL','BEKOR')),
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT,
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT
);

CREATE TABLE IF NOT EXISTS nayman_receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  waybill_id INTEGER NOT NULL UNIQUE REFERENCES waybills(id),
  accepted_kg REAL NOT NULL CHECK (accepted_kg >= 0),
  diff_kg REAL NOT NULL,
  diff_reason TEXT,
  received_date TEXT NOT NULL,
  receiver_name TEXT,
  price_per_kg INTEGER,
  amount INTEGER,
  note TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  season_year INTEGER NOT NULL REFERENCES seasons(year),
  waybill_id INTEGER REFERENCES waybills(id),
  payment_date TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK (amount > 0),
  method TEXT,
  payer TEXT NOT NULL DEFAULT 'Nayman',
  note TEXT,
  client_uuid TEXT UNIQUE,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT
);

CREATE TABLE IF NOT EXISTS expenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  season_year INTEGER NOT NULL REFERENCES seasons(year),
  expense_date TEXT NOT NULL,
  category TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK (amount > 0),
  field_id INTEGER REFERENCES fields(id),
  brigadier_id INTEGER REFERENCES brigadiers(id),
  equipment_id INTEGER REFERENCES equipment(id),
  payer TEXT,
  note TEXT,
  cash_entry_id INTEGER,
  client_uuid TEXT UNIQUE,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT
);

CREATE TABLE IF NOT EXISTS cash_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  season_year INTEGER NOT NULL REFERENCES seasons(year),
  entry_date TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
  category TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK (amount > 0),
  worker_id INTEGER REFERENCES workers(id),
  expense_id INTEGER REFERENCES expenses(id),
  payment_id INTEGER REFERENCES payments(id),
  counterparty TEXT,
  note TEXT,
  client_uuid TEXT UNIQUE,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cash_season ON cash_entries(season_year, entry_date);

CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id INTEGER,
  path TEXT NOT NULL,
  thumb_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  caption TEXT,
  taken_at TEXT,
  uploaded_at TEXT NOT NULL,
  lat REAL,
  lon REAL,
  source TEXT NOT NULL DEFAULT 'web',
  tg_file_unique_id TEXT,
  season_year INTEGER,
  load_id INTEGER REFERENCES trailer_loads(id),
  field_id INTEGER REFERENCES fields(id),
  brigadier_id INTEGER REFERENCES brigadiers(id),
  waybill_id INTEGER REFERENCES waybills(id),
  uploaded_by INTEGER REFERENCES users(id),
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_photos_entity ON photos(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_photos_load ON photos(load_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_photos_same_file_same_entity
  ON photos(sha256, entity_type, IFNULL(entity_id, 0)) WHERE voided_at IS NULL;

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT,
  old_json TEXT,
  new_json TEXT,
  reason TEXT,
  source TEXT NOT NULL DEFAULT 'web',
  ip TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);
-- Audit history is append-only: nobody (not even admin through the app) can rewrite it.
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update BEFORE UPDATE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete BEFORE DELETE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;

CREATE TABLE IF NOT EXISTS login_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(key, at);

CREATE TABLE IF NOT EXISTS telegram_updates (
  update_id INTEGER PRIMARY KEY,
  received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_sessions (
  telegram_id TEXT PRIMARY KEY,
  step TEXT NOT NULL,
  data_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);

-- Area/brigade as they were when a season was closed, so old kg/ha never changes later.
CREATE TABLE IF NOT EXISTS field_seasons (
  year INTEGER NOT NULL REFERENCES seasons(year),
  field_id INTEGER NOT NULL REFERENCES fields(id),
  area_ha REAL NOT NULL,
  brigadier_id INTEGER REFERENCES brigadiers(id),
  PRIMARY KEY (year, field_id)
);

-- External read-only clients: Azizbek ERP (kind='erp') and TV screens (kind='tv').
-- Only a SHA-256 hash of each key is stored; the key itself is shown once at creation.
CREATE TABLE IF NOT EXISTS integration_clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('erp','tv')),
  name TEXT NOT NULL,
  key_prefix TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  scopes TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  revoked_at TEXT,
  revoked_by INTEGER REFERENCES users(id),
  last_used_at TEXT,
  last_ip TEXT
);

CREATE TABLE IF NOT EXISTS integration_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER REFERENCES integration_clients(id),
  at TEXT NOT NULL,
  at_epoch REAL NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  query TEXT,
  status INTEGER NOT NULL,
  rows INTEGER,
  ip TEXT,
  ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_integration_log ON integration_log(client_id, at_epoch);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
'''


def connect(path):
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA synchronous = NORMAL')
    conn.execute('PRAGMA busy_timeout = 30000')
    return conn


def get_db():
    if 'db' not in g:
        g.db = connect(current_app.config['SURXON'].DB_PATH)
    return g.db


def close_db(_exc=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


@contextmanager
def tx(db=None):
    """Run a block inside one IMMEDIATE transaction; roll back on any error."""
    db = db or get_db()
    if db.in_transaction:
        # Nested call: the outer transaction owns commit/rollback.
        yield db
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        yield db
    except BaseException:
        db.execute('ROLLBACK')
        raise
    else:
        db.execute('COMMIT')


def q(sql, params=(), one=False, db=None):
    cur = (db or get_db()).execute(sql, params)
    return cur.fetchone() if one else cur.fetchall()


def scalar(sql, params=(), db=None, default=0):
    row = (db or get_db()).execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def migrate(db):
    db.executescript(SCHEMA)
    # single atomic statement: safe when several gunicorn workers start at once
    db.execute('INSERT INTO schema_version(version) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM schema_version)',
               (SCHEMA_VERSION,))
    # Future migrations: if row['version'] < 2: ALTER TABLE ...; UPDATE schema_version ...


def next_counter(db, name):
    """Gap-free sequence. Must be called inside tx() so the increment is atomic."""
    assert db.in_transaction, 'next_counter requires an open transaction'
    db.execute('INSERT OR IGNORE INTO counters(name, value) VALUES (?, 0)', (name,))
    db.execute('UPDATE counters SET value = value + 1 WHERE name = ?', (name,))
    return db.execute('SELECT value FROM counters WHERE name = ?', (name,)).fetchone()[0]

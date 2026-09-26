"""SQLite access, schema migrations and transaction helper.

All writes go through ``tx()`` which opens ``BEGIN IMMEDIATE`` so that two
requests can never interleave inside one business operation (for example two
scale operators finishing weighings at the same second both asking for the
next waybill number).
"""
import sqlite3
from contextlib import contextmanager

from flask import current_app, g

SCHEMA_VERSION = 11

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
  last_login_at TEXT,
  station_id INTEGER REFERENCES stations(id),     -- punkt operator: the one receiving point they work at
  cashbox_id INTEGER REFERENCES cashboxes(id)     -- cashier: the cash box they hand money out of
);

-- Real cash boxes (naqd pul). Money is always whole so‘m (INTEGER), never float.
CREATE TABLE IF NOT EXISTS cashboxes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

-- Receiving points (qabul punktlari), e.g. Nayman-1.
CREATE TABLE IF NOT EXISTS stations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  address TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
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
  created_at TEXT NOT NULL,
  tariff_type TEXT CHECK (tariff_type IS NULL OR tariff_type IN ('tonna','gektar','kunlik')),  -- combine pay basis
  tariff_rate INTEGER CHECK (tariff_rate IS NULL OR tariff_rate > 0)                            -- so‘m per unit
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
  voided_at TEXT,
  trip_no TEXT,                                   -- TL-2026-000026, issued by the server, never edited
  station_id INTEGER REFERENCES stations(id)      -- receiving point the trip goes to
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
  rate INTEGER,              -- pay rate in force when this kg was written (so‘m per rate_unit); never recalculated
  rate_unit TEXT,            -- 'kg' (hand) or 'tonna' (combine)
  amount INTEGER,            -- kg × rate, frozen; NULL = no rate was set → “hisoblanmagan”
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
  -- 'tarozi' = weighbridge / Nayman point (gross - tare); 'dala' = hand-only trip closed with the sum of field-scale kg
  basis TEXT NOT NULL DEFAULT 'tarozi' CHECK (basis IN ('tarozi','dala')),
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
  voided_at TEXT,
  arrived_at TEXT,                                -- punkt operator marked "KELDI"
  arrived_by INTEGER REFERENCES users(id)
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
  doc_no TEXT,
  cashbox_id INTEGER REFERENCES cashboxes(id),
  station_id INTEGER REFERENCES stations(id),
  status TEXT NOT NULL DEFAULT 'TASDIQLANGAN' CHECK (status IN ('TEKSHIRILMAGAN','TASDIQLANGAN')),
  checked_by INTEGER REFERENCES users(id),
  checked_at TEXT,
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
  doc_no TEXT,                                    -- INC-2026-000001 / EXP-… / PAY-… / ADJ-…
  cashbox_id INTEGER REFERENCES cashboxes(id),
  source TEXT,                                    -- where incoming money came from (Direktor, Nayman…)
  combine_id INTEGER REFERENCES equipment(id),
  payout_id INTEGER,
  debt_id INTEGER,
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

-- Payment orders: the accountant prepares, the cashier hands out the cash ("BERILDI") exactly once.
CREATE TABLE IF NOT EXISTS payouts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_no TEXT NOT NULL UNIQUE,
  season_year INTEGER NOT NULL REFERENCES seasons(year),
  kind TEXT NOT NULL CHECK (kind IN ('worker','combine')),
  purpose TEXT NOT NULL DEFAULT 'pay' CHECK (purpose IN ('pay','advance')),
  worker_id INTEGER REFERENCES workers(id),
  combine_id INTEGER REFERENCES equipment(id),
  amount INTEGER NOT NULL CHECK (amount > 0),
  status TEXT NOT NULL DEFAULT 'TAYYOR' CHECK (status IN ('TAYYOR','BERILDI','BEKOR')),
  note TEXT,
  client_uuid TEXT UNIQUE,
  cashbox_id INTEGER REFERENCES cashboxes(id),
  prepared_by INTEGER REFERENCES users(id),
  prepared_at TEXT NOT NULL,
  paid_by INTEGER REFERENCES users(id),
  paid_at TEXT,
  cash_entry_id INTEGER REFERENCES cash_entries(id),
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT,
  CHECK ((kind = 'worker' AND worker_id IS NOT NULL) OR (kind = 'combine' AND combine_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_payout_open_worker ON payouts(worker_id) WHERE status='TAYYOR' AND kind='worker';
CREATE UNIQUE INDEX IF NOT EXISTS uq_payout_open_combine ON payouts(combine_id) WHERE status='TAYYOR' AND kind='combine';

-- Combine work paid per day or per hectare (per-tonne pay is frozen on the harvest rows themselves).
CREATE TABLE IF NOT EXISTS combine_work (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  season_year INTEGER NOT NULL REFERENCES seasons(year),
  combine_id INTEGER NOT NULL REFERENCES equipment(id),
  work_date TEXT NOT NULL,
  unit TEXT NOT NULL CHECK (unit IN ('kunlik','gektar')),
  qty REAL NOT NULL CHECK (qty > 0),
  rate INTEGER NOT NULL CHECK (rate > 0),
  amount INTEGER NOT NULL CHECK (amount > 0),
  field_id INTEGER REFERENCES fields(id),
  source TEXT NOT NULL DEFAULT 'manual',
  note TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_combine_day ON combine_work(combine_id, work_date) WHERE unit='kunlik' AND voided_at IS NULL;

-- Day close: system balance vs counted cash; the difference is booked, the day is then locked.
CREATE TABLE IF NOT EXISTS cash_days (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cashbox_id INTEGER NOT NULL REFERENCES cashboxes(id),
  day TEXT NOT NULL,
  opening INTEGER NOT NULL,
  inflow INTEGER NOT NULL,
  outflow INTEGER NOT NULL,
  system_balance INTEGER NOT NULL,
  counted INTEGER NOT NULL,
  diff INTEGER NOT NULL,
  reason TEXT,
  note TEXT,
  adjust_entry_id INTEGER REFERENCES cash_entries(id),
  closed_by INTEGER REFERENCES users(id),
  closed_at TEXT NOT NULL,
  UNIQUE (cashbox_id, day)
);

-- Receivables (biz olishimiz kerak) and payables (biz berishimiz kerak); settled through the cash box.
CREATE TABLE IF NOT EXISTS debts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_no TEXT NOT NULL UNIQUE,
  season_year INTEGER NOT NULL REFERENCES seasons(year),
  direction TEXT NOT NULL CHECK (direction IN ('OLISH','BERISH')),
  counterparty TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK (amount > 0),
  reason TEXT,
  debt_date TEXT NOT NULL,
  due_date TEXT,
  note TEXT,
  client_uuid TEXT UNIQUE,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT
);

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

-- v2: generated documents (waybill PDFs) — every version is kept
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  waybill_id INTEGER NOT NULL REFERENCES waybills(id),
  kind TEXT NOT NULL CHECK (kind IN ('nayman','ichki')),
  version INTEGER NOT NULL,
  reason TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  superseded INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE (waybill_id, kind, version)
);

-- v2: reliable delivery queue to external archives (Telegram channel, Google Sheets).
-- Jobs are written in the same transaction as the business record, sent later with retries.
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  next_try_at REAL NOT NULL DEFAULT 0,
  sent_at TEXT,
  UNIQUE (channel, kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(status, next_try_at);

-- v2: last real success / error per external channel (shown honestly on the status page)
CREATE TABLE IF NOT EXISTS channel_status (
  channel TEXT PRIMARY KEY,
  last_ok_at TEXT,
  last_error_at TEXT,
  last_error TEXT,
  ok_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0
);

-- v6: "Kuzatuv" — the system asks people (tractor drivers, brigadiers, agronomists...) for photos/videos
-- through the Telegram bot; the answers show on the manager's dashboard.
CREATE TABLE IF NOT EXISTS tg_chats (
  chat_id TEXT PRIMARY KEY,
  title TEXT,
  type TEXT,
  is_work INTEGER NOT NULL DEFAULT 0,       -- confirmed by admin/manager as the work group
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT
);

-- v11: where staff are (Telegram live location, or the phone while the app is open). Only admin / director see it.
CREATE TABLE IF NOT EXISTS staff_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id),
  member_id INTEGER REFERENCES tg_members(id),
  lat REAL NOT NULL, lon REAL NOT NULL, acc REAL,
  source TEXT NOT NULL,                      -- telegram / ilova
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_staffpos_user ON staff_positions(user_id, at);
CREATE INDEX IF NOT EXISTS idx_staffpos_member ON staff_positions(member_id, at);

CREATE TABLE IF NOT EXISTS tg_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  full_name TEXT NOT NULL,
  role_label TEXT,                          -- Traktorchi, Brigadir, Agronom ... (free text)
  telegram_id TEXT UNIQUE,
  username TEXT,
  user_id INTEGER UNIQUE REFERENCES users(id),
  equipment_id INTEGER REFERENCES equipment(id),
  field_id INTEGER REFERENCES fields(id),
  status TEXT NOT NULL DEFAULT 'YANGI' CHECK (status IN ('YANGI','FAOL','NOFAOL')),
  source TEXT NOT NULL DEFAULT 'admin',     -- admin / guruh / bot / tizim
  link_code TEXT UNIQUE,
  dm_ok INTEGER NOT NULL DEFAULT 0,         -- has opened the bot privately, so the bot may write to them directly
  group_chat_id TEXT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS media_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  text TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'any' CHECK (kind IN ('photo','video','any')),
  times TEXT NOT NULL,                      -- "09:00,16:00"
  weekdays TEXT NOT NULL DEFAULT '1234567', -- 1 = Monday
  members_json TEXT NOT NULL DEFAULT '[]',  -- member ids
  deadline_min INTEGER NOT NULL DEFAULT 120,
  active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id INTEGER NOT NULL REFERENCES tg_members(id),
  text TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'any' CHECK (kind IN ('photo','video','any')),
  rule_id INTEGER REFERENCES media_rules(id),
  slot TEXT,                                -- rule firing "YYYY-MM-DD HH:MM" (one request per member per slot)
  requested_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  due_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'KUTILMOQDA' CHECK (status IN ('KUTILMOQDA','JAVOB','KECHIKDI','BEKOR')),
  sent_at TEXT,
  sent_via TEXT,                            -- dm / group
  chat_id TEXT,
  tg_message_id INTEGER,
  send_attempts INTEGER NOT NULL DEFAULT 0,
  send_error TEXT,
  reminded_at TEXT,
  answered_at TEXT,
  late_alerted INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_media_req_slot ON media_requests(rule_id, member_id, slot) WHERE rule_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_media_req_member ON media_requests(member_id, status);
CREATE INDEX IF NOT EXISTS idx_media_req_msg ON media_requests(chat_id, tg_message_id);

CREATE TABLE IF NOT EXISTS media_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id INTEGER REFERENCES media_requests(id),
  member_id INTEGER NOT NULL REFERENCES tg_members(id),
  kind TEXT NOT NULL CHECK (kind IN ('photo','video')),
  path TEXT,                                -- NULL when a video is over the 20 MB Bot API download limit
  thumb_path TEXT,
  size_bytes INTEGER,
  duration_s INTEGER,
  caption TEXT,
  lat REAL,
  lon REAL,
  chat_id TEXT,
  tg_message_id INTEGER,
  tg_file_id TEXT,
  tg_file_unique_id TEXT UNIQUE,
  note TEXT,
  created_at TEXT NOT NULL,
  void_reason TEXT,
  voided_by INTEGER REFERENCES users(id),
  voided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_media_items_created ON media_items(created_at);
CREATE INDEX IF NOT EXISTS idx_media_items_path ON media_items(path);

-- v7: a mistake in a saved cash operation is fixed by a correction record: the original row is voided (kept, never
-- deleted or overwritten), a replacement row is written when needed, and who/when/why/old/new stays here.
-- A person without the approve right only requests it; the accountant approves or rejects.
CREATE TABLE IF NOT EXISTS cash_corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cash_entry_id INTEGER NOT NULL REFERENCES cash_entries(id),
  reason TEXT NOT NULL,
  note TEXT,
  new_amount INTEGER CHECK (new_amount IS NULL OR new_amount > 0),
  new_worker_id INTEGER REFERENCES workers(id),
  new_category TEXT,
  new_party TEXT,
  cancel INTEGER NOT NULL DEFAULT 0,
  old_json TEXT,
  new_json TEXT,
  status TEXT NOT NULL DEFAULT 'KUTILMOQDA' CHECK (status IN ('KUTILMOQDA','BAJARILDI','RAD')),
  client_uuid TEXT UNIQUE,
  requested_by INTEGER REFERENCES users(id),
  requested_at TEXT NOT NULL,
  decided_by INTEGER REFERENCES users(id),
  decided_at TEXT,
  decision_note TEXT,
  new_cash_entry_id INTEGER REFERENCES cash_entries(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_correction_open ON cash_corrections(cash_entry_id) WHERE status IN ('KUTILMOQDA','BAJARILDI');

-- v8: diesel (solyarka). Two separate balances: a ticket's money (so‘m) and the liters a fuel keeper holds.
-- Taking fuel at a station moves money off a ticket (liters × the price in force, frozen on the row) and liters onto
-- the keeper; giving fuel to a machine only moves liters off the keeper. QR codes are mandatory (a scan row is
-- issued by the server and used once); times are the server's.
CREATE TABLE IF NOT EXISTS fuel_stations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  address TEXT,
  qr_token TEXT NOT NULL UNIQUE,
  approved INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fuel_tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_no TEXT NOT NULL UNIQUE,
  station_id INTEGER NOT NULL REFERENCES fuel_stations(id),
  price_per_l INTEGER NOT NULL CHECK (price_per_l > 0),
  status TEXT NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV','YOPILGAN')),
  note TEXT,
  opened_by INTEGER REFERENCES users(id),
  opened_at TEXT NOT NULL,
  closed_by INTEGER REFERENCES users(id),
  closed_at TEXT,
  close_note TEXT,
  station_balance INTEGER
);
CREATE TABLE IF NOT EXISTS fuel_ticket_funds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL REFERENCES fuel_tickets(id),
  amount INTEGER NOT NULL CHECK (amount > 0),
  paid_from TEXT NOT NULL CHECK (paid_from IN ('kassa','bank')),
  expense_id INTEGER REFERENCES expenses(id),
  note TEXT,
  client_uuid TEXT UNIQUE,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  voided_at TEXT, voided_by INTEGER REFERENCES users(id), void_reason TEXT
);
CREATE TABLE IF NOT EXISTS fuel_prices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL REFERENCES fuel_tickets(id),
  price_per_l INTEGER NOT NULL CHECK (price_per_l > 0),
  set_by INTEGER REFERENCES users(id),
  set_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fuel_scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  target TEXT NOT NULL CHECK (target IN ('station','equipment')),
  target_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  used_at TEXT
);
CREATE TABLE IF NOT EXISTS fuel_ops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_no TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('OLISH','BERISH')),
  keeper_id INTEGER NOT NULL REFERENCES users(id),
  station_id INTEGER REFERENCES fuel_stations(id),
  ticket_id INTEGER REFERENCES fuel_tickets(id),
  equipment_id INTEGER REFERENCES equipment(id),
  liters REAL NOT NULL CHECK (liters > 0),
  price_per_l INTEGER,
  amount INTEGER,
  scan_id INTEGER REFERENCES fuel_scans(id),
  scan_at TEXT,
  photo_id INTEGER REFERENCES photos(id),
  flag TEXT,
  reason TEXT,
  reason_note TEXT,
  client_uuid TEXT UNIQUE,
  created_at TEXT NOT NULL,
  voided_at TEXT, voided_by INTEGER REFERENCES users(id), void_reason TEXT,
  CHECK ((kind='OLISH' AND ticket_id IS NOT NULL AND station_id IS NOT NULL AND amount IS NOT NULL)
         OR (kind='BERISH' AND equipment_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_fuel_ops_keeper ON fuel_ops(keeper_id, id);
CREATE INDEX IF NOT EXISTS idx_fuel_ops_equipment ON fuel_ops(equipment_id, created_at);

-- v9: field contours imported from KML / GeoJSON. A batch is parsed and kept for review; nothing reaches the
-- fields table until the person confirms names, areas and brigades. Re-importing matches by source_id (no duplicates).
CREATE TABLE IF NOT EXISTS field_imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT,
  sha256 TEXT NOT NULL,
  count INTEGER NOT NULL,
  map_total_ha REAL NOT NULL,
  data_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'KORIB_CHIQISH' CHECK (status IN ('KORIB_CHIQISH','SAQLANDI','BEKOR')),
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  saved_by INTEGER REFERENCES users(id),
  saved_at TEXT,
  result_json TEXT
);
-- v9: which brigade a field was given to, and from when (past trips keep the brigade written on them)
CREATE TABLE IF NOT EXISTS field_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  field_id INTEGER NOT NULL REFERENCES fields(id),
  brigadier_id INTEGER REFERENCES brigadiers(id),
  from_date TEXT NOT NULL,
  set_by INTEGER REFERENCES users(id),
  set_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_field_assign ON field_assignments(field_id, id);
CREATE INDEX IF NOT EXISTS idx_media_items_thumb ON media_items(thumb_path);

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
    # v1 -> v2 only added tables (documents, outbox, channel_status), created above with IF NOT EXISTS.
    # v2 -> v3: weighings.basis (hand-only trips closed from the field-scale sum)
    cols = {r[1] for r in db.execute('PRAGMA table_info(weighings)')}
    if 'basis' not in cols:
        db.execute("ALTER TABLE weighings ADD COLUMN basis TEXT NOT NULL DEFAULT 'tarozi' "
                   "CHECK (basis IN ('tarozi','dala'))")
    # v3 -> v4: receiving points, trip numbers, "KELDI" mark
    _add_column(db, 'users', 'station_id', 'INTEGER REFERENCES stations(id)')
    _add_column(db, 'trailer_loads', 'trip_no', 'TEXT')
    _add_column(db, 'trailer_loads', 'station_id', 'INTEGER REFERENCES stations(id)')
    _add_column(db, 'waybills', 'arrived_at', 'TEXT')
    _add_column(db, 'waybills', 'arrived_by', 'INTEGER REFERENCES users(id)')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_loads_trip_no ON trailer_loads(trip_no)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_loads_station ON trailer_loads(station_id)')
    _backfill_trip_numbers(db)
    # v4 -> v5: accounting (rate history, cash boxes, document numbers, payouts, combine tariffs, day close, debts)
    old_version = db.execute('SELECT version FROM schema_version').fetchone()[0]
    _add_column(db, 'users', 'cashbox_id', 'INTEGER REFERENCES cashboxes(id)')
    _add_column(db, 'equipment', 'tariff_type', "TEXT CHECK (tariff_type IS NULL OR tariff_type IN ('tonna','gektar','kunlik'))")
    _add_column(db, 'equipment', 'tariff_rate', 'INTEGER CHECK (tariff_rate IS NULL OR tariff_rate > 0)')
    for col, decl in (('rate', 'INTEGER'), ('rate_unit', 'TEXT'), ('amount', 'INTEGER')):
        _add_column(db, 'harvests', col, decl)
    for col, decl in (('doc_no', 'TEXT'), ('cashbox_id', 'INTEGER REFERENCES cashboxes(id)'), ('source', 'TEXT'),
                      ('combine_id', 'INTEGER REFERENCES equipment(id)'), ('payout_id', 'INTEGER'), ('debt_id', 'INTEGER')):
        _add_column(db, 'cash_entries', col, decl)
    for col, decl in (('doc_no', 'TEXT'), ('cashbox_id', 'INTEGER REFERENCES cashboxes(id)'),
                      ('station_id', 'INTEGER REFERENCES stations(id)'),
                      ('status', "TEXT NOT NULL DEFAULT 'TASDIQLANGAN' CHECK (status IN ('TEKSHIRILMAGAN','TASDIQLANGAN'))"),
                      ('checked_by', 'INTEGER REFERENCES users(id)'), ('checked_at', 'TEXT')):
        _add_column(db, 'expenses', col, decl)
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_doc ON cash_entries(doc_no)')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_expense_doc ON expenses(doc_no)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_cash_payout ON cash_entries(payout_id)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_harvest_worker_season ON harvests(worker_id, season_year)')
    if old_version < 5:
        _backfill_v5(db)
    _backfill_doc_numbers(db)
    # v6: whether the bot is admin / member / removed in a chat (channels are picked on the Integrations page)
    _add_column(db, 'tg_chats', 'bot_status', 'TEXT')
    # v6: the pay rate is chosen once per trip (so‘m/kg for hand AND combine) and frozen on every weighing
    _add_column(db, 'trailer_loads', 'method', "TEXT CHECK (method IS NULL OR method IN ('hand','combine'))")
    _add_column(db, 'trailer_loads', 'rate', 'INTEGER CHECK (rate IS NULL OR rate > 0)')
    _add_column(db, 'trailer_loads', 'rate_unit', 'TEXT')
    _add_column(db, 'workers', 'note', 'TEXT')
    # v6: punkt scale brutto and tara kept next to the accepted netto (NULL when a ready netto was typed)
    _add_column(db, 'nayman_receipts', 'station_gross_kg', 'REAL')
    _add_column(db, 'nayman_receipts', 'station_tare_kg', 'REAL')
    # v8: which fuel a machine burns (solyarka / benzin / gaz), whether it carries the keeper's diesel, its QR code
    _add_column(db, 'equipment', 'fuel_type', "TEXT CHECK (fuel_type IS NULL OR fuel_type IN ('solyarka','benzin','gaz'))")
    _add_column(db, 'equipment', 'fuel_carrier', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(db, 'equipment', 'qr_token', 'TEXT')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_equipment_qr ON equipment(qr_token)')
    # v9: where a field's area comes from (map = computed from the contour, not yet confirmed; qo‘lda / tasdiqlangan =
    # a person entered or confirmed it), the computed map area kept apart, and the contour's source id (KML-001…)
    _add_column(db, 'fields', 'source_id', 'TEXT')
    _add_column(db, 'fields', 'map_area_ha', 'REAL')
    _add_column(db, 'fields', 'area_source', "TEXT CHECK (area_source IS NULL OR area_source IN ('xarita','qo‘lda','tasdiqlangan'))")
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_fields_source ON fields(source_id)')
    _add_column(db, 'field_seasons', 'crop', 'TEXT')
    # v10: where the phone was — each weighing and the opening of a trip (for “which field” and “where was picked today”)
    for col in ('lat', 'lon', 'gps_acc'):
        _add_column(db, 'harvests', col, 'REAL')
    for col in ('open_lat', 'open_lon', 'open_acc'):
        _add_column(db, 'trailer_loads', col, 'REAL')
    # v11: which harvest round (1st / 2nd / 3rd picking) a trip belongs to and which part of the field it picked
    # (grid cell ids from geo.field_grid, and the hectares they cover) — for kg and centner per hectare per round
    _add_column(db, 'trailer_loads', 'harvest_round', 'INTEGER')
    _add_column(db, 'trailer_loads', 'picked_cells', 'TEXT')
    _add_column(db, 'trailer_loads', 'picked_ha', 'REAL')
    _add_column(db, 'trailer_loads', 'picked_split', 'TEXT')
    _add_column(db, 'users', 'avatar_path', 'TEXT')
    _add_column(db, 'tg_members', 'avatar_path', 'TEXT')
    _add_column(db, 'stations', 'lat', 'REAL')           # where the punkt is — for “when will the trailer arrive”
    _add_column(db, 'stations', 'lon', 'REAL')   # {field_id: hectares} when a trip picked across 2+ fields
    # Future column changes go here as: if version < N: ALTER TABLE ...
    db.execute('UPDATE schema_version SET version=? WHERE version < ?', (SCHEMA_VERSION, SCHEMA_VERSION))


def _add_column(db, table, column, decl):
    if column not in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
        db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {decl}')


def trip_number(db, season_year):
    """TL-2026-000026: per-season sequence from the counters table (call inside tx())."""
    return f'TL-{season_year}-{next_counter(db, f"trip-{season_year}"):06d}'


def _backfill_trip_numbers(db):
    """Trips created before v4 get numbers in the order they were opened (idempotent)."""
    rows = db.execute('SELECT id, season_year FROM trailer_loads WHERE trip_no IS NULL ORDER BY id').fetchall()
    if not rows:
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        for r in db.execute('SELECT id, season_year FROM trailer_loads WHERE trip_no IS NULL ORDER BY id').fetchall():
            db.execute('UPDATE trailer_loads SET trip_no=? WHERE id=?', (trip_number(db, r[1]), r[0]))
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def doc_number(db, prefix, year):
    """INC-2026-000001 style unique operation id (call inside tx()); retries never create a second one."""
    return f'{prefix}-{year}-{next_counter(db, f"doc-{prefix}-{year}"):06d}'


def cash_prefix(direction, category):
    if direction == 'IN':
        return 'INC'
    if category in ('worker_pay', 'advance', 'combine_pay'):
        return 'PAY'
    if category in ('adjust_in', 'adjust_out'):
        return 'ADJ'
    return 'EXP'


def _backfill_v5(db):
    """Once, when upgrading to v5: freeze the hand-picking rate that was in force (the rate shown until now)
    on existing rows. Rows written while no rate was set stay NULL (“hisoblanmagan”) — never guessed."""
    row = db.execute("SELECT value FROM settings WHERE key='worker_rate_hand'").fetchone()
    try:
        rate = int(float(str(row[0]).replace(',', '.'))) if row and str(row[0]).strip() else None
    except ValueError:
        rate = None
    if rate and rate > 0:
        db.execute("UPDATE harvests SET rate=?, rate_unit='kg', amount=CAST(ROUND(kg * ?) AS INTEGER) "
                   "WHERE method='hand' AND rate IS NULL", (rate, rate))


def _backfill_doc_numbers(db):
    """Operations created before v5 get INC/EXP/PAY numbers in the order they were written (idempotent)."""
    todo_c = db.execute('SELECT id, season_year, direction, category FROM cash_entries WHERE doc_no IS NULL ORDER BY id').fetchall()
    todo_e = db.execute('SELECT id, season_year, cash_entry_id FROM expenses WHERE doc_no IS NULL ORDER BY id').fetchall()
    if not todo_c and not todo_e:
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        for e in db.execute('SELECT id, season_year, cash_entry_id FROM expenses WHERE doc_no IS NULL ORDER BY id').fetchall():
            no = doc_number(db, 'EXP', e[1])
            db.execute('UPDATE expenses SET doc_no=? WHERE id=?', (no, e[0]))
            if e[2]:
                db.execute('UPDATE cash_entries SET doc_no=? WHERE id=? AND doc_no IS NULL', (no, e[2]))
        for c in db.execute('SELECT id, season_year, direction, category FROM cash_entries WHERE doc_no IS NULL ORDER BY id').fetchall():
            db.execute('UPDATE cash_entries SET doc_no=? WHERE id=?', (doc_number(db, cash_prefix(c[2], c[3]), c[1]), c[0]))
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def backup_before_upgrade(db):
    """Consistent copy of the database file before a schema upgrade (kept next to the database)."""
    try:
        row = db.execute('SELECT version FROM schema_version').fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or row[0] >= SCHEMA_VERSION:
        return None
    path = next((r[2] for r in db.execute('PRAGMA database_list') if r[1] == 'main'), '')
    if not path:
        return None
    from datetime import datetime
    target = f'{path}.oldin-v{row[0]}-{datetime.now():%Y%m%d-%H%M%S}.bak'
    db.execute('VACUUM INTO ?', (target,))
    return target



def next_counter(db, name):
    """Gap-free sequence. Must be called inside tx() so the increment is atomic."""
    assert db.in_transaction, 'next_counter requires an open transaction'
    db.execute('INSERT OR IGNORE INTO counters(name, value) VALUES (?, 0)', (name,))
    db.execute('UPDATE counters SET value = value + 1 WHERE name = ?', (name,))
    return db.execute('SELECT value FROM counters WHERE name = ?', (name,)).fetchone()[0]

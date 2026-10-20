from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB_PATH = DATA / "show.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS securities (
  symbol TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  market TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_cache (
  video_id TEXT PRIMARY KEY,
  title TEXT,
  description TEXT,
  published_at TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
  video_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  broadcast_date TEXT NOT NULL,
  published_at TEXT,
  url TEXT NOT NULL,
  corpus TEXT,
  entry_date TEXT,
  settle_date TEXT
);

CREATE TABLE IF NOT EXISTS mentions (
  video_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL,
  snippet TEXT,
  source TEXT NOT NULL,
  PRIMARY KEY (video_id, symbol)
);

CREATE TABLE IF NOT EXISTS suppressions (
  video_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  PRIMARY KEY (video_id, symbol)
);

CREATE TABLE IF NOT EXISTS prices (
  symbol TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS trades (
  video_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL,
  broadcast_date TEXT NOT NULL,
  entry_date TEXT,
  entry_price REAL,
  shares INTEGER,
  cost REAL,
  settle_date TEXT,
  exit_price REAL,
  mark_price REAL,
  pnl REAL,
  pnl_pct REAL,
  status TEXT NOT NULL,
  snippet TEXT,
  PRIMARY KEY (video_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_episodes_settle ON episodes(settle_date);
CREATE INDEX IF NOT EXISTS idx_prices_symbol ON prices(symbol);
"""


def connect() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )

"""Lapisan SQLite: transaksi, kategori, job, run agent, dan langkah trace."""
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import get_settings

CATEGORIES = [
    ("makanan_minuman", "pengeluaran", "Makan, minum, jajan, groceries harian"),
    ("transportasi", "pengeluaran", "Bensin, ojol, parkir, tol, tiket"),
    ("belanja", "pengeluaran", "Belanja barang, marketplace, pakaian, elektronik"),
    ("tagihan", "pengeluaran", "Listrik, air, internet, pulsa, langganan, cicilan"),
    ("kesehatan", "pengeluaran", "Obat, dokter, asuransi"),
    ("pendidikan", "pengeluaran", "Kuliah, buku, kursus"),
    ("hiburan", "pengeluaran", "Nonton, game, liburan, hobi"),
    ("transfer_keluar", "pengeluaran", "Kirim uang ke orang lain"),
    ("lainnya_keluar", "pengeluaran", "Pengeluaran lain"),
    ("gaji", "pemasukan", "Gaji, honor, upah"),
    ("transfer_masuk", "pemasukan", "Terima transfer / kiriman uang"),
    ("lainnya_masuk", "pemasukan", "Pemasukan lain (bonus, cashback, jual barang)"),
]
CATEGORY_NAMES = [c[0] for c in CATEGORIES]

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
  name TEXT PRIMARY KEY, kind TEXT NOT NULL, description TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('pemasukan','pengeluaran')),
  amount INTEGER NOT NULL,
  category TEXT NOT NULL,
  description TEXT,
  merchant TEXT,
  date TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  items_json TEXT,
  run_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  input_type TEXT NOT NULL,
  status TEXT NOT NULL,
  run_id TEXT,
  payload_json TEXT,
  error TEXT,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  job_id TEXT,
  conversation_id TEXT NOT NULL,
  input_type TEXT NOT NULL,
  input_text TEXT,
  input_meta_json TEXT,
  status TEXT NOT NULL,
  final_answer TEXT,
  error TEXT,
  model TEXT,
  prompt_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  llm_calls INTEGER DEFAULT 0,
  tool_calls INTEGER DEFAULT 0,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  duration_ms INTEGER,
  messages_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_conv ON runs(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  name TEXT,
  status TEXT NOT NULL DEFAULT 'ok',
  input_json TEXT,
  output_json TEXT,
  started_at REAL NOT NULL,
  duration_ms INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  tokens_per_second REAL
);
CREATE INDEX IF NOT EXISTS ix_steps_run ON steps(run_id, seq);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now_local() -> datetime:
    return datetime.now(ZoneInfo(get_settings().timezone))


def iso_now() -> str:
    return now_local().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(get_settings().db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db() -> None:
    with _lock:
        c = conn()
        c.executescript(SCHEMA)
        # migrasi ringan (aditif) untuk DB lama: kolom baru boleh NULL
        for table, col, typ in (("runs", "outcome", "TEXT"), ("runs", "dedup_key", "TEXT"), ("runs", "engine", "TEXT"),
                                ("steps", "node", "TEXT")):
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        c.execute("CREATE INDEX IF NOT EXISTS ix_runs_dedup ON runs(dedup_key)")
        c.executemany("INSERT OR IGNORE INTO categories(name, kind, description) VALUES (?,?,?)", CATEGORIES)
        # Job yang tertinggal saat server mati dianggap gagal.
        c.execute("UPDATE jobs SET status='error', error='server restart' WHERE status IN ('queued','running')")
        c.execute("UPDATE runs SET status='error', error='server restart' WHERE status IN ('queued','running')")


def execute(sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
    with _lock:
        return conn().execute(sql, params)


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(s: str | None) -> Any:
    return json.loads(s) if s else None


def tx_row(row: dict) -> dict:
    row = dict(row)
    row["items"] = loads(row.pop("items_json", None))
    row.pop("deleted", None)
    return row


def ts() -> float:
    return time.time()

"""SQLite system of record (SPEC.md §4). WAL mode, schema versioned."""

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = "1"

DDL = """
CREATE TABLE IF NOT EXISTS photos (
  id            INTEGER PRIMARY KEY,
  sha256        TEXT NOT NULL UNIQUE,
  phash         TEXT,
  width         INTEGER, height INTEGER,
  taken_at      TEXT,
  camera        TEXT,
  gps_lat REAL, gps_lon REAL,
  place_id      INTEGER REFERENCES places(id),
  media_kind    TEXT NOT NULL DEFAULT 'photo',
  status        TEXT NOT NULL,
  screen_score  REAL,
  library_path  TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  photo_id INTEGER REFERENCES photos(id),
  source_id INTEGER NOT NULL REFERENCES sources(id),
  source_path TEXT NOT NULL,
  size INTEGER, mtime TEXT,
  media_kind TEXT NOT NULL DEFAULT 'photo',
  disposition TEXT NOT NULL,
  discovered_at TEXT NOT NULL,
  UNIQUE(source_id, source_path)
);

CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  root TEXT NOT NULL UNIQUE,
  description TEXT,
  last_scan_at TEXT
);

CREATE TABLE IF NOT EXISTS tags (
  photo_id INTEGER NOT NULL REFERENCES photos(id),
  dimension TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL,
  model_version TEXT NOT NULL,
  PRIMARY KEY (photo_id, dimension, value)
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  keeper_photo_id INTEGER NOT NULL REFERENCES photos(id)
);

CREATE TABLE IF NOT EXISTS duplicate_members (
  group_id INTEGER NOT NULL REFERENCES duplicate_groups(id),
  file_id  INTEGER NOT NULL REFERENCES files(id),
  decision TEXT NOT NULL DEFAULT 'pending',
  PRIMARY KEY (group_id, file_id)
);

CREATE TABLE IF NOT EXISTS places (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  lat REAL, lon REAL
);

CREATE TABLE IF NOT EXISTS photo_edges (
  photo_id_a INTEGER NOT NULL REFERENCES photos(id),
  photo_id_b INTEGER NOT NULL REFERENCES photos(id),
  relation   TEXT NOT NULL,
  weight     REAL NOT NULL,
  PRIMARY KEY (photo_id_a, photo_id_b, relation)
);
CREATE INDEX IF NOT EXISTS idx_edges_a ON photo_edges(photo_id_a);
CREATE INDEX IF NOT EXISTS idx_edges_b ON photo_edges(photo_id_b);

CREATE TABLE IF NOT EXISTS embeddings (
  photo_id INTEGER NOT NULL REFERENCES photos(id),
  model    TEXT NOT NULL,
  vector   BLOB NOT NULL,
  PRIMARY KEY (photo_id, model)
);

CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY,
  stage TEXT NOT NULL,
  source_path TEXT,
  photo_id INTEGER,
  error TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  last_attempt TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  stage TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT,
  stats_json TEXT
);

CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);

-- sha ledgers: photos gone from the library that must never re-ingest from
-- a surviving source copy (purged = deleted forever; vaulted = encrypted)
CREATE TABLE IF NOT EXISTS purged (sha256 TEXT PRIMARY KEY, purged_at TEXT);
CREATE TABLE IF NOT EXISTS vaulted (sha256 TEXT PRIMARY KEY, vaulted_at TEXT);

-- caption + OCR + orientation layer (describe.py); orientation is applied
-- to thumbs/display renditions only — originals are never rewritten
CREATE TABLE IF NOT EXISTS descriptions (
  photo_id INTEGER PRIMARY KEY, caption TEXT, ocr_text TEXT,
  orientation TEXT, model_version TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: Path, readonly: bool = False) -> sqlite3.Connection:
    # timeout: wait out writer locks (pipeline batches) instead of raising
    # OperationalError at the caller — the Brain serves during ingest runs.
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(DDL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn


def record_error(conn, stage: str, error: str, source_path=None, photo_id=None):
    conn.execute(
        "INSERT INTO errors(stage, source_path, photo_id, error, last_attempt) "
        "VALUES (?,?,?,?,?)",
        (stage, str(source_path) if source_path else None, photo_id, error, now()),
    )


def bump_stat(conn, key: str, delta: int = 1):
    conn.execute(
        "INSERT INTO stats(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + ?",
        (key, str(delta), delta),
    )


def start_run(conn, stage: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs(stage, started_at) VALUES (?, ?)", (stage, now())
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, stats: dict):
    import json

    conn.execute(
        "UPDATE runs SET finished_at = ?, stats_json = ? WHERE id = ?",
        (now(), json.dumps(stats), run_id),
    )
    conn.commit()


def funnel(conn) -> dict:
    """The mvault-status numbers. Vault/review are aggregate counters only —
    per-item vault state is never recorded (SPEC.md invariant #3)."""
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    return {
        "sources": q("SELECT COUNT(*) FROM sources"),
        "files_discovered": q("SELECT COUNT(*) FROM files"),
        "photos_ingested": q("SELECT COUNT(*) FROM photos"),
        "duplicates": q("SELECT COUNT(*) FROM files WHERE disposition='duplicate'"),
        "screened_safe": q("SELECT COUNT(*) FROM photos WHERE status IN ('screened','tagged','noted')"),
        "vaulted_total": int(q("SELECT COALESCE((SELECT value FROM stats WHERE key='vaulted_total'), 0)")),
        "review_total": int(q("SELECT COALESCE((SELECT value FROM stats WHERE key='review_total'), 0)")),
        "tagged": q("SELECT COUNT(*) FROM photos WHERE status IN ('tagged','noted')"),
        "noted": q("SELECT COUNT(*) FROM photos WHERE status='noted'"),
        "edges": q("SELECT COUNT(*) FROM photo_edges"),
        "errors_open": q("SELECT COUNT(*) FROM errors WHERE resolved=0"),
    }

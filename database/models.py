"""
Database schema — SQLite.

Two tables:
  jobs         — every unique job listing we have ever seen
  applications — one row per application attempt (filled by the auto-apply engine)
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key           TEXT    NOT NULL UNIQUE,

    source_name         TEXT    NOT NULL,
    source_job_id       TEXT    NOT NULL,

    title               TEXT    NOT NULL,
    title_normalized    TEXT    NOT NULL,
    company             TEXT,
    company_normalized  TEXT,
    location            TEXT,
    description         TEXT,
    url                 TEXT,

    salary_min          REAL,
    salary_max          REAL,
    currency            TEXT,
    contract_type       TEXT,

    created_at_source   TEXT,
    first_seen_at       TEXT    NOT NULL,
    last_seen_at        TEXT    NOT NULL,

    freshness_label     TEXT,
    status              TEXT    NOT NULL DEFAULT 'ACTIVE',

    found_on_sources    TEXT    NOT NULL DEFAULT '[]',

    ai_match_score      INTEGER,
    ai_recommendation   TEXT,
    ai_cover_letter     TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_dedup_key        ON jobs(dedup_key);
CREATE INDEX IF NOT EXISTS idx_jobs_title_normalized ON jobs(title_normalized);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at     ON jobs(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status           ON jobs(status);

CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    status          TEXT    NOT NULL,
    redirect_url    TEXT,
    applied_at      TEXT,
    error_reason    TEXT,
    screenshot_path TEXT,
    logs            TEXT    NOT NULL DEFAULT '[]',
    ai_match_score  INTEGER,
    ai_cover_letter TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_applications_job_id ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
"""

# Migration queries to add AI columns to existing databases
MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN ai_match_score INTEGER",
    "ALTER TABLE jobs ADD COLUMN ai_recommendation TEXT",
    "ALTER TABLE jobs ADD COLUMN ai_cover_letter TEXT",
    "ALTER TABLE applications ADD COLUMN ai_match_score INTEGER",
    "ALTER TABLE applications ADD COLUMN ai_cover_letter TEXT",
]


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True) if Path(db_path).parent.name else None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db(db_path: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()

        # Run migrations for existing databases that don't have the AI columns yet
        for migration in MIGRATIONS:
            try:
                conn.execute(migration)
                conn.commit()
            except sqlite3.OperationalError:
                # Column already exists — safe to ignore
                pass
    finally:
        conn.close()

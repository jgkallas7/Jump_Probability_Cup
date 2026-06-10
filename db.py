"""wc_cup.db schema + connection. Append-only tape + state tables (SPEC §6).

Deviations from the SPEC sketch, both deliberate:
  - matches table added: the SPEC references match_id but never defines it.
  - market_snapshots is one row per (book, market, outcome) with fair probs
    from BOTH devig methods, so divergence flags are queryable in SQL.
"""

from __future__ import annotations

import sqlite3

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches(
    match_id     TEXT PRIMARY KEY,      -- The Odds API event id
    home         TEXT NOT NULL,
    away         TEXT NOT NULL,
    kickoff_utc  TEXT NOT NULL,
    stage        TEXT NOT NULL DEFAULT 'group',
    grp          TEXT,
    status       TEXT NOT NULL DEFAULT 'scheduled',
    bm_event_id  TEXT                   -- BookMaker idgm, once matched
);

CREATE TABLE IF NOT EXISTS questions(
    qid            TEXT PRIMARY KEY,    -- SportsPredict question id
    match_id       TEXT REFERENCES matches(match_id),
    qtype          TEXT,                -- match_result|total|btts|prop|futures|...
    text           TEXT,
    opens_at       TEXT,
    deadline       TEXT,
    weight         REAL DEFAULT 1.0,    -- stage multiplier
    status         TEXT DEFAULT 'open',
    market_mapping TEXT                 -- book market it maps to, or NO_MARKET
);

CREATE TABLE IF NOT EXISTS market_snapshots(
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    source         TEXT NOT NULL,       -- odds_api | bookmaker_gateway
    book           TEXT NOT NULL,       -- pinnacle | bookmaker_eu | ...
    match_id       TEXT,
    event_label    TEXT,
    market         TEXT NOT NULL,       -- h2h | totals | futures:<league>
    outcome        TEXT NOT NULL,
    point          REAL,                -- totals line, NULL otherwise
    raw_price      REAL,                -- decimal odds
    raw_prob       REAL NOT NULL,
    fair_prob      REAL NOT NULL,       -- power devig (primary)
    fair_prob_mult REAL NOT NULL,       -- multiplicative (sanity check)
    divergence_pts REAL NOT NULL        -- |power - mult| * 100
);
CREATE INDEX IF NOT EXISTS idx_snap_match ON market_snapshots(match_id, market, ts);

CREATE TABLE IF NOT EXISTS forecasts(
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    qid              TEXT REFERENCES questions(qid),
    ts               TEXT NOT NULL,
    consensus_prob   REAL,
    model_prob       REAL,
    blend_w          REAL,
    final_prob       REAL NOT NULL,
    deviation_bps    REAL DEFAULT 0,
    deviation_reason TEXT,              -- MANDATORY when final != consensus
    submitted_at     TEXT,
    submitted_prob   REAL
);

CREATE TABLE IF NOT EXISTS outcomes(
    qid             TEXT PRIMARY KEY REFERENCES questions(qid),
    resolved_at     TEXT,
    outcome         INTEGER,            -- 1 = YES, 0 = NO
    brier           REAL,
    field_avg_brier REAL,
    relative_points REAL,
    multiplier      REAL
);

CREATE TABLE IF NOT EXISTS calibration_buckets(
    bucket        TEXT PRIMARY KEY,     -- e.g. '0.60-0.70'
    n             INTEGER,
    mean_forecast REAL,
    hit_rate      REAL,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS credit_log(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    endpoint  TEXT NOT NULL,
    cost      INTEGER,                  -- x-requests-last from response headers
    used      INTEGER,                  -- x-requests-used (month to date)
    remaining INTEGER                   -- x-requests-remaining
);
"""


def connect(db_path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def init(db_path=None) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


if __name__ == "__main__":
    c = init()
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"wc_cup.db ready at {DB_PATH}")
    print("tables:", ", ".join(tables))

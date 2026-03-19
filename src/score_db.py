"""
score_db.py
───────────
SQLite-backed persistent score store.

The continuous scanner writes scores here after analyzing each ticker.
The 9:30 AM job reads from here to get the top picks.

Schema:
  scores(
    ticker TEXT PRIMARY KEY,
    name TEXT,
    composite_score REAL,
    investment_rating TEXT,
    technical_grade TEXT,
    sentiment_grade TEXT,
    price REAL,
    sector TEXT,
    pool TEXT,          -- 'mainstream' or 'hidden_gem'
    entry_zone TEXT,
    target_1y TEXT,
    target_3y TEXT,
    rationale_bullets TEXT,  -- JSON array
    risks TEXT,              -- JSON array
    category_scores TEXT,    -- JSON object
    analyzed_at REAL,        -- Unix timestamp
    run_date TEXT
  )
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

DB_PATH = Path("data/scores.db")


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    """Create the scores table if it doesn't exist."""
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                ticker           TEXT PRIMARY KEY,
                name             TEXT,
                composite_score  REAL,
                investment_rating TEXT,
                technical_grade  TEXT,
                sentiment_grade  TEXT,
                price            REAL,
                sector           TEXT,
                pool             TEXT DEFAULT 'mainstream',
                entry_zone       TEXT,
                target_1y        TEXT,
                target_3y        TEXT,
                rationale_bullets TEXT,
                risks            TEXT,
                category_scores  TEXT,
                analyzed_at      REAL,
                run_date         TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS scan_state (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
    log.info("Database initialized at %s", DB_PATH)


def upsert_score(scored, pool: str = "mainstream"):
    """Write or update a scored stock result."""
    from .scoring_engine import ScoredStock
    now = time.time()
    from datetime import datetime
    run_date = datetime.now().strftime("%Y-%m-%d")

    bullets = json.dumps(scored.rationale_bullets or [])
    risks   = json.dumps(scored.risks or [])
    cats    = json.dumps({c.category: c.score for c in (scored.category_scores or [])})

    with _conn() as con:
        con.execute("""
            INSERT INTO scores (
                ticker, name, composite_score, investment_rating,
                technical_grade, sentiment_grade, price, sector, pool,
                entry_zone, target_1y, target_3y,
                rationale_bullets, risks, category_scores,
                analyzed_at, run_date
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET
                name=excluded.name,
                composite_score=excluded.composite_score,
                investment_rating=excluded.investment_rating,
                technical_grade=excluded.technical_grade,
                sentiment_grade=excluded.sentiment_grade,
                price=excluded.price,
                sector=excluded.sector,
                pool=excluded.pool,
                entry_zone=excluded.entry_zone,
                target_1y=excluded.target_1y,
                target_3y=excluded.target_3y,
                rationale_bullets=excluded.rationale_bullets,
                risks=excluded.risks,
                category_scores=excluded.category_scores,
                analyzed_at=excluded.analyzed_at,
                run_date=excluded.run_date
        """, (
            scored.ticker, scored.name, scored.composite_score, scored.investment_rating,
            scored.technical_grade, scored.sentiment_grade, scored.price, scored.sector, pool,
            scored.entry_zone, scored.target_1y, scored.target_3y,
            bullets, risks, cats,
            now, run_date
        ))


def get_top_picks(
    n: int = 5,
    pool: Optional[str] = None,
    min_score: float = 0,
    max_age_hours: float = 30,
) -> List[dict]:
    """
    Get the top N picks by composite score.
    Optionally filter by pool ('mainstream' or 'hidden_gem').
    Only returns results analyzed within max_age_hours.
    """
    cutoff = time.time() - (max_age_hours * 3600)
    with _conn() as con:
        if pool:
            rows = con.execute("""
                SELECT * FROM scores
                WHERE pool = ? AND analyzed_at > ? AND composite_score >= ?
                ORDER BY composite_score DESC
                LIMIT ?
            """, (pool, cutoff, min_score, n)).fetchall()
        else:
            rows = con.execute("""
                SELECT * FROM scores
                WHERE analyzed_at > ? AND composite_score >= ?
                ORDER BY composite_score DESC
                LIMIT ?
            """, (cutoff, min_score, n)).fetchall()

    return [_row_to_dict(r) for r in rows]


def get_all_scores(max_age_hours: float = 30) -> List[dict]:
    """Get all scores analyzed within max_age_hours, sorted by score."""
    cutoff = time.time() - (max_age_hours * 3600)
    with _conn() as con:
        rows = con.execute("""
            SELECT ticker, composite_score, pool, sector
            FROM scores
            WHERE analyzed_at > ?
            ORDER BY composite_score DESC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def get_scan_progress() -> dict:
    """Returns current scanner state — how many analyzed, which ticker is next, etc."""
    with _conn() as con:
        rows = con.execute("SELECT key, value FROM scan_state").fetchall()
    state = {r["key"]: r["value"] for r in rows}

    # Count how many analyzed in last 24h
    cutoff = time.time() - 86400
    with _conn() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM scores WHERE analyzed_at > ?", (cutoff,)
        ).fetchone()[0]

    state["analyzed_24h"] = str(count)
    return state


def set_scan_state(key: str, value: str):
    with _conn() as con:
        con.execute("""
            INSERT INTO scan_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, value))


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["rationale_bullets"] = json.loads(d.get("rationale_bullets") or "[]")
    d["risks"]             = json.loads(d.get("risks") or "[]")
    d["category_scores"]   = json.loads(d.get("category_scores") or "{}")
    return d


def count_analyzed_today() -> int:
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    with _conn() as con:
        return con.execute(
            "SELECT COUNT(*) FROM scores WHERE run_date = ?", (today,)
        ).fetchone()[0]

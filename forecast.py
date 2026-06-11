"""Forecast engine: question -> book market -> weighted consensus -> forecast.

For every open, book-mapped question whose match has fresh snapshots:
  1. resolve the question to (market, outcome, point) via its text
  2. weighted consensus of fair_prob across whitelisted books
     (BOOK_WEIGHTS, staleness-filtered on the snapshot batch)
  3. shrink to [PROB_FLOOR, PROB_CEILING], write a forecasts row

Honest probabilities only — final_prob == consensus_prob in v1; any future
deviation REQUIRES deviation_reason (SPEC §6).

Usage:
  python forecast.py [--hours 36]      # forecast + print submission sheet
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone

import db
from config import BOOK_WEIGHTS, DEFAULT_BOOK_WEIGHT
from devig import shrink_extremes

MIN_BOOKS = 2          # never forecast off a single book
MAX_SNAP_AGE_MIN = 90  # snapshot batch must be this fresh

# SP question names that differ from Odds API team names.
TEAM_ALIASES = {
    "united states": "USA",
    "south korea": "South Korea",
    "ivory coast": "Ivory Coast",
    "czechia": "Czech Republic",
    "bosnia and herzegovina": "Bosnia & Herzegovina",
    "curacao": "Curaçao",
}


def resolve_team(name: str, home: str, away: str) -> str | None:
    """Map a team name from question text to the Odds API team string."""
    n = name.strip().lower()
    n = TEAM_ALIASES.get(n, name.strip())
    for t in (home, away):
        if n.lower() == t.lower():
            return t
    return None


def map_question(text: str, mapping: str, home: str, away: str):
    """-> (market, outcome, point) or None if unmappable."""
    t = text.strip()

    if mapping == "h2h":
        m = re.match(r"Will (.+?) win the match\?", t)
        if m:
            team = resolve_team(m.group(1), home, away)
            return ("h2h", team, None) if team else None
        if re.search(r"end in a draw|finish in a draw", t, re.I):
            return ("h2h", "Draw", None)
        return None

    if mapping == "totals":
        m = re.search(r"match have (\d+) or (fewer|less|more) total goals", t, re.I)
        if m:
            n, direction = int(m.group(1)), m.group(2)
            if direction in ("fewer", "less"):
                return ("totals", "Under", n + 0.5)
            return ("totals", "Over", n - 0.5)
        return None

    if mapping == "totals_half":
        m = re.search(r"(first|second) half have (\d+) or (fewer|less|more) total goals",
                      t, re.I)
        if m:
            half = "totals_h1" if m.group(1).lower() == "first" else "totals_h2"
            n, direction = int(m.group(2)), m.group(3)
            if direction in ("fewer", "less"):
                return (half, "Under", n + 0.5)
            return (half, "Over", n - 0.5)
        return None

    if mapping == "team_totals":
        m = re.match(r"Will (.+?) score at least (\d+) goals?\?", t)
        if m:
            team = resolve_team(m.group(1), home, away)
            n = int(m.group(2))
            if team:
                return ("team_totals", f"{team} Over", n - 0.5)
        return None

    if mapping == "btts":
        if re.search(r"both teams .*score", t, re.I) and " and " not in t.lower():
            return ("btts", "Yes", None)
        return None

    return None


def consensus(conn, match_id: str, market: str, outcome: str,
              point: float | None, now: datetime):
    """Weighted consensus of latest fresh fair_prob per whitelisted book.

    Returns (prob, n_books, detail) or (None, 0, reason)."""
    point_clause = "AND point IS NULL" if point is None else "AND point = ?"
    params: list = [match_id, market, outcome]
    if point is not None:
        params.append(point)
    cutoff = (now - timedelta(minutes=MAX_SNAP_AGE_MIN)).isoformat()
    params.append(cutoff)
    rows = conn.execute(f"""
        SELECT book, fair_prob, MAX(ts) ts FROM market_snapshots
        WHERE match_id = ? AND market = ? AND outcome = ? {point_clause}
          AND ts >= ?
        GROUP BY book""", params).fetchall()

    weighted = [(BOOK_WEIGHTS.get(r["book"], DEFAULT_BOOK_WEIGHT), r["fair_prob"],
                 r["book"]) for r in rows]
    weighted = [(w, p, b) for w, p, b in weighted if w > 0]
    if len(weighted) < MIN_BOOKS:
        return None, len(weighted), "insufficient books"
    tot = sum(w for w, _, _ in weighted)
    prob = sum(w * p for w, p, _ in weighted) / tot
    detail = {b: round(p, 4) for _, p, b in sorted(weighted, reverse=True)}
    return prob, len(weighted), detail


def run(conn, hours: float = 36) -> list[dict]:
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=hours)).isoformat()
    qs = conn.execute("""
        SELECT q.qid, q.text, q.market_mapping, q.deadline, q.match_id,
               m.home, m.away, m.kickoff_utc
        FROM questions q JOIN matches m ON m.match_id = q.match_id
        WHERE q.status = 'open' AND q.market_mapping NOT IN ('NO_MARKET')
          AND m.kickoff_utc <= ? AND m.kickoff_utc >= ?
        ORDER BY m.kickoff_utc, q.qid""",
        (horizon, (now - timedelta(hours=3)).isoformat())).fetchall()

    ts = now.isoformat()
    sheet, skipped = [], []
    for q in qs:
        target = map_question(q["text"], q["market_mapping"], q["home"], q["away"])
        if target is None:
            skipped.append((q["text"], "unparseable"))
            continue
        market, outcome, point = target
        prob, n, detail = consensus(conn, q["match_id"], market, outcome, point, now)
        if prob is None:
            skipped.append((q["text"], f"{detail} ({market}/{outcome}/{point})"))
            continue
        final = shrink_extremes(prob)
        conn.execute("""
            INSERT INTO forecasts(qid, ts, consensus_prob, blend_w, final_prob,
                                  deviation_bps, deviation_reason)
            VALUES (?,?,?,?,?,0,NULL)""",
            (q["qid"], ts, round(prob, 5), 1.0, round(final, 5)))
        sheet.append({"qid": q["qid"], "text": q["text"],
                      "match": f"{q['home']} vs {q['away']}",
                      "kickoff": q["kickoff_utc"],
                      "market": f"{market}/{outcome}"
                                + (f"@{point}" if point is not None else ""),
                      "prob": final, "submit_int": int(round(final * 100)) or 1,
                      "n_books": n, "books": detail})
    conn.commit()
    return sheet, skipped


if __name__ == "__main__":
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) \
        if "--hours" in sys.argv else 36
    conn = db.init()
    sheet, skipped = run(conn, hours)
    print(f"=== submission sheet: {len(sheet)} priced questions ===")
    cur_match = None
    for row in sheet:
        if row["match"] != cur_match:
            cur_match = row["match"]
            print(f"\n{cur_match}  (ko {row['kickoff']})")
        print(f"  {row['submit_int']:3d}  {row['text']}")
        print(f"       <- {row['market']}  n={row['n_books']} {row['books']}")
    if skipped:
        print(f"\n=== skipped: {len(skipped)} ===")
        for text, why in skipped[:15]:
            print(f"  [{why}] {text}")

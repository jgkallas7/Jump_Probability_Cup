"""Settlement sync + the self-improvement loop (measure, report, never
auto-adjust — weight changes are a human trading decision).

sync:   GET /results -> outcomes table (outcome derived from API brier +
        our submitted prob), questions marked settled.
report: per-book closing-line Brier on settled questions (who would have
        scored best had we followed only them), our calibration buckets,
        and an ECE-style reliability readout. Books ranked by Brier —
        the scored metric; ECE is the diagnostic for WHY and converges
        faster at small n (many questions per game).

Usage:
  python calibrate.py sync
  python calibrate.py report
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import db
from config import STAGE_MULTIPLIER
from forecast import map_question
from submit import _lobby

MIN_N_FOR_OPINION = 5   # below this, the report prints but says so


def _norm_prob(p: float) -> float:
    """API read-back is documented as 0-1 decimal but observed as the 1-99
    integer (2026-06-11). Accept either."""
    return p / 100.0 if p > 1 else p


def _derive_outcome(submitted_prob: float, brier: float) -> int:
    """API gives brier=(p-o)^2; pick o in {0,1} consistent with it."""
    return 1 if abs((submitted_prob - 1) ** 2 - brier) < abs(submitted_prob ** 2 - brier) else 0


def cmd_sync(conn) -> None:
    from sp_client import SPClient
    c = SPClient()
    results = c.results(_lobby(conn))
    n_new = 0
    for r in results:
        if r.get("brier_score") is None:
            continue
        qid = r["market_id"] if "market_id" in r else r.get("id")
        p = _norm_prob(float(r.get("probability_submitted") or 0))
        brier = float(r["brier_score"])
        outcome = _derive_outcome(p, brier)
        stage = conn.execute(
            "SELECT m.stage FROM questions q JOIN matches m USING(match_id) "
            "WHERE q.qid=?", (qid,)).fetchone()
        mult = STAGE_MULTIPLIER.get(stage["stage"] if stage else "group", 1.0)
        cur = conn.execute(
            """INSERT INTO outcomes(qid, resolved_at, outcome, brier, multiplier)
               VALUES (?,?,?,?,?)
               ON CONFLICT(qid) DO UPDATE SET
                 outcome=excluded.outcome, brier=excluded.brier""",
            (qid, datetime.now(timezone.utc).isoformat(), outcome, brier, mult))
        if cur.rowcount:
            n_new += 1
        conn.execute("UPDATE questions SET status='settled' WHERE qid=?", (qid,))
    conn.commit()
    print(f"synced {len(results)} settled results ({n_new} rows upserted)")


def _closing_book_prob(conn, qid: str) -> dict[str, float]:
    """Each book's LAST pre-deadline fair prob for the question's mapping."""
    q = conn.execute(
        """SELECT q.text, q.market_mapping, q.deadline, q.match_id, m.home, m.away
           FROM questions q JOIN matches m USING(match_id) WHERE q.qid=?""",
        (qid,)).fetchone()
    if not q:
        return {}
    target = map_question(q["text"], q["market_mapping"], q["home"], q["away"])
    if not target:
        return {}
    market, outcome, point = target
    point_clause = "AND point IS NULL" if point is None else "AND point = ?"
    params: list = [q["match_id"], market, outcome]
    if point is not None:
        params.append(point)
    params.append(q["deadline"] or "9999")
    rows = conn.execute(f"""
        SELECT book, fair_prob, MAX(ts) FROM market_snapshots
        WHERE match_id=? AND market=? AND outcome=? {point_clause} AND ts <= ?
        GROUP BY book""", params).fetchall()
    return {r["book"]: r["fair_prob"] for r in rows}


def cmd_report(conn) -> None:
    settled = conn.execute("""
        SELECT o.qid, o.outcome, o.brier FROM outcomes o
        JOIN questions q ON q.qid = o.qid
        WHERE q.market_mapping != 'NO_MARKET'""").fetchall()
    print(f"settled book-mapped questions: {len(settled)}")
    if not settled:
        return

    # per-book closing Brier
    book_stats: dict[str, list[float]] = {}
    for s in settled:
        probs = _closing_book_prob(conn, s["qid"])
        for book, p in probs.items():
            book_stats.setdefault(book, []).append((p - s["outcome"]) ** 2)
    print("\n=== book closing-line Brier (lower = sharper, n in parens) ===")
    ranked = sorted(book_stats.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
    for book, briers in ranked:
        n = len(briers)
        flag = "" if n >= MIN_N_FOR_OPINION else "  [n too small — no opinion]"
        print(f"  {sum(briers)/n:.4f}  {book:24s} (n={n}){flag}")

    # our own reliability buckets (ECE-style diagnostic)
    ours = conn.execute("""
        WITH last_sub AS (
          SELECT qid, submitted_prob,
                 ROW_NUMBER() OVER (PARTITION BY qid ORDER BY submitted_at DESC) rn
          FROM forecasts WHERE submitted_at IS NOT NULL)
        SELECT l.submitted_prob p, o.outcome FROM last_sub l
        JOIN outcomes o ON o.qid = l.qid WHERE l.rn = 1""").fetchall()
    if ours:
        buckets: dict[str, list] = {}
        for r in ours:
            lo = int(r["p"] * 10) * 10
            buckets.setdefault(f"{lo:02d}-{lo+10:02d}", []).append(r)
        print("\n=== our reliability (submitted prob vs hit rate) ===")
        ece_num = 0.0
        for k in sorted(buckets):
            rows = buckets[k]
            mean_p = sum(r["p"] for r in rows) / len(rows)
            hit = sum(r["outcome"] for r in rows) / len(rows)
            ece_num += len(rows) * abs(mean_p - hit)
            print(f"  {k}%: n={len(rows):3d}  forecast={mean_p:.3f}  hit={hit:.3f}")
        print(f"  ECE = {ece_num/len(ours):.4f} over {len(ours)} settled "
              f"(diagnostic only — Brier is the scored metric)")
        ts = datetime.now(timezone.utc).isoformat()
        for k in sorted(buckets):
            rows = buckets[k]
            conn.execute(
                """INSERT INTO calibration_buckets(bucket, n, mean_forecast,
                     hit_rate, updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(bucket) DO UPDATE SET n=excluded.n,
                     mean_forecast=excluded.mean_forecast,
                     hit_rate=excluded.hit_rate, updated_at=excluded.updated_at""",
                (k, len(rows), sum(r["p"] for r in rows) / len(rows),
                 sum(r["outcome"] for r in rows) / len(rows), ts))
        conn.commit()


if __name__ == "__main__":
    conn = db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "sync":
        cmd_sync(conn)
    elif cmd == "report":
        cmd_report(conn)
    else:
        raise SystemExit(f"unknown command: {cmd}")

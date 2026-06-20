"""One-off revision: re-price the live 'both teams have at least 1 shot on
target' submissions through the new qmodel handler and PATCH them.

These were filled by placeholder.v0 at a flat 0.45 ('shot' singular missed the
SOT family regex -> catch-all), but the event is a near-certainty (field ~0.72,
6/6 settled YES). The qmodel handler now prices them per-match (~0.86-0.94 full
match; half-scaled for half variants). placeholders.py only fills blanks and
submit.py revise only touches book questions, so these NO_MARKET predictions
need an explicit PATCH. Idempotent; safe to re-run.

Usage:
  python revise_bothsot.py            # dry run (default) — prints old -> new
  python revise_bothsot.py --execute  # PATCH the live predictions
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import db
import qmodel
import team_rates
from derive import match_lambdas, BASE
from sp_client import SPClient
from submit import _to_int

REVISE_THRESHOLD_PTS = 2


def _match_now(conn, mid):
    r = conn.execute("SELECT MAX(ts) t FROM market_snapshots WHERE match_id=?",
                     (mid,)).fetchone()
    return datetime.fromisoformat(r["t"]) if r and r["t"] else None


def main(execute: bool) -> None:
    conn = db.connect()
    rates = team_rates.build_rates()
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = conn.execute("""
        WITH last_sub AS (
          SELECT qid, submitted_prob, sp_prediction_id,
                 ROW_NUMBER() OVER (PARTITION BY qid ORDER BY submitted_at DESC) rn
          FROM forecasts WHERE submitted_at IS NOT NULL)
        SELECT q.qid, q.text, q.match_id, m.home, m.away, m.kickoff_utc,
               s.submitted_prob, s.sp_prediction_id
        FROM questions q JOIN matches m USING(match_id)
        JOIN last_sub s ON s.qid = q.qid AND s.rn = 1
        WHERE q.status = 'open' AND m.kickoff_utc > ?
          AND q.text LIKE '%both teams have at least 1 shot on target%'
        ORDER BY m.kickoff_utc""", (now_iso,)).fetchall()

    print(f"{len(rows)} open 'both teams >=1 SOT' predictions"
          f"{'' if execute else ' (DRY RUN)'}\n")
    c = SPClient() if execute else None
    n = 0
    for r in rows:
        now = _match_now(conn, r["match_id"])
        lam = match_lambdas(conn, {"match_id": r["match_id"], "home": r["home"],
                                   "away": r["away"]}, now) if now else \
            (BASE["goals_lambda"] / 2, BASE["goals_lambda"] / 2, BASE["goals_lambda"])
        res = qmodel.price_question(r["text"], r["home"], r["away"], rates, lam)
        if res is None:
            print(f"  [unpriced!] {r['home']} v {r['away']}: {r['text'][:50]}")
            continue
        new_p, reason = res
        new_int = _to_int(new_p)
        old_int = int(round((r["submitted_prob"] or 0) * 100))
        flag = "" if abs(new_int - old_int) >= REVISE_THRESHOLD_PTS else "  [skip <2pt]"
        print(f"  {old_int:3d} -> {new_int:3d}  {r['home']} v {r['away']:24s} "
              f"({(r['kickoff_utc'] or '')[:16]})  [{reason}]{flag}")
        if not execute or flag or not r["sp_prediction_id"]:
            continue
        c.revise(r["sp_prediction_id"], new_int)
        conn.execute(
            """INSERT INTO forecasts(qid, ts, blend_w, final_prob, deviation_bps,
               deviation_reason, submitted_at, submitted_prob, sp_prediction_id)
               VALUES (?,?,0,?,0,?,?,?,?)""",
            (r["qid"], now_iso, round(new_p, 5),
             f"qmodel revision: {reason}", now_iso, new_int / 100,
             r["sp_prediction_id"]))
        conn.commit()
        n += 1
    print(f"\n{'revised ' + str(n) if execute else 'would revise'} predictions")


if __name__ == "__main__":
    main(execute="--execute" in sys.argv)

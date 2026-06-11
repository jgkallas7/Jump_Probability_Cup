"""Insurance placeholders: ensure EVERY open question has a submission.

Runs AFTER forecast.py and derive.py have priced everything priceable.
Whatever remains gets a family base-rate placeholder — deliberately
conservative, shaded toward 50 to bound downside, and tagged so the
weekly review can measure whether placeholder families earn or bleed.

A blank scores 0 relative points. A placeholder scores
(field_brier - ours) x 100 — positive only when our number is closer to
truth than the field average. Family base rates beat blanks where the
family has a stable rate; flat 50s would LOSE on easy questions, which
is why every family below is informed, not flat.

Usage: python placeholders.py [--submit] [--hours 2000]
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone

import db

# (regex, placeholder prob, rationale) — family base rates, v0.
# UNVERIFIED training-knowledge rates, same flag discipline as derive BASE.
FAMILIES = [
    (r"score or assist", 0.24, "attacking player score-or-assist family base"),
    (r"score a goal \(excluding own goals\)", 0.18, "anytime scorer family base"),
    (r"have \d+ or more shots on target", 0.55, "featured-player 1+ SOT family base"),
    (r"shots on target", 0.50, "SOT comparison/count family base"),
    (r"score the first goal of the game and", 0.18, "first-goal AND combo family base"),
    (r"score the first goal", 0.35, "team first-goal family base"),
    (r"more fouls than", 0.47, "fouls comparison, tie drag"),
    (r"more corner kicks than", 0.45, "corner comparison w/ half quals, tie drag"),
    (r"more cards than|receive more cards", 0.42, "cards comparison, tie drag"),
    (r"caught offside", 0.45, "offsides count family base"),
    (r"penalty kick be awarded OR a red card", 0.33, "pen-or-red union base"),
    (r"penalty kick be awarded", 0.28, "pen base"),
    (r"corner kicks", 0.50, "corner count family"),
    (r"cards", 0.50, "cards count family"),
    (r"tied at halftime|match be tied", 0.33, "HT draw base"),
    (r"be winning", 0.35, "HT leader family base"),
    (r"more goals than the first half", 0.46, "2H>1H strict, tie drag"),
    (r"", 0.45, "unclassified fallback — shaded under 50 (most Will-X props skew No)"),
]


def placeholder_for(text: str) -> tuple[float, str]:
    for pattern, p, why in FAMILIES:
        if pattern == "" or re.search(pattern, text, re.I):
            return p, why
    return 0.45, "fallback"


def run(conn, hours: float = 2000, submit_mode: bool = False) -> None:
    from sp_client import SPClient
    from submit import _lobby, _to_int
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=hours)).isoformat()
    rows = conn.execute("""
        SELECT q.qid, q.text FROM questions q JOIN matches m USING(match_id)
        WHERE q.status='open' AND m.kickoff_utc > ? AND m.kickoff_utc <= ?
          AND q.qid NOT IN (SELECT qid FROM forecasts WHERE submitted_at IS NOT NULL)
        ORDER BY m.kickoff_utc""", (now.isoformat(), horizon)).fetchall()
    print(f"{len(rows)} open questions still unsubmitted -> placeholders")
    if not rows:
        return
    ts = now.isoformat()
    batch_items = []
    for r in rows:
        p, why = placeholder_for(r["text"])
        conn.execute(
            """INSERT INTO forecasts(qid, ts, blend_w, final_prob,
               deviation_bps, deviation_reason) VALUES (?,?,0,?,0,?)""",
            (r["qid"], ts, p, f"placeholder.v0 [{why}] UNVERIFIED"))
        batch_items.append((r["qid"], _to_int(p), r["text"]))
    conn.commit()
    if not submit_mode:
        from collections import Counter
        dist = Counter(p for _, p, _ in batch_items)
        print("dry distribution:", dict(sorted(dist.items())))
        return

    c = SPClient()
    lobby = _lobby(conn)
    ok_n = 0
    for i in range(0, len(batch_items), 50):
        chunk = batch_items[i:i + 50]
        resp = c.submit_batch([{"market_id": qid, "lobby_id": lobby,
                                "probability": p} for qid, p, _ in chunk])
        ok = {x["market_id"]: x for x in resp.get("results", []) if x.get("success")}
        for qid, p, text in chunk:
            if qid in ok:
                ok_n += 1
                conn.execute(
                    """UPDATE forecasts SET submitted_at=?, submitted_prob=?,
                       sp_prediction_id=? WHERE qid=? AND ts=?""",
                    (ts, p / 100, (ok[qid].get("trade") or {}).get("id"), qid, ts))
            else:
                err = next((x.get("error") for x in resp.get("results", [])
                            if x.get("market_id") == qid), "?")
                print(f"  FAIL {text[:60]}: {err}")
        conn.commit()
        print(f"  batch {i//50+1}: {resp.get('succeeded')}/{resp.get('total')}")
    print(f"placeholders submitted: {ok_n}/{len(batch_items)}")


if __name__ == "__main__":
    conn = db.init()
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) \
        if "--hours" in sys.argv else 2000
    run(conn, hours, submit_mode="--submit" in sys.argv)

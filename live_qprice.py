"""Live demo: price an upcoming match's NO_MARKET (alpha) questions with the
counted-rate quant engine (qmodel) — goal rates from the market tape, stat
rates from FBref. Shows the engine end-to-end on real, current data.

Usage: python live_qprice.py [--hours 48]
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import db
import qmodel
import team_rates
from derive import match_lambdas, BASE, derive_question


def main(hours: float = 48):
    conn = db.connect()
    rates = team_rates.build_rates()
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=hours)).isoformat()
    # next match (by kickoff) that has open NO_MARKET questions
    row = conn.execute("""
        SELECT m.match_id, m.home, m.away, m.kickoff_utc, COUNT(*) nq
        FROM matches m JOIN questions q ON q.match_id=m.match_id
        WHERE q.status='open' AND q.market_mapping='NO_MARKET'
          AND m.kickoff_utc >= ? AND m.kickoff_utc <= ?
        GROUP BY m.match_id ORDER BY m.kickoff_utc LIMIT 1""",
        (now.isoformat(), horizon)).fetchone()
    if not row:
        print("no upcoming match with open alpha questions in window"); return
    mid, home, away = row["match_id"], row["home"], row["away"]
    print(f"=== {home} vs {away}  (ko {row['kickoff_utc']})  {row['nq']} alpha Qs ===")

    snap_now = conn.execute("SELECT MAX(ts) t FROM market_snapshots WHERE match_id=?",
                            (mid,)).fetchone()["t"]
    if snap_now:
        lam = match_lambdas(conn, {"match_id": mid, "home": home, "away": away},
                            datetime.fromisoformat(snap_now))
        src = f"market tape @ {snap_now[:16]}"
    else:
        lam = (BASE["goals_lambda"]/2, BASE["goals_lambda"]/2, BASE["goals_lambda"])
        src = "NO TAPE — base goal lambda"
    print(f"goal rates: home={lam[0]:.2f} away={lam[1]:.2f} total={lam[2]:.2f}  ({src})\n")
    print(f"{'qmodel':>6} {'old':>5}   question")
    print("-"*92)
    n_new = n_old = 0
    for q in conn.execute("""SELECT qid, text FROM questions WHERE match_id=?
                             AND status='open' AND market_mapping='NO_MARKET'
                             ORDER BY qid""", (mid,)):
        res = qmodel.price_question(q["text"], home, away, rates, lam)
        old = derive_question(conn, {"text": q["text"], "match_id": mid,
                                     "home": home, "away": away}, now)
        new_s = f"{round(res[0]*100):>5}%" if res else "   — "
        old_s = f"{round(old[0]*100):>4}%" if old else "  — "
        if res: n_new += 1
        if old: n_old += 1
        tag = res[1] if res else ""
        print(f"{new_s} {old_s}   {q['text'][:60]}")
        if res:
            print(f"                 └─ {tag}")
    print(f"\nqmodel priced {n_new}/{row['nq']} alpha Qs (old engine: {n_old})")


if __name__ == "__main__":
    h = float(sys.argv[sys.argv.index("--hours")+1]) if "--hours" in sys.argv else 48
    main(h)

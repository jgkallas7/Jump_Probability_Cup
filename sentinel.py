"""Pre-match sentinel: runs every 15 min via systemd timer.

If any match closes within SENTINEL_WINDOW_MIN: fresh snapshot + PATCH
revisions for those matches. No matches near = no API spend (events-free
logic lives in the DB; nothing is pulled).

Credit guard: skips paid pulls if remaining credits < CREDIT_FLOOR
(shared key with the golf tracker — reserve honored both sides).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db
import snapshot
import submit

SENTINEL_WINDOW_MIN = 75
CREDIT_FLOOR = 5000
FAILURES_LOG = str(Path.home() / "wc_logs" / "FAILURES.log")
# ntfy topics are public-by-name, so the topic lives outside the repo.
NTFY_TOPIC = os.environ.get("WC_NTFY_TOPIC", "")


def _alert(msg: str) -> None:
    """Failure visibility: append to FAILURES.log and best-effort phone push."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp}] {msg}"
    print(line)
    try:
        with open(FAILURES_LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if not NTFY_TOPIC:
        return
    try:
        import requests
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=msg.encode(), timeout=10,
                      headers={"Title": "WC Cup pipeline failure",
                               "Priority": "high"})
    except Exception:
        pass


def remaining_credits(conn) -> int | None:
    row = conn.execute(
        "SELECT remaining FROM credit_log ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["remaining"]) if row and row["remaining"] is not None else None


def main() -> None:
    now = datetime.now(timezone.utc)
    conn = db.init()
    horizon = (now + timedelta(minutes=SENTINEL_WINDOW_MIN)).isoformat()
    near = conn.execute(
        """SELECT match_id, home, away, kickoff_utc FROM matches
           WHERE status='scheduled' AND kickoff_utc > ? AND kickoff_utc <= ?""",
        (now.isoformat(), horizon)).fetchall()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    if not near:
        print(f"[{stamp}] sentinel: no matches within {SENTINEL_WINDOW_MIN}m")
        return

    rem = remaining_credits(conn)
    if rem is not None and rem < CREDIT_FLOOR:
        print(f"[{stamp}] sentinel: CREDIT FLOOR HIT ({rem} < {CREDIT_FLOOR}) — "
              f"skipping pulls, submissions stand as-is")
        return

    for m in near:
        print(f"[{stamp}] sentinel: {m['home']} vs {m['away']} ko {m['kickoff_utc']}")
    hours = SENTINEL_WINDOW_MIN / 60 + 0.25
    # every step isolated: a failure in one must never abort the rest
    # (review finding), and every failure lands in FAILURES.log + ntfy
    steps = [
        ("snapshot", lambda: snapshot.snapshot_pinnacle(conn, hours=hours)),
        ("forecast", lambda: __import__("forecast").run(conn, hours=hours)),
        ("rescue-submit", lambda: submit.cmd_submit(conn, hours=hours, dry=False)),
        ("revise", lambda: submit.cmd_revise(conn, hours=hours, dry=False)),
        ("derive", lambda: __import__("derive").run(conn, hours=hours,
                                                    submit_mode=True)),
        # insurance net: anything still unpriced after forecast+derive gets a
        # family base-rate placeholder, so a closing question is never a blank
        # (0 relative pts). Fills gaps only; never revises a real forecast.
        ("placeholders", lambda: __import__("placeholders").run(
            conn, hours=hours, submit_mode=True)),
        ("sheet", lambda: __import__("sheet").write_for_window(conn,
                                                               hours=hours)),
    ]
    for name, fn in steps:
        try:
            fn()
        except Exception as e:
            _alert(f"sentinel step '{name}' FAILED: {e}")

    unanswered = conn.execute(
        """SELECT COUNT(*) FROM questions q JOIN matches m USING(match_id)
           WHERE q.status='open' AND m.kickoff_utc <= ?
           AND q.qid NOT IN (SELECT qid FROM forecasts
                             WHERE submitted_at IS NOT NULL)""",
        (horizon,)).fetchone()[0]
    if unanswered:
        # placeholders ran in the chain above, so a residual gap is a real
        # failure (a forfeit), not routine — page it.
        _alert(f"{unanswered} questions closing soon have NO submission after "
               f"forecast+derive+placeholders — investigate (forfeit risk)")


if __name__ == "__main__":
    main()

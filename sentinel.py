"""Pre-match sentinel: runs every 15 min via systemd timer.

If any match closes within SENTINEL_WINDOW_MIN: fresh snapshot + PATCH
revisions for those matches. No matches near = no API spend (events-free
logic lives in the DB; nothing is pulled).

Credit guard: skips paid pulls if remaining credits < CREDIT_FLOOR
(shared key with the golf tracker — reserve honored both sides).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import db
import snapshot
import submit

SENTINEL_WINDOW_MIN = 75
CREDIT_FLOOR = 5000


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
    snapshot.snapshot_pinnacle(conn, hours=hours)
    submit.cmd_revise(conn, hours=hours, dry=False)

    # alpha questions can't auto-revise until derive.py exists — count and warn
    unanswered = conn.execute(
        """SELECT COUNT(*) FROM questions q JOIN matches m USING(match_id)
           WHERE q.status='open' AND m.kickoff_utc <= ?
           AND q.qid NOT IN (SELECT qid FROM forecasts
                             WHERE submitted_at IS NOT NULL)""",
        (horizon,)).fetchone()[0]
    if unanswered:
        print(f"[{stamp}] WARNING: {unanswered} questions closing soon have NO "
              f"submission (alpha gap — needs derive.py or manual sheet)")


if __name__ == "__main__":
    main()

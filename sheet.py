"""Keyable sheet for the manual (human) leaderboard entry.

Sentinel calls write_for_window(push=True) as each match enters the 75-min
close window: writes data/sheets/<date>_<home>_v_<away>.txt (repo is in
OneDrive, so the file syncs to the phone) and pushes the sheet text to the
ntfy topic ONCE per match, so it lands as a phone notification with about
an hour left to key it into the SportsPredict app.

The morning routine calls write_for_window(hours=30, push=False) for
provisional files only — no pushes outside the close window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SHEET_DIR = Path(__file__).resolve().parent / "data" / "sheets"
NTFY_TOPIC = "wc-cup-kidtwist-a7x3"   # same topic as sentinel failure alerts
CT = ZoneInfo("America/Chicago")


def _sheet_text(conn, m) -> str:
    rows = conn.execute(
        """SELECT q.text,
                  (SELECT submitted_prob FROM forecasts f
                   WHERE f.qid = q.qid AND f.submitted_at IS NOT NULL
                   ORDER BY f.submitted_at DESC LIMIT 1) AS p
           FROM questions q WHERE q.match_id = ? ORDER BY q.text""",
        (m["match_id"],)).fetchall()
    ko = datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00"))
    now_ct = datetime.now(timezone.utc).astimezone(CT)
    lines = [
        f"{m['home']} v {m['away']} — closes {ko.astimezone(CT):%-I:%M %p} CT",
        f"(numbers as of {now_ct:%-I:%M %p} CT; key as-is, override where "
        f"you disagree)",
        "",
    ]
    for r in rows:
        p = f"{round(r['p'] * 100):>3}" if r["p"] is not None else " --"
        lines.append(f"{p}  {r['text']}")
    return "\n".join(lines)


def _push_once(conn, m, text: str) -> bool:
    """ntfy push, gated to once per match (sentinel re-runs every 15 min)."""
    key = f"sheet_pushed:{m['match_id']}"
    cur = conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
                       (key, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    if not cur.rowcount:
        return False
    try:
        import requests
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=text.encode(),
                      timeout=10,
                      headers={"Title": f"Key-in sheet: {m['home']} v "
                                        f"{m['away']}"})
    except Exception:
        pass  # file still lands via OneDrive; push is best-effort
    return True


def write_for_window(conn, hours: float = 1.25, push: bool = True) -> int:
    now = datetime.now(timezone.utc)
    matches = conn.execute(
        """SELECT match_id, home, away, kickoff_utc FROM matches
           WHERE status='scheduled' AND kickoff_utc > ? AND kickoff_utc <= ?
           ORDER BY kickoff_utc""",
        (now.isoformat(), (now + timedelta(hours=hours)).isoformat())
    ).fetchall()
    SHEET_DIR.mkdir(parents=True, exist_ok=True)
    pushed = 0
    for m in matches:
        text = _sheet_text(conn, m)
        name = (f"{m['kickoff_utc'][:10]}_{m['home']}_v_{m['away']}.txt"
                .replace(" ", ""))
        (SHEET_DIR / name).write_text(text)
        if push and _push_once(conn, m, text):
            pushed += 1
    print(f"sheets: {len(matches)} written, {pushed} pushed")
    return len(matches)

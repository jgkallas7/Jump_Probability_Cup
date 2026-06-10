"""Build-order step 2: verify the WC sport key + seed matches (FREE calls only).

Usage: python fetch_schedule.py
"""

from __future__ import annotations

import db
from config import SPORT_KEY
from odds_client import OddsClient


def main() -> None:
    conn = db.init()
    client = OddsClient(conn)

    sports = client.sports()
    wc = [s for s in sports if "world cup" in s.get("title", "").lower()
          or "world_cup" in s.get("key", "")]
    print("World Cup sport keys on The Odds API:")
    for s in wc:
        print(f"  {s['key']:40s} active={s.get('active')}  {s.get('title')}")
    keys = {s["key"] for s in wc}
    if SPORT_KEY not in keys:
        raise SystemExit(
            f"FATAL: expected key {SPORT_KEY!r} not found — fix config.SPORT_KEY")

    events = client.events(SPORT_KEY)
    print(f"\n{len(events)} events returned for {SPORT_KEY}")
    for ev in events:
        conn.execute(
            """INSERT INTO matches(match_id, home, away, kickoff_utc)
               VALUES (?,?,?,?)
               ON CONFLICT(match_id) DO UPDATE SET
                 home=excluded.home, away=excluded.away,
                 kickoff_utc=excluded.kickoff_utc""",
            (ev["id"], ev["home_team"], ev["away_team"], ev["commence_time"]))
    conn.commit()

    rows = conn.execute(
        "SELECT home, away, kickoff_utc FROM matches ORDER BY kickoff_utc LIMIT 8"
    ).fetchall()
    print("\nNext matches:")
    for r in rows:
        print(f"  {r['kickoff_utc']}  {r['home']} vs {r['away']}")
    total = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"\nmatches table: {total} rows")


if __name__ == "__main__":
    main()

"""Build-order step 4: snapshot fetcher -> market_snapshots tape.

  snapshot_pinnacle()    targeted Odds API pull near deadlines (2 credits per
                         event: h2h,totals x eu) — the rationed sharp anchor

(The free BookMaker gateway source was removed 2026-07-12 — its auth broke
and the Odds API tape covers pricing alone; historical rows with
source='bookmaker_gateway' remain on the tape.)

Usage:
  python snapshot.py pinnacle [--hours 48]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import db
from config import SPORT_KEY
from devig import decimal_to_prob, devig_probs, devig_three_way
from odds_client import OddsClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert(conn, rows: list[tuple]) -> None:
    try:
        conn.execute("ALTER TABLE market_snapshots ADD COLUMN quote_ts TEXT")
    except Exception:
        pass  # column exists
    conn.executemany(
        """INSERT INTO market_snapshots
           (ts, source, book, match_id, event_label, market, outcome, point,
            raw_price, raw_prob, fair_prob, fair_prob_mult, divergence_pts,
            quote_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows)
    conn.commit()


# --------------------------------------------------------------------------
# Pinnacle via The Odds API (rationed)
# --------------------------------------------------------------------------

def snapshot_pinnacle(conn, hours: int = 48, markets: str = "",
                      regions: str = "") -> int:
    """Snapshot whitelisted-book odds for matches inside the window.

    Default markets cover tonight's submittable set; regions eu,uk,us span
    the whitelist (pinnacle/eu, betfair_ex_uk+matchbook+smarkets/uk,
    dk+fd+betonline/us). Cost = markets x regions per call.
    """
    # Featured markets (h2h/totals) come from the BULK endpoint: one call
    # covers every event. Additional markets (team_totals, totals_h2, btts,
    # props) are ONLY served by the per-event endpoint (bulk returns 422).
    featured = markets or "h2h,totals"
    additional = ("team_totals,totals_h1,totals_h2,team_totals_h1,"
                  "alternate_team_totals_h2,alternate_spreads_cards,"
                  "alternate_spreads_corners,alternate_totals_corners,"
                  "alternate_totals_cards,alternate_totals_corners_h1,"
                  "btts,player_assists,player_assists_alternate,"
                  "h2h_3_way_h1,h2h_3_way_h2,"
                  "player_goal_scorer_anytime,player_shots_on_target")
    regions = regions or "eu,uk,us"
    ts = _now()
    now_dt = datetime.now(timezone.utc)
    # retire finished matches: keeps them out of every window forever
    # (review finding: stale event ids 404 the chain / leak credits)
    conn.execute("UPDATE matches SET status='completed' "
                 "WHERE status='scheduled' AND kickoff_utc < ?",
                 ((now_dt - timedelta(hours=3.5)).isoformat(),))
    conn.commit()
    horizon = (now_dt + timedelta(hours=hours)).isoformat()
    match_ids = [r["match_id"] for r in conn.execute(
        "SELECT match_id FROM matches WHERE kickoff_utc <= ? "
        "AND kickoff_utc > ? AND status = 'scheduled' ORDER BY kickoff_utc",
        (horizon, (now_dt - timedelta(hours=2)).isoformat()))]
    if not match_ids:
        print(f"no matches inside {hours}h window")
        return 0

    client = OddsClient(conn)
    events = client.odds(SPORT_KEY, featured, regions, event_ids=match_ids)
    for mid in match_ids:
        try:
            ev_extra = client.event_odds(SPORT_KEY, mid, additional, regions)
        except Exception as e:  # one dead event must never kill the chain
            print(f"[snapshot] event {mid[:8]} additional pull failed: {e}",
                  file=sys.stderr)
            continue
        if ev_extra.get("bookmakers"):
            events.append(ev_extra)
    rows: list[tuple] = []
    for ev in events:
        label = f"{ev['away_team']} vs {ev['home_team']}"
        for bm in ev.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                # price 1.0 = suspended/settled outcome — skip, never raise
                outs = [o for o in mkt.get("outcomes", [])
                        if (o.get("price") or 0) > 1.0]
                probs = [decimal_to_prob(o["price"]) for o in outs]
                if mkt["key"] == "h2h" and len(outs) == 3:
                    by = {o["name"]: decimal_to_prob(o["price"]) for o in outs}
                    tw = devig_three_way(by[ev["home_team"]], by["Draw"],
                                         by[ev["away_team"]])
                    fair_by = {ev["home_team"]: (tw.home, tw.home_mult),
                               "Draw": (tw.draw, tw.draw_mult),
                               ev["away_team"]: (tw.away, tw.away_mult)}
                    for o in outs:
                        fp, fm = fair_by[o["name"]]
                        rows.append((ts, "odds_api", bm["key"], ev["id"], label,
                                     "h2h", o["name"], None, o["price"],
                                     decimal_to_prob(o["price"]), fp, fm,
                                     tw.divergence_pts, mkt.get("last_update")))
                else:
                    # Markets like team_totals/alternate_totals lump several
                    # independent 2-way books into one outcomes list. Devig
                    # within (description, point) groups, never across them.
                    # Spread pairs are (team A at +p) <-> (team B at -p): a
                    # ladder has BOTH pairings per |p| — key on the signed
                    # point of the alphabetically-first team so the two
                    # pairings never collapse into one 4-way devig.
                    is_spread = "spreads" in mkt["key"]
                    groups: dict[tuple, list] = {}
                    for o in outs:
                        pt = o.get("point")
                        if is_spread and pt is not None:
                            first = min(x["name"] for x in outs)
                            signed = pt if o["name"] == first else -pt
                            gkey = (None, f"pair@{signed}")
                        else:
                            gkey = (o.get("description"), pt)
                        groups.setdefault(gkey, []).append(o)
                    for (desc, point), grp in groups.items():
                        gprobs = [decimal_to_prob(o["price"]) for o in grp]
                        if len(grp) >= 2:
                            fair = devig_probs(gprobs, "power")
                            fair_m = devig_probs(gprobs, "multiplicative")
                            div = round(max(abs(a - b)
                                        for a, b in zip(fair, fair_m)) * 100, 3)
                        else:
                            fair, fair_m, div = gprobs, gprobs, -1.0  # unpaired: raw
                        for o, rp, fp, fm in zip(grp, gprobs, fair, fair_m):
                            outcome = (f"{desc} {o['name']}" if desc else o["name"])
                            rows.append((ts, "odds_api", bm["key"], ev["id"], label,
                                         mkt["key"], outcome,
                                         o.get("point", point),  # signed for spreads
                                         o["price"], rp, fp, fm, div,
                                         mkt.get("last_update")))

    _insert(conn, rows)
    books = {r[2] for r in rows}
    print(f"pinnacle window: {len(events)} events -> {len(rows)} rows; books={sorted(books)}")
    return len(rows)


if __name__ == "__main__":
    conn = db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pinnacle"
    if cmd == "pinnacle":
        hours = int(sys.argv[sys.argv.index("--hours") + 1]) \
            if "--hours" in sys.argv else 48
        snapshot_pinnacle(conn, hours)
    else:
        raise SystemExit(f"unknown source: {cmd}")

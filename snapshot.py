"""Build-order step 4: snapshot fetchers -> market_snapshots tape.

Two sources, one table:
  snapshot_bookmaker()   free gateway pull, all WC leagues (continuous tape)
  snapshot_pinnacle()    targeted Odds API pull near deadlines (2 credits per
                         event: h2h,totals x eu) — the rationed sharp anchor

Usage:
  python snapshot.py bookmaker
  python snapshot.py pinnacle [--hours 48]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import db
import bookmaker_client
from config import CORE_MARKETS, PINNACLE_REGION, SPORT_KEY
from devig import american_to_prob, decimal_to_prob, devig_probs, devig_three_way
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
# BookMaker gateway (free)
# --------------------------------------------------------------------------

def snapshot_bookmaker(conn) -> int:
    ts = _now()
    raw = bookmaker_client.fetch_schedule()
    games = bookmaker_client.parse_games(raw)
    rows: list[tuple] = []
    flagged = 0

    for g in games:
        if g["kind"] == "match":
            probs = [american_to_prob(s["american"]) for s in g["selections"]]
            tw = devig_three_way(*probs)  # selections ordered home/draw/away
            fair = [tw.home, tw.draw, tw.away]
            fair_m = [tw.home_mult, tw.draw_mult, tw.away_mult]
            if tw.flagged:
                flagged += 1
                print(f"[divergence] {g['event_label']}: {tw.divergence_pts}pts",
                      file=sys.stderr)
            for sel, rp, fp, fm in zip(g["selections"], probs, fair, fair_m):
                rows.append((ts, "bookmaker_gateway", "bookmaker_eu", None,
                             g["event_label"], "h2h", sel["label"], None,
                             None, rp, fp, fm, tw.divergence_pts, ts))
            for t in g.get("totals", []):
                rp_o = american_to_prob(t["over"])
                rp_u = american_to_prob(t["under"])
                f_o, f_u = devig_probs([rp_o, rp_u], "power")
                m_o, m_u = devig_probs([rp_o, rp_u], "multiplicative")
                div = round(max(abs(f_o - m_o), abs(f_u - m_u)) * 100, 3)
                rows.append((ts, "bookmaker_gateway", "bookmaker_eu", None,
                             g["event_label"], "totals", "Over", t["point"],
                             None, rp_o, f_o, m_o, div, ts))
                rows.append((ts, "bookmaker_gateway", "bookmaker_eu", None,
                             g["event_label"], "totals", "Under", t["point"],
                             None, rp_u, f_u, m_u, div, ts))
        else:
            probs = [american_to_prob(s["american"]) for s in g["selections"]]
            fair = devig_probs(probs, "power")
            fair_m = devig_probs(probs, "multiplicative")
            market = f"futures:{g['league_desc']}"
            for sel, rp, fp, fm in zip(g["selections"], probs, fair, fair_m):
                rows.append((ts, "bookmaker_gateway", "bookmaker_eu", None,
                             g["event_label"], market, sel["label"], None,
                             None, rp, fp, fm,
                             round(max(abs(a - b) for a, b in zip(fair, fair_m)) * 100, 3),
                             ts))

    _insert(conn, rows)
    print(f"bookmaker: {len(games)} markets -> {len(rows)} rows "
          f"({flagged} divergence-flagged)")
    return len(rows)


# --------------------------------------------------------------------------
# Pinnacle via The Odds API (rationed)
# --------------------------------------------------------------------------

def snapshot_pinnacle(conn, hours: int = 48) -> int:
    ts = _now()
    horizon = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    match_ids = [r["match_id"] for r in conn.execute(
        "SELECT match_id FROM matches WHERE kickoff_utc <= ? "
        "AND status = 'scheduled' ORDER BY kickoff_utc", (horizon,))]
    if not match_ids:
        print(f"no matches inside {hours}h window")
        return 0

    client = OddsClient(conn)
    events = client.odds(SPORT_KEY, CORE_MARKETS, PINNACLE_REGION,
                         event_ids=match_ids)
    rows: list[tuple] = []
    for ev in events:
        label = f"{ev['away_team']} vs {ev['home_team']}"
        for bm in ev.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                outs = mkt.get("outcomes", [])
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
                    fair = devig_probs(probs, "power")
                    fair_m = devig_probs(probs, "multiplicative")
                    div = round(max(abs(a - b) for a, b in zip(fair, fair_m)) * 100, 3) \
                        if fair else 0.0
                    for o, rp, fp, fm in zip(outs, probs, fair, fair_m):
                        rows.append((ts, "odds_api", bm["key"], ev["id"], label,
                                     mkt["key"], o["name"], o.get("point"),
                                     o["price"], rp, fp, fm, div,
                                     mkt.get("last_update")))

    _insert(conn, rows)
    books = {r[2] for r in rows}
    print(f"pinnacle window: {len(events)} events -> {len(rows)} rows; books={sorted(books)}")
    return len(rows)


if __name__ == "__main__":
    conn = db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "bookmaker"
    if cmd == "bookmaker":
        snapshot_bookmaker(conn)
    elif cmd == "pinnacle":
        hours = int(sys.argv[sys.argv.index("--hours") + 1]) \
            if "--hours" in sys.argv else 48
        snapshot_pinnacle(conn, hours)
    else:
        raise SystemExit(f"unknown source: {cmd}")

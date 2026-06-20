"""Historical national-team stat priors + referee tendencies from API-Football
(free tier = seasons 2022-2024). Builds the stable priors that fix the
matchday-2 blocker for the offsides/fouls/corners buckets, plus ref card/foul
rates — both inherently historical, so the season limit doesn't bite.

FRUGAL BY DESIGN (100 req/day): pulls each tournament's fixture list once, then
per-fixture statistics up to a per-run BUDGET cap, caching everything (re-runs
free). Accumulates coverage over nights. aggregate() reads ALL cached fixtures.

  python apifootball_history.py pull [max]    # spend up to `max` requests (cap)
  python apifootball_history.py aggregate     # build rates from cache (0 requests)
"""
from __future__ import annotations

import glob
import json
import sys
import time
import unicodedata
import re
from collections import defaultdict
from pathlib import Path

import apifootball

THROTTLE_S = 6.5    # free tier = 10 requests/MINUTE -> stay just under

CACHE = apifootball.CACHE
# free-tier competitions with national-team stats. (league, season).
# ORDERED freshest-first: 2024 is the most recent free year (2025/2026 blocked),
# so a budget-limited pull spends on the LEAST-stale data first. Ref tendencies
# are year-insensitive; team stat rates lean on the most recent available.
COMPS = [
    (4, 2024),    # UEFA Euro 2024  (freshest, top teams)
    (9, 2024),    # Copa America 2024
    (32, 2024), (31, 2024), (29, 2024), (30, 2024),   # WC Qualification 2024 (EU/CONCACAF/CAF/AFC)
    (10, 2024), (5, 2024),    # Friendlies / Nations League 2024
    (32, 2023), (31, 2023), (29, 2023), (30, 2023),   # WCQ 2023
    (10, 2023), (5, 2023),    # 2023 friendlies / NL
    (1, 2022),    # World Cup 2022 (oldest, but rich stat sample)
]
STATS = {"Offsides": "offsides", "Fouls": "fouls", "Corner Kicks": "corners",
         "Shots on Goal": "sot", "Total Shots": "shots",
         "Yellow Cards": "yellow", "Red Cards": "red"}


def _norm(s):
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", str(s))
                  .encode("ascii", "ignore").decode().lower())


def pull(max_requests: int = 60) -> None:
    c = apifootball.APIFootball()
    spent = 0
    for league, season in COMPS:
        if spent >= max_requests or (c.remaining or 99) < apifootball.MIN_BUDGET + 1:
            break
        try:
            fx = c.fixtures(league=league, season=season)   # cached -> free
        except Exception as e:
            print(f"  L{league} {season}: {str(e)[:60]}"); continue
        spent += 0 if (CACHE / "x").exists() else 0   # fixtures may have cost 1
        fin = [f for f in fx if f["fixture"]["status"]["short"] == "FT"]
        for f in fin:
            if spent >= max_requests or (c.remaining or 99) < apifootball.MIN_BUDGET + 1:
                break
            fid = f["fixture"]["id"]
            cp = c._cache_path("fixtures/statistics", {"fixture": fid})
            if cp.exists():
                continue                      # already cached -> skip (free)
            try:
                c.fixture_statistics(fid)     # spends 1, caches
                spent += 1
                time.sleep(THROTTLE_S)        # respect 10/min
            except Exception as e:
                print(f"  stat {fid}: {str(e)[:50]}"); break
        print(f"  L{league} {season}: {len(fin)} finished; spent~{spent}; remaining {c.remaining}")
    print(f"pull done; ~{spent} requests spent; {c.remaining} remaining today")


def aggregate() -> dict:
    """Read ALL cached fixture-statistics -> team rates + referee tendencies.
    Needs fixture lists (for referee) + statistics in cache. 0 requests."""
    # referee per fixture id, from cached fixture lists
    ref_of = {}
    for f in glob.glob(str(CACHE / "fixtures_*.json")):
        if "statistics" in Path(f).name:        # those are fixture-stats, not lists
            continue
        d = json.load(open(f))
        for fx in d.get("response", []):
            if "fixture" not in fx:
                continue
            ref_of[fx["fixture"]["id"]] = (fx["fixture"].get("referee") or "").split(",")[0].strip()
    team_acc = defaultdict(lambda: defaultdict(list))
    ref_acc = defaultdict(lambda: defaultdict(list))
    for f in glob.glob(str(CACHE / "fixtures_statistics_*.json")):
        d = json.load(open(f))
        fid = int((d.get("parameters") or {}).get("fixture", 0) or 0)
        resp = d.get("response", [])
        match_cards = match_fouls = 0
        for side in resp:
            team = side["team"]["name"]
            for s in side.get("statistics", []):
                k = STATS.get(s["type"])
                v = s.get("value")
                if k is None or v is None:
                    continue
                v = float(str(v).replace("%", "") or 0)
                team_acc[_norm(team)][k].append(v)
                if k in ("yellow", "red"):
                    match_cards += v
                if k == "fouls":
                    match_fouls += v
        ref = ref_of.get(fid)
        if ref:
            ref_acc[ref]["cards"].append(match_cards)
            ref_acc[ref]["fouls"].append(match_fouls)
    teams = {t: {k: round(sum(v) / len(v), 2) for k, v in d.items()} |
                {"_n": max(len(v) for v in d.values())} for t, d in team_acc.items()}
    refs = {r: {k: round(sum(v) / len(v), 2) for k, v in d.items()} |
               {"_n": len(d["cards"])} for r, d in ref_acc.items() if d["cards"]}
    return {"teams": teams, "referees": refs}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "aggregate"
    if cmd == "pull":
        pull(int(sys.argv[2]) if len(sys.argv) > 2 else 60)
    agg = aggregate()
    print(f"\nteams with stats: {len(agg['teams'])}; referees: {len(agg['referees'])}")
    import itertools
    print("\nsample team rates (offsides/fouls/corners per match):")
    for t, r in itertools.islice(sorted(agg["teams"].items()), 0, 8):
        print(f"  {t:18s} n={r.get('_n',0):2d} off={r.get('offsides','?')} "
              f"fouls={r.get('fouls','?')} corners={r.get('corners','?')}")
    print("\nsample referee tendencies (cards/fouls per match):")
    for r, d in itertools.islice(sorted(agg["referees"].items(), key=lambda x: -x[1].get('cards',0)), 0, 6):
        print(f"  {r:22s} n={d.get('_n',0):2d} cards={d.get('cards','?')} fouls={d.get('fouls','?')}")

"""API-Football client (api-sports.io v3) — the source for national-team
per-fixture stats (shots/SoT/fouls/corners/offsides/cards) + REFEREE assignment +
lineups, across WC + qualifiers + friendlies. Closes the gap intl_form.py can't
(scores only) and the soccerdata FBref international gap.

FREE TIER = 100 requests/day, 10/min. So the #1 rule is FRUGALITY:
  - every GET is CACHED to disk (data/sheets/apifootball_cache/*.json); a cache
    hit costs ZERO requests. Re-runs are free.
  - prefer AGGREGATE endpoints (/teams/statistics = one team-season in 1 req)
    over per-fixture loops.
  - a quota guard refuses live calls when the daily budget is nearly spent.

Read-only data key (data quota, not money). Stored ~/.apifootball_key (0600).

  python apifootball.py status                 # account + remaining quota (cached)
  python apifootball.py fixtures <league> <season>
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE = "https://v3.football.api-sports.io"
CACHE = Path(__file__).parent / "data" / "sheets" / "apifootball_cache"
KEY_FILE = Path.home() / ".apifootball_key"
MIN_BUDGET = 5          # refuse live calls when fewer than this remain today


def _key() -> str:
    k = os.environ.get("APIFOOTBALL_KEY", "")
    if not k and KEY_FILE.exists():
        k = KEY_FILE.read_text().strip()
    if not k:
        raise RuntimeError("APIFOOTBALL_KEY missing (env or ~/.apifootball_key)")
    return k


class APIFootball:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["x-apisports-key"] = _key()
        CACHE.mkdir(parents=True, exist_ok=True)
        self.remaining = None        # daily requests remaining (from last live call)

    def _cache_path(self, endpoint: str, params: dict) -> Path:
        h = hashlib.sha1(f"{endpoint}?{sorted(params.items())}".encode()).hexdigest()[:16]
        return CACHE / f"{endpoint.replace('/', '_')}_{h}.json"

    def get(self, endpoint: str, params: dict | None = None, force: bool = False) -> dict:
        """Cached GET. Cache hit = 0 requests. On miss, spend 1 request (guarded)."""
        params = params or {}
        cp = self._cache_path(endpoint, params)
        if cp.exists() and not force:
            return json.loads(cp.read_text())
        if self.remaining is not None and self.remaining < MIN_BUDGET:
            raise RuntimeError(f"quota guard: {self.remaining} req left today (< {MIN_BUDGET}); "
                               "not spending. Cached data only.")
        for attempt in range(3):
            r = self.s.get(f"{BASE}/{endpoint}", params=params, timeout=20)
            if r.status_code == 429:          # per-minute (10/min) — wait
                time.sleep(6); continue
            r.raise_for_status()
            rem = r.headers.get("x-ratelimit-requests-remaining")
            if rem is not None:
                self.remaining = int(rem)
            d = r.json()
            # API-Football returns body-level errors with HTTP 200 (e.g. plan
            # limits). Surface them — and do NOT cache an error as if it were data.
            errs = d.get("errors")
            if errs and (errs if isinstance(errs, list) else list(errs.values())):
                raise RuntimeError(f"API-Football {endpoint} error: {errs}")
            cp.write_text(json.dumps(d))      # cache so we never re-spend
            return d
        r.raise_for_status()

    # ---- frugal, aggregate-first helpers ----
    def status(self) -> dict:
        return self.get("status")

    def fixtures(self, **params) -> list:
        """e.g. fixtures(league=1, season=2026) — fixtures incl. referee + ids."""
        return self.get("fixtures", params).get("response", [])

    def fixture_statistics(self, fixture_id: int) -> list:
        """per-team shots/SoT/fouls/corners/offsides/cards for ONE fixture (1 req)."""
        return self.get("fixtures/statistics", {"fixture": fixture_id}).get("response", [])

    def team_statistics(self, team: int, league: int, season: int) -> dict:
        """aggregate team season stats in ONE request (frugal)."""
        return self.get("teams/statistics",
                        {"team": team, "league": league, "season": season}).get("response", {})


if __name__ == "__main__":
    c = APIFootball()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        d = c.status().get("response", {})
        sub = (d.get("subscription") or {})
        req = (d.get("requests") or {})
        print("account:", d.get("account", {}).get("email", "?"),
              "| plan:", sub.get("plan"), "| active:", sub.get("active"))
        print(f"requests today: {req.get('current')}/{req.get('limit_day')}  "
              f"(remaining now: {c.remaining})")
    elif cmd == "fixtures":
        fx = c.fixtures(league=int(sys.argv[2]), season=int(sys.argv[3]))
        print(f"{len(fx)} fixtures")
        for f in fx[:5]:
            fi = f["fixture"]; t = f["teams"]
            print(f"  {fi['date'][:10]} {t['home']['name']} v {t['away']['name']}  "
                  f"ref={fi.get('referee')}  id={fi['id']}")

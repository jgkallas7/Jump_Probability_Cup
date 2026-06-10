"""The Odds API client — credit-aware, rationed for the free tier.

Free endpoints: /sports, /sports/{key}/events. Poll liberally.
Paid endpoints cost markets x regions per call; every call logs cost from
the x-requests-* response headers into credit_log.

Free-tier ration plan: one targeted per-event pull (h2h,totals / eu only
= 2 credits) near each match deadline. ~300 credits/month for 104 matches.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import requests

from config import ODDS_API_BASE, odds_api_key


class OddsClient:
    def __init__(self, conn=None):
        self.key = odds_api_key()
        self.conn = conn  # optional sqlite conn for credit_log

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        params = dict(params or {})
        params["apiKey"] = self.key
        r = requests.get(f"{ODDS_API_BASE}{path}", params=params, timeout=30)
        r.raise_for_status()
        self._log_credits(path, r.headers)
        return r.json()

    def _log_credits(self, endpoint: str, headers) -> None:
        cost = headers.get("x-requests-last")
        used = headers.get("x-requests-used")
        remaining = headers.get("x-requests-remaining")
        if remaining is not None:
            print(f"[credits] {endpoint}: cost={cost} used={used} remaining={remaining}",
                  file=sys.stderr)
        if self.conn is not None and remaining is not None:
            self.conn.execute(
                "INSERT INTO credit_log(ts, endpoint, cost, used, remaining) "
                "VALUES (?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), endpoint,
                 cost, used, remaining))
            self.conn.commit()

    # ---- free ----
    def sports(self, include_all: bool = True) -> list:
        return self._get("/sports", {"all": "true"} if include_all else {})

    def events(self, sport_key: str) -> list:
        return self._get(f"/sports/{sport_key}/events")

    # ---- paid: cost = markets x regions ----
    def odds(self, sport_key: str, markets: str, regions: str,
             event_ids: list[str] | None = None) -> list:
        params = {"markets": markets, "regions": regions,
                  "oddsFormat": "decimal"}
        if event_ids:
            params["eventIds"] = ",".join(event_ids)
        return self._get(f"/sports/{sport_key}/odds", params)

    def event_odds(self, sport_key: str, event_id: str,
                   markets: str, regions: str) -> dict:
        return self._get(
            f"/sports/{sport_key}/events/{event_id}/odds",
            {"markets": markets, "regions": regions, "oddsFormat": "decimal"})

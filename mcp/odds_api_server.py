"""The Odds API MCP server — credit-disciplined access for the Probability Cup.

Mirrors kalshi-tracker's FastMCP pattern. Key from ODDS_API_KEY env or
~/.odds_api_key. Every paid call logs cost to wc_cup.db credit_log and
returns the running total, so credit burn is visible in-conversation.

Credit guardrails (the "wasted credits doing dumb shit" defense):
  - every tool returns estimated/actual cost alongside data
  - paid calls above MAX_CALL_COST (default 30) are REFUSED unless
    force=true is passed explicitly
  - historical endpoints cost 10x — the multiplier is enforced in the
    estimate, not discovered after the fact

Costs (docs v4): /sports FREE, /events FREE, /odds = markets x regions,
event /markets discovery = 1, /scores = 1-2, participants = 1,
historical odds = 10 x markets x regions, historical events = 1.

Usage: .mcp.json runs this with the project venv python.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from mcp.server.fastmcp import FastMCP

BASE = "https://api.the-odds-api.com/v4"
DB_PATH = os.environ.get("WC_DB_PATH", "/home/jgkal/wc_cup.db")
DEFAULT_SPORT = "soccer_fifa_world_cup"
MAX_CALL_COST = int(os.environ.get("ODDS_MCP_MAX_COST", "30"))

mcp = FastMCP("odds-api", log_level="WARNING")


def _key() -> str:
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        kf = Path.home() / ".odds_api_key"
        if kf.exists():
            key = kf.read_text().strip()
    if not key:
        raise RuntimeError("no Odds API key (env ODDS_API_KEY or ~/.odds_api_key)")
    return key


def _log_credits(endpoint: str, headers) -> dict:
    cost = headers.get("x-requests-last")
    used = headers.get("x-requests-used")
    remaining = headers.get("x-requests-remaining")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            "INSERT INTO credit_log(ts, endpoint, cost, used, remaining) VALUES (?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), endpoint, cost, used, remaining))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"credit_log write failed: {e}", file=sys.stderr)
    return {"cost": cost, "used_this_month": used, "remaining": remaining}


def _get(path: str, params: dict | None = None) -> tuple[object, dict]:
    params = dict(params or {})
    params["apiKey"] = _key()
    r = requests.get(f"{BASE}{path}", params=params, timeout=30)
    credits = _log_credits(path, r.headers)
    r.raise_for_status()
    return r.json(), credits


def _estimate(markets: str, regions: str, historical: bool = False) -> int:
    n = len([m for m in markets.split(",") if m.strip()]) \
        * len([x for x in regions.split(",") if x.strip()])
    return n * 10 if historical else n


def _guard(est: int, force: bool) -> dict | None:
    if est > MAX_CALL_COST and not force:
        return {"refused": True,
                "estimated_cost": est,
                "max_call_cost": MAX_CALL_COST,
                "hint": "trim markets/regions, or pass force=true if intentional"}
    return None


@mcp.tool()
def credit_status() -> str:
    """Current credit usage: latest header readings plus today's burn from credit_log."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    last = conn.execute("SELECT * FROM credit_log ORDER BY id DESC LIMIT 1").fetchone()
    today = conn.execute(
        "SELECT COALESCE(SUM(cost),0) FROM credit_log WHERE ts >= date('now')"
    ).fetchone()[0]
    n_today = conn.execute(
        "SELECT COUNT(*) FROM credit_log WHERE ts >= date('now')").fetchone()[0]
    conn.close()
    return json.dumps({
        "remaining": last["remaining"] if last else None,
        "used_this_month": last["used"] if last else None,
        "as_of": last["ts"] if last else None,
        "calls_today": n_today,
        "credits_spent_today": today,
    })


@mcp.tool()
def get_sports(all_sports: bool = False) -> str:
    """List sports (FREE). all_sports=true includes out-of-season."""
    data, credits = _get("/sports", {"all": "true"} if all_sports else {})
    return json.dumps({"credits": credits, "sports": data})


@mcp.tool()
def get_events(sport: str = DEFAULT_SPORT, commence_from: str = "",
               commence_to: str = "") -> str:
    """List events with ids/teams/kickoffs (FREE — poll liberally).
    commence_from/to are ISO8601 like 2026-06-11T00:00:00Z."""
    params: dict = {}
    if commence_from:
        params["commenceTimeFrom"] = commence_from
    if commence_to:
        params["commenceTimeTo"] = commence_to
    data, credits = _get(f"/sports/{sport}/events", params)
    return json.dumps({"credits": credits, "count": len(data), "events": data})


@mcp.tool()
def discover_markets(event_id: str, sport: str = DEFAULT_SPORT,
                     regions: str = "eu,us") -> str:
    """List which market keys each bookmaker quotes for one event (1 credit).
    Run this before pulling odds so you only pay for markets that exist."""
    data, credits = _get(f"/sports/{sport}/events/{event_id}/markets",
                         {"regions": regions})
    by_key: dict[str, list[str]] = {}
    for bm in data.get("bookmakers", []):
        for m in bm.get("markets", []):
            by_key.setdefault(m["key"], []).append(bm["key"])
    return json.dumps({"credits": credits,
                       "market_keys": {k: sorted(v) for k, v in sorted(by_key.items())}})


@mcp.tool()
def get_event_odds(event_id: str, markets: str, sport: str = DEFAULT_SPORT,
                   regions: str = "eu", force: bool = False) -> str:
    """Odds for ONE event. COST = markets x regions credits (e.g. 'h2h_3_way,totals'
    x 'eu' = 2). Calls estimated above 30 credits are refused without force=true."""
    est = _estimate(markets, regions)
    refusal = _guard(est, force)
    if refusal:
        return json.dumps(refusal)
    data, credits = _get(f"/sports/{sport}/events/{event_id}/odds",
                         {"markets": markets, "regions": regions,
                          "oddsFormat": "decimal"})
    return json.dumps({"credits": credits, "odds": data})


@mcp.tool()
def get_odds_bulk(markets: str, sport: str = DEFAULT_SPORT, regions: str = "eu",
                  event_ids: str = "", force: bool = False) -> str:
    """Odds for many events in one call. COST = markets x regions (NOT per
    event — filtering with event_ids is free, use it). event_ids comma-separated."""
    est = _estimate(markets, regions)
    refusal = _guard(est, force)
    if refusal:
        return json.dumps(refusal)
    params = {"markets": markets, "regions": regions, "oddsFormat": "decimal"}
    if event_ids:
        params["eventIds"] = event_ids
    data, credits = _get(f"/sports/{sport}/odds", params)
    return json.dumps({"credits": credits, "count": len(data), "odds": data})


@mcp.tool()
def get_scores(sport: str = DEFAULT_SPORT, days_from: int = 1) -> str:
    """Scores for live + recent matches (settlement grading). COST: 2 credits
    with days_from, 1 for live-only (days_from=0)."""
    params = {"daysFrom": days_from} if days_from else {}
    data, credits = _get(f"/sports/{sport}/scores", params)
    return json.dumps({"credits": credits, "count": len(data), "scores": data})


@mcp.tool()
def get_historical_events(date: str, sport: str = "soccer_fifa_world_cup",
                          commence_from: str = "", commence_to: str = "") -> str:
    """Historical events snapshot at ISO date (1 credit). For WC 2022
    calibration use sport='soccer_fifa_world_cup' with 2022 dates."""
    params: dict = {"date": date}
    if commence_from:
        params["commenceTimeFrom"] = commence_from
    if commence_to:
        params["commenceTimeTo"] = commence_to
    data, credits = _get(f"/historical/sports/{sport}/events", params)
    return json.dumps({"credits": credits, "data": data})


@mcp.tool()
def get_historical_event_odds(event_id: str, date: str, markets: str,
                              sport: str = "soccer_fifa_world_cup",
                              regions: str = "eu", force: bool = False) -> str:
    """Historical odds for one event at a snapshot date. COST = 10 x markets x
    regions (e.g. h2h_3_way x eu = 10 credits). Snapshots every 5 min since
    Sept 2022. Use for closing-line calibration of our devig."""
    est = _estimate(markets, regions, historical=True)
    refusal = _guard(est, force)
    if refusal:
        return json.dumps(refusal)
    data, credits = _get(
        f"/historical/sports/{sport}/events/{event_id}/odds",
        {"date": date, "markets": markets, "regions": regions,
         "oddsFormat": "decimal"})
    return json.dumps({"credits": credits, "data": data})


if __name__ == "__main__":
    mcp.run()

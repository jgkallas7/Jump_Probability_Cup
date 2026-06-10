"""BookMaker BetslipProxy gateway client — free continuous tape.

Adapted from kalshi-tracker clients/bookmaker.py (betslip body + adapt
logic, trimmed to what this project stores). Public odds work without a
session cookie. Rate discipline: the tracker's tampermonkey budgets ~2
gateway calls/min with exponential backoff on 429 — same rules apply to
any poller built on this client.

The s_ml==1 row is the 3-way moneyline (hoddst/voddst/drawoddst). Soccer
games with no draw line are SKIPPED, never emitted 2-way: devigging 2 of
3 outcomes inflates favorites 15-20% (paid-for tracker lesson, 2026-06-09).
"""

from __future__ import annotations

import os
from typing import Any

from curl_cffi import requests as cf_requests

from config import BETSLIP_BASE, WC_LEAGUES


def _as_list(value: Any) -> list:
    """ASP.NET JSON: single child is an object, multiple is an array."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _schedule_body(league_ids: list[str]) -> dict:
    return {"o": {"BORequestData": {"BOParameters": {
        "BORt": {},
        "LeaguesIdList": ",".join(league_ids),
        "LanguageId": "0",
        "LineStyle": "E",
        "ScheduleType": "american",
        "LinkDeriv": "true",
    }}}}


def fetch_schedule(league_ids: list[str] | None = None,
                   timeout: int = 25) -> dict:
    """POST GetSchedule for the WC leagues; return the raw JSON response.

    Transport notes (verified 2026-06-10):
      - plain requests -> Cloudflare 403; curl_cffi chrome impersonation
        passes Cloudflare.
      - the gateway then requires a player session: anonymous calls return
        {"status": "error", "error_message": "[Unauthorized] Player is not
        authenticated"}. Supply BOOKMAKER_SESSION_COOKIE (copy the Cookie
        header from a logged-in browser's GetSchedule request, devtools
        Network tab). Raises RuntimeError on the auth error so callers
        never mistake it for an empty schedule.
    """
    ids = league_ids or list(WC_LEAGUES)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/javascript, */*",
        "Origin": "https://be.bookmaker.eu",
        "Referer": "https://be.bookmaker.eu/",
    }
    cookie = os.environ.get("BOOKMAKER_SESSION_COOKIE", "")
    if cookie:
        headers["Cookie"] = cookie
    r = cf_requests.post(
        f"{BETSLIP_BASE}/GetSchedule",
        json=_schedule_body(ids),
        headers=headers,
        impersonate="chrome",
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(f"gateway error: {data.get('error_message')}")
    return data


def parse_games(raw: dict) -> list[dict]:
    """Extract match-odds games and futures from a GetSchedule response.

    Returns dicts: {league_id, league_desc, kind, event_label, bm_event_id,
    start_time, selections: [{label, american}]}. kind is 'match' for the
    3-way h2h games, 'futures' otherwise.
    """
    try:
        leagues = _as_list(raw["Schedule"]["Data"]["Leagues"]["League"])
    except (KeyError, TypeError):
        return []

    out: list[dict] = []
    for league in leagues:
        league_id = str(league.get("IdLeague", ""))
        league_desc = str(league.get("Description", ""))
        for dg in _as_list(league.get("dateGroup", [])):
            for game in _as_list(dg.get("game", [])):
                lines = _as_list(game.get("Derivatives", {}).get("line", []))
                htm = str(game.get("htm", "")).strip()
                vtm = str(game.get("vtm", "")).strip()
                selections: list[dict] = []
                kind = "futures"
                event_label = str(game.get("gdesc", league_desc))

                totals: list[dict] = []
                if htm and vtm:
                    ml = next((l for l in lines if str(l.get("s_ml")) == "1"), None)
                    if ml:
                        vo = str(ml.get("voddst", "")).strip()
                        ho = str(ml.get("hoddst", "")).strip()
                        do = str(ml.get("drawoddst", "") or ml.get("doddst", "")).strip()
                        if vo and ho and do:  # 3-way only, never 2-way soccer
                            selections = [
                                {"label": htm, "american": ho},
                                {"label": "Draw", "american": do},
                                {"label": vtm, "american": vo},
                            ]
                            kind = "match"
                            event_label = f"{vtm} vs {htm}"
                    # Totals ladder: every line row carries one O/U quote
                    # (ovt == unt per row, ladder spans ~1.5-3.0 goals).
                    seen_pts = set()
                    for l in lines:
                        ovt = str(l.get("ovt", "")).strip()
                        ov = str(l.get("ovoddst", "")).strip()
                        un = str(l.get("unoddst", "")).strip()
                        if ovt and ov and un and ovt not in seen_pts:
                            seen_pts.add(ovt)
                            totals.append({"point": float(ovt),
                                           "over": ov, "under": un})
                else:
                    for line in lines:
                        label = str(line.get("tmname", "")).strip()
                        odds = str(line.get("odds", "")).strip()
                        if label and odds:
                            selections.append({"label": label, "american": odds})

                if not selections and not totals:
                    continue
                out.append({
                    "league_id": league_id,
                    "league_desc": WC_LEAGUES.get(league_id, league_desc),
                    "kind": kind,
                    "event_label": event_label,
                    "bm_event_id": str(game.get("idgm", "") or league_id),
                    "start_time": (str(game.get("gmdt", "")) + " "
                                   + str(game.get("gmtm", ""))).strip(),
                    "selections": selections,
                    "totals": totals,
                })
    return out

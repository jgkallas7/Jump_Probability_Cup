"""Project config: paths, API key loading, contest constants.

The Odds API key is shared with kalshi-tracker (single credit pool).
Key resolution order: ODDS_API_KEY env var, then the tracker's api/.env.
"""

from __future__ import annotations

import os
from pathlib import Path

# wc_cup.db lives on ext4, never OneDrive (WAL corrupts there — tracker lesson).
DB_PATH = Path(os.environ.get("WC_DB_PATH", "/home/jgkal/wc_cup.db"))

TRACKER_ENV = Path("/mnt/c/Users/jgkal/OneDrive/[redacted]")

# The Odds API
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "soccer_fifa_world_cup"  # verified against /sports by fetch_schedule.py
PINNACLE_REGION = "eu"               # Pinnacle rides the eu region
CORE_MARKETS = "h2h,totals"

# BookMaker BetslipProxy gateway (mirrors kalshi-tracker clients/bookmaker.py)
BETSLIP_BASE = "https://be.bookmaker.eu/gateway/BetslipProxy.aspx"
WC_MATCH_LEAGUE = "12641"
WC_LEAGUES: dict[str, str] = {
    "12641": "MATCHES",
    "13335": "ODDS TO WIN",
    "19791": "NAME THE FINALISTS",
    "19787": "WINNING CONFEDERATION",
    "19987": "TO REACH ROUND X",
    "17147": "GROUP FUTURES - SPECIALS",
    "20191": "GROUP A", "20192": "GROUP B", "20193": "GROUP C",
    "20194": "GROUP D", "20195": "GROUP E", "20196": "GROUP F",
    "20197": "GROUP G", "20198": "GROUP H", "20199": "GROUP I",
    "20200": "GROUP J", "20201": "GROUP K", "20202": "GROUP L",
}

# Consensus weights by book key (Odds API bookmaker keys + our 'bookmaker_eu').
# Pinnacle is the sharp anchor for soccer; everything else default-weights 1.
BOOK_WEIGHTS: dict[str, float] = {
    "pinnacle": 3.0,
    "bookmaker_eu": 1.5,
}
DEFAULT_BOOK_WEIGHT = 1.0

# SportsPredict contest API (confirmed from /probabilitycup/api docs).
SP_API_BASE = "https://api.sportspredict.com/api/v1"
SP_RATE_LIMIT_PER_MIN = 60          # per IP, REST + MCP combined

# Submissions are INTEGERS 1-99 (API constraint) — floor/ceiling follows.
# Internally we keep float probs; round only at submission time.
PROB_FLOOR = 0.01
PROB_CEILING = 0.99

# Stage multipliers per contest rules.
STAGE_MULTIPLIER = {
    "group": 1.0,
    "r32": 2.0, "r16": 2.0, "qf": 2.0, "sf": 2.0, "third": 2.0,
    "final": 3.0,
}


def odds_api_key() -> str:
    """Resolve the shared Odds API key without copying it into this repo."""
    key = os.environ.get("ODDS_API_KEY", "")
    if key:
        return key
    if TRACKER_ENV.exists():
        for line in TRACKER_ENV.read_text().splitlines():
            line = line.strip()
            if line.startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(
        "ODDS_API_KEY not found in env or kalshi-tracker api/.env"
    )

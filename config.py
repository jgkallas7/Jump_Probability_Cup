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

# Consensus is WHITELIST-ONLY: books with weight > 0 enter fair value;
# everything else (45+ soft books echoing each other) is excluded from
# pricing but still snapshotted — soft-book average is the CROWD PROXY
# for the SPEC §5 posture model, not a fair-value input.
# Empirics (opener, 2026-06-10): exchange cluster (betfair_ex_uk/eu,
# matchbook, smarkets) locked at identical prices, 0.5% overround;
# Pinnacle 3.6% vig. betfair_ex_eu excluded as a duplicate of _uk.
BOOK_WEIGHTS: dict[str, float] = {
    "pinnacle": 3.0,
    "betfair_ex_uk": 2.5,     # deepest market list (32), near-zero overround
    "bookmaker_eu": 1.5,      # our gateway tape (sharp offshore)
    "kalshi": 1.5,            # our own data, liquid near kickoff
    "matchbook": 0.5,         # exchange echo of betfair — low extra info
    "smarkets": 0.5,
    # draftkings excluded from fair value (user call, 2026-06-11) — retail
    # book; its 16 prop markets still snapshot for coverage/crowd reference.
    "fanduel": 1.0,           # props coverage (user wants it for props)
    "betonlineag": 1.0,       # sharp-ish offshore
    "betanysports": 0.75,     # reduced-juice shop
    "onexbet": 0.75,          # big grey book, decent soccer
    "marathonbet": 0.75,
}
DEFAULT_BOOK_WEIGHT = 0.0     # soft books never price fair value

# Quotes older than this at decision time are dropped from consensus
# (books pull/repost on lineup news around T-60 — exactly when we pull).
MAX_QUOTE_AGE_MIN = 10.0

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
    """Resolve the Odds API key without copying it into this repo.

    Order: env var, ~/.odds_api_key (current free key, 2026-06-10),
    then the tracker's api/.env (stale 401 key as of 2026-06-10).
    """
    key = os.environ.get("ODDS_API_KEY", "")
    if key:
        return key
    key_file = Path.home() / ".odds_api_key"
    if key_file.exists():
        key = key_file.read_text().strip()
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

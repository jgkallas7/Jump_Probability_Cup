"""Counted per-team match rates from FBref (soccerdata) — ROADMAP #1.

Replaces derive.py's UNVERIFIED hardcoded BASE rates (offsides, fouls, shots,
cards, corners) with rates COUNTED from the current tournament. This is the
orthogonal signal the crowd lacks: nobody eyeballs a team's offsides-per-match,
so a counted rate + Skellam is where we can actually beat the field.

Small-sample reality: the World Cup is days old (1-3 games/team), so every team
rate is shrunk toward the tournament mean with a pseudo-count prior (empirical
Bayes). As games accrue the team's own rate dominates.

Data: data/sheets/wc_team_match_{shooting,misc}.csv, refreshed by
fetch_team_stats() (browser scrape, cached by soccerdata). Read-only at price
time — never blocks the pipeline on a live scrape.

Usage:
  python team_rates.py refresh   # scrape + rebuild parquet (slow, browser)
  python team_rates.py           # print the rate table from cache
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

SHEET_DIR = Path(__file__).parent / "data" / "sheets"
STAT_TYPES = ("shooting", "misc")
PSEUDO = 2.0   # prior strength in "tournament-average games"

# Use API-Football 2022-2024 historical national-team rates as the PRIOR (instead
# of the flat tournament mean) when WC_APIF_PRIOR=1. This is the team-specific,
# multi-match, strictly-prior estimate that fixes the matchday-1 sample problem
# (no 2nd game needed). Falls back to tournament mean per team/stat not covered.
APIF_PRIOR_ON = os.environ.get("WC_APIF_PRIOR", "") == "1"
APIF_MIN_N = 2          # need >=2 historical matches to trust a team's rate
_APIF_CACHE = None


def _apif_prior() -> dict:
    """{norm_team: {stat: historical_per_match_rate}} from apifootball_history."""
    global _APIF_CACHE
    if _APIF_CACHE is None:
        try:
            import apifootball_history as ah
            agg = ah.aggregate()
            _APIF_CACHE = {t: r for t, r in agg.get("teams", {}).items()
                           if r.get("_n", 0) >= APIF_MIN_N}
        except Exception:
            _APIF_CACHE = {}
    return _APIF_CACHE

# FBref team string -> Odds API team string (only where they differ).
FBREF_ALIASES = {
    "korea republic": "South Korea", "ir iran": "Iran", "iran": "Iran",
    "united states": "USA", "turkiye": "Turkey", "türkiye": "Turkey",
    "czechia": "Czech Republic", "bosnia-herzegovina": "Bosnia & Herzegovina",
    "curacao": "Curaçao", "ivory coast": "Ivory Coast",
    "cabo verde": "Cape Verde", "cape verde": "Cape Verde",
}

# the stat columns we care about, mapped to FBref column candidates (first hit).
STAT_COLS = {
    "shots":    ("Sh", "Standard_Sh", "shots"),
    "sot":      ("SoT", "Standard_SoT", "shots_on_target"),
    "fouls":    ("Fls", "Performance_Fls", "fouls"),
    "yellow":   ("CrdY", "Performance_CrdY", "cards_yellow"),
    "red":      ("CrdR", "Performance_CrdR", "cards_red"),
    "offsides": ("Off", "Performance_Off", "offsides"),
    # PKwon/PKcon: FBref's misc sheet has published these as ALL-NaN for WC2026
    # (found 2026-07-02: fillna(0) turned the dead column into "every team wins
    # 0 penalties", tournament prior 0.0, and qmodel priced pen|red at 15 vs a
    # 32 field). Standard_PKatt (shooting sheet) is the live fallback — a pen
    # attempted ≈ a pen won. _find_col_with_data skips dead columns entirely.
    "pk_won":   ("PKwon", "Performance_PKwon", "Standard_PKatt"),
    "pk_con":   ("PKcon", "Performance_PKcon"),
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def to_oddsapi_name(fbref_team: str) -> str:
    return FBREF_ALIASES.get(str(fbref_team).strip().lower(), str(fbref_team).strip())


def _find_col(df: pd.DataFrame, candidates) -> str | None:
    cols = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols:
            return cols[cand.lower()]
    # fuzzy: endswith
    for cand in candidates:
        for lc, orig in cols.items():
            if lc.endswith(cand.lower()):
                return orig
    return None


def _find_col_with_data(df: pd.DataFrame, candidates) -> str | None:
    """Like _find_col, but a column that exists with ZERO non-null values is
    treated as missing (a dead scrape column, not a real all-zero count) and
    the next candidate is tried. Prevents fillna(0) laundering 'no data' into
    'zero events' — the 2026-07-02 pk_won bug."""
    for cand in candidates:
        col = _find_col(df, (cand,))
        if col is not None and pd.to_numeric(df[col], errors="coerce").notna().sum() > 0:
            return col
    return None


MATCHES_COLS = ("90s", "Playing Time_90s", "MP", "Playing Time_MP", "matches")


def _load() -> pd.DataFrame:
    frames = []
    for st in STAT_TYPES:
        p = SHEET_DIR / f"wc_team_season_{st}.csv"
        if p.exists():
            frames.append(pd.read_csv(p))
    if not frames:
        raise FileNotFoundError(
            f"no team-stat parquet in {SHEET_DIR}; run: python team_rates.py refresh")
    df = frames[0]
    for f in frames[1:]:
        df = df.join(f[[c for c in f.columns if c not in df.columns]], how="outer")
    return df


def build_rates() -> dict[str, dict[str, float]]:
    """Return {oddsapi_team: {stat: shrunk_per_match_rate, ..., '_games': n}}.

    Season stats are TOTALS; per-match rate = total / matches_played, then
    empirical-Bayes shrunk toward the tournament mean (small WC sample)."""
    df = _load().reset_index()
    team_col = _find_col(df, ("team",)) or "team"
    df["_team"] = df[team_col].map(to_oddsapi_name)
    mcol = _find_col(df, MATCHES_COLS)
    matches = pd.to_numeric(df[mcol], errors="coerce") if mcol else pd.Series(1.0, index=df.index)
    matches = matches.clip(lower=1.0)

    resolved = {s: _find_col_with_data(df, c) for s, c in STAT_COLS.items()}
    # per-match rate per team-row, then tournament mean for the prior. FBref
    # leaves count columns BLANK (NaN) for zero events (common for penalties/
    # reds early in a tournament) — a blank is a 0 count, so fillna(0).
    rate_df = pd.DataFrame({"_team": df["_team"], "_games": matches})
    for stat, col in resolved.items():
        if col is not None:
            rate_df[stat] = pd.to_numeric(df[col], errors="coerce").fillna(0) / matches
    tourn = {s: float(rate_df[s].mean()) for s in resolved if s in rate_df}
    tourn["cards"] = tourn.get("yellow", 0.0) + tourn.get("red", 0.0)

    apif = _apif_prior() if APIF_PRIOR_ON else {}
    out: dict[str, dict[str, float]] = {}
    for _, row in rate_df.iterrows():
        n = float(row["_games"])
        rates = {"_games": n}
        hist = apif.get(norm(row["_team"]), {})    # historical prior for this team
        for stat in resolved:
            if stat in rate_df and pd.notna(row.get(stat)):
                obs = float(row[stat])
                # prior: API-Football historical rate if covered, else tourn mean
                prior = hist.get(stat, tourn.get(stat, 0.0))
                rates[stat] = (n * obs + PSEUDO * prior) / (n + PSEUDO)
        rates["cards"] = rates.get("yellow", 0.0) + rates.get("red", 0.0)
        out[row["_team"]] = rates
    out["_tournament"] = tourn
    return out


def fetch_team_stats() -> None:
    """Browser-scrape current WC team SEASON stats into parquet (one page per
    stat type — fast). Totals + matches-played give per-match rates."""
    import soccerdata
    SHEET_DIR.mkdir(parents=True, exist_ok=True)
    # no_cache: soccerdata served a Jun-15 cached season page to every daily
    # refresh (caught 2026-07-03 — rates were matchday-1 totals for 18 days)
    fb = soccerdata.FBref(leagues="INT-World Cup", seasons=2026, no_cache=True)
    for st in STAT_TYPES:
        df = fb.read_team_season_stats(stat_type=st)
        df.columns = ["_".join([c for c in col if c]).strip("_")
                      if isinstance(col, tuple) else col for col in df.columns]
        df.to_csv(SHEET_DIR / f"wc_team_season_{st}.csv")
        print(f"  {st}: {df.shape} cached; cols={list(df.columns)[:25]}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        fetch_team_stats()
    rates = build_rates()
    t = rates.pop("_tournament", {})
    print("tournament per-match means:",
          {k: round(v, 2) for k, v in t.items()})
    print(f"\n{'team':22s} {'gms':>3} {'shots':>6} {'sot':>5} {'fouls':>6} "
          f"{'cards':>6} {'offs':>5}")
    for team in sorted(rates):
        r = rates[team]
        print(f"{team:22s} {r.get('_games',0):>3} {r.get('shots',0):>6.1f} "
              f"{r.get('sot',0):>5.1f} {r.get('fouls',0):>6.1f} "
              f"{r.get('cards',0):>6.2f} {r.get('offsides',0):>5.2f}")

"""Stage 1 — club-form team-rate prior (the user's "use players' club data" idea).

The in-tournament sample is ~1 game/team, so team_rates shrinks it to a flat
tournament mean — barely informative. This builds a TEAM-SPECIFIC prior from the
squad's CLUB form (large, current sample): for each national team, estimate its
per-match rate for a stat as the minutes-weighted club per-90 of its likely XI.

  team offsides/match  ~  sum over likely XI of (club offsides per 90)
  team fouls/match     ~  sum over likely XI of (club fouls per 90)   ... etc.

Clean (club season precedes the WC, strictly prior) and needs no matchday-2.
CAVEAT: club system != national-team system, and the XI is uncertain — so this
is a noisy prior, validated OOS (evaluate_qmodel.py --prior-only) before trust.
Players outside the covered club leagues fall back to the flat prior.

Data (from soccerdata, pulled by the Stage 1 background job):
  data/sheets/wc2026_squads.csv          national-team rosters
  data/sheets/club_{standard,misc,shooting}.csv   club per-90 rates + minutes

Usage: python club_form.py        # print the club-form prior table
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd

from team_rates import to_oddsapi_name, _find_col

SHEET = Path(__file__).parent / "data" / "sheets"
XI = 11                       # scale a per-player mean to a team-of-11 total
MIN_CLUB_90S = 3.0            # ignore players with too little club time (noisy per-90)

# per-90 club columns -> our stat name. FBref per-90 lives in the "Per 90 Minutes"
# block for shooting; misc has totals only, so we derive per-90 from totals/90s.
PER90 = {
    "shots": ("Standard_Sh", "shots"),       # shooting: total -> /90s
    "sot":   ("Standard_SoT", "shots_on_target"),
    "fouls": ("Performance_Fls", "fouls"),   # misc
    "cards": ("Performance_CrdY", "cards_yellow"),
    "offsides": ("Performance_Off", "offsides"),
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def _player_key(df: pd.DataFrame) -> pd.Series:
    """A robust per-row player identity for matching squad<->club. Prefer an
    FBref id column if present, else normalized player name."""
    for c in df.columns:
        if c.lower() in ("player_id", "id", "fbref_id"):
            return df[c].astype(str)
    pc = _find_col(df, ("player",)) or "player"
    return df[pc].map(_norm)


def _load(name: str) -> pd.DataFrame | None:
    p = SHEET / f"{name}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p).reset_index(drop=True)


def build_club_prior() -> dict[str, dict[str, float]]:
    """{oddsapi_team: {stat: per-match estimate}} from squads' club form.
    Empty dict if the Stage 1 data isn't on disk yet."""
    squads = _load("wc2026_squads")
    std = _load("club_standard")
    misc = _load("club_misc")
    shoot = _load("club_shooting")
    if squads is None or std is None:
        return {}

    # club minutes (90s) per player -> the weight + per-90 denominator
    nineties_col = _find_col(std, ("90s", "Playing Time_90s", "MP"))
    club = pd.DataFrame({"key": _player_key(std),
                         "n90": pd.to_numeric(std[nineties_col], errors="coerce")})
    # gather each stat's club total, join, divide by 90s -> per-90
    for stat, (cands_a, cands_b) in PER90.items():
        src = shoot if stat in ("shots", "sot") else misc
        if src is None:
            continue
        col = _find_col(src, (cands_a, cands_b))
        if col is None:
            continue
        tmp = pd.DataFrame({"key": _player_key(src),
                            stat: pd.to_numeric(src[col], errors="coerce")})
        club = club.merge(tmp, on="key", how="left")
        club[f"{stat}_p90"] = club[stat] / club["n90"].clip(lower=0.1)
    club = club[club["n90"] >= MIN_CLUB_90S]
    club = club.drop_duplicates("key").set_index("key")

    # map squad players -> national team, attach their club per-90
    tcol = _find_col(squads, ("team",)) or "team"
    sq = pd.DataFrame({"team": squads[tcol].map(to_oddsapi_name),
                       "key": _player_key(squads)})
    sq = sq.join(club, on="key")

    out: dict[str, dict[str, float]] = {}
    for team, grp in sq.groupby("team"):
        rates = {"_squad_matched": int(grp["shots_p90"].notna().sum())
                 if "shots_p90" in grp else 0}
        # likely XI ~ top-11 club-minutes players that we matched; mean per-90 x11
        g = grp.dropna(subset=["n90"]).sort_values("n90", ascending=False).head(XI)
        for stat in PER90:
            col = f"{stat}_p90"
            if col in g and g[col].notna().any():
                rates[stat] = float(g[col].mean() * XI)
        out[team] = rates
    return out


if __name__ == "__main__":
    pr = build_club_prior()
    if not pr:
        print("Stage 1 data not on disk yet (squads/club CSVs). Run the pull first.")
    else:
        print(f"club-form prior for {len(pr)} teams:\n")
        print(f"{'team':22s} {'matched':>7} {'shots':>6} {'sot':>5} {'fouls':>6} "
              f"{'cards':>6} {'offs':>5}")
        for t in sorted(pr):
            r = pr[t]
            print(f"{t:22s} {r.get('_squad_matched',0):>7} {r.get('shots',0):>6.1f} "
                  f"{r.get('sot',0):>5.1f} {r.get('fouls',0):>6.1f} "
                  f"{r.get('cards',0):>6.2f} {r.get('offsides',0):>5.2f}")

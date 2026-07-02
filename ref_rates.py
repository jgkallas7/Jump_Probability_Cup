"""Referee tendency layer (HANDOFF #10): join each WC2026 match's referee
(FBref schedule, cached CSV) to that referee's historical cards-per-match
(API-Football 2022-24 aggregate) as a SHRUNK multiplier on card-level lambdas.

Scope, deliberately narrow:
  - Card LEVEL questions only. 43/53 settled card-level rows were priced with
    NO book cards line (counted/base/placeholder tiers, 2026-07-02 audit), so
    a referee signal is genuinely additive there. Where a book line exists
    (derived tier) the market already prices the ref — untouched.
  - The fouls/cards RACE is a ratio (both teams share the ref), the multiplier
    cancels — untouched.
  - Ref unknown (FBref posts assignments late; scrape is flaky) -> 1.0, no-op.

Data quality guards: name matching is accent-folded (lastname, first-initial)
— API-Football stores "J. Valenzuela", FBref "Jesús Valenzuela"; thin ref
samples shrink toward the competition mean (w = n/(n+6)); the final multiplier
is clamped to [0.75, 1.35].
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

SCHEDULE_CSV = Path(__file__).resolve().parent / "data" / "sheets" / "wc_schedule_refs.csv"
SHRINK_K = 6           # pseudo-matches toward the comp mean
CLAMP = (0.75, 1.35)

_schedule = None       # cached [(date, home_norm, away_norm, referee), ...]
_ref_table = None      # cached {(last, initial): (cards_per_match, n)}
_comp_mean = None


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


def _ref_key(name: str) -> tuple[str, str] | None:
    """('valenzuela', 'j') from 'Jesús Valenzuela' OR 'J. Valenzuela'."""
    parts = [p for p in re.split(r"[\s.]+", _fold(name).lower()) if p]
    if len(parts) < 2:
        return None
    return parts[-1], parts[0][0]


def _team_norm(s: str) -> str:
    from team_rates import to_oddsapi_name
    from ingest_questions import norm_team
    return norm_team(to_oddsapi_name(_fold(s)))


def refresh() -> int:
    """Re-scrape the FBref schedule (refs appear as matches are played /
    announced). Guarded: any failure keeps the existing CSV. Returns the
    number of rows with a referee, or -1 on failure."""
    try:
        import soccerdata
        fb = soccerdata.FBref(leagues="INT-World Cup", seasons=2026, no_cache=True)
        df = fb.read_schedule().reset_index()
        if "referee" not in df.columns or not len(df):
            return -1
        df.to_csv(SCHEDULE_CSV, index=False)
        global _schedule
        _schedule = None                      # invalidate
        return int(df["referee"].notna().sum())
    except Exception:
        return -1


def _load_schedule():
    global _schedule
    if _schedule is not None:
        return _schedule
    _schedule = []
    try:
        import csv
        with open(SCHEDULE_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ref = (row.get("referee") or "").strip()
                if not ref:
                    continue
                _schedule.append(((row.get("date") or "")[:10],
                                  _team_norm(row.get("home_team", "")),
                                  _team_norm(row.get("away_team", "")), ref))
    except OSError:
        pass
    return _schedule


def _load_refs():
    global _ref_table, _comp_mean
    if _ref_table is not None:
        return _ref_table, _comp_mean
    _ref_table = {}
    tot = n_tot = 0.0
    try:
        import apifootball_history as ah
        for name, d in (ah.aggregate().get("referees") or {}).items():
            k = _ref_key(name)
            n = float(d.get("_n") or 0)
            c = d.get("cards")
            if not k or c is None or n <= 0:
                continue
            # keep the larger sample on key collisions (folded homonyms)
            if k not in _ref_table or n > _ref_table[k][1]:
                _ref_table[k] = (float(c), n)
            tot += float(c) * n
            n_tot += n
    except Exception:
        pass
    _comp_mean = (tot / n_tot) if n_tot else None
    return _ref_table, _comp_mean


def ref_for(home: str, away: str, date: str) -> str | None:
    """Referee for a WC2026 match, or None. `date` ISO (kickoff_utc ok)."""
    d = (date or "")[:10]
    h, a = _team_norm(home), _team_norm(away)
    for sd, sh, sa, ref in _load_schedule():
        if {sh, sa} == {h, a} and abs_days(sd, d) <= 1:
            return ref
    return None


def abs_days(d1: str, d2: str) -> int:
    from datetime import date
    try:
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2)
        return abs((a - b).days)
    except ValueError:
        return 99


def cards_multiplier(home: str, away: str, date: str) -> tuple[float, str | None]:
    """(shrunk clamped multiplier, referee name) — (1.0, None) when unknown."""
    ref = ref_for(home, away, date)
    if not ref:
        return 1.0, None
    table, mean = _load_refs()
    k = _ref_key(ref)
    if not k or k not in table or not mean:
        return 1.0, ref
    rate, n = table[k]
    w = n / (n + SHRINK_K)
    mult = 1.0 + w * (rate / mean - 1.0)
    return min(CLAMP[1], max(CLAMP[0], mult)), ref

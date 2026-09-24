"""Stage 1 (pivoted) — national-team attack/defence ratings from REAL recent
international results (the user's "qualifiers + friendlies 2022-2026" idea).

Source: martj42/international_results (CC0, complete intl results incl. WC
qualifiers, friendlies, Nations League; current through 2026). Plain CSV, no
scraping, no key. ~40-50 matches/team since 2022 — a large, current, same-cycle
sample of the actual national team under its current coach, far better than the
~1 in-tournament game team_rates currently has.

Builds opponent-adjusted, recency-weighted Poisson ratings:
  lam(home vs away) = base * attack[home] * defence[away] * home_adv
  lam(away)         = base * attack[away] * defence[home] / home_adv
These goal-rate lambdas are an INDEPENDENT estimate to blend with / sanity-check
the market lambdas (derive.match_lambdas) and to rescue thin-market matches.
Scores only — informs goals/totals/BTTS/first-goal/result, NOT offsides/fouls
(those need API-Football per-fixture stats; see docs/IMPROVEMENT_CHARTER.md).

Usage:
  python intl_form.py refresh   # download + cache results.csv
  python intl_form.py           # print ratings + a sample matchup
"""
from __future__ import annotations

import csv
import io
import math
import re
import ssl
import sys
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

RAW = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
CACHE = Path(__file__).parent / "data" / "sheets" / "intl_results.csv"
SINCE = "2022-08-01"        # current World Cup cycle
HALFLIFE_DAYS = 400.0       # recency weighting
ASOF = date(2026, 6, 16)    # pin "now" (Date.now is fine in plain runs; pinned for reproducibility)

# martj42 name -> our Odds API / pipeline name (only where they differ).
INTL_ALIASES = {
    "United States": "USA", "South Korea": "South Korea", "Cape Verde": "Cape Verde",
    "Cabo Verde": "Cape Verde", "Ivory Coast": "Ivory Coast",
    "Republic of Ireland": "Ireland", "Turkey": "Turkey", "Türkiye": "Turkey",
    "Czech Republic": "Czech Republic", "Czechia": "Czech Republic",
    "DR Congo": "DR Congo", "Bosnia and Herzegovina": "Bosnia & Herzegovina",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def name(t: str) -> str:
    return INTL_ALIASES.get(t.strip(), t.strip())


def refresh() -> None:
    req = urllib.request.Request(RAW, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=40, context=ssl.create_default_context()).read()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(data)
    print(f"cached {CACHE} ({len(data)//1024} KB)")


def _load_rows() -> list[dict]:
    if not CACHE.exists():
        refresh()
    rows = list(csv.DictReader(io.StringIO(CACHE.read_text(encoding="utf-8", errors="ignore"))))
    return [r for r in rows if r["date"] >= SINCE and r["home_score"] != ""
            and r["date"] <= ASOF.isoformat()]


def _weight(d: str) -> float:
    age = (ASOF - date.fromisoformat(d)).days
    return math.exp(-max(age, 0) / HALFLIFE_DAYS)


def build_ratings(iters: int = 6) -> dict:
    """{'attack':{team:..}, 'defence':{team:..}, 'base':float, 'home_adv':float,
    'n':{team:..}} — opponent-adjusted, recency-weighted."""
    rows = _load_rows()
    # expand to (team, gf, ga, opp, w, home?) rows
    recs = []
    wsum = gsum = 0.0
    hg = ag = hw = 0.0
    for r in rows:
        try:
            hs, as_ = float(r["home_score"]), float(r["away_score"])
        except ValueError:
            continue
        w = _weight(r["date"])
        h, a = name(r["home_team"]), name(r["away_team"])
        neutral = r.get("neutral", "").strip().upper() in ("TRUE", "1")
        recs.append((h, hs, as_, a, w, not neutral))
        recs.append((a, as_, hs, h, w, False))
        wsum += w; gsum += w * (hs + as_)
        if not neutral:
            hg += w * hs; ag += w * as_; hw += w
    base = (gsum / (2 * wsum)) if wsum else 1.3          # avg goals per team per match
    home_adv = math.sqrt((hg / hw) / (ag / hw)) if hw and ag else 1.1

    teams = {t for t, *_ in recs}
    attack = {t: 1.0 for t in teams}
    defence = {t: 1.0 for t in teams}
    for _ in range(iters):
        gf_num, gf_den = defaultdict(float), defaultdict(float)
        ga_num, ga_den = defaultdict(float), defaultdict(float)
        for t, gf, ga, opp, w, _h in recs:
            gf_num[t] += w * gf
            gf_den[t] += w * base * defence[opp]
            ga_num[t] += w * ga
            ga_den[t] += w * base * attack[opp]
        for t in teams:
            if gf_den[t] > 0:
                attack[t] = gf_num[t] / gf_den[t]
            if ga_den[t] > 0:
                defence[t] = ga_num[t] / ga_den[t]
        # normalise so mean attack/defence = 1
        ma = sum(attack.values()) / len(attack)
        md = sum(defence.values()) / len(defence)
        attack = {t: v / ma for t, v in attack.items()}
        defence = {t: v / md for t, v in defence.items()}
    n = defaultdict(int)
    for t, *_ in recs:
        n[t] += 1
    return {"attack": attack, "defence": defence, "base": base,
            "home_adv": home_adv, "n": dict(n)}


def intl_lambdas(home: str, away: str, R: dict | None = None):
    """(lam_home, lam_away, lam_total) from intl ratings, or None if either team
    is unrated (too few intl matches)."""
    R = R or build_ratings()
    h, a = name(home), name(away)
    if h not in R["attack"] or a not in R["attack"]:
        return None
    base, hadv = R["base"], R["home_adv"]
    lh = base * R["attack"][h] * R["defence"][a] * hadv
    la = base * R["attack"][a] * R["defence"][h] / hadv
    return lh, la, lh + la


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        refresh()
    R = build_ratings()
    teams = sorted(R["attack"], key=lambda t: -R["attack"][t])
    print(f"base goals/team/match={R['base']:.2f}  home_adv={R['home_adv']:.3f}  "
          f"({len(R['attack'])} teams rated)\n")
    print("strongest attacks:", [f"{t} {R['attack'][t]:.2f}" for t in teams[:6]])
    print("stingiest defences:",
          [f"{t} {R['defence'][t]:.2f}" for t in sorted(R['defence'], key=lambda t: R['defence'][t])[:6]])
    for h, a in [("Iran", "New Zealand"), ("Spain", "Cape Verde"), ("Brazil", "Morocco")]:
        lam = intl_lambdas(h, a, R)
        if lam:
            print(f"  {h} vs {a}: lam_home={lam[0]:.2f} lam_away={lam[1]:.2f} total={lam[2]:.2f}")

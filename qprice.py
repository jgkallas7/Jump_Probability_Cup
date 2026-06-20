"""Quantitative pricers for the specific contest question shapes.

Pure math (scipy + numpy) — NO I/O, so each pricer is unit-testable and the
parameters (the Poisson rates) are supplied by callers from live data. The
question grammar reduces to a small set of closed forms:

  "N or more X"                -> Poisson survival         P(X >= N)
  "team A more X than team B"   -> Skellam (Poisson diff)   P(A - B >= 1)
  "both teams score AND >=N"    -> bivariate Poisson tail   (enumerated)
  "X scores first AND <result>" -> goal-sequence model      (analytic)

Rates (lambdas) come from the market (supremacy + total -> goal rates) and,
for stat counts, from team rate feeds; this module just turns rates into
probabilities. Everything is anchored, auditable, and deterministic.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import poisson, skellam

# Empirical share of a match's events landing in the FIRST half. Soccer is
# slightly back-loaded: more goals/shots/corners/cards arrive after the break
# as legs tire and games open up. Defaults are league-stable priors; callers
# may override per stat. (cards especially: ~37% H1 / 63% H2.)
H1_SHARE = {
    "goals": 0.45, "shots": 0.47, "sot": 0.47, "corners": 0.48,
    "cards": 0.37, "fouls": 0.49, "offsides": 0.48,
}


def clip(p: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return float(min(hi, max(lo, p)))


def prob_n_or_more(lam: float, n: int) -> float:
    """P(X >= n), X ~ Poisson(lam). 'N or more total cards/corners/offsides'."""
    if n <= 0:
        return 1.0
    return float(poisson.sf(n - 1, lam))


def prob_exactly(lam: float, n: int) -> float:
    return float(poisson.pmf(n, lam))


def prob_a_more_than_b(lam_a: float, lam_b: float, by: int = 1) -> float:
    """P(A - B >= by) for independent A~Pois(lam_a), B~Pois(lam_b).

    'Will A have more fouls/corners/SOT than B?' is strictly-greater => by=1
    (the difference is an integer, so >0 is >=1). Skellam is the exact pmf of
    the difference of two Poissons — no simulation needed."""
    return float(skellam.sf(by - 1, lam_a, lam_b))


def prob_a_more_than_b_half(lam_a: float, lam_b: float, stat: str,
                            half: str) -> float:
    """Same comparison restricted to one half: scale each rate by the half
    share, then Skellam. 'In the second half, will A have more corners than B?'"""
    s = H1_SHARE.get(stat, 0.48)
    frac = s if half == "h1" else (1.0 - s)
    return prob_a_more_than_b(lam_a * frac, lam_b * frac)


def prob_btts(lam_h: float, lam_a: float) -> float:
    """P(both teams score) = P(home>=1)P(away>=1), independent Poisson goals."""
    return float((1 - np.exp(-lam_h)) * (1 - np.exp(-lam_a)))


def _score_grid(lam_h: float, lam_a: float, kmax: int = 12) -> np.ndarray:
    """Joint pmf grid P(home=i, away=j) for i,j in [0,kmax], independent."""
    ph = poisson.pmf(np.arange(kmax + 1), lam_h)
    pa = poisson.pmf(np.arange(kmax + 1), lam_a)
    return np.outer(ph, pa)


def prob_btts_and_total(lam_h: float, lam_a: float, n: int,
                        kmax: int = 12) -> float:
    """P(home>=1 AND away>=1 AND home+away>=n). 'Both teams score AND the
    match has N or more total goals.' Enumerated over the joint grid."""
    g = _score_grid(lam_h, lam_a, kmax)
    i = np.arange(kmax + 1)[:, None]
    j = np.arange(kmax + 1)[None, :]
    mask = (i >= 1) & (j >= 1) & (i + j >= n)
    return float(g[mask].sum())


def prob_team_total(lam_team: float, n: int) -> float:
    """'Will <team> score N or more goals?' -> Poisson survival on team rate."""
    return prob_n_or_more(lam_team, n)


def prob_first_goal_and_result(lam_for: float, lam_against: float,
                               result: str, kmax: int = 12) -> float:
    """P(<team> scores the game's first goal AND <result>).

    Goal arrivals are two independent Poisson processes with rates lam_for,
    lam_against. The first scorer is the team whose first arrival comes first:
    P(team scores first | a goal is scored) = lam_for / (lam_for + lam_against)
    (memoryless competing exponentials). Conditional on having scored first,
    the REMAINING goals are still independent Poisson, so we evaluate the
    requested final-result clause on the residual score distribution plus the
    1-0 head start.

    result in {"win", "not_lose", "team_wins"} — 'and go on to win', etc.
    For the common SP combo 'A scores first AND B wins' use result='opp_win'.
    """
    tot = lam_for + lam_against
    if tot <= 0:
        return 0.0
    p_first = lam_for / tot                  # P(team scores first | >=1 goal)
    p_any = 1 - np.exp(-tot)                  # P(at least one goal in match)
    g = _score_grid(lam_for, lam_against, kmax)   # residual goals after 1st
    i = np.arange(kmax + 1)[:, None]
    j = np.arange(kmax + 1)[None, :]
    # team already leads 1-0; final = (1+i) vs j
    fh, fa = 1 + i, j
    if result in ("win", "team_wins"):
        mask = fh > fa
    elif result == "not_lose":
        mask = fh >= fa
    elif result == "opp_win":            # team scores first but OPPONENT wins
        mask = fa > fh
    elif result == "draw":
        mask = fh == fa
    else:
        raise ValueError(f"unknown result clause: {result}")
    p_clause_given_first = float(g[mask].sum())
    return float(p_any * p_first * p_clause_given_first)


def prob_penalty_or_red(pen_rate: float, red_rate: float) -> float:
    """P(a penalty awarded OR a red card shown) = 1 - P(neither), treating the
    two as independent Poisson-rare events over the match. Rates are expected
    counts per match (e.g. pen_rate~0.25, red_rate~0.12 league-wide)."""
    p_no_pen = np.exp(-pen_rate)
    p_no_red = np.exp(-red_rate)
    return float(1 - p_no_pen * p_no_red)


if __name__ == "__main__":
    # smoke checks against hand figures
    print("P(>=2 cards | lam=3.8) =", round(prob_n_or_more(3.8, 2), 4))
    print("P(A>B | 5.5 vs 4.8 corners) =", round(prob_a_more_than_b(5.5, 4.8), 4))
    print("BTTS (1.6,1.1) =", round(prob_btts(1.6, 1.1), 4))
    print("BTTS & >=3 goals (1.6,1.1) =", round(prob_btts_and_total(1.6, 1.1, 3), 4))
    print("A scores first & wins (1.6 vs 1.0) =",
          round(prob_first_goal_and_result(1.6, 1.0, "win"), 4))
    print("A first & OPP wins (1.6 vs 1.0) =",
          round(prob_first_goal_and_result(1.6, 1.0, "opp_win"), 4))
    print("penalty or red (0.25,0.12) =", round(prob_penalty_or_red(0.25, 0.12), 4))
    # sanity: BTTS&>=2 must equal plain BTTS (every BTTS match has >=2 goals)
    a = prob_btts(1.6, 1.1); b = prob_btts_and_total(1.6, 1.1, 2)
    assert abs(a - b) < 1e-6, (a, b)   # grid truncation at kmax
    print("invariant BTTS == BTTS&>=2 : OK")

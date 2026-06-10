"""De-vigging: book implied probabilities -> fair probabilities.

Core math ported from kalshi-tracker sports/devig.py (multiplicative,
additive, power, shin). New here:

  devig_three_way   power-primary devig of a 3-way soccer market with
                    multiplicative sanity check and divergence flagging
                    (SPEC: flag >1.5pts on any outcome for manual look).
  binary mapping    "Will X win?" YES = p(X), NO = draw + other. The draw
                    is never renormalized away — that is the field's
                    soft spot and our steadiest edge.
  shrink_extremes   floor/ceiling so we never submit 0 or 100.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from config import PROB_CEILING, PROB_FLOOR

DevigMethod = Literal["multiplicative", "additive", "power", "shin"]

DIVERGENCE_FLAG_PTS = 1.5


# --------------------------------------------------------------------------
# Odds conversions
# --------------------------------------------------------------------------

def decimal_to_prob(decimal_odds: float) -> float:
    """Implied probability (with vig) from decimal odds."""
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {decimal_odds}")
    return 1.0 / decimal_odds


def american_to_prob(odds: str | int | float) -> float:
    """Implied probability (with vig) from American odds (BookMaker quotes)."""
    a = float(str(odds).replace("+", ""))
    if a == 0:
        raise ValueError("American odds of 0 are invalid")
    if a > 0:
        return 100.0 / (a + 100.0)
    return -a / (-a + 100.0)


# --------------------------------------------------------------------------
# Core probability de-vig (ported verbatim from kalshi-tracker)
# --------------------------------------------------------------------------

def devig_probs(raw_probs: list[float], method: DevigMethod = "power") -> list[float]:
    """De-vig a list of raw implied probabilities to fair probs summing to 1."""
    clean = [p for p in raw_probs if p > 0]
    if not clean or sum(raw_probs) <= 0:
        return raw_probs
    if method == "multiplicative":
        return _multiplicative(raw_probs)
    if method == "additive":
        return _additive(raw_probs)
    if method == "power":
        return _power(raw_probs)
    if method == "shin":
        return _shin(raw_probs)
    raise ValueError(f"unknown devig method: {method}")


def _multiplicative(raw: list[float]) -> list[float]:
    total = sum(raw)
    return [p / total for p in raw]


def _additive(raw: list[float]) -> list[float]:
    n = len(raw)
    over = sum(raw)
    vig = (over - 1.0) / n
    adj = [max(1e-6, p - vig) for p in raw]
    t = sum(adj)
    return [p / t for p in adj]


def _power(raw: list[float], tol: float = 1e-9) -> list[float]:
    lo, hi, k = 0.5, 3.0, 1.0
    for _ in range(100):
        k = (lo + hi) / 2
        s = sum(p ** k for p in raw)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = k
        else:
            hi = k
    out = [p ** k for p in raw]
    t = sum(out)
    return [p / t for p in out]


def _shin(raw: list[float], tol: float = 1e-9) -> list[float]:
    over = sum(raw)
    q = [p / over for p in raw]

    def probs(z: float) -> list[float]:
        if z >= 1.0:
            return q
        out = []
        for qi in q:
            disc = z * z + 4 * (1 - z) * qi * qi
            out.append((math.sqrt(disc) - z) / (2 * (1 - z)))
        return out

    lo, hi, z = 0.0, 0.5, 0.0
    for _ in range(200):
        z = (lo + hi) / 2
        s = sum(probs(z))
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = z
        else:
            hi = z
    out = probs(z)
    t = sum(out)
    return [p / t for p in out]


# --------------------------------------------------------------------------
# 3-way soccer market: power-primary with divergence flag
# --------------------------------------------------------------------------

@dataclass
class ThreeWayFair:
    """Devigged 3-way market. Order is always (home, draw, away)."""
    home: float
    draw: float
    away: float
    home_mult: float
    draw_mult: float
    away_mult: float
    overround: float
    divergence_pts: float    # max |power - mult| across outcomes, in points
    flagged: bool            # divergence_pts > DIVERGENCE_FLAG_PTS

    def yes_prob(self, side: Literal["home", "draw", "away"]) -> float:
        """YES probability for 'Will <side> win?' (NO includes the draw)."""
        return {"home": self.home, "draw": self.draw, "away": self.away}[side]


def devig_three_way(raw_home: float, raw_draw: float, raw_away: float) -> ThreeWayFair:
    """De-vig a 3-way market from raw implied probs (vig included)."""
    raw = [raw_home, raw_draw, raw_away]
    power = devig_probs(raw, "power")
    mult = devig_probs(raw, "multiplicative")
    div_pts = max(abs(p - m) for p, m in zip(power, mult)) * 100.0
    return ThreeWayFair(
        home=power[0], draw=power[1], away=power[2],
        home_mult=mult[0], draw_mult=mult[1], away_mult=mult[2],
        overround=sum(raw),
        divergence_pts=round(div_pts, 3),
        flagged=div_pts > DIVERGENCE_FLAG_PTS,
    )


def shrink_extremes(p: float, floor: float = PROB_FLOOR,
                    ceiling: float = PROB_CEILING) -> float:
    """Clamp a forecast away from 0/1 — never submit certainty."""
    return min(max(p, floor), ceiling)

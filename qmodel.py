"""Counted-rate alpha pricer: NO_MARKET questions priced from FBref team rates
(team_rates.py) + market goal-rates, via the scipy closed forms in qprice.py.

This is the quantitative replacement for derive.py's BASE-rate / weak-proxy
handlers on the stat buckets (offsides, fouls, SOT, cards, penalty_or_red).
Each pricer is a thin shell: pick the right counted rates, scale for opponent /
half, hand to a qprice closed form. No simulation, no magic constants beyond
the half-shares in qprice.

price_question(text, home, away, rates, lam) -> (prob, reason) or None
  text  : SP question text
  rates : team_rates.build_rates() dict
  lam   : (lam_home, lam_away, lam_total) goal rates from the market
The caller supplies live `rates` and `lam`; this module is pure given them.
"""
from __future__ import annotations

import os
import re

import qprice
from forecast import resolve_team

# opponent strength barely moves a team's own foul/offside/card counts, but it
# does move shot volume (you shoot more vs a team camped in its box). Scale shot
# & SOT rates by how dominant the team is, centered on parity. Mild slope.
DOM_SLOPE = 0.5

# fouls run the OTHER way to shots: the UNDERDOG commits more (less possession,
# more chasing/tactical fouling) — a robust game-state effect the crowd prices
# and a symmetric counted-rate Skellam misses (the fouls_race bucket was the
# single biggest alpha leak vs the field, -128 pts / 55 Qs; the old derive
# handler h_fouls_race had the right sign but a ~0.08 coefficient, too weak, and
# WC_QMODEL preempts it). WC_FOULS_DOM is the tilt slope; 0.0 = OFF = the exact
# symmetric behaviour we ship today. A human flips it after the OOS gate.
FOUL_DOM_SLOPE = float(os.environ.get("WC_FOULS_DOM", "0") or 0)

# both-teams >=1 SOT in a HALF: the hand-set 0.68 anchor overshot — post-fix
# submissions averaged 0.70 vs a 0.63 field on questions that settle YES ~65%
# (h1 62% n=8, h2 67% n=9 in the outcomes table), realizing -26 over n=10.
# WC_BTS_HALF_ANCHOR moves the anchor; default 0.68 = today's behaviour
# (candidate 0.63 — review_report gate; a human flips it in flags.sh).
BTS_HALF_ANCHOR = float(os.environ.get("WC_BTS_HALF_ANCHOR", "0.68") or 0.68)


def _r(rates, team, stat, default=None):
    d = rates.get(team) or {}
    v = d.get(stat)
    if v is None:
        v = (rates.get("_tournament") or {}).get(stat, default)
    return v


def _dom(lam_team, lam_opp):
    """shot-volume multiplier from goal-rate dominance, centered at 1.0."""
    tot = (lam_team or 0) + (lam_opp or 0)
    if tot <= 0:
        return 1.0
    share = lam_team / tot
    return 1.0 + DOM_SLOPE * (share - 0.5) * 2  # share .5->1.0, .65->1.15


def _foul_dom(lam_team, lam_opp):
    """foul-volume multiplier from goal-rate dominance, INVERSE of _dom(): the
    underdog (goal-share < 0.5) fouls MORE, the favorite fouls LESS. Centered at
    1.0; slope = WC_FOULS_DOM (0.0 -> flat 1.0, i.e. current symmetric prices)."""
    tot = (lam_team or 0) + (lam_opp or 0)
    if tot <= 0:
        return 1.0
    share = lam_team / tot
    return 1.0 + FOUL_DOM_SLOPE * (0.5 - share) * 2  # underdog .35->1+0.3*slope


def _half_word(text):
    t = text.lower()
    if "second half" in t:
        return "h2"
    if "first half" in t or "halftime" in t:
        return "h1"
    return None


def price_question(text, home, away, rates, lam):
    lam_h, lam_a, lam_t = lam
    t = text.strip()

    # --- offsides: counted team rate -> Poisson survival ---
    m = re.search(r"Will (.+?) be caught offside (\d+) or more", t)
    if m:
        team = resolve_team(m.group(1), home, away)
        if not team:
            return None
        n = int(m.group(2))
        rate = _r(rates, team, "offsides", 1.3)
        return (qprice.clip(qprice.prob_n_or_more(rate, n)),
                f"offsides counted lam={rate:.2f} P(>={n})")

    # --- fouls race: counted foul rates, tilted by game-state (underdog fouls
    # more), -> Skellam. The tilt (_foul_dom) is the fix for the -128 leak; with
    # WC_FOULS_DOM=0 it's a no-op and this is the old symmetric counted price. ---
    m = re.search(r"Will (.+?) commit more fouls than (.+?)\?", t)
    if m:
        a = resolve_team(m.group(1), home, away)
        b = resolve_team(m.group(2), home, away)
        if not a or not b:
            return None
        ra, rb = _r(rates, a, "fouls", 12.7), _r(rates, b, "fouls", 12.7)
        la = lam_h if a == home else lam_a
        lb = lam_h if b == home else lam_a
        ra *= _foul_dom(la, lb)
        rb *= _foul_dom(lb, la)
        return (qprice.clip(qprice.prob_a_more_than_b(ra, rb)),
                f"fouls race Skellam {ra:.1f} vs {rb:.1f}")

    # --- total shots on target: sum of counted SOT rates, opp-scaled, half ---
    m = re.search(r"Will there be (\d+) or more total shots on target"
                  r"( in the second half| in the first half)?", t)
    if m:
        n = int(m.group(1))
        half = _half_word(t)
        sot_h = _r(rates, home, "sot", 4.25) * _dom(lam_h, lam_a)
        sot_a = _r(rates, away, "sot", 4.25) * _dom(lam_a, lam_h)
        lam_sot = sot_h + sot_a
        if half:
            s = qprice.H1_SHARE["sot"]
            lam_sot *= s if half == "h1" else (1 - s)
        return (qprice.clip(qprice.prob_n_or_more(lam_sot, n)),
                f"total SOT counted lam={lam_sot:.2f}")

    # --- both teams record a shot on target: independent P(>=1 SOT) each,
    # counted SOT rates, dominance-scaled. A near-certainty the field underrates
    # (~0.72); the full match gets a mild shrink toward 0.80 to guard the hot
    # single-Poisson tail (Poisson underestimates P(0 SOT) for low-shot sides).
    # Half-qualified variants are NOT near-certain, so half-scale and skip the
    # shrink. (Was hitting placeholder.v0's 0.45 catch-all — 'shot' singular
    # missed the 'shots' SOT family regex — and bleeding ~-15/match on YES.) ---
    if re.search(r"will both teams have at least 1 shot on target", t, re.I):
        sot_h = _r(rates, home, "sot", 4.25) * _dom(lam_h, lam_a)
        sot_a = _r(rates, away, "sot", 4.25) * _dom(lam_a, lam_h)
        half = _half_word(t)
        if half:
            # half-restricted ("by halftime" / "in the second half"): a moderate
            # lean (settled 3/3 1H, 2/3 2H; field ~0.72), NOT a near-certainty.
            # The single-half Poisson product (~0.85) is badly overconfident
            # (a team scoring 0 SOT in one half is common), so anchor hard to
            # the ~0.68 base rate with only a whisper of per-match SOT signal —
            # lands ~0.70, near the field (don't over-deviate from consensus).
            s = qprice.H1_SHARE["sot"]
            frac = s if half == "h1" else (1 - s)
            p_raw = (1 - qprice.np.exp(-sot_h * frac)) * (1 - qprice.np.exp(-sot_a * frac))
            p = 0.30 * p_raw + 0.70 * BTS_HALF_ANCHOR
            return (qprice.clip(p),
                    f"both>=1 SOT {half} base@{BTS_HALF_ANCHOR:.2f} raw={p_raw:.2f}")
        p_raw = (1 - qprice.np.exp(-sot_h)) * (1 - qprice.np.exp(-sot_a))
        p = 0.75 * p_raw + 0.25 * 0.80     # overdispersion guard (full match)
        return (qprice.clip(p), f"both>=1 SOT {sot_h:.2f}/{sot_a:.2f} raw={p_raw:.2f}")

    # --- team SOT count: 'Will X have N or more shots on target [in 2H]' ---
    m = re.search(r"Will (.+?) have (\d+) or more shots on target"
                  r"( in the second half| in the first half)?", t)
    if m:
        team = resolve_team(m.group(1), home, away)
        if not team:
            return None
        n = int(m.group(2))
        opp_lam = lam_a if team == home else lam_h
        rate = _r(rates, team, "sot", 4.25) * _dom(
            lam_h if team == home else lam_a, opp_lam)
        half = _half_word(t)
        if half:
            s = qprice.H1_SHARE["sot"]
            rate *= s if half == "h1" else (1 - s)
        return (qprice.clip(qprice.prob_n_or_more(rate, n)),
                f"team SOT counted lam={rate:.2f}")

    # --- SOT race [usually 2nd half]: counted SOT rates -> Skellam ---
    m = re.search(r"Will (.+?) have more shots on target than (.+?)"
                  r"( in the second half| in the first half)?\??$", t)
    if m:
        a = resolve_team(m.group(1), home, away)
        b = resolve_team(m.group(2), home, away)
        if a and b:
            # raw counted rates for the race — dominance scaling over-punishes
            # underdogs that still shoot (validated worse OOS than no scaling).
            ra = _r(rates, a, "sot", 4.25)
            rb = _r(rates, b, "sot", 4.25)
            return (qprice.clip(qprice.prob_a_more_than_b(ra, rb)),
                    f"SOT race Skellam {ra:.2f} vs {rb:.2f}")

    # --- total cards [in 2H]: counted card rates summed, half-scaled ---
    m = re.search(r"Will there be (\d+) or more total cards"
                  r"( shown)?( in the second half| in the first half)?", t)
    if m:
        n = int(m.group(1))
        lam_cards = _r(rates, home, "cards", 1.5) + _r(rates, away, "cards", 1.5)
        half = _half_word(t)
        if half:
            s = qprice.H1_SHARE["cards"]
            lam_cards *= s if half == "h1" else (1 - s)
        return (qprice.clip(qprice.prob_n_or_more(lam_cards, n)),
                f"total cards counted lam={lam_cards:.2f}")

    # --- penalty OR red card: counted PK + red rates, union ---
    if re.search(r"penalty kick be awarded OR a red card", t, re.I):
        pen = (_r(rates, home, "pk_won", 0.18) + _r(rates, away, "pk_won", 0.18))
        red = (_r(rates, home, "red", 0.06) + _r(rates, away, "red", 0.06))
        return (qprice.clip(qprice.prob_penalty_or_red(pen, red)),
                f"pen|red counted pen={pen:.2f} red={red:.2f}")

    # --- penalty awarded in match: counted PK rate ---
    if re.search(r"Will a penalty kick be awarded( in the match)?\?", t):
        pen = (_r(rates, home, "pk_won", 0.18) + _r(rates, away, "pk_won", 0.18))
        return (qprice.clip(1 - qprice.np.exp(-pen)), f"penalty counted lam={pen:.2f}")

    # --- first-goal combo: 'X score the first goal of the game AND Y score in
    # the 2nd half' = P(X gets the opening goal) * P(Y scores after the break).
    # Both from market goal-rates; independence approx (early vs late events). ---
    m = re.search(r"Will (.+?) score the first goal of the game and (.+?) "
                  r"score in the second half", t)
    if m:
        x = resolve_team(m.group(1), home, away)
        y = resolve_team(m.group(2), home, away)
        if x and y:
            lx = lam_h if x == home else lam_a
            ly = lam_h if y == home else lam_a
            tot = lam_h + lam_a
            p_x_first = (lx / tot) * (1 - qprice.np.exp(-tot)) if tot > 0 else 0.0
            p_y_h2 = 1 - qprice.np.exp(-ly * (1 - qprice.H1_SHARE["goals"]))
            return (qprice.clip(p_x_first * p_y_h2),
                    f"firstgoal&2H p(first)={p_x_first:.2f} p(2H)={p_y_h2:.2f}")
        return None

    # --- both teams score AND total>=N: bivariate Poisson on market goals ---
    m = re.search(r"Will both teams score AND the match have (\d+) or more", t)
    if m:
        n = int(m.group(1))
        return (qprice.clip(qprice.prob_btts_and_total(lam_h, lam_a, n)),
                f"btts&O{n-0.5} bivar-Poisson {lam_h:.2f}/{lam_a:.2f}")

    return None

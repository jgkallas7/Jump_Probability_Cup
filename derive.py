"""Derived/alpha pricing engine: NO_MARKET questions -> market-anchored
Poisson/Skellam derivations, base rates only where no related market exists.

Tiers (per derivation, recorded in deviation_reason):
  [derived]  built from devigged market quantities on the tape
  [anchored] market proxy with a basis adjustment (e.g. cards spread -> fouls)
  [base]     UNVERIFIED base-rate lambda (training-knowledge values, flagged;
             replaced by FBref current-squad build per ROADMAP #1)

Usage:
  python derive.py [--hours 30] [--submit] [--dry-run]
With --submit: batch-submits never-submitted questions; PATCHes already-
submitted ones whose derived value moved >= ALPHA_REVISE_PTS.
"""

from __future__ import annotations

import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import db
from config import BOOK_WEIGHTS, DEFAULT_BOOK_WEIGHT, THIN_MARKET_EXTRA_WEIGHTS
from devig import shrink_extremes
from forecast import MAX_SNAP_AGE_MIN, _player_tokens, consensus, resolve_team

ALPHA_REVISE_PTS = 3   # alpha derivations churn more than book consensus

# Counted-rate quant pricer (qmodel + FBref team_rates). OFF by default: the
# backtest that favours it (evaluate_qmodel.py, +258 vs +78) still has rate
# look-ahead, and the shrink-lesson above mandates a CLEAN out-of-sample check
# before any change reaches live submission. Flip WC_QMODEL=1 once a forward
# match day confirms the edge. Disabled buckets stay on the handlers below.
QMODEL_ON = os.environ.get("WC_QMODEL", "") == "1"
QMODEL_DISABLED_PREFIXES = ("team SOT",)   # n=1, regressed OOS — keep on derive
_QM_RATES = None
_QM_LAM: dict[str, tuple] = {}

# Kalshi WC live crowd mids (kalshi_wc). OFF by default — external dependency,
# and like qmodel it wants a forward validation before live. When on, it takes
# PRIORITY for the buckets it covers (corners/totals/score-or-assist): a
# real-money prediction-market mid is the closest live proxy to the SP field.
KALSHI_ON = os.environ.get("WC_KALSHI", "") == "1"
_KALSHI_CLIENT = None
_KALSHI_BOOK: dict[str, dict] = {}

# Kalshi score-or-assist mids come from THIN player-prop orderbooks and mid()
# had no spread guard — a wide book (bid 0.10 / ask 0.80) pins the mid near 0.5
# regardless of truth. Realized (2026-07-01): kalshi-priced SOA edge -40 over
# n=5 settled rows, avg sent 0.49 on questions resolving YES 20%; the book-union
# h_score_or_assist tracked the field (-5 over n=6). WC_KALSHI_NO_SOA=1 skips
# Kalshi for the score-or-assist family so it falls through to the book union.
# REFINEMENT (2026-07-02, user's point): Kalshi SOA is a TWO-SIDED exchange
# book — where it's tight (stars ~1-4c) its mid is a real crowd probability,
# arguably the best fair for a family the sportsbooks only quote one-sided.
# WC_KALSHI_SOA_MAXSPREAD (e.g. 0.08), when set, re-admits Kalshi SOA books
# tighter than the threshold (kalshi_wc drops the wide ones); the blanket ban
# then covers only the unset case. Unset by default — no historical spreads
# were stored, so validation is FORWARD-only (watch the score_or_assist family
# row after any flip).
KALSHI_NO_SOA = os.environ.get("WC_KALSHI_NO_SOA", "") == "1"
KALSHI_SOA_MAXSPREAD = float(os.environ.get("WC_KALSHI_SOA_MAXSPREAD", "0") or 0) or None

# SOT-threshold base anchor (OFF by default, WC_SOT_THRESH_ANCHOR). Our raw
# counted-rate Poisson survival systematically UNDER-prices "N-or-more shots on
# target" questions: contest SOT lines are written low, so they resolve YES
# ~65-70%, but we price them ~45-55%. Per-template parse_locked (2026-06-22):
# the loss is DIRECTIONAL (level bias), not overconfidence — shrink-to-0.5 barely
# helps (+4), but blending toward a 0.65 base rate recovers +38 over n=21 on the
# still-mispriced total_sot+team_sot templates (both>=1 was already anchored at
# qmodel.py; the SOT race is level-invariant and EXCLUDED). beta=0.5 keeps half
# the per-match signal so "8+" still prices below "4+". Tunable via WC_SOT_ANCHOR
# / WC_SOT_BETA. Forward-validate the gate (review_report) before go-live.
SOT_THRESH_ANCHOR_ON = os.environ.get("WC_SOT_THRESH_ANCHOR", "") == "1"
SOT_ANCHOR = float(os.environ.get("WC_SOT_ANCHOR", "0.65"))
SOT_BETA = float(os.environ.get("WC_SOT_BETA", "0.5"))

# The pooled 0.65 anchor averages two families with OPPOSITE biases (settled
# outcomes, 2026-07-01): TOTAL-SOT thresholds resolve YES 84% (n=19 — the
# contest writes those lines low) while TEAM-SOT thresholds resolve 39% (n=33).
# Blending team-SOT UP toward 0.65 is actively wrong; the 06-22 "+38 pooled"
# validation hid the split. These knobs split the anchor by family; unset
# (default) each inherits SOT_ANCHOR, i.e. exactly today's behaviour. The
# review_report gate sweeps candidates; a human sets them in flags.sh.
SOT_TOTAL_ANCHOR = float(os.environ.get("WC_SOT_TOTAL_ANCHOR", "0") or 0) or None
SOT_TEAM_ANCHOR = float(os.environ.get("WC_SOT_TEAM_ANCHOR", "0") or 0) or None

# --- 2H SOT-race de-compression (WC_SOT_RACE_DECOMP) -------------------------
# The team-vs-team "more SOT than" race was long believed a contrarian edge: we
# compress toward 0.5 vs a supposedly over-dispersed field. The matchday-3 locked-
# email harvest (2026-06-25) FALSIFIED that. Over n=32 derive-priced races the
# field is well-calibrated (race favorites win ~70%, the field says ~68%) while our
# double-damped price (0.5x share-damp in sot_share, then x0.6 prob-damp in
# h_sot_race_h2) sits at ~53% on favorites and ~38% on underdogs — squashed toward
# 0.5 on BOTH sides. Realized: OURS -94.7 (negative!) vs field-clone +76.8.
# De-compression p' = 0.5 + gamma*(p-0.5), gamma>=1, pushes the final race price
# back toward the extremes where the field AND reality live. Pricer-agnostic (wraps
# the derive dispatch, so it covers both h_sot_race_h2 and the qmodel raw-Skellam
# branch). Honest expanding-window recovers +98 banked pts over as-sent; the gain is
# monotonic across gamma (not a knife-edge), but the OOS optimum sits at the grid
# edge (gamma>=3) — a tail-risk flag, so default to a CONSERVATIVE gamma (mirror the
# WC_SOT_BETA=0.5-over-1.0 caution). gamma=1.0 is the no-op default (flag OFF).
# Scope is is_sot_race ONLY. Forward-validate the review_report gate before raising.
RACE_DECOMP = float(os.environ.get("WC_SOT_RACE_DECOMP", "1.0"))

# --- 2H SOT-race goal-share routing (WC_SOT_RACE_GS) ------------------------
# The race has TWO pricers: derive's h_sot_race_h2 (market goal-share -> Skellam,
# DIFFERENTIATES the favorite) and qmodel's raw-Skellam on counted SOT rates
# (which cluster ~4.25 across teams -> share~0.5 -> price ~0.44 for EVERYONE).
# qmodel is tried first, so once teams log a few games the undifferentiated raw
# price shadows the better goal-share one — and that's the live forward regime.
# Re-pricing the settled races through the goal-share path (now=kickoff, no look-
# ahead; reproduction err 0.057 on derive-priced races): on the qmodel-raw races
# the goal-share price tracks the field on BOTH sides (fav 0.43->0.55, dog
# 0.47->0.29), cutting realized edge -79 -> -38; with WC_SOT_RACE_DECOMP=1.5 on top
# -79 -> -22 (R3 current regime -88 -> -20). Goal-share gets the SIDE right, then
# de-comp amplifies the now-correct signal — that's the validated SEQUENCE (de-comp
# alone was premature). This flag skips the qmodel race branch so the race falls
# through to h_sot_race_h2. Re-pricing backtest, not a sent-price rescale gate, so
# FORWARD-validate (watch the SOT-race bucket edge in review_report). Default OFF.
RACE_GS_ON = os.environ.get("WC_SOT_RACE_GS", "") == "1"

# --- Placeholder-coverage router (WC_PH_COVERAGE) ----------------------------
# The late-slate question wording (knockout-era "in regulation (90 minutes +
# stoppage time)", "hydration break", brace/"any player score more than 1 goal")
# defeats the classifier, so a growing share of NO_MARKET questions reach neither
# qmodel nor a handler and fall to the flat 0.45 placeholder (rate spiked from
# ~15% to 37% on 2026-06-28). Placeholder Qs realize ~2x the negative edge/Q of
# priced ones. The cheap, high-confidence wins reuse pricers we ALREADY own and
# only failed on wording: "end in a tie" == the h2h draw (our standing not-
# renormalized draw edge); "ahead at halftime" == the 1st-half h2h_3_way_h1
# winner; "any player score 2+ goals" == a brace, priced off team goal-lambdas.
# These run as a FALLBACK after qmodel + the standard HANDLERS (never override a
# real pricer) and only when this flag is on. Default OFF — forward-validate the
# re-priced-vs-placeholder edge on settled questions (parse_locked) before flip.
PH_COVERAGE_ON = os.environ.get("WC_PH_COVERAGE", "") == "1"

# corner_share() supremacy-fallback slope (see comment at the fallback).
CORNER_SUP_SLOPE = float(os.environ.get("WC_CORNER_SUP_SLOPE", "") or 0.20)

# Referee cards multiplier (WC_REF_CARDS, default OFF — HANDOFF #10). Scales
# card-level lambdas by the assigned ref's historical cards-per-match (shrunk,
# clamped; ref_rates.py). Applies ONLY where no book cards line prices the ref
# already: the BASE fallback in cards_lambda and qmodel's counted total-cards.
# Races cancel the ref; derived-tier is market-priced — both untouched.
REF_CARDS_ON = os.environ.get("WC_REF_CARDS", "") == "1"
_REF_MULT: dict[str, tuple[float, str | None]] = {}


def _ref_cards_mult(conn, m):
    """Per-match (multiplier, ref) — cached; (1.0, None) on any failure."""
    mid = m["match_id"]
    if mid not in _REF_MULT:
        try:
            import ref_rates
            row = conn.execute("SELECT kickoff_utc FROM matches WHERE match_id=?",
                               (mid,)).fetchone()
            _REF_MULT[mid] = ref_rates.cards_multiplier(
                m["home"], m["away"], row["kickoff_utc"] if row else "")
        except Exception:
            _REF_MULT[mid] = (1.0, None)
    return _REF_MULT[mid]


def is_sot_threshold(text: str) -> bool:
    """A 'shots on target' threshold question THIS FLAG SHOULD ANCHOR. Single
    source of truth for the flag's scope — both the live applier (_apply_sot_anchor)
    and the review_report gate key off this, so live and validated scope can't
    drift. Excludes two SOT families it must NOT touch:
      - the level-invariant team-vs-team race ('...more SOT than...') — a separate
        confirmed edge; anchoring it would destroy that edge.
      - 'both teams >=1 SOT' — already hand-anchored to ~0.68 in qmodel.py;
        re-anchoring would double-anchor a deliberately calibrated price."""
    t = text.lower()
    if "shot" not in t or "on target" not in t:
        return False
    if "both teams" in t:                       # already anchored in qmodel.py
        return False
    return not ("more" in t and "than" in t)    # exclude '...more SOT than...' race


def _sot_anchor_value(text):
    """Family-specific anchor: total-SOT vs team-SOT settle at opposite base
    rates (84% vs 39%), so each family gets its own knob, falling back to the
    pooled SOT_ANCHOR when unset (the default = current behaviour)."""
    if "total shots on target" in text.lower():
        return SOT_TOTAL_ANCHOR or SOT_ANCHOR
    return SOT_TEAM_ANCHOR or SOT_ANCHOR


def _apply_sot_anchor(text, res):
    """Blend a SOT-threshold price toward its family's anchor base rate. No-op
    when the flag is off, the result is empty, or it isn't a SOT threshold."""
    if res is None or not SOT_THRESH_ANCHOR_ON or not is_sot_threshold(text):
        return res
    prob, tier, reason = res
    anchor = _sot_anchor_value(text)
    blended = (1 - SOT_BETA) * prob + SOT_BETA * anchor
    return blended, tier, f"{reason} | sotanchor@{anchor:.2f} {prob:.2f}->{blended:.2f}"


def is_sot_race(text: str) -> bool:
    """The 2nd-half team-vs-team SOT race ('Will X have more shots on target than
    Y in the second half?'). Single source of truth for WC_SOT_RACE_GS routing AND
    WC_SOT_RACE_DECOMP scope — _route_race_to_goal_share, _apply_race_decomp and the
    review_report gate all key off this, so live and validated scope can't drift.

    Matches the h_sot_race_h2 handler's scope: 'more shots on target than' together
    with 'in the second half', in EITHER order — the trailing form ('Will X have more
    SOT than Y in the second half?', 45/54 settled) and the leading form ('In the
    second half, will X have more SOT than Y?', ~9). Both now dispatch to h_sot_race_h2
    (the leading regex was added 2026-06-29, closing the old issue #4 where the leading
    form fell to the placeholder), so is_sot_race ⟹ 'h_sot_race_h2 can price this' still
    holds — WC_SOT_RACE_GS routing can never strand the question at the placeholder.
    Requires 'than', so it stays mutually exclusive with is_sot_threshold."""
    t = text.lower()
    return "more shots on target than" in t and "in the second half" in t


def _route_race_to_goal_share(text):
    """True when WC_SOT_RACE_GS is on AND this is the 2H SOT race — derive_question
    then skips qmodel's undifferentiated raw-Skellam so the race falls through to
    h_sot_race_h2 (market goal-share, which identifies the favorite)."""
    return RACE_GS_ON and is_sot_race(text)


def _apply_race_decomp(text, res):
    """De-compress a 2H SOT-race price away from 0.5 by RACE_DECOMP. No-op when
    gamma == 1.0 (the default / flag OFF), the result is empty, or the question
    isn't a race. Clipped to [0.01, 0.99] to match shrink_extremes."""
    if res is None or RACE_DECOMP == 1.0 or not is_sot_race(text):
        return res
    prob, tier, reason = res
    dec = min(0.99, max(0.01, 0.5 + RACE_DECOMP * (prob - 0.5)))
    return dec, tier, f"{reason} | racedecomp g={RACE_DECOMP:g} {prob:.2f}->{dec:.2f}"


def _kalshi_price(conn, m, text):
    """Try a live Kalshi mid for this question. Fully guarded — any failure
    (auth, network, no market) returns None and the pipeline proceeds."""
    global _KALSHI_CLIENT
    # thin-book mids mislead on SOA; with WC_KALSHI_SOA_MAXSPREAD set, tight
    # books pass through instead (kalshi_wc drops the wide ones itself)
    if KALSHI_NO_SOA and KALSHI_SOA_MAXSPREAD is None \
            and "score or assist" in text.lower():
        return None
    try:
        import kalshi_wc
        if _KALSHI_CLIENT is None:
            _KALSHI_CLIENT = kalshi_wc.KalshiRO()
        mid = m["match_id"]
        if mid not in _KALSHI_BOOK:
            row = conn.execute("SELECT kickoff_utc FROM matches WHERE match_id=?",
                               (mid,)).fetchone()
            date = (row["kickoff_utc"] or "")[:10] if row else ""
            _KALSHI_BOOK[mid] = kalshi_wc.match_book(
                _KALSHI_CLIENT, m["home"], m["away"], date) if date else {}
        res = kalshi_wc.price_question(text, _KALSHI_BOOK[mid])
        if res is None:
            return None
        prob, reason = res
        return prob, "kalshi", reason
    except Exception:
        return None


def _qmodel_price(conn, m, text, now):
    """Try the counted-rate pricer; return (prob, tier, reason) or None.
    Caches the rate table once and the goal lambdas per match."""
    global _QM_RATES
    import qmodel
    import team_rates
    if _QM_RATES is None:
        try:
            _QM_RATES = team_rates.build_rates()
        except Exception:
            _QM_RATES = {}
    mid = m["match_id"]
    if mid not in _QM_LAM:
        _QM_LAM[mid] = match_lambdas(conn, m, now)
    rates = _QM_RATES
    if REF_CARDS_ON:
        mult, _ = _ref_cards_mult(conn, m)
        if mult != 1.0:
            rates = {**_QM_RATES, "_ref_cards": mult}
    res = qmodel.price_question(text, m["home"], m["away"], rates, _QM_LAM[mid])
    if res is None:
        return None
    prob, reason = res
    if any(reason.startswith(p) for p in QMODEL_DISABLED_PREFIXES):
        return None
    return prob, "qmodel", reason

# LESSON (2026-06-14): a blanket alpha shrink-toward-0.5 (λ=0.5) was tried and
# REVERTED. It looked great IN-SAMPLE — a locked-email backtest over the first 4
# settled matches (parse_locked.py) put the realized-points optimum at λ=0.5.
# But the first OUT-OF-SAMPLE match it went live on (Brazil-Morocco) the shrink
# cost ~26 alpha points: our calls there were good and confident, and pulling
# them toward 50 threw that away. Re-sweeping λ over all 32 settled alpha
# questions then moved the optimum to λ≈0.9–1.0 (i.e. no shrink). The alpha
# signal is NOT uniformly noise — uniform shrinkage discards the good days. Do
# not re-add a blanket shrink; any future shrink must be per-question (keyed to
# a real confidence signal) and validated OUT-OF-SAMPLE before going live.

# ---- base rates: UNVERIFIED (ROADMAP #1 replaces with counted data) ----
BASE = {
    "pen_lambda": 0.36,        # in-game penalties per match (WC18/22 blend)
    "red_lambda": 0.06,        # red cards per match
    "cards_lambda": 3.8,       # total cards per match fallback
    "corners_lambda": 9.5,     # total corners fallback
    "sot_lambda": 8.2,         # total shots on target per match
    "goals_lambda": 2.6,       # reference total-goals lambda
    "offside_base": 1.3,       # team offsides lambda = base + slope*p_win
    "offside_slope": 0.8,
    "h2_goal_share": 0.55,
    "h2_card_share": 0.60,
    "h2_corner_share": 0.56,
    "h2_sot_share": 0.54,
    "h1_corner_share": 0.44,
    "brace_share": 0.42,       # P(a team's multi-goal haul is one player's brace)
    "goal_share_first30": 0.21,  # share of match goals before the ~30' hydration break
    "sot_brace_share": 0.50,   # P(a team's multi-SOT haul concentrates 2+ in one player)
    # --- knockout-wording placeholder-gap handlers (WC_PH_COVERAGE) ---
    # Constants are honest POSTERIORS integrating published timing data + knockout
    # context, NOT fits to the post-hoc email field (there is no field/market visible
    # at submit time for these bookless props, so a "match the field" target is not
    # available — only an honest model number is):
    #  - goal timing: 1st half 43.8% / 2nd 56.2%; 76-90' holds 21.7% of goals (Sapub
    #    2014). Cards are MORE back-loaded than goals and peak in the final interval
    #    (PMC10923682; 2nd bookings avg min 74.4), so the generic 75'+stoppage card
    #    share is ~0.25 (price ~0.73). Discounted to 0.20 (price ~0.67) for knockout
    #    caution + the lone R32 settle landing NO -> a MILD data-side lean over the
    #    crowd, not the full generic bet (which ignored KO context).
    #  - subs scored 13.2% of goals at recent World Cups/Euros (PMC11167463); ~13% PL.
    #  - offside timing is NOT published at interval granularity (needs raw Opta F24
    #    feeds). ~Uniform prior (1/3 before 30') discounted to 0.30 (offsides tick up
    #    with late attacking). Total offside lambda IS counted; only the split is prior.
    "offside_share_first30": 0.30,  # ~uniform prior, mild late-attacking discount
    "card_share_after75": 0.20,  # generic 0.25 discounted for KO caution + R32 NO settle
    "ko_extra_time_prob": 0.25,  # fallback P(regulation level -> extra time) when no h2h draw quote
    "sub_goal_share": 0.13,    # subs scored 13.2% of goals at recent WC/Euros (PMC11167463)
    # --- no-book prop fallbacks (market-anchored where possible; flat where the rate
    #     is environment-level — team history regressed 3x OOS, see CLAUDE.md dead-ends)
    "corner_share_first30": 0.28,  # corners mildly back-loaded: h1 share 0.44 x ~2/3
    "own_goal_rate": 0.07,        # own goal in a match — rare, ~6-8% (environment-level)
    "red_card_match_rate": 0.16,  # >=1 red card in a match (~WC base; environment-level)
    "total_shots_rate": 0.58,     # P(>=20-22 total shots) — matches avg ~25 (flat base)
    "stoppage_goal_rate": 0.13,   # goal in one half's stoppage window — short, low
    "goal_share_after75": 0.23,   # share of match goals after the ~75' break (back-loaded)
    "sub_before_half_rate": 0.22,  # >=1 sub before halftime (injury-driven; field 22-23 twice)
}


# ---- math primitives ----

def pois_pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def p_geq(lam: float, k: int) -> float:
    return 1.0 - sum(pois_pmf(lam, i) for i in range(k))


def skellam_gt(la: float, lb: float, n: int = 40) -> float:
    """P(A > B) for independent Poissons."""
    pa = [pois_pmf(la, k) for k in range(n)]
    pb = [pois_pmf(lb, k) for k in range(n)]
    return sum(pa[i] * sum(pb[:i]) for i in range(1, n))


def skellam_geq(la: float, lb: float, m: int, n: int = 40) -> float:
    """P(A - B >= m) for independent Poissons; m may be negative."""
    pa = [pois_pmf(la, k) for k in range(n)]
    pb = [pois_pmf(lb, k) for k in range(n)]
    return sum(pa[i] * pb[j] for i in range(n) for j in range(n) if i - j >= m)


def lam_from_over(p_over: float, line: float) -> float:
    """Solve lambda such that P(N > line) = p_over (line is x.5)."""
    lo, hi = 0.05, 25.0
    for _ in range(60):
        lam = (lo + hi) / 2
        if p_geq(lam, int(line) + 1) > p_over:
            hi = lam
        else:
            lo = lam
    return (lo + hi) / 2


# ---- market readers (consensus over whitelisted books on the tape) ----

def mprob(conn, match_id, market, outcome, point, now):
    p, n, _ = consensus(conn, match_id, market, outcome, point, now)
    return p


def match_lambdas(conn, m, now):
    """(lam_home, lam_away) from devigged totals + h2h on the tape."""
    p_u25 = mprob(conn, m["match_id"], "totals", "Under", 2.5, now)
    lam_tot = lam_from_over(1 - p_u25, 2.5) if p_u25 is not None \
        else BASE["goals_lambda"]
    p_home = mprob(conn, m["match_id"], "h2h", m["home"], None, now)
    if p_home is None:
        return lam_tot / 2, lam_tot / 2, lam_tot
    lo, hi = 0.15, 0.85
    for _ in range(40):
        s = (lo + hi) / 2
        if skellam_gt(lam_tot * s, lam_tot * (1 - s)) > p_home:
            hi = s
        else:
            lo = s
    return lam_tot * s, lam_tot * (1 - s), lam_tot


def cards_lambda(conn, m, now):
    for line in (3.5, 4.5, 2.5):
        p_over = mprob(conn, m["match_id"], "alternate_totals_cards", "Over",
                       line, now)
        if p_over is not None:
            return lam_from_over(p_over, line), "derived"   # book prices the ref
    lam = BASE["cards_lambda"]
    if REF_CARDS_ON:
        lam *= _ref_cards_mult(conn, m)[0]
    return lam, "base"


def corners_lambda(conn, m, now):
    for line in (9.5, 8.5, 10.5):
        p_over = mprob(conn, m["match_id"], "alternate_totals_corners", "Over",
                       line, now)
        if p_over is not None:
            return lam_from_over(p_over, line), "derived"
    return BASE["corners_lambda"], "base"


def corner_share(conn, m, team, now):
    """Team's corner share from the corner-spread ladder: devig every quoted
    half-line, invert each through the skellam to an implied share, take the
    median. Books ladder lines around the expected margin, so no single point
    (e.g. -0.5) is reliably quoted — game one bug: Pinnacle quoted -1.5..-3.5,
    we read only -0.5, fell back, submitted 48 vs field 65 on a YES.
    Supremacy fallback only when no spread is quoted at all (style caveat —
    corners track style, not strength)."""
    lam, _ = corners_lambda(conn, m, now)
    shares = []
    for pt in (-4.5, -3.5, -2.5, -1.5, -0.5, 0.5, 1.5, 2.5, 3.5, 4.5):
        p_cover = mprob(conn, m["match_id"], "alternate_spreads_corners",
                        team, pt, now)
        if p_cover is None:
            continue
        need = math.ceil(-pt)  # team covers pt iff corner margin >= need
        lo, hi = 0.25, 0.75
        for _ in range(40):
            s = (lo + hi) / 2
            if skellam_geq(lam * s, lam * (1 - s), need) > p_cover:
                hi = s
            else:
                lo = s
        shares.append(s)
    if shares:
        shares.sort()
        return shares[len(shares) // 2], "derived"
    p_win = mprob(conn, m["match_id"], "h2h", team, None, now) or 0.5
    # Fallback slope: at 0.20 the tilt caps at ±0.10 while ladder-implied shares
    # routinely run ±0.17 from parity — the same right-sign-too-weak defect class
    # as the old h_fouls_race (the two settled races priced here lost -32.5
    # combined, both under-tilted). WC_CORNER_SUP_SLOPE strengthens it; default
    # 0.20 is bit-identical to the old constant. Gate in review_report (thin n).
    return 0.5 + CORNER_SUP_SLOPE * (p_win - 0.5), "anchored-supremacy"


def goal_share(conn, m, team, now):
    lh, la, lt = match_lambdas(conn, m, now)
    return (lh if team == m["home"] else la) / lt


# ---- question handlers: (regex, fn(match_row, regex_match, conn, now)) ----
# Each returns (prob, tier, reason) or None.

def h_offside(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    k = int(g.group(2))
    p_win = mprob(conn, m["match_id"], "h2h", team, None, now) or 0.4
    lam = BASE["offside_base"] + BASE["offside_slope"] * p_win
    return p_geq(lam, k), "base", f"offside lam={lam:.2f} (UNVERIFIED base)"


def h_team_score_and_total(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    k = int(g.group(2))
    lh, la, _ = match_lambdas(conn, m, now)
    lt_team = lh if team == m["home"] else la
    lt_other = la if team == m["home"] else lh
    # P(team >= 1 AND team+other >= k), independent poisson
    p = sum(pois_pmf(lt_team, a) * sum(pois_pmf(lt_other, b)
            for b in range(0, 25) if a + b >= k)
            for a in range(1, 25))
    return p, "derived", f"joint poisson team_lam={lt_team:.2f}"


def h_total_sot(m, g, conn, now):
    k, half = int(g.group(1)), bool(g.group(2))
    _, _, lt = match_lambdas(conn, m, now)
    lam = BASE["sot_lambda"] * (lt / BASE["goals_lambda"])
    if half:
        lam *= BASE["h2_sot_share"]
    p = 0.5 + 0.6 * (p_geq(lam, k) - 0.5)  # damped: weakest family (review)
    return p, "anchored", f"sot lam={lam:.2f} damped0.6"


def _team_sot_price(m, team, k, half, conn, now):
    """Single team-SOT threshold pricer, shared by BOTH wordings ('N or more'
    and 'at least N'). Before 2026-07-01 the two wordings hit two DIVERGENT
    formulas (this damped one vs a raw-goal-share one in the coverage handler
    — underdog 7+ priced 0.232 vs 0.028 by phrasing). Consolidated on the
    incumbent: the raw-share variant scored +15/n=29 better anchored, but
    that's same-sample noise, and consistency is the actual defect.
    half: None (full match), 'h1', or 'h2' — the old bool applied the H2 share
    to first-half questions (latent share bug, fixed 2026-07-02)."""
    _, _, lt = match_lambdas(conn, m, now)
    lam = BASE["sot_lambda"] * (lt / BASE["goals_lambda"]) \
        * sot_share(conn, m, team, now)
    if half == "h2":
        lam *= BASE["h2_sot_share"]
    elif half == "h1":
        lam *= 1 - BASE["h2_sot_share"]
    p = 0.5 + 0.6 * (p_geq(lam, k) - 0.5)  # damped: weakest family (review)
    return p, "anchored", f"team sot lam={lam:.2f} damped0.6"


def h_team_sot(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None  # player SOT questions are book-mapped, not derived
    half_txt = (g.group(3) or "").lower()
    half = "h1" if "first" in half_txt else "h2" if "second" in half_txt else None
    return _team_sot_price(m, team, int(g.group(2)), half, conn, now)


def h_pen_or_red(m, g, conn, now):
    p = 1 - math.exp(-BASE["pen_lambda"]) * math.exp(-BASE["red_lambda"])
    return p, "base", "pen+red union (UNVERIFIED base lambdas)"


def h_pen(m, g, conn, now):
    return (1 - math.exp(-BASE["pen_lambda"]), "base",
            "pen lambda 0.36 (UNVERIFIED base)")


def h_total_cards(m, g, conn, now):
    k, half = int(g.group(1)), bool(g.group(2))
    lam, tier = cards_lambda(conn, m, now)
    if half:
        lam *= BASE["h2_card_share"]
    return p_geq(lam, k), tier, f"cards lam={lam:.2f}"


def h_team_cards_h2(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    k = int(g.group(2))
    lam, tier = cards_lambda(conn, m, now)
    p_more = mprob(conn, m["match_id"], "alternate_spreads_cards", team,
                   -0.5, now)
    share = 0.5 if p_more is None else 0.5 + 0.5 * (p_more - 0.45) / 0.55 * 0.3
    return (p_geq(lam * share * BASE["h2_card_share"], k), tier,
            f"team cards lam={lam*share*BASE['h2_card_share']:.2f}")


def h_total_corners(m, g, conn, now):
    k, half = int(g.group(1)), bool(g.group(2))
    lam, tier = corners_lambda(conn, m, now)
    if half:
        lam *= BASE["h2_corner_share"]
    return p_geq(lam, k), tier, f"corners lam={lam:.2f}"


def h_team_corners(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    k = int(g.group(2))
    lam, _ = corners_lambda(conn, m, now)
    share, tier = corner_share(conn, m, team, now)
    return p_geq(lam * share, k), tier, f"team corners lam={lam*share:.2f}"


def h_corners_race(m, g, conn, now):
    half = (g.group(1) or "").lower()
    team = resolve_team(g.group(2), m["home"], m["away"])
    other = resolve_team(g.group(3), m["home"], m["away"])
    if not team or not other:
        return None
    lam, _ = corners_lambda(conn, m, now)
    share, tier = corner_share(conn, m, team, now)
    if "second" in half:
        lam *= BASE["h2_corner_share"]
    elif "halftime" in half or "first" in half:
        lam *= BASE["h1_corner_share"]
    return (skellam_gt(lam * share, lam * (1 - share)), tier,
            f"corner race share={share:.3f} lam={lam:.2f}")


def h_team_scores_half(m, g, conn, now):
    """Fallback when no weighted book quotes alternate_team_totals_h2."""
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    half_share = BASE["h2_goal_share"] if g.group(2).lower() == "second" \
        else 1 - BASE["h2_goal_share"]
    lh, la, _ = match_lambdas(conn, m, now)
    lam = (lh if team == m["home"] else la) * half_share
    return 1 - math.exp(-lam), "derived", f"team {g.group(2)}H lam={lam:.2f}"


def h_fouls_race(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    p_cards = mprob(conn, m["match_id"], "alternate_spreads_cards", team,
                    -0.5, now)
    if p_cards is not None:
        return (0.5 + 0.6 * (p_cards - 0.5), "anchored",
                f"cards spread {p_cards:.3f} shaded for fouls basis")
    p_win = mprob(conn, m["match_id"], "h2h", team, None, now) or 0.5
    return 0.5 - 0.08 * (p_win - 0.5) / 0.5, "base", "underdogs foul more (weak)"


def sot_share(conn, m, team, now):
    """Shot volume regresses toward even vs goal share (finishing asymmetry
    is not volume asymmetry) — dampen the fitted goal share by 0.5."""
    gs = goal_share(conn, m, team, now)
    return 0.5 + 0.5 * (gs - 0.5)


def h_sot_race_h2(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    _, _, lt = match_lambdas(conn, m, now)
    share = sot_share(conn, m, team, now)
    lam = BASE["sot_lambda"] * (lt / BASE["goals_lambda"]) * BASE["h2_sot_share"]
    p = 0.5 + 0.6 * (skellam_gt(lam * share, lam * (1 - share)) - 0.5)
    return p, "anchored", f"sot race share={share:.3f} damped0.6"


def h_h2_gt_h1(m, g, conn, now):
    _, _, lt = match_lambdas(conn, m, now)
    l1 = lt * (1 - BASE["h2_goal_share"])
    l2 = lt * BASE["h2_goal_share"]
    return (skellam_gt(l2, l1), "derived",
            f"h2 vs h1 goals lam {l2:.2f}/{l1:.2f}")


def h_first_goal_h2(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    _, _, lt = match_lambdas(conn, m, now)
    share = goal_share(conn, m, team, now)
    p_any = 1 - math.exp(-lt * BASE["h2_goal_share"])
    return share * p_any, "derived", f"first 2H goal share={share:.3f}"


# One-sided player markets carry full vig (no opposite side to devig against),
# flagged divergence_pts == -1 on the tape; the haircut approximates a one-sided
# devig (anytime-scorer / assist margins run ~7-10%).
PLAYER_ONE_SIDED_HAIRCUT = {"player_goal_scorer_anytime": 0.93, "player_assists": 0.90}


def _player_prob(conn, match_id, market, player, suffix, now, point=None):
    """Weighted consensus that a player hits the `suffix` side ('Yes' for
    anytime-scorer, 'Over' for assists). Matches name TOKENS, since book name
    orders differ ('Heung-Min Son' vs 'Son Heung-min'). Returns prob or None.
    Mirrors forecast.consensus weighting (whitelist + thin-market extras).
    Name tokens via forecast._player_tokens — the KO slate writes 'Kevin De
    Bruyne (Belgium)' with accents while books store ASCII, country-free names;
    the old naive tokenizer kept '(Belgium)' and never matched a book line, so
    every KO score-or-assist fell through to Kalshi/placeholder (cc4a453 fixed
    forecast.py but missed this twin)."""
    tokens = _player_tokens(player)
    if not tokens:
        return None
    clause = " AND ".join(["outcome LIKE ?"] * len(tokens)) + " AND outcome LIKE ?"
    # point filter matters for laddered player markets (SOT quotes 0.5/1.5/2.5;
    # without it GROUP BY book returns an arbitrary line's prob). None = any
    # line (correct for single-line markets like anytime-scorer/assists).
    pt_clause = " AND point = ?" if point is not None else ""
    cutoff = (now - timedelta(minutes=MAX_SNAP_AGE_MIN)).isoformat()
    params = [match_id, market] + [f"%{t}%" for t in tokens] + [f"% {suffix}"] \
        + ([point] if point is not None else []) \
        + [cutoff, now.isoformat()]   # ts<=now: no in-play leak in backtests
    rows = conn.execute(f"""
        SELECT book, fair_prob, divergence_pts, MAX(ts) ts FROM market_snapshots
        WHERE match_id = ? AND market = ? AND {clause}{pt_clause} AND ts >= ? AND ts <= ?
        GROUP BY book""", params).fetchall()
    weights = dict(BOOK_WEIGHTS)
    for b, w in THIN_MARKET_EXTRA_WEIGHTS.items():
        weights.setdefault(b, w)
    hc = PLAYER_ONE_SIDED_HAIRCUT.get(market, 1.0)
    wsum = psum = 0.0
    for r in rows:
        w = weights.get(r["book"], DEFAULT_BOOK_WEIGHT)
        if w <= 0:
            continue
        p = r["fair_prob"] * (hc if r["divergence_pts"] == -1.0 else 1.0)
        wsum += w
        psum += w * p
    return psum / wsum if wsum > 0 else None


def h_score_or_assist(m, g, conn, now):
    """"Will X score or assist?" — union of anytime-scorer and assist markets
    we already snapshot. Independence approx (slightly high under +correlation,
    but far sharper than a base rate). Needs at least one side quoted."""
    player = g.group(1)
    ps = _player_prob(conn, m["match_id"], "player_goal_scorer_anytime", player, "Yes", now)
    pa = _player_prob(conn, m["match_id"], "player_assists", player, "Over", now)
    if ps is None and pa is None:
        return None
    ps, pa = ps or 0.0, pa or 0.0
    p = 1 - (1 - ps) * (1 - pa)
    return p, "derived-mkt", f"score|assist 1-(1-{ps:.2f})(1-{pa:.2f})"


HANDLERS = [
    (r"Will (.+?) score or assist a goal", h_score_or_assist),
    (r"Will (.+?) be caught offside (\d+) or more", h_offside),
    (r"Will (.+?) score AND the match have (\d+) or more total goals",
     h_team_score_and_total),
    (r"Will both teams score AND the match have (\d+) or more", None),  # special
    (r"Will there be (\d+) or more total shots on target( in the second half)?",
     h_total_sot),
    (r"Will (.+?) have (\d+) or more shots on target( in the second half)?",
     h_team_sot),
    (r"penalty kick be awarded OR a red card", h_pen_or_red),
    (r"Will a penalty kick be awarded(?: in the match| in regulation.*)?\?", h_pen),
    (r"Will there be (\d+) or more total cards shown( in the second half)?",
     h_total_cards),
    (r"Will (.+?) receive at least (\d+) cards? in the second half",
     h_team_cards_h2),
    (r"Will there be (\d+) or more total corner kicks( in the second half)?",
     h_total_corners),
    (r"Will (.+?) have (\d+) or more corner kicks", h_team_corners),
    (r"(At halftime|In the second half)?,? ?[Ww]ill (.+?) have more corner "
     r"kicks than (.+?)\?", h_corners_race),
    (r"Will (.+?) score in the (second|first) half\?", h_team_scores_half),
    (r"Will (.+?) commit more fouls than", h_fouls_race),
    (r"Will (.+?) have more shots on target than .+? in the second half",
     h_sot_race_h2),
    # leading form ('In the second half, will X have more SOT than Y?') — same
    # pricer, was issue #4 (missed by both pricers -> placeholder); now covered.
    (r"[Ii]n the second half, will (.+?) have more shots on target than",
     h_sot_race_h2),
    (r"Will the second half have more (?:total )?goals than the first half",
     h_h2_gt_h1),
    (r"Will (.+?) score the first goal of the second half", h_first_goal_h2),
]


def h_btts_and_total(m, k, conn, now):
    lh, la, _ = match_lambdas(conn, m, now)
    p = sum(pois_pmf(lh, a) * sum(pois_pmf(la, b)
            for b in range(1, 25) if a + b >= k)
            for a in range(1, 25))
    return p, "derived", f"btts+O{k-0.5} joint poisson {lh:.2f}/{la:.2f}"


# ---- WC_PH_COVERAGE handlers: reuse pricers we already own for questions the ----
# ---- classifier dropped to the flat placeholder (see PH_COVERAGE_ON above). ----

def h_ends_in_tie(m, g, conn, now):
    """'Will regulation end in a tie?' is the match draw. Price off the h2h draw
    consensus (kept deliberately un-renormalized — our standing draw edge)."""
    p = mprob(conn, m["match_id"], "h2h", "Draw", None, now)
    if p is None:
        return None
    return p, "derived", f"ends-in-tie<-h2h draw {p:.3f}"


def h_ahead_at_halftime(m, g, conn, now):
    """'Will <team> be ahead at halftime?' is the 1st-half match-winner. Price
    off the h2h_3_way_h1 consensus for that team."""
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    p = mprob(conn, m["match_id"], "h2h_3_way_h1", team, None, now)
    if p is None:
        return None
    return p, "derived", f"ahead-at-HT<-h2h_3way_h1 {p:.3f}"


def h_any_player_brace(m, g, conn, now):
    """'Will any player score 2+ goals?' — no book market. P(some player gets a
    brace) from team goal-lambdas: a team's multi-goal haul is one player's brace
    BRACE_SHARE of the time (P=22-31% across share 0.35-0.50; field-matched)."""
    lh, la, _ = match_lambdas(conn, m, now)
    share = BASE["brace_share"]

    def team_brace(lam):                      # P(team scores >=2) * P(it's one player)
        return (1 - math.exp(-lam) * (1 + lam)) * share

    p = 1 - (1 - team_brace(lh)) * (1 - team_brace(la))
    return p, "derived", f"any-brace lam={lh:.2f}/{la:.2f} share={share:.2f}"


def h_any_player_sot_brace(m, g, conn, now):
    """'Will any player record 2+ shots on target?' — the SOT analogue of the goal
    brace, and far more common (a star routinely gets 2+ SOT). The OLD flat 0.50
    placeholder bled here (field ~0.70). Team SOT lambda x a concentration share,
    split by SOT share. UNVERIFIED & 'base' tier: the results feed has no player-SOT,
    so this can't auto-settle — it's field-anchored (lands ~0.70), watch via the UI."""
    _, _, lt = match_lambdas(conn, m, now)
    sot_tot = BASE["sot_lambda"] * (lt / BASE["goals_lambda"])
    sh = sot_share(conn, m, m["home"], now)
    share = BASE["sot_brace_share"]

    def tb(lam):                                # P(team records >=2 SOT) * concentration
        return (1 - math.exp(-lam) * (1 + lam)) * share

    p = 1 - (1 - tb(sot_tot * sh)) * (1 - tb(sot_tot * (1 - sh)))
    return p, "base", f"any-2sot sot={sot_tot:.1f} share={share}"


def h_team_first_goal_match(m, g, conn, now):
    """'Will <team> score the first goal of the match?' — the flat 0.35 placeholder
    ignored favorite strength (Germany at 0.35 vs a 0.72 win prob). For two Poisson
    scoring processes P(team first) = goal_share x P(>=1 goal in the match)."""
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    lh, la, _ = match_lambdas(conn, m, now)
    lam_t, lam_o = (lh, la) if team == m["home"] else (la, lh)
    if lam_t + lam_o <= 0:
        return None
    p = lam_t / (lam_t + lam_o) * (1 - math.exp(-(lh + la)))
    return p, "derived", f"first-goal share={lam_t / (lam_t + lam_o):.2f}"


def h_goal_before_hydration(m, g, conn, now):
    """'Will a goal be scored before the first hydration break?' — the first WC
    cooling break is ~30', so this is P(>=1 goal in the first ~30 min). Poisson on
    the first-30 goal share of the match lambda (goals are back-loaded, so ~0.21
    of them land before 30', not 30/90=0.33)."""
    _, _, lt = match_lambdas(conn, m, now)
    lam30 = lt * BASE["goal_share_first30"]
    return 1 - math.exp(-lam30), "derived", f"goal-by-1st-break lam30={lam30:.2f}"


def h_half_total_goals(m, g, conn, now):
    """'Will the {first,second} half have N or more total goals?' — no whitelisted
    book quotes half totals (Kalshi half-totals are flagged off), so derive from the
    match lambda x the half's goal share. Prefers the half-totals book line if one
    is ever quoted."""
    half, k, direction = g.group(1).lower(), int(g.group(2)), g.group(3).lower()
    _, _, lt = match_lambdas(conn, m, now)
    share = BASE["h2_goal_share"] if half == "second" else 1 - BASE["h2_goal_share"]
    lam = lt * share
    under = direction in ("fewer", "less")
    mkt = "totals_h2" if half == "second" else "totals_h1"
    p = mprob(conn, m["match_id"], mkt, "Under" if under else "Over",
              (k + 0.5) if under else (k - 0.5), now)
    if p is None:                                  # P(<=k) for 'fewer', P(>=k) for 'more'
        p = (1 - p_geq(lam, k + 1)) if under else p_geq(lam, k)
    return p, "derived", f"{half[0]}H goals {'<=' if under else '>='}{k} lam={lam:.2f}"


def h_either_offside_before_hydration(m, g, conn, now):
    """'Will either team be ruled offside before the first hydration break?' — the
    ~30' cooling break, so P(>=1 MATCH offside in the first ~30 min). Offsides are
    ~uniform in time (offside timing is not published at interval granularity, so a
    uniform 1/3-before-30' prior; total offside lambda IS counted). Beats the flat
    placeholder, which ignored that a match almost always has an early offside (field ~0.52)."""
    lam30 = 2 * BASE["offside_base"] * BASE["offside_share_first30"]
    return 1 - math.exp(-lam30), "derived", \
        f"either-offside-by-1st-break lam30={lam30:.2f}"


def h_card_after_2nd_break(m, g, conn, now):
    """'Will a card be shown after the second hydration break, including any extra
    time?' — the ONE knockout question scoped to INCLUDE extra time. The 2nd cooling
    break is ~75', so the window is the card-dense last ~15'+stoppage PLUS extra time
    when the match is level after 90. Cards are back-loaded; ET only materialises with
    P(regulation draw) but then adds 30 high-card minutes. Anchors total cards to the
    cards market when quoted. The 0.20 share (generic 0.25 timing rate discounted for
    knockout caution + the lone R32 NO settle) prices ~0.67 -- a MILD lean over the
    crowd's ~0.61, justified by the back-loading data, not the full generic bet. WATCH."""
    lam_cards, _ = cards_lambda(conn, m, now)
    p_et = mprob(conn, m["match_id"], "h2h", "Draw", None, now)
    if p_et is None:
        p_et = BASE["ko_extra_time_prob"]
    lam_window = lam_cards * BASE["card_share_after75"] \
        + lam_cards * (30.0 / 90.0) * p_et
    return 1 - math.exp(-lam_window), "derived", \
        f"card-after-2nd-break lam={lam_window:.2f} pET={p_et:.2f}"


def h_card_in_first_half(m, g, conn, now):
    """'Will a card be shown in the first half?' — P(>=1 card in H1). Total cards from
    the cards market (or base) x the first-half share (cards are back-loaded, ~40% land
    in H1; h2_card_share=0.60). A first-half card is a high-probability event (~0.72)
    that the flat 0.35 placeholder badly understated -- a clear, market-anchored fix."""
    lam_cards, _ = cards_lambda(conn, m, now)
    lam_h1 = lam_cards * (1 - BASE["h2_card_share"])
    return 1 - math.exp(-lam_h1), "derived", f"card-1H lam={lam_h1:.2f}"


def h_substitute_scores(m, g, conn, now):
    """'Will a substitute score a goal?' — no market. Substitutes supply ~1/7 of
    goals (knockouts skew higher: deeper benches, late game-state subs, an extra-time
    sub window), so scale the match goal-lambda by that share -> P(>=1 sub goal).
    Flat placeholder ~0.45 vs field ~0.30; this also scales up in high-scoring games."""
    _, _, lt = match_lambdas(conn, m, now)
    lam_sub = lt * BASE["sub_goal_share"]
    return 1 - math.exp(-lam_sub), "derived", f"sub-scores lam={lam_sub:.2f}"


def h_score_both_halves(m, g, conn, now):
    """'Will <team> score in both halves of regulation?' — P(team scores in H1) x
    P(team scores in H2), each a Poisson on the team's per-half goal-lambda (the 2nd
    half carries the larger share). Independence is the SIGN-NEUTRAL default: half-to-
    half scoring correlation is theoretically ambiguous (momentum is +, a favourite
    game-managing a lead is -), so no dependence fudge. The structural win is on the
    underdog side (~0.12, where a flat 0.45 placeholder is wildly high); on a favourite
    it lands ~0.29 vs an observed field ~0.42 (n=1) — possibly the field over-weighting
    dominance, possibly an independence under-bias. WATCH this bucket as it settles."""
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    lh, la, _ = match_lambdas(conn, m, now)
    lam_t = lh if team == m["home"] else la
    h2 = BASE["h2_goal_share"]
    p1 = 1 - math.exp(-lam_t * (1 - h2))
    p2 = 1 - math.exp(-lam_t * h2)
    return p1 * p2, "derived", f"both-halves lam={lam_t:.2f}"


def h_team_sot_total(m, g, conn, now):
    """'Will <team> have at least N shots on target?' — the coverage-wording
    twin of h_team_sot. Delegates to the SAME shared pricer (_team_sot_price)
    so the two phrasings can't diverge again (pre-2026-07-01 this used a raw
    goal-share formula that priced an underdog 7+ at 0.028 vs the damped 0.232
    — equivalent questions, different prices by wording). Returns None for a
    non-team subject (player questions are book-mapped, not derived)."""
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    return _team_sot_price(m, team, int(g.group(2)), False, conn, now)


def h_corners_before_hydration(m, g, conn, now):
    """'Will N or more corner kicks be taken before the first hydration break?' —
    the ~30' cooling break. Market-anchored on the corners line; corners are
    mildly back-loaded (h1 share 0.44), so ~0.28 of the match lambda lands before
    30'. New knockout-slate wording (2026-07-01) that fell to the placeholder."""
    k = int(g.group(1))
    lam, tier = corners_lambda(conn, m, now)
    lam30 = lam * BASE["corner_share_first30"]
    return p_geq(lam30, k), tier, f"corners-by-1st-break lam30={lam30:.2f}"


def h_team_corners_atleast_half(m, g, conn, now):
    """'Will X have at least N corner kicks (in the first/second half)?' — the
    'at least' wording missed h_team_corners's 'N or more' regex and fell to the
    0.45 placeholder (settled example: Argentina >=1 corner in H1 — field 0.83,
    we sent 0.45, -27). Same market-anchored pricer: corners lambda x team share
    (spread ladder / supremacy) x half share -> Poisson survival."""
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    k = int(g.group(2))
    half = (g.group(3) or "").lower()
    lam, _ = corners_lambda(conn, m, now)
    share, tier = corner_share(conn, m, team, now)
    lam_t = lam * share
    if "first" in half:
        lam_t *= BASE["h1_corner_share"]
    elif "second" in half:
        lam_t *= BASE["h2_corner_share"]
    return p_geq(lam_t, k), tier, f"team corners(at-least) lam={lam_t:.2f} P(>={k})"


def h_sot_atleast_half(m, g, conn, now):
    """'Will X have at least N shot(s) on target in the <first|second> half?' —
    the (at-least + half-scope + often SINGULAR 'shot') wording missed every
    pricer's regex and fell to the flat placeholder 13 times for -42, the
    entire half_other family leak (Kane/Diaz/Olmo 0.45 vs field ~0.55 YES).
    TEAM subject -> the shared team-SOT pricer, half-scaled. PLAYER subject ->
    the book's full-match player-SOT Over-0.5 line, Poisson-scaled to the half
    (books quote no half-scoped player props). No book line -> None (family
    placeholder, 0.25)."""
    subj, k = g.group(1), int(g.group(2))
    half = "h1" if g.group(3).lower() == "first" else "h2"
    team = resolve_team(subj, m["home"], m["away"])
    if team:
        return _team_sot_price(m, team, k, half, conn, now)
    p_full = _player_prob(conn, m["match_id"], "player_shots_on_target",
                          subj, "Over", now, point=0.5)
    if p_full is None:
        return None
    lam_full = -math.log(max(1e-9, 1.0 - min(p_full, 0.995)))
    share = BASE["h2_sot_share"] if half == "h2" else 1 - BASE["h2_sot_share"]
    p = p_geq(lam_full * share, k)
    return p, "derived-mkt", f"player-SOT {half} from book O0.5={p_full:.2f}"


def h_win_by_margin(m, g, conn, now):
    """'Will X win by N or more goals?' — Skellam margin on the market
    goal-lambdas (no goals-spread market exists on the tape). Was falling to
    the flat 0.35 placeholder every match (2026-07-01: USA priced 35 vs field
    46 on a YES, England 35 vs 51 — the flat value ignores the favourite's λ
    split, which is the entire question)."""
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    n = int(g.group(2))
    lh, la, _ = match_lambdas(conn, m, now)
    lam_t, lam_o = (lh, la) if team == m["home"] else (la, lh)
    return (skellam_geq(lam_t, lam_o, n), "derived",
            f"win-by>={n} skellam {lam_t:.2f}/{lam_o:.2f}")


def h_own_goal(m, g, conn, now):
    """'Will an own goal be scored?' — environment-level base rate (~7%); team form
    doesn't move it. The flat 0.35 catch-all was 5x too high."""
    return BASE["own_goal_rate"], "base", "own-goal base"


def h_both_teams_card(m, g, conn, now):
    """'Will both teams receive at least one card?' — MARKET-anchored on the cards
    line: split total cards evenly, P(a team >=1 card) = 1-exp(-lam/2), both = square.
    Near-certain in most matches; the flat 0.35 was badly low."""
    lam_cards, _ = cards_lambda(conn, m, now)
    pe = 1 - math.exp(-lam_cards / 2.0)
    return pe * pe, "derived", f"both-teams-card lam/2={lam_cards / 2:.2f}"


def h_red_card_match(m, g, conn, now):
    """'Will a red card be shown in the match?' — environment-level base (~16%); the
    flat 0.35 was ~2x too high. (Distinct from the pen-or-red union handler.)"""
    return BASE["red_card_match_rate"], "base", "red-card base"


def h_total_shots_match(m, g, conn, now):
    """'Will there be N or more total shots (on and off target)?' — no total-shots
    book line. Was a flat 0.58 for EVERY N (threshold-blind — the Jul-01 commit
    audit flagged it as the same defect class as the sot_total leak: '15 or more'
    and '30 or more' priced identically). Total shots ~ Normal(mean ~24.5 scaled
    by the market goal environment, sd 6.5); survival at N-0.5. Still environment-
    level (no team history — that's the validated dead-end), but line-aware:
    18+ ~0.82, 20+ ~0.78, 25+ ~0.50, 30+ ~0.18 at a neutral goal line."""
    n = int(g.group(1))
    _, _, lt = match_lambdas(conn, m, now)
    # sqrt scaling + sd 8.0 (2026-07-02): the first live out priced '22+' at 87
    # vs a 57 field (goal line 3.05 -> LINEAR mean 28.8) and lost -43 on a NO.
    # Linear lambda-scaling + sd 6.5 were uncited guesses — shot volume scales
    # SUBLINEARLY with the goal line (defensive games still shoot) and match
    # shot totals disperse ~8. Both changes pull every price toward the
    # field-calibrated 0.58 base (that question re-prices 87 -> ~73).
    mean = 24.5 * math.sqrt(lt / BASE["goals_lambda"])
    sd = 8.0
    p = 0.5 * math.erfc((n - 0.5 - mean) / (sd * math.sqrt(2)))
    return p, "anchored", f"total-shots N({mean:.1f},{sd}) P(>={n})"


def h_stoppage_goal(m, g, conn, now):
    """'Will a goal be scored in (first/second)-half stoppage time?' — a short added-
    time window, so low; the flat 0.35 over-priced it ~2-3x."""
    return BASE["stoppage_goal_rate"], "base", "stoppage-goal base"


def h_goal_after_2nd_break(m, g, conn, now):
    """'Will a goal be scored after the second hydration break?' — the ~75' break, so
    P(>=1 goal in the last ~15'+stoppage). Goals are back-loaded (~23% land after 75'),
    so MARKET-anchored on the match lambda; mirrors h_goal_before_hydration."""
    _, _, lt = match_lambdas(conn, m, now)
    lam = lt * BASE["goal_share_after75"]
    return 1 - math.exp(-lam), "derived", f"goal-after-2nd-break lam={lam:.2f}"


def h_clean_sheet(m, g, conn, now):
    """'Will X keep a clean sheet?' — P(opponent scores 0) = exp(-lam_opp) off
    the market goal lambdas. Argentina-Cabo Verde (2026-07-03) went out as an
    unclassified 0.35 placeholder vs a 0.64 field; exp(-lam_opp) reproduces
    the field from data we already had on the tape."""
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    lam_h, lam_a, _ = match_lambdas(conn, m, now)
    lam_opp = lam_a if team == m["home"] else lam_h
    return math.exp(-lam_opp), "derived", f"clean-sheet exp(-{lam_opp:.2f})"


def h_sub_before_half(m, g, conn, now):
    """'Will a substitution be made before halftime?' — environment-level base;
    the unclassified 0.35 fallback sat ~12 pts above a consistent 0.22-0.23
    field (US-Bosnia settled NO; asked again AUS-EGY 2026-07-03)."""
    return BASE["sub_before_half_rate"], "base", "sub-before-half base"


# Tried as a FALLBACK only (after qmodel + HANDLERS), and only when PH_COVERAGE_ON.
COVERAGE_HANDLERS = [
    (r"(?:regulation|the match|match).*end in a tie|end in a tie", h_ends_in_tie),
    (r"[Ww]ill (.+?) be (?:ahead|leading|in front) at halftime", h_ahead_at_halftime),
    (r"any player score (?:more than (?:1|one)|2 or more) goals?",
     h_any_player_brace),
    (r"any player (?:record|have) \d+ or more shots on target",
     h_any_player_sot_brace),
    (r"[Ww]ill (.+?) score the first goal of the match", h_team_first_goal_match),
    (r"goal.*before the first hydration break", h_goal_before_hydration),
    (r"offside before the first hydration break",
     h_either_offside_before_hydration),
    (r"card.*after the second hydration break", h_card_after_2nd_break),
    (r"card be shown in the first half", h_card_in_first_half),
    (r"[Ww]ill (.+?) have at least (\d+) shots? on target in the "
     r"(first|second) half", h_sot_atleast_half),
    (r"[Ww]ill (.+?) have (?:at least )?(\d+)(?: or more)? shots on target",
     h_team_sot_total),
    (r"(\d+) or more corner kicks be taken before the first hydration break",
     h_corners_before_hydration),
    (r"[Ww]ill (.+?) have at least (\d+) corner kicks?"
     r"(?: in the (first|second) half)?", h_team_corners_atleast_half),
    (r"[Ww]ill (.+?) win by (\d+) or more goals", h_win_by_margin),
    (r"own goal be scored", h_own_goal),
    (r"both teams .*(?:receive|record|be shown|get|have) (?:at least |1 or more )?"
     r"(?:one |1 |a )?card", h_both_teams_card),
    (r"red card be shown", h_red_card_match),
    (r"(\d+) or more total shots", h_total_shots_match),
    (r"goal be scored in (?:first|second)[\s-]half stoppage", h_stoppage_goal),
    (r"goal.*after the second hydration break", h_goal_after_2nd_break),
    (r"[Ww]ill a substitute score a goal", h_substitute_scores),
    (r"[Ww]ill (.+?) keep a clean sheet", h_clean_sheet),
    (r"substitution be made before halftime", h_sub_before_half),
    (r"[Ww]ill (.+?) score in both halves", h_score_both_halves),
    (r"(first|second) half have (\d+) or (more|fewer|less) total goals",
     h_half_total_goals),
]


def derive_question(conn, q, now):
    text = q["text"]
    m = {"match_id": q["match_id"], "home": q["home"], "away": q["away"]}
    res = None
    if KALSHI_ON:
        res = _kalshi_price(conn, m, text)
    if res is None and QMODEL_ON and not _route_race_to_goal_share(text):
        res = _qmodel_price(conn, m, text, now)
    if res is None:
        g = re.search(r"Will both teams score AND the match have (\d+) or more", text)
        if g:
            res = h_btts_and_total(m, int(g.group(1)), conn, now)
    if res is None:
        for pattern, fn in HANDLERS:
            if fn is None:
                continue
            g = re.search(pattern, text)
            if g:
                res = fn(m, g, conn, now)
                break
    # WC_PH_COVERAGE: last-resort router for classifier-dropped questions that map
    # to pricers we already own. Fallback only — never overrides qmodel/a HANDLER.
    if res is None and PH_COVERAGE_ON:
        for pattern, fn in COVERAGE_HANDLERS:
            g = re.search(pattern, text)
            if g:
                res = fn(m, g, conn, now)
                break
    # post-process: SOT-threshold base anchor, then 2H-race de-compression
    # (both flag-gated; no-ops otherwise; scopes are mutually exclusive)
    return _apply_race_decomp(text, _apply_sot_anchor(text, res))


def run(conn, hours: float = 30, submit_mode: bool = False, dry: bool = False):
    from sp_client import SPClient
    from submit import _lobby, _to_int
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=hours)).isoformat()
    # NO_MARKET only: book-mapped questions belong to forecast/consensus —
    # derive must NEVER PATCH its cruder Poisson over a sharp consensus value
    # (review finding: ping-pong with derive winning at close). EXCEPTION:
    # player_shots_on_target also carries the mis-classified TEAM SOT totals
    # ('Will France have 7+ SOT'), which have NO player book line; we include
    # them so h_team_sot_total can price/PATCH them. Safe — every SOT handler
    # gates on resolve_team, so a real player question returns None (no PATCH).
    qs = conn.execute("""
        SELECT q.qid, q.text, q.match_id, m.home, m.away
        FROM questions q JOIN matches m USING(match_id)
        WHERE q.status='open'
          AND (q.market_mapping = 'NO_MARKET'
               OR q.market_mapping = 'player_shots_on_target')
          AND m.kickoff_utc > ? AND m.kickoff_utc <= ?
        ORDER BY m.kickoff_utc""",
        ((now - timedelta(hours=2)).isoformat(), horizon)).fetchall()

    prev = {r["qid"]: r["submitted_prob"] for r in conn.execute("""
        WITH s AS (SELECT qid, submitted_prob, sp_prediction_id,
                   ROW_NUMBER() OVER (PARTITION BY qid ORDER BY submitted_at DESC) rn
                   FROM forecasts WHERE submitted_at IS NOT NULL)
        SELECT qid, submitted_prob FROM s WHERE rn=1""")}
    pred_ids = {r["qid"]: r["sp_prediction_id"] for r in conn.execute("""
        WITH s AS (SELECT qid, sp_prediction_id,
                   ROW_NUMBER() OVER (PARTITION BY qid ORDER BY submitted_at DESC) rn
                   FROM forecasts WHERE submitted_at IS NOT NULL)
        SELECT qid, sp_prediction_id FROM s WHERE rn=1""")}

    ts = now.isoformat()
    new, patches, skipped = [], [], 0
    for q in qs:
        res = derive_question(conn, q, now)
        if res is None:
            skipped += 1
            continue
        prob, tier, reason = res
        prob = shrink_extremes(prob)
        full_reason = f"derive.v1 [{tier}] {reason}"
        old = prev.get(q["qid"])
        new_int = _to_int(prob)
        if old is None:
            if not dry:  # dry-run must be DRY (review finding)
                conn.execute(
                    """INSERT INTO forecasts(qid, ts, blend_w, final_prob,
                       deviation_bps, deviation_reason) VALUES (?,?,0,?,0,?)""",
                    (q["qid"], ts, round(prob, 5), full_reason))
            new.append((q["qid"], new_int, q["text"]))
        elif abs(new_int - round(old * 100)) >= ALPHA_REVISE_PTS \
                and pred_ids.get(q["qid"]):
            patches.append((q["qid"], pred_ids[q["qid"]], round(old * 100),
                            new_int, q["text"], prob, full_reason))
    conn.commit()

    print(f"derive: {len(qs)} open questions in window; {len(new)} new, "
          f"{len(patches)} patch-candidates, {skipped} no-handler")
    if dry or not submit_mode:
        for qid, p, t in new[:25]:
            print(f"  NEW   {p:3d}  {t[:75]}")
        for _, _, o, n_, t, *_ in patches[:15]:
            print(f"  PATCH {o:3d} -> {n_:3d}  {t[:70]}")
        return

    c = SPClient()
    lobby = _lobby(conn)
    for i in range(0, len(new), 50):
        chunk = new[i:i + 50]
        resp = c.submit_batch([{"market_id": qid, "lobby_id": lobby,
                                "probability": p} for qid, p, _ in chunk])
        ok = {x["market_id"]: x for x in resp.get("results", [])
              if x.get("success")}
        for qid, p, t in chunk:
            if qid in ok:
                conn.execute(
                    """UPDATE forecasts SET submitted_at=?, submitted_prob=?,
                       sp_prediction_id=? WHERE qid=? AND ts=?""",
                    (ts, p / 100, (ok[qid].get("trade") or {}).get("id"),
                     qid, ts))
                print(f"  ok {p:3d}  {t[:70]}")
            else:
                print(f"  FAIL  {t[:70]}")
        conn.commit()
    locked = db.locked_prediction_ids(conn)
    for qid, pid, old, n_, t, prob, reason in patches:
        if pid in locked:
            continue
        try:
            c.revise(pid, n_)
        except Exception as e:
            print(f"  PATCH FAIL {t[:60]}: {e}")
            # 400 = market locked server-side — remember, stop retrying (the
            # sentinel re-derives every 15 min; Jun-27 saw 213 futile PATCHes)
            resp = getattr(e, "response", None)
            if resp is not None and getattr(resp, "status_code", None) == 400:
                db.mark_prediction_locked(conn, pid, qid, str(e))
            continue
        conn.execute(
            """INSERT INTO forecasts(qid, ts, blend_w, final_prob, deviation_bps,
               deviation_reason, submitted_at, submitted_prob, sp_prediction_id)
               VALUES (?,?,0,?,0,?,?,?,?)""",
            (qid, ts, round(prob, 5), reason, ts, n_ / 100, pid))
        conn.commit()
        print(f"  patched {old:3d} -> {n_:3d}  {t[:65]}")


if __name__ == "__main__":
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) \
        if "--hours" in sys.argv else 30
    conn = db.init()
    run(conn, hours, submit_mode="--submit" in sys.argv,
        dry="--dry-run" in sys.argv)

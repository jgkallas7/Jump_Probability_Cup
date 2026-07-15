"""WC_PH_COVERAGE: classifier-dropped questions routed to pricers we already own.

tie -> h2h draw, ahead-at-halftime -> h2h_3_way_h1, any-player-brace -> team lambdas.
"""

import re
from datetime import datetime, timedelta, timezone

import db as dbmod
import derive
from ingest_questions import classify


def _snap(conn, market, outcome, point, fair, match_id="M1"):
    # stamp a minute in the past — real snapshots precede pricing `now`, and the
    # consensus ts<=now guard correctly excludes any snapshot at/after now.
    ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    conn.executemany(
        """INSERT INTO market_snapshots
           (ts, source, book, match_id, event_label, market, outcome, point,
            raw_price, raw_prob, fair_prob, fair_prob_mult, divergence_pts, quote_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(ts, "odds_api", b, match_id, "x", market, outcome, point,
          None, fair, fair, fair, 0.5, ts) for b in ("pinnacle", "betfair_ex_uk")])


# ---- classifier: brace must NOT be a match-goals total ----

def test_brace_classifies_no_market_not_totals():
    qt, mapping = classify("Will any player score more than 1 goal "
                           "(excluding own goals) in regulation?")
    assert mapping == "NO_MARKET" and qt == "brace"
    # the generic goal-totals rule still maps real match totals:
    assert classify("Will the match have 3 or more total goals?")[1] == "totals"


def _cov_pattern(fn):
    """Look up a COVERAGE_HANDLERS pattern by its handler fn (order-independent)."""
    return next(p for p, f in derive.COVERAGE_HANDLERS if f is fn)


# ---- tie -> h2h draw ----

def test_ends_in_tie_prices_from_h2h_draw():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "h2h", "Draw", None, 0.28)
    m = {"match_id": "M1", "home": "South Africa", "away": "Canada"}
    g = re.search(_cov_pattern(derive.h_ends_in_tie),
                  "Will regulation (90 minutes + stoppage time) end in a tie?")
    assert g is not None
    p, tier, _ = derive.h_ends_in_tie(m, g, conn, now)
    assert abs(p - 0.28) < 1e-9 and tier == "derived"


def test_ends_in_tie_none_without_h2h():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    m = {"match_id": "M1", "home": "A", "away": "B"}
    assert derive.h_ends_in_tie(m, None, conn, now) is None


# ---- ahead at halftime -> h2h_3_way_h1 ----

def test_ahead_at_halftime_prices_from_h1_3way():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "h2h_3_way_h1", "Canada", None, 0.31)
    m = {"match_id": "M1", "home": "South Africa", "away": "Canada"}
    g = re.search(_cov_pattern(derive.h_ahead_at_halftime), "Will Canada be ahead at halftime?")
    assert g is not None
    p, tier, _ = derive.h_ahead_at_halftime(m, g, conn, now)
    assert abs(p - 0.31) < 1e-9 and tier == "derived"


# ---- any player brace -> team lambdas ----

def test_any_player_brace_sane_range():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    # total ~2.4 goals, home modest favourite -> ~0.8 / ~1.6 split
    _snap(conn, "totals", "Under", 2.5, 0.58)
    _snap(conn, "h2h", "South Africa", None, 0.34)
    m = {"match_id": "M1", "home": "South Africa", "away": "Canada"}
    g = re.search(_cov_pattern(derive.h_any_player_brace),
                  "Will any player score more than 1 goal (excluding own goals)?")
    assert g is not None
    p, tier, _ = derive.h_any_player_brace(m, g, conn, now)
    assert 0.18 < p < 0.34 and tier == "derived"     # field said 25%, not 45%


# ---- dispatch gating: coverage only fires as a fallback when the flag is on ----

def test_coverage_is_flag_gated(monkeypatch):
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "h2h", "Draw", None, 0.28)
    q = {"text": "Will regulation end in a tie?", "match_id": "M1",
         "home": "A", "away": "B"}
    monkeypatch.setattr(derive, "PH_COVERAGE_ON", False)
    assert derive.derive_question(conn, q, now) is None      # -> placeholder
    monkeypatch.setattr(derive, "PH_COVERAGE_ON", True)
    res = derive.derive_question(conn, q, now)
    assert res is not None and abs(res[0] - 0.28) < 1e-9


# ---- derivable remainders: hydration goal, half totals, SOT-race leading form ----

def _lam_snaps(conn):
    # total ~2.4 goals, home modest favourite -> match_lambdas resolvable
    _snap(conn, "totals", "Under", 2.5, 0.58)
    _snap(conn, "h2h", "A", None, 0.45)


def test_goal_before_hydration_prices_from_lambda():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _lam_snaps(conn)
    m = {"match_id": "M1", "home": "A", "away": "B"}
    g = re.search(r"goal.*before the first hydration break",
                  "Will a goal be scored before the first hydration break?")
    assert g is not None
    p, tier, _ = derive.h_goal_before_hydration(m, g, conn, now)
    assert 0.30 < p < 0.55 and tier == "derived"   # ~0.42, not the flat placeholder


def test_half_total_goals_prices_from_lambda():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _lam_snaps(conn)
    m = {"match_id": "M1", "home": "A", "away": "B"}
    g = re.search(r"(first|second) half have (\d+) or (more|fewer|less) total goals",
                  "Will the second half have 2 or more total goals?")
    assert g is not None
    p, tier, _ = derive.h_half_total_goals(m, g, conn, now)
    assert 0.25 < p < 0.55 and tier == "derived"


def test_half_total_goals_now_routes_to_derive():
    from ingest_questions import classify
    assert classify("Will the second half have 2 or more total goals "
                    "in regulation?")[1] == "NO_MARKET"


def test_sot_race_leading_form_is_a_race_both_orders():
    assert derive.is_sot_race("In the second half, will Germany have more "
                              "shots on target than Paraguay?")
    assert derive.is_sot_race("Will Germany have more shots on target than "
                              "Paraguay in the second half?")          # trailing still
    assert not derive.is_sot_race("Will Germany have more corner kicks than Spain?")


def test_sot_race_leading_form_routes_to_pricer():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _lam_snaps(conn)
    q = {"text": "In the second half, will A have more shots on target than B?",
         "match_id": "M1", "home": "A", "away": "B"}
    res = derive.derive_question(conn, q, now)        # qmodel/kalshi off in tests
    assert res is not None and 0.0 < res[0] < 1.0     # priced, not stranded


def test_any_player_sot_brace_is_high_not_a_coin_flip():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _lam_snaps(conn)
    m = {"match_id": "M1", "home": "A", "away": "B"}
    g = re.search(r"any player (?:record|have) \d+ or more shots on target",
                  "Will any player record 2 or more shots on target?")
    p, tier, _ = derive.h_any_player_sot_brace(m, g, conn, now)
    assert 0.60 < p < 0.85 and tier == "base"   # ~0.70 (field), not the flat 0.50


# ---- knockout-wording placeholder gaps surfaced by the R32 games ----

def test_either_offside_before_hydration_tracks_field():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    m = {"match_id": "M1", "home": "A", "away": "B"}
    g = re.search(r"offside before the first hydration break",
                  "Will either team be ruled offside before the first hydration break?")
    assert g is not None
    p, tier, _ = derive.h_either_offside_before_hydration(m, g, conn, now)
    assert 0.45 < p < 0.62 and tier == "derived"   # field ~0.52, not flat 0.45


def test_card_after_2nd_break_includes_extra_time():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    # P(regulation draw) drives the extra-time term -> a level KO prices higher
    _snap(conn, "h2h", "Draw", None, 0.29)
    m = {"match_id": "M1", "home": "A", "away": "B"}
    g = re.search(r"card.*after the second hydration break",
                  "Will a card be shown after the second hydration break, "
                  "including any extra time?")
    assert g is not None
    p, tier, _ = derive.h_card_after_2nd_break(m, g, conn, now)
    assert 0.55 < p < 0.75 and tier == "derived"   # field ~0.61, not flat 0.45
    # the ET term must lift the price: a runaway favourite (tiny draw) prices lower
    _snap(conn, "h2h", "Draw", None, 0.05, match_id="M2")
    m2 = {"match_id": "M2", "home": "A", "away": "B"}
    p_lo, _, _ = derive.h_card_after_2nd_break(m2, g, conn, now)
    assert p_lo < p


def test_substitute_scores_scales_with_goals():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "totals", "Under", 2.5, 0.55)        # ~2.5-goal match
    _snap(conn, "h2h", "A", None, 0.45)
    m = {"match_id": "M1", "home": "A", "away": "B"}
    g = re.search(r"[Ww]ill a substitute score a goal",
                  "Will a substitute score a goal in regulation?")
    assert g is not None
    p, tier, _ = derive.h_substitute_scores(m, g, conn, now)
    assert 0.22 < p < 0.40 and tier == "derived"     # field ~0.30, not flat 0.45


def test_score_both_halves_scales_with_team_strength():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "totals", "Under", 2.5, 0.40)        # higher-scoring match
    _snap(conn, "h2h", "A", None, 0.62)              # A the clear favourite
    m = {"match_id": "M1", "home": "A", "away": "B"}
    fav = re.search(r"[Ww]ill (.+?) score in both halves",
                    "Will A score in both halves of regulation?")
    dog = re.search(r"[Ww]ill (.+?) score in both halves",
                    "Will B score in both halves of regulation?")
    pa, tier, _ = derive.h_score_both_halves(m, fav, conn, now)
    pb, _, _ = derive.h_score_both_halves(m, dog, conn, now)
    assert pa > pb and tier == "derived"
    assert 0.25 < pa < 0.55                           # favourite ~field, dog far lower


def test_card_in_first_half_is_high_not_a_coin_flip():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    # cards market quoted -> cards_lambda derived; first-half card is near-certain
    _snap(conn, "alternate_totals_cards", "Over", 3.5, 0.60)
    m = {"match_id": "M1", "home": "A", "away": "B"}
    g = re.search(r"card be shown in the first half",
                  "Will a card be shown in the first half?")
    assert g is not None
    p, tier, _ = derive.h_card_in_first_half(m, g, conn, now)
    assert 0.60 < p < 0.85 and tier == "derived"   # ~0.72, not the flat 0.35


def test_ko_gap_texts_route_through_coverage(monkeypatch):
    # all four must reach the coverage fallback (qmodel/kalshi off in tests), never strand
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "totals", "Under", 2.5, 0.50)
    _snap(conn, "h2h", "A", None, 0.50)
    _snap(conn, "h2h", "Draw", None, 0.28)
    monkeypatch.setattr(derive, "PH_COVERAGE_ON", True)
    for text in (
        "Will either team be ruled offside before the first hydration break?",
        "Will a card be shown after the second hydration break, including any extra time?",
        "Will a substitute score a goal in regulation (90 minutes + stoppage time)?",
        "Will A score in both halves of regulation (90 minutes + stoppage time)?",
    ):
        q = {"text": text, "match_id": "M1", "home": "A", "away": "B"}
        res = derive.derive_question(conn, q, now)
        assert res is not None and 0.0 < res[0] < 1.0, text


def test_team_first_goal_scales_with_favourite():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "totals", "Under", 2.5, 0.45)        # higher-scoring match
    _snap(conn, "h2h", "A", None, 0.65)              # A is the clear favourite
    m = {"match_id": "M1", "home": "A", "away": "B"}
    fav = re.search(r"[Ww]ill (.+?) score the first goal of the match",
                    "Will A score the first goal of the match?")
    dog = re.search(r"[Ww]ill (.+?) score the first goal of the match",
                    "Will B score the first goal of the match?")
    pa, _, _ = derive.h_team_first_goal_match(m, fav, conn, now)
    pb, _, _ = derive.h_team_first_goal_match(m, dog, conn, now)
    assert pa > pb and pa > 0.45        # favourite well above the flat 0.35 placeholder


# ---- clean sheet -> exp(-lam_opp) off the market lambdas ----

def test_clean_sheet_prices_from_opponent_lambda():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    # heavy favorite: total 2.5-line under prob low (high-scoring), home dominant
    _snap(conn, "totals", "Under", 2.5, 0.35)
    _snap(conn, "h2h", "Argentina", None, 0.80)
    m = {"match_id": "M1", "home": "Argentina", "away": "Cape Verde"}
    pat = next(p for p, fn in derive.COVERAGE_HANDLERS if fn is derive.h_clean_sheet)
    g = re.search(pat, "Will Argentina keep a clean sheet in regulation "
                       "(90 minutes + stoppage time)?")
    assert g is not None
    p, tier, tag = derive.h_clean_sheet(m, g, conn, now)
    lam_h, lam_a, _ = derive.match_lambdas(conn, m, now)
    assert abs(p - pow(2.718281828, -lam_a)) < 1e-6 and tier == "derived"
    # the favorite's clean sheet must be MORE likely than the minnow's
    g2 = re.search(pat, "Will Cape Verde keep a clean sheet in regulation "
                        "(90 minutes + stoppage time)?")
    p2, _, _ = derive.h_clean_sheet(m, g2, conn, now)
    assert p > p2


def test_sub_before_half_base():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    m = {"match_id": "M1", "home": "Australia", "away": "Egypt"}
    pat = next(p for p, fn in derive.COVERAGE_HANDLERS
               if fn is derive.h_sub_before_half)
    g = re.search(pat, "Will a substitution be made before halftime?")
    assert g is not None
    p, tier, _ = derive.h_sub_before_half(m, g, conn, now)
    assert abs(p - 0.22) < 1e-9 and tier == "base"


# ---- late-KO novel wordings (France-Spain SF 2026-07-14) ----

def test_tied_at_end_of_regulation_routes_to_h2h_draw():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "h2h", "Draw", None, 0.317)
    m = {"match_id": "M1", "home": "France", "away": "Spain"}
    text = ("Will the match be tied at the end of regulation (90 minutes + "
            "stoppage time) and go to extra time?")
    fn = next(f for p, f in derive.COVERAGE_HANDLERS if re.search(p, text))
    assert fn is derive.h_ends_in_tie
    p, tier, _ = fn(m, re.search(_cov_pattern(fn), text), conn, now)
    assert abs(p - 0.317) < 1e-9 and tier == "derived"


def test_goal_between_breaks_window_math():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "totals", "Under", 2.5, 0.45)   # lively match, lam ~2.7
    m = {"match_id": "M1", "home": "France", "away": "Spain"}
    text = ("Will a goal be scored after the first hydration break but before "
            "the second hydration break?")
    fn = next(f for p, f in derive.COVERAGE_HANDLERS if re.search(p, text))
    assert fn is derive.h_goal_between_breaks
    g = re.search(_cov_pattern(fn), text)
    p, tier, _ = fn(m, g, conn, now)
    # window share = 1 - 0.21 - 0.23 = 0.56 of the match lambda
    import math
    _, _, lt = derive.match_lambdas(conn, m, now)
    assert abs(p - (1 - math.exp(-lt * 0.56))) < 1e-9 and tier == "derived"
    # the mid-window must be the most likely of the three break windows
    g_pre = re.search(_cov_pattern(derive.h_goal_before_hydration),
                      "Will a goal be scored before the first hydration break?")
    p_pre, _, _ = derive.h_goal_before_hydration(m, g_pre, conn, now)
    g_post = re.search(_cov_pattern(derive.h_goal_after_2nd_break),
                       "Will a goal be scored after the second hydration break?")
    p_post, _, _ = derive.h_goal_after_2nd_break(m, g_post, conn, now)
    assert p > p_pre and p > p_post


def test_novel_prop_placeholder_families():
    from placeholders import placeholder_for
    p, why = placeholder_for(
        "Will Spain make the first substitution of the match in regulation "
        "(90 minutes + stoppage time)?")
    assert p == 0.50 and "race" in why
    p, why = placeholder_for(
        "Will the referee conduct an on-field review at the pitchside VAR "
        "monitor at any point in regulation (90 minutes + stoppage time)?")
    assert p == 0.35 and "VAR" in why
    p, why = placeholder_for(
        "Will the first goal of the match (including extra time, excluding "
        "penalty shootout) be scored by a player wearing a single-digit shirt "
        "number (1-9)?")
    assert p == 0.35 and "shirt" in why

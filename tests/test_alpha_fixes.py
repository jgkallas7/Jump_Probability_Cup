"""2026-07-01 alpha-bucket fixes: SOA name-matching + routing, SOT anchor split,
BTS-half anchor knob, corner coverage handlers + supremacy slope.

Evidence (settled locked-email corpus + outcomes table, data/alpha_audit_2026-07-01.md):
  - 'own_goal -55' was a MISCLASSIFIED family: every score-or-assist question
    contains '(excluding own goals)'. The real bleed was SOA pricing — thin-book
    Kalshi mids (-40/n=5) and a book-union pricer that never fired because
    derive._player_prob kept the '(Belgium)' token (cc4a453 fixed forecast.py,
    missed the derive twin).
  - total-SOT thresholds settle YES 84% (n=19), team-SOT 39% (n=33): one pooled
    0.65 anchor is wrong for both. Split anchors sweep +49/+36 sens (total 0.78)
    and +76/+36 sens (team 0.42).
  - the corner-race supremacy fallback caps its tilt at ±0.10 while ladder-implied
    shares run ±0.17 — same right-sign-too-weak class as old h_fouls_race.
"""
import re
from datetime import datetime, timedelta, timezone

import db as dbmod
import derive
import qmodel
import review_report


def _snap(conn, market, outcome, point, fair, match_id="M1"):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    conn.executemany(
        """INSERT INTO market_snapshots
           (ts, source, book, match_id, event_label, market, outcome, point,
            raw_price, raw_prob, fair_prob, fair_prob_mult, divergence_pts, quote_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(ts, "odds_api", b, match_id, "x", market, outcome, point,
          None, fair, fair, fair, 0.5, ts) for b in ("pinnacle", "betfair_ex_uk")])


M = {"match_id": "M1", "home": "Belgium", "away": "Japan"}


# ---- SOA: parenthetical/accent-robust book matching in derive._player_prob ----

def test_player_prob_matches_ko_wording():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "player_goal_scorer_anytime", "Kevin De Bruyne Yes", None, 0.30)
    p = derive._player_prob(conn, "M1", "player_goal_scorer_anytime",
                            "Kevin De Bruyne (Belgium)", "Yes", now)
    assert p is not None and abs(p - 0.30) < 1e-9


def test_score_or_assist_union_with_country_qualifier():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "player_goal_scorer_anytime", "Kevin De Bruyne Yes", None, 0.30)
    _snap(conn, "player_assists", "Kevin De Bruyne Over", 0.5, 0.15)
    g = re.search(r"Will (.+?) score or assist a goal",
                  "Will Kevin De Bruyne (Belgium) score or assist a goal "
                  "(excluding own goals) in regulation?")
    p, tier, _ = derive.h_score_or_assist(M, g, conn, now)
    assert tier == "derived-mkt"
    assert abs(p - (1 - 0.70 * 0.85)) < 1e-9   # 1-(1-.30)(1-.15)


def test_kalshi_no_soa_flag_skips_before_network(monkeypatch):
    # flag ON -> SOA questions never touch the Kalshi client (returns None
    # immediately; anything else would need network and blow up the test)
    monkeypatch.setattr(derive, "KALSHI_NO_SOA", True)
    assert derive._kalshi_price(None, M, "Will Kevin De Bruyne (Belgium) score "
                                "or assist a goal (excluding own goals)?") is None


# ---- SOT anchor split (WC_SOT_TOTAL_ANCHOR / WC_SOT_TEAM_ANCHOR) ----

TOTAL_Q = "Will there be 8 or more total shots on target?"
TEAM_Q = "Will Belgium have 4 or more shots on target?"


def test_sot_anchor_default_is_pooled(monkeypatch):
    # knobs unset -> both families anchor to SOT_ANCHOR (today's behaviour)
    monkeypatch.setattr(derive, "SOT_TOTAL_ANCHOR", None)
    monkeypatch.setattr(derive, "SOT_TEAM_ANCHOR", None)
    assert derive._sot_anchor_value(TOTAL_Q) == derive.SOT_ANCHOR
    assert derive._sot_anchor_value(TEAM_Q) == derive.SOT_ANCHOR


def test_sot_anchor_split_routes_by_family(monkeypatch):
    monkeypatch.setattr(derive, "SOT_TOTAL_ANCHOR", 0.78)
    monkeypatch.setattr(derive, "SOT_TEAM_ANCHOR", 0.42)
    assert derive._sot_anchor_value(TOTAL_Q) == 0.78
    assert derive._sot_anchor_value(TEAM_Q) == 0.42


def test_sot_anchor_blend_uses_family_anchor(monkeypatch):
    monkeypatch.setattr(derive, "SOT_THRESH_ANCHOR_ON", True)
    monkeypatch.setattr(derive, "SOT_TOTAL_ANCHOR", 0.80)
    p, _, reason = derive._apply_sot_anchor(TOTAL_Q, (0.50, "t", "r"))
    assert abs(p - (0.5 * 0.50 + 0.5 * 0.80)) < 1e-9
    assert "@0.80" in reason
    # race/both-teams scopes stay excluded (is_sot_threshold unchanged)
    assert derive._apply_sot_anchor(
        "Will Belgium have more shots on target than Japan in the second half?",
        (0.50, "t", "r")) == (0.50, "t", "r")


# ---- BTS-half anchor knob (WC_BTS_HALF_ANCHOR) ----

RATES = {"_tournament": {"sot": 4.25}}
LAM = (1.3, 1.3, 2.6)
BTS_HALF_Q = "Will both teams have at least 1 shot on target in the second half?"


def test_bts_half_anchor_default_unchanged(monkeypatch):
    monkeypatch.setattr(qmodel, "BTS_HALF_ANCHOR", 0.68)
    p, _ = qmodel.price_question(BTS_HALF_Q, "Belgium", "Japan", RATES, LAM)
    raw = qmodel.price_question(BTS_HALF_Q, "Belgium", "Japan", RATES, LAM)
    assert abs(p - raw[0]) < 1e-12          # deterministic
    # anchor dominates: price within a few points of 0.68
    assert 0.60 < p < 0.76


def test_bts_half_anchor_moves_price(monkeypatch):
    monkeypatch.setattr(qmodel, "BTS_HALF_ANCHOR", 0.68)
    p68 = qmodel.price_question(BTS_HALF_Q, "Belgium", "Japan", RATES, LAM)[0]
    monkeypatch.setattr(qmodel, "BTS_HALF_ANCHOR", 0.60)
    p60 = qmodel.price_question(BTS_HALF_Q, "Belgium", "Japan", RATES, LAM)[0]
    assert abs((p68 - p60) - 0.70 * 0.08) < 1e-9    # exactly the anchor delta x weight
    # full-match variant untouched by the knob
    ft = "Will both teams have at least 1 shot on target?"
    monkeypatch.setattr(qmodel, "BTS_HALF_ANCHOR", 0.68)
    f68 = qmodel.price_question(ft, "Belgium", "Japan", RATES, LAM)[0]
    monkeypatch.setattr(qmodel, "BTS_HALF_ANCHOR", 0.60)
    assert qmodel.price_question(ft, "Belgium", "Japan", RATES, LAM)[0] == f68


# ---- corner coverage handlers (WC_PH_COVERAGE extensions) ----

def _coverage_price(conn, text, now):
    for pattern, fn in derive.COVERAGE_HANDLERS:
        g = re.search(pattern, text)
        if g:
            return fn(M, g, conn, now)
    return None


def test_corners_before_hydration_priced():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    res = _coverage_price(conn, "Will 2 or more corner kicks be taken before "
                          "the first hydration break?", now)
    assert res is not None
    p, tier, reason = res
    assert "corners-by-1st-break" in reason
    # base corners lambda 9.5 x 0.28 = 2.66 -> P(>=2) ~ 0.75
    assert 0.65 < p < 0.85


def test_team_atleast_corner_in_half_priced():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    res = _coverage_price(conn, "Will Belgium have at least 1 corner kick "
                          "in the first half?", now)
    assert res is not None
    p, tier, reason = res
    assert "team corners(at-least)" in reason
    # ~9.5 x share 0.5 x h1 0.44 = 2.09 -> P(>=1) ~ 0.88; field settled ~0.83
    assert 0.75 < p < 0.95
    # was the -27 leak: the flat 0.45 placeholder is far outside this range


def test_atleast_wording_does_not_shadow_or_more():
    # 'N or more corner kicks' still belongs to HANDLERS h_team_corners
    assert not re.search(derive.COVERAGE_HANDLERS[0][0], "")  # sanity: patterns compile
    at_least = "Will Belgium have at least 1 corner kick in the first half?"
    or_more = "Will Belgium have 6 or more corner kicks in regulation?"
    assert re.search(r"have at least (\d+) corner", at_least)
    assert not re.search(r"have at least (\d+) corner", or_more)


# ---- corner supremacy fallback slope (WC_CORNER_SUP_SLOPE) ----

def test_corner_sup_slope_default_matches_old_constant(monkeypatch):
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "h2h", "Belgium", None, 0.70)
    monkeypatch.setattr(derive, "CORNER_SUP_SLOPE", 0.20)
    share, tier = derive.corner_share(conn, M, "Belgium", now)
    assert tier == "anchored-supremacy"
    assert abs(share - (0.5 + 0.20 * (0.70 - 0.5))) < 1e-9   # old formula, bit-identical


def test_corner_sup_slope_strengthens_tilt(monkeypatch):
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "h2h", "Belgium", None, 0.70)
    monkeypatch.setattr(derive, "CORNER_SUP_SLOPE", 0.50)
    share, _ = derive.corner_share(conn, M, "Belgium", now)
    assert abs(share - 0.60) < 1e-9


# ---- review_report family classifier: the own_goal mislabel can't recur ----

def test_family_classifier_soa_not_own_goal():
    fam = review_report._alpha_family
    assert fam("Will Harry Kane score or assist a goal (excluding own goals)?") \
        == "score_or_assist"
    assert fam("Will an own goal be scored?") == "own_goal"
    assert fam("Will there be 8 or more total shots on target?") == "sot_total"
    assert fam("Will Belgium have 4 or more shots on target?") == "sot_team"
    assert fam("In the second half, will Spain have more corner kicks than Chile?") \
        == "corners_race_h2"


# ---- KO calendar stage fallback (audit: 13/21 KO matches stuck at 'group') ----

def test_stage_from_calendar_boundaries():
    from ingest_questions import stage_from_calendar
    assert stage_from_calendar("2026-06-28T02:00:00Z") is None      # last group games
    assert stage_from_calendar("2026-06-28T19:00:00Z") == "r32"
    assert stage_from_calendar("2026-07-04T01:30:00Z") == "r32"     # late-US R32 finale
    assert stage_from_calendar("2026-07-04T17:00:00Z") == "r16"
    assert stage_from_calendar("2026-07-19T20:00:00Z") == "final"
    assert stage_from_calendar("2026-06-15T18:00:00Z") is None      # group stage


def test_backfill_stages_calendar_and_multiplier():
    import ingest_questions as iq
    conn = dbmod.init(":memory:")
    conn.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("""INSERT INTO matches(match_id, home, away, kickoff_utc, stage)
                    VALUES ('K1','Germany','Paraguay','2026-06-29T20:30:00Z','group')""")
    conn.execute("""INSERT INTO questions(qid, match_id, text, status, market_mapping)
                    VALUES ('Q1','K1','Will Germany win the match?','settled','h2h')""")
    conn.execute("""INSERT INTO outcomes(qid, resolved_at, outcome, brier, multiplier)
                    VALUES ('Q1','2026-06-29T23:00:00Z',1,0.1,1.0)""")
    n = iq.backfill_stages(conn)
    assert n >= 1
    assert conn.execute("SELECT stage FROM matches WHERE match_id='K1'").fetchone()[0] == "r32"
    # settled outcome's frozen 1x multiplier is repaired to the KO 2x
    assert conn.execute("SELECT multiplier FROM outcomes WHERE qid='Q1'").fetchone()[0] == 2.0


# ---- total-shots handler is threshold-aware now (was flat 0.58 for every N) ----

def test_total_shots_threshold_aware():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    def price(n):
        g = re.search(r"(\d+) or more total shots",
                      f"Will there be {n} or more total shots (on and off target)?")
        return derive.h_total_shots_match(M, g, conn, now)[0]
    p18, p20, p25, p30 = price(18), price(20), price(25), price(30)
    assert p18 > p20 > p25 > p30            # monotone in the line
    assert 0.70 < p20 < 0.88                # ~0.78 at a neutral goal line
    assert p30 < 0.30                       # '30 or more' is no longer 0.58


# ---- placeholder ordering: both-teams-card beats the generic cards rule ----

def test_placeholder_both_teams_cards_plural():
    from placeholders import placeholder_for
    p, why = placeholder_for("Will both teams receive cards in regulation?")
    assert p == 0.82, why
    p, why = placeholder_for("Will both teams receive at least one card?")
    assert p == 0.82, why


# ---- PATCH-400 lock bookkeeping (stop retrying server-locked predictions) ----

def test_locked_prediction_roundtrip():
    conn = dbmod.init(":memory:")
    assert dbmod.locked_prediction_ids(conn) == set()
    dbmod.mark_prediction_locked(conn, "pid-1", "Q1", "400 Client Error: locked")
    dbmod.mark_prediction_locked(conn, "pid-1", "Q1", "repeat is idempotent")
    assert dbmod.locked_prediction_ids(conn) == {"pid-1"}


# ---- team-SOT: both wordings now hit ONE shared pricer ----

def test_team_sot_wordings_price_identically():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    g1 = re.search(r"Will (.+?) have (\d+) or more shots on target"
                   r"( in the second half| in the first half)?",
                   "Will Belgium have 7 or more shots on target?")
    g2 = re.search(r"[Ww]ill (.+?) have (?:at least )?(\d+)(?: or more)? shots on target",
                   "Will Belgium have at least 7 shots on target?")
    p1 = derive.h_team_sot(M, g1, conn, now)[0]
    p2 = derive.h_team_sot_total(M, g2, conn, now)[0]
    assert abs(p1 - p2) < 1e-12   # was 0.232 vs 0.028 by wording


# ---- 2026-07-02 post-mortem fixes (first new-regime match night) ----

def test_win_by_margin_coverage():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "totals", "Under", 2.5, 0.40)          # lam_tot ~2.9
    _snap(conn, "h2h", "Belgium", None, 0.65)          # clear favourite
    res = _coverage_price(conn, "Will Belgium win by 2 or more goals in "
                          "regulation (90 minutes + stoppage time)?", now)
    assert res is not None
    p, tier, reason = res
    assert "win-by>=2" in reason
    assert 0.15 < p < 0.45          # favourite's 2+ margin, NOT a flat 0.35
    res_dog = _coverage_price(conn, "Will Japan win by 2 or more goals in "
                              "regulation?", now)
    # unknown team resolves None -> falls through (no crash)
    assert res_dog is None or res_dog[0] < p


def test_total_shots_sublinear_and_wide():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    # high goal line (lam ~3.0): '22 or more' must NOT price near 0.87 again
    _snap(conn, "totals", "Under", 2.5, 0.35)
    g = re.search(r"(\d+) or more total shots",
                  "Will there be 22 or more total shots (on and off target)?")
    p = derive.h_total_shots_match(M, g, conn, now)[0]
    assert p < 0.80                 # was 0.87 on 2026-07-01, lost -43 vs field 57


def test_substitute_scorer_classifies_no_market():
    from ingest_questions import classify
    qt, mapping = classify("Will a substitute score a goal (excluding own "
                           "goals) in regulation (90 minutes + stoppage time)?")
    assert mapping == "NO_MARKET"
    # real player scorer questions still map to the book market
    qt, mapping = classify("Will Harry Kane score a goal (excluding own goals) "
                           "in regulation?")
    assert mapping == "player_goal_scorer_anytime"


def test_team_scorer_maps_to_team_totals():
    from forecast import map_question
    mkt = map_question("Will DR Congo score a goal (excluding own goals) in "
                       "regulation?", "player_goal_scorer_anytime",
                       "England", "DR Congo")
    assert mkt == ("team_totals", "Over", 0.5)
    # player subjects untouched
    mkt = map_question("Will Harry Kane score a goal (excluding own goals)?",
                       "player_goal_scorer_anytime", "England", "DR Congo")
    assert mkt[0] == "player_goal_scorer_anytime"


def test_dead_stat_column_not_zeroed():
    import pandas as pd
    import team_rates as tr
    df = pd.DataFrame({"Performance_PKwon": [float("nan")] * 3,
                       "Standard_PKatt": [1.0, 0.0, 2.0]})
    col = tr._find_col_with_data(df, ("PKwon", "Performance_PKwon", "Standard_PKatt"))
    assert col == "Standard_PKatt"   # dead all-NaN column skipped, live fallback used
    assert tr._find_col_with_data(df, ("Performance_PKwon",)) is None

"""WC_PH_COVERAGE: classifier-dropped questions routed to pricers we already own.

tie -> h2h draw, ahead-at-halftime -> h2h_3_way_h1, any-player-brace -> team lambdas.
"""

import re
from datetime import datetime, timezone

import db as dbmod
import derive
from ingest_questions import classify


def _snap(conn, market, outcome, point, fair, match_id="M1"):
    ts = datetime.now(timezone.utc).isoformat()
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


# ---- tie -> h2h draw ----

def test_ends_in_tie_prices_from_h2h_draw():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    _snap(conn, "h2h", "Draw", None, 0.28)
    m = {"match_id": "M1", "home": "South Africa", "away": "Canada"}
    g = re.search(derive.COVERAGE_HANDLERS[0][0],
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
    g = re.search(derive.COVERAGE_HANDLERS[1][0], "Will Canada be ahead at halftime?")
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
    g = re.search(derive.COVERAGE_HANDLERS[2][0],
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

"""Question->market mapping and consensus math."""

from datetime import datetime, timezone

import db as dbmod
from forecast import consensus, map_question


def test_h2h_mapping_with_alias():
    assert map_question("Will Mexico win the match?", "h2h",
                        "Mexico", "South Africa") == ("h2h", "Mexico", None)
    assert map_question("Will United States win the match?", "h2h",
                        "USA", "Paraguay") == ("h2h", "USA", None)
    assert map_question("Will the match end in a draw?", "h2h",
                        "Mexico", "South Africa") == ("h2h", "Draw", None)


def test_totals_mapping_thresholds():
    assert map_question("Will the match have 2 or fewer total goals?", "totals",
                        "A", "B") == ("totals", "Under", 2.5)
    assert map_question("Will the match have 3 or more total goals?", "totals",
                        "A", "B") == ("totals", "Over", 2.5)


def test_half_and_team_totals():
    assert map_question("Will the second half have 2 or more total goals?",
                        "totals_half", "A", "B") == ("totals_h2", "Over", 1.5)
    assert map_question("Will Qatar score at least 1 goal?", "team_totals",
                        "Qatar", "Switzerland") == ("team_totals", "Qatar Over", 0.5)


def test_btts_combo_never_maps_to_plain_btts():
    assert map_question("Will both teams score AND the match have 3 or more "
                        "total goals?", "btts", "A", "B") is None


def test_match_total_corner_card_mapping():
    # "N or more" -> match Over (N-0.5); books quote these as match totals.
    assert map_question("Will there be 9 or more total corner kicks?",
                        "alternate_totals_corners", "A", "B") == \
        ("alternate_totals_corners", "Over", 8.5)
    assert map_question("Will there be 4 or more total cards shown?",
                        "alternate_totals_cards", "A", "B") == \
        ("alternate_totals_cards", "Over", 3.5)


def test_team_and_half_corner_totals_stay_unmapped():
    from ingest_questions import classify
    # team corner total: no book market -> NO_MARKET (priced by derive)
    assert classify("Will Morocco have 5 or more corner kicks?")[1] == "NO_MARKET"
    # half-qualified total: no 2nd-half totals market -> NO_MARKET
    assert classify("Will there be 5 or more total corner kicks in the "
                    "second half?")[1] == "NO_MARKET"
    # but the plain match totals DO map now
    assert classify("Will there be 9 or more total corner kicks?")[1] == \
        "alternate_totals_corners"
    assert classify("Will there be 4 or more total cards shown?")[1] == \
        "alternate_totals_cards"


def test_score_or_assist_union_from_player_markets():
    import re
    from derive import h_score_or_assist
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    ts = now.isoformat()

    def snap(market, outcome, point, fair):
        conn.execute(
            """INSERT INTO market_snapshots
               (ts, source, book, match_id, event_label, market, outcome, point,
                raw_price, raw_prob, fair_prob, fair_prob_mult, divergence_pts, quote_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, "odds_api", "pinnacle", "M1", "x", market, outcome, point,
             None, fair, fair, fair, -1.0, ts))  # div=-1 -> one-sided, haircut applies

    snap("player_goal_scorer_anytime", "Vinicius Junior Yes", None, 0.40)
    snap("player_assists", "Vinicius Junior Over", 0.5, 0.20)
    g = re.search(r"Will (.+?) score or assist a goal",
                  "Will Vinicius Junior score or assist a goal (excluding own goals)?")
    m = {"match_id": "M1", "home": "Brazil", "away": "Morocco"}
    p, tier, _ = h_score_or_assist(m, g, conn, now)
    # haircuts: score 0.40*0.93, assist 0.20*0.90; union = 1-(1-s)(1-a)
    expected = 1 - (1 - 0.40 * 0.93) * (1 - 0.20 * 0.90)
    assert abs(p - expected) < 1e-9
    assert tier == "derived-mkt"


def test_consensus_weights_and_min_books():
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    rows = [
        (ts, "odds_api", "pinnacle", "M1", "x", "h2h", "Mexico", None,
         None, 0.72, 0.70, 0.71, 0.5, ts),
        (ts, "odds_api", "betfair_ex_uk", "M1", "x", "h2h", "Mexico", None,
         None, 0.71, 0.69, 0.69, 0.1, ts),
        (ts, "odds_api", "skybet", "M1", "x", "h2h", "Mexico", None,
         None, 0.80, 0.78, 0.78, 0.2, ts),  # soft book: weight 0, excluded
    ]
    conn.executemany(
        """INSERT INTO market_snapshots
           (ts, source, book, match_id, event_label, market, outcome, point,
            raw_price, raw_prob, fair_prob, fair_prob_mult, divergence_pts, quote_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    prob, n, detail = consensus(conn, "M1", "h2h", "Mexico", None, now)
    assert n == 2                       # skybet excluded by zero weight
    # pinnacle 3.0 x 0.70 + betfair 2.5 x 0.69 over 5.5
    assert abs(prob - (3.0 * 0.70 + 2.5 * 0.69) / 5.5) < 1e-9

    prob2, n2, _ = consensus(conn, "M1", "h2h", "Draw", None, now)
    assert prob2 is None and n2 == 0    # no quotes -> refuse, don't guess

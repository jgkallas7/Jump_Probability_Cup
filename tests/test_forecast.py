"""Question->market mapping and consensus math."""

from datetime import datetime, timezone

import db as dbmod
from forecast import consensus, map_question, to_advance_prob


def _ins_h2h(conn, ts, outcome, prob):
    conn.executemany(
        """INSERT INTO market_snapshots
           (ts, source, book, match_id, event_label, market, outcome, point,
            raw_price, raw_prob, fair_prob, fair_prob_mult, divergence_pts, quote_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(ts, "odds_api", b, "M1", "x", "h2h", outcome, None,
          None, prob, prob, prob, 0.5, ts) for b in ("pinnacle", "betfair_ex_uk")])


def test_to_advance_two_way_devig():
    # advancing = win in regulation OR survive ET/pens; draw split proportionally
    # collapses to the draw-no-bet devig: P(adv) = P(win)/(1-P(draw)).
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc); ts = now.isoformat()
    _ins_h2h(conn, ts, "Brazil", 0.60)
    _ins_h2h(conn, ts, "Draw", 0.25)          # opp implied 0.15
    q = {"text": "Will Brazil advance to the round of 16?", "match_id": "M1",
         "home": "Brazil", "away": "Japan"}
    res = to_advance_prob(conn, q, now)
    assert res is not None
    p_adv, p_win, p_draw, _ = res
    assert abs(p_adv - 0.60 / (1 - 0.25)) < 1e-9     # = 0.80, beats a ~0.50 placeholder
    assert abs(p_win - 0.60) < 1e-9 and abs(p_draw - 0.25) < 1e-9


def test_to_advance_none_without_h2h():
    # no h2h snapshot -> return None so the caller falls back to the placeholder
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    q = {"text": "Will Brazil advance to the round of 16?", "match_id": "M1",
         "home": "Brazil", "away": "Japan"}
    assert to_advance_prob(conn, q, now) is None


def test_h2h_mapping_with_alias():
    assert map_question("Will Mexico win the match?", "h2h",
                        "Mexico", "South Africa") == ("h2h", "Mexico", None)
    assert map_question("Will United States win the match?", "h2h",
                        "USA", "Paraguay") == ("h2h", "USA", None)
    assert map_question("Will the match end in a draw?", "h2h",
                        "Mexico", "South Africa") == ("h2h", "Draw", None)


def test_win_in_regulation_maps_to_h2h():
    # knockout phrasing for the match winner — must reach h2h, not the placeholder
    assert map_question("Will Germany win in regulation (90 minutes + stoppage time)?",
                        "h2h", "Germany", "Paraguay") == ("h2h", "Germany", None)
    # group phrasing still works
    assert map_question("Will Mexico win the match?", "h2h",
                        "Mexico", "South Africa") == ("h2h", "Mexico", None)


def test_player_props_handle_ko_wording_accents_country():
    # KO wording broke EVERY player prop: the SOT regex anchored '?' right after
    # "on target" (the "in regulation..." suffix sits between), and book names are
    # ASCII/country-free ("Kylian Mbappe") vs the question's "Kylian Mbappé (France)".
    assert map_question(
        "Will Kylian Mbappé (France) have 2 or more shots on target "
        "in regulation (90 minutes + stoppage time)?",
        "player_shots_on_target", "France", "Sweden") \
        == ("player_shots_on_target", "tokens:Over:Kylian|Mbappe", 1.5)
    assert map_question(
        "Will Raúl Jiménez (Mexico) score a goal (excluding own goals) in regulation?",
        "player_goal_scorer_anytime", "Mexico", "Ecuador") \
        == ("player_goal_scorer_anytime", "tokens:Yes:Raul|Jimenez", None)
    # a TEAM SOT total must NOT be matched as a player (no player book line) — it
    # falls through (-> placeholder/derive), not a bogus 'France' player lookup.
    assert map_question(
        "Will France have 7 or more shots on target in regulation?",
        "player_shots_on_target", "France", "Sweden") is None


def test_team_total_without_the_word_total():
    from ingest_questions import classify
    # "score 2 or more goals" (no 'total') is a team total, with the KO suffix
    assert classify("Will Brazil score 2 or more goals in regulation "
                    "(90 minutes + stoppage time)?")[1] == "team_totals"
    assert map_question("Will Brazil score 2 or more goals in regulation "
                        "(90 minutes + stoppage time)?", "team_totals",
                        "Brazil", "Japan") == ("team_totals", "Brazil Over", 1.5)
    # the brace ("any player score 2 or more goals") must NOT become a team total
    assert classify("Will any player score 2 or more goals?")[1] == "NO_MARKET"


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


def test_stage_from_advance_target_round():
    from ingest_questions import stage_from_advance
    # the named round is what the team advances TO; stage = round it is IN
    assert stage_from_advance("Will South Africa advance to the Round of 16?") == "r32"
    assert stage_from_advance("Will Brazil advance to the Round of 32?") == "group"
    assert stage_from_advance("Will France advance to the quarter-finals?") == "r16"
    assert stage_from_advance("Will Spain advance to the semi-finals?") == "qf"
    assert stage_from_advance("Will Italy advance to the final?") == "sf"
    # not an advancement question -> no signal
    assert stage_from_advance("Will Brazil win the match?") is None


def test_backfill_stages_promotes_only_knockouts():
    from ingest_questions import backfill_stages
    conn = dbmod.init(":memory:")
    conn.execute("INSERT INTO matches(match_id, home, away, kickoff_utc) "
                 "VALUES ('KO','South Africa','Canada','2026-06-28T19:00:00Z')")
    conn.execute("INSERT INTO matches(match_id, home, away, kickoff_utc) "
                 "VALUES ('GRP','A','B','2026-06-20T19:00:00Z')")
    conn.execute("""INSERT INTO questions(qid, match_id, qtype, text, status,
                    market_mapping) VALUES
                    ('q1','KO','advancement',
                     'Will South Africa advance to the Round of 16?','open','to_advance')""")
    assert backfill_stages(conn) == 1                      # only the KO match moves
    rows = {r["match_id"]: r["stage"] for r in
            conn.execute("SELECT match_id, stage FROM matches")}
    assert rows["KO"] == "r32" and rows["GRP"] == "group"  # group untouched (default)
    assert backfill_stages(conn) == 0                      # idempotent

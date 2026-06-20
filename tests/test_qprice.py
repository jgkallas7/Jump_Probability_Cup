"""Invariants for the quant pricers (qprice) and the alpha router (qmodel)."""
import qprice
import qmodel


def test_n_or_more_monotonic_decreasing():
    lam = 3.8
    ps = [qprice.prob_n_or_more(lam, n) for n in range(1, 7)]
    assert all(a >= b for a, b in zip(ps, ps[1:]))
    assert abs(qprice.prob_n_or_more(lam, 0) - 1.0) < 1e-12


def test_btts_equals_btts_and_two():
    # every both-teams-score match has >= 2 goals, so the clauses coincide
    for lh, la in [(1.6, 1.1), (0.8, 2.2), (1.0, 1.0)]:
        assert abs(qprice.prob_btts(lh, la)
                   - qprice.prob_btts_and_total(lh, la, 2)) < 1e-6


def test_btts_and_total_monotone_in_n():
    lh, la = 1.7, 1.2
    ps = [qprice.prob_btts_and_total(lh, la, n) for n in range(2, 7)]
    assert all(a >= b for a, b in zip(ps, ps[1:]))


def test_skellam_race_partition():
    # P(A>B) + P(B>A) + P(tie) == 1
    la, lb = 1.3, 1.0
    p_ab = qprice.prob_a_more_than_b(la, lb)
    p_ba = qprice.prob_a_more_than_b(lb, la)
    p_tie = float(__import__("scipy.stats", fromlist=["skellam"]).skellam.pmf(0, la, lb))
    assert abs(p_ab + p_ba + p_tie - 1.0) < 1e-9
    assert qprice.prob_a_more_than_b(1.0, 1.0) < 0.5   # ties depress equal-rate race


def test_penalty_or_red_bounds_and_monotone():
    assert qprice.prob_penalty_or_red(0.0, 0.0) == 0.0
    assert qprice.prob_penalty_or_red(0.3, 0.1) > qprice.prob_penalty_or_red(0.1, 0.1)
    assert 0 <= qprice.prob_penalty_or_red(0.5, 0.3) <= 1


def test_first_goal_decomposition():
    # P(team first & win) + P(team first & draw) + P(team first & opp win)
    # == P(team scores first) == p_any * lam_for/(lam_for+lam_against)
    lf, la = 1.6, 1.0
    import numpy as np
    p_any = 1 - np.exp(-(lf + la))
    p_first = p_any * lf / (lf + la)
    s = sum(qprice.prob_first_goal_and_result(lf, la, r)
            for r in ("win", "draw", "opp_win"))
    assert abs(s - p_first) < 1e-6


def test_qmodel_offsides_uses_counted_rate():
    rates = {"Tunisia": {"offsides": 2.86}, "Haiti": {"offsides": 0.5},
             "_tournament": {"offsides": 1.29}}
    lam = (1.2, 1.2, 2.4)
    p_hi, _ = qmodel.price_question(
        "Will Tunisia be caught offside 2 or more times?", "Tunisia", "Haiti", rates, lam)
    p_lo, _ = qmodel.price_question(
        "Will Haiti be caught offside 2 or more times?", "Tunisia", "Haiti", rates, lam)
    assert p_hi > p_lo   # higher counted rate -> higher prob
    assert 0.01 <= p_lo <= 0.99 and 0.01 <= p_hi <= 0.99


def test_qmodel_first_goal_combo():
    rates = {"_tournament": {}}
    # strong favourite (home) more likely to score first; combo < either leg
    p, reason = qmodel.price_question(
        "Will Brazil score the first goal of the game and Mexico score in the "
        "second half?", "Brazil", "Mexico", rates, (1.9, 0.8, 2.7))
    assert "firstgoal" in reason
    assert 0.01 <= p <= 0.5      # a conjunction of two sub-certain events
    # favourite scoring first beats underdog scoring first
    p_fav, _ = qmodel.price_question(
        "Will Brazil score the first goal of the game and Mexico score in the "
        "second half?", "Brazil", "Mexico", rates, (1.9, 0.8, 2.7))
    p_dog, _ = qmodel.price_question(
        "Will Mexico score the first goal of the game and Brazil score in the "
        "second half?", "Brazil", "Mexico", rates, (1.9, 0.8, 2.7))
    assert p_fav > p_dog


def test_qmodel_returns_none_for_unhandled():
    assert qmodel.price_question("Will Spain win the match?", "Spain", "Italy",
                                 {"_tournament": {}}, (1.5, 1.0, 2.5)) is None


def test_kalshi_match_code_spans_utc_offset():
    import kalshi_wc
    # late-UTC kickoff (02:00Z Jun18) must also yield the US-local Jun17 code
    codes = kalshi_wc.match_code("Uzbekistan", "Colombia", "2026-06-18T02:00:00Z")
    assert "26JUN17UZBCOL" in codes      # date-1 (US local)
    assert "26JUN18UZBCOL" in codes      # date (UTC)
    assert "26JUN17COLUZB" in codes      # away-first order too
    assert kalshi_wc.match_code("Nowhere", "Atlantis", "2026-06-18") == []


def test_kalshi_price_question_from_book():
    import kalshi_wc
    book = {"corners": {9: 0.60}, "totals": {3: 0.525, 4: 0.305},
            "soa": {"eldorshomurodov": 0.14}}
    p, r = kalshi_wc.price_question("Will there be 9 or more total corner kicks?", book)
    assert abs(p - 0.60) < 1e-9 and "corners" in r
    p, r = kalshi_wc.price_question("Will the match have 3 or more total goals?", book)
    assert abs(p - 0.525) < 1e-9
    p, r = kalshi_wc.price_question("Will the match have 3 or fewer total goals?", book)
    assert abs(p - (1 - 0.305)) < 1e-9    # 3 or fewer == 1 - P(4+)
    p, r = kalshi_wc.price_question("Will Eldor Shomurodov score or assist a goal?", book)
    assert abs(p - 0.14) < 1e-9
    assert kalshi_wc.price_question("Will Spain win the match?", book) is None


def test_forecast_combine_kalshi():
    import forecast as F
    # book only (no Kalshi): unchanged, w=1.0
    rec, pre, w, reason, bps = F.combine_kalshi(0.50, None)
    assert rec == 0.50 and pre == 0.50 and w == 1.0 and reason is None
    # rescue (no book): Kalshi is sole source, w=0.0
    rec, pre, w, reason, bps = F.combine_kalshi(None, 0.60)
    assert rec == 0.60 and pre == 0.60 and w == 0.0 and "kalshi only" in reason
    # blend: book primary, nudged toward Kalshi by w
    rec, pre, w, reason, bps = F.combine_kalshi(0.40, 0.60, w=0.35)
    assert rec == 0.40 and abs(pre - (0.65*0.40 + 0.35*0.60)) < 1e-9
    assert w == 0.65 and "blend" in reason and bps == round((pre-0.40)*10000)
    # both None handled by caller (skip) — combine assumes at least one present

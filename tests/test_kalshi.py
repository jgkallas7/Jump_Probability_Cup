"""Kalshi reader: accent folding + half-total / score-or-assist pricing.

price_question is pure given a pre-fetched `book`, so these need no network."""
import kalshi_wc as kw


def test_norm_name_folds_accents():
    # the bug these guard: '[^a-z]' alone drops the umlaut entirely, so the
    # question key and Kalshi's ascii subtitle key diverged and never matched.
    assert kw._norm_name("Viktor Gyökeres") == kw._norm_name("Viktor Gyokeres")
    assert kw._norm_name("Darwin Núñez") == "darwinnunez"
    assert kw._norm_name("Orkun Kökçü") == kw._norm_name("Orkun Kokcu")


def test_score_or_assist_matches_accented_name():
    book = {"soa": {kw._norm_name("Viktor Gyokeres"): 0.41}}
    res = kw.price_question(
        "Will Viktor Gyökeres score or assist a goal (excluding own goals)?", book)
    assert res is not None and abs(res[0] - 0.41) < 1e-9


def test_half_total_more_reads_ladder():
    # ticker '...-2' == 'Over 1.5 goals' == P(2+). 'N or more' reads ladder[N].
    book = {"h2tot": {1: 0.78, 2: 0.45, 3: 0.20}}
    res = kw.price_question(
        "Will the second half have 2 or more total goals?", book)
    assert res is not None and abs(res[0] - 0.45) < 1e-9
    assert "2H" in res[1]


def test_half_total_fewer_is_complement():
    # 'N or fewer' == 1 - P(N+1 or more)
    book = {"h1tot": {1: 0.80, 2: 0.50, 3: 0.22}}
    res = kw.price_question(
        "Will the first half have 2 or fewer total goals?", book)
    assert res is not None and abs(res[0] - (1 - 0.22)) < 1e-9
    assert "1H" in res[1]


def test_half_total_absent_when_ladder_missing():
    # flag OFF -> match_book(extras=False) never populates h1tot/h2tot, so the
    # branch must return None and the pipeline falls back to the book consensus.
    assert kw.price_question(
        "Will the second half have 2 or more total goals?", {}) is None

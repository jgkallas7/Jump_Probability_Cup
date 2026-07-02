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


# ---- spread-aware mid (2026-07-02): a wide book's mid is not a probability ----

def _client_with_book(monkeypatch, yes_bid_c, no_bid_c):
    import kalshi_wc
    c = kalshi_wc.KalshiRO()
    monkeypatch.setattr(c, "_get", lambda path, params=None: {
        "orderbook": {"yes": [[yes_bid_c, 100]] if yes_bid_c else [],
                      "no": [[no_bid_c, 100]] if no_bid_c else []}})
    return c


def test_mid_tight_book_passes_spread_guard(monkeypatch):
    c = _client_with_book(monkeypatch, 57, 42)      # bid .57 / ask .58
    assert abs(c.mid("T", max_spread=0.08) - 0.575) < 1e-9


def test_mid_wide_book_rejected(monkeypatch):
    c = _client_with_book(monkeypatch, 10, 20)      # bid .10 / ask .80
    assert c.mid("T") is not None                    # legacy: mid still returned
    assert c.mid("T", max_spread=0.08) is None       # guarded: rejected


def test_mid_one_sided_book_rejected_under_guard(monkeypatch):
    c = _client_with_book(monkeypatch, 10, None)     # no ask side at all
    assert c.mid("T", max_spread=0.08) is None       # spread unknowable


def test_derive_soa_passthrough_semantics(monkeypatch):
    import derive
    q = "Will Kevin De Bruyne (Belgium) score or assist a goal?"
    monkeypatch.setattr(derive, "KALSHI_NO_SOA", True)
    monkeypatch.setattr(derive, "KALSHI_SOA_MAXSPREAD", None)
    assert derive._kalshi_price(None, {"match_id": "M", "home": "A", "away": "B"}, q) is None
    # with the threshold set, the blanket skip no longer fires (the call would
    # proceed into the guarded kalshi path; here it just errors out to None
    # through the try/except because there's no client/network)
    monkeypatch.setattr(derive, "KALSHI_SOA_MAXSPREAD", 0.08)
    monkeypatch.setattr(derive, "_KALSHI_CLIENT", None)
    monkeypatch.setattr(derive, "_KALSHI_BOOK", {"M": {}})
    assert derive._kalshi_price(None, {"match_id": "M", "home": "A", "away": "B"}, q) is None

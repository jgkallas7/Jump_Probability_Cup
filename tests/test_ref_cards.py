"""WC_REF_CARDS: referee cards multiplier on non-book card-level lambdas.

Scope contract: applies ONLY where no book cards line exists (base fallback +
qmodel counted rates); the derived tier and all races are untouched. Ref
unknown -> exact no-op. Name matching folds accents and keys on
(lastname, first-initial) — API-Football stores 'J. Valenzuela', FBref
'Jesús Valenzuela'.
"""
from datetime import datetime, timezone

import db as dbmod
import derive
import qmodel
import ref_rates


def test_ref_key_matches_across_sources():
    assert ref_rates._ref_key("Jesús Valenzuela") == ("valenzuela", "j")
    assert ref_rates._ref_key("J. Valenzuela") == ("valenzuela", "j")
    assert ref_rates._ref_key("Slavko Vinčič") == ("vincic", "s")
    assert ref_rates._ref_key("single") is None


def _fake_tables(monkeypatch, rate, n, mean=4.0):
    monkeypatch.setattr(ref_rates, "_ref_table",
                        {("valenzuela", "j"): (rate, n)})
    monkeypatch.setattr(ref_rates, "_comp_mean", mean)
    monkeypatch.setattr(ref_rates, "_schedule",
                        [("2026-07-02", ref_rates._team_norm("Spain"),
                          ref_rates._team_norm("Austria"), "Jesús Valenzuela")])


def test_multiplier_shrinks_and_clamps(monkeypatch):
    # card-happy ref, decent sample: 6/match vs mean 4 -> raw 1.5, w=10/16
    _fake_tables(monkeypatch, 6.0, 10.0)
    mult, ref = ref_rates.cards_multiplier("Spain", "Austria", "2026-07-02T19:00:00Z")
    assert ref == "Jesús Valenzuela"
    assert abs(mult - (1 + (10 / 16) * 0.5)) < 1e-9      # 1.3125, inside clamp
    # extreme ref clamps at 1.35
    _fake_tables(monkeypatch, 12.0, 50.0)
    mult, _ = ref_rates.cards_multiplier("Spain", "Austria", "2026-07-02T19:00:00Z")
    assert mult == 1.35


def test_unknown_ref_is_noop(monkeypatch):
    monkeypatch.setattr(ref_rates, "_schedule", [])
    assert ref_rates.cards_multiplier("Spain", "Austria", "2026-07-02") == (1.0, None)


def test_qmodel_total_cards_scales_with_ref():
    rates = {"_tournament": {"cards": 2.0}}
    lam = (1.3, 1.3, 2.6)
    q = "Will there be 4 or more total cards shown in regulation?"
    p_flat = qmodel.price_question(q, "Spain", "Austria", rates, lam)[0]
    p_ref = qmodel.price_question(q, "Spain", "Austria",
                                  {**rates, "_ref_cards": 1.3}, lam)[0]
    assert p_ref > p_flat                     # card-happy ref raises P(4+)
    # absent key = exact old behaviour
    assert qmodel.price_question(q, "Spain", "Austria", rates, lam)[0] == p_flat


def test_cards_lambda_base_tier_only(monkeypatch):
    conn = dbmod.init(":memory:")
    now = datetime.now(timezone.utc)
    m = {"match_id": "M1", "home": "Spain", "away": "Austria"}
    monkeypatch.setattr(derive, "REF_CARDS_ON", True)
    monkeypatch.setattr(derive, "_REF_MULT", {"M1": (1.3, "J. Valenzuela")})
    lam, tier = derive.cards_lambda(conn, m, now)     # no book line -> base
    assert tier == "base" and abs(lam - derive.BASE["cards_lambda"] * 1.3) < 1e-9
    monkeypatch.setattr(derive, "REF_CARDS_ON", False)
    lam0, _ = derive.cards_lambda(conn, m, now)
    assert lam0 == derive.BASE["cards_lambda"]        # flag off = no-op

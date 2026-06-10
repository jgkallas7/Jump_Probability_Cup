"""De-vig math tests: ported invariants + new 3-way/binary behavior."""

import math

import pytest

from devig import (
    DIVERGENCE_FLAG_PTS,
    american_to_prob,
    decimal_to_prob,
    devig_probs,
    devig_three_way,
    shrink_extremes,
)


def test_all_methods_sum_to_one():
    raw = [american_to_prob(a) for a in ["+1600", "-750", "+900"]]
    for method in ("multiplicative", "additive", "power", "shin"):
        fair = devig_probs(raw, method)
        assert abs(sum(fair) - 1.0) < 1e-6, method


def test_power_handles_large_field():
    raw = [american_to_prob(a) for a in ["+250", "+400", "+600", "+800", "+1200", "+2000"]]
    fair = devig_probs(raw, "power")
    assert abs(sum(fair) - 1.0) < 1e-6
    assert all(0 < p < 1 for p in fair)


def test_odds_conversions():
    assert abs(decimal_to_prob(2.0) - 0.5) < 1e-9
    assert abs(decimal_to_prob(4.0) - 0.25) < 1e-9
    assert abs(american_to_prob("+100") - 0.5) < 1e-9
    assert abs(american_to_prob("-200") - 2 / 3) < 1e-9
    with pytest.raises(ValueError):
        decimal_to_prob(1.0)


def test_three_way_known_prices():
    # Pinnacle-style WC group match: home 2.50, draw 3.10, away 3.20
    raw = [decimal_to_prob(2.50), decimal_to_prob(3.10), decimal_to_prob(3.20)]
    tw = devig_three_way(*raw)
    assert tw.overround == pytest.approx(sum(raw))
    assert tw.overround > 1.0
    assert tw.home + tw.draw + tw.away == pytest.approx(1.0, abs=1e-6)
    assert tw.home_mult + tw.draw_mult + tw.away_mult == pytest.approx(1.0, abs=1e-6)
    # favorite stays favorite, ordering preserved
    assert tw.home > tw.draw and tw.home > tw.away


def test_power_shrinks_longshots_more_than_mult():
    # Heavy favorite vs longshot: power assigns the longshot LESS than
    # multiplicative (favorite-longshot bias correction).
    raw = [decimal_to_prob(1.20), decimal_to_prob(7.00), decimal_to_prob(15.00)]
    tw = devig_three_way(*raw)
    assert tw.away < tw.away_mult          # longshot shrunk
    assert tw.home > tw.home_mult          # favorite boosted


def test_divergence_flag_on_skewed_market():
    # Big favorite + high vig: methods disagree enough to flag.
    raw = [decimal_to_prob(1.12), decimal_to_prob(9.00), decimal_to_prob(26.00)]
    tw = devig_three_way(*raw)
    assert tw.divergence_pts > 0
    # Balanced low-vig market: methods nearly agree, no flag.
    even = [decimal_to_prob(2.95), decimal_to_prob(3.05), decimal_to_prob(3.00)]
    tw_even = devig_three_way(*even)
    assert tw_even.divergence_pts < DIVERGENCE_FLAG_PTS
    assert not tw_even.flagged


def test_binary_mapping_no_includes_draw():
    raw = [decimal_to_prob(2.50), decimal_to_prob(3.10), decimal_to_prob(3.20)]
    tw = devig_three_way(*raw)
    yes_home = tw.yes_prob("home")
    # NO on "Will home win?" must be draw + away, not renormalized 2-way
    assert 1.0 - yes_home == pytest.approx(tw.draw + tw.away, abs=1e-9)
    # the draw never disappears
    assert tw.draw > 0.2


def test_shrink_extremes():
    # contest submissions are integers 1-99 -> floor/ceiling 0.01/0.99
    assert shrink_extremes(0.0) == 0.01
    assert shrink_extremes(1.0) == 0.99
    assert shrink_extremes(0.5) == 0.5

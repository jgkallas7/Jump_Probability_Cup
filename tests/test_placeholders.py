"""Placeholder family base rates (recalibrated 2026-06-29 from settled data)."""

from placeholders import placeholder_for


def test_player_one_plus_sot_no_line_uses_realized_base():
    # "at least 1 shot on target" (no book line) resolved YES ~0.23 over n=39 settled
    # — must NOT get the old flat 0.45/0.55; the over-pricing field makes 0.25 an edge.
    p, _ = placeholder_for("Will Julio Enciso have at least 1 shot on target?")
    assert p == 0.25


def test_player_two_plus_sot_below_one_plus():
    # "2 or more shots on target" must be monotonically below the 1+ rate (was 0.55)
    p, _ = placeholder_for("Will Harry Kane have 2 or more shots on target?")
    assert p == 0.15
    assert p < 0.25


def test_player_one_or_more_shots_plural_is_not_the_2plus_rate():
    # regression: "1 or more shots on target" (plural) was wrongly hitting the 2+
    # rule (0.15); it is a 1+ question and must get the 0.25 single-SOT rate.
    p, _ = placeholder_for("Will Julio Enciso have 1 or more shots on target "
                           "in regulation?")
    assert p == 0.25


def test_any_player_sot_brace_backstop_is_high():
    # "any player record 2+ SOT" is near-certain (field ~0.70), not the 0.50 generic
    p, _ = placeholder_for("Will any player record 2 or more shots on target?")
    assert p == 0.72


def test_unclassified_catch_all_recalibrated():
    # residual (non-SOT) catch-all realized ~0.34 over n=32 — was a too-high 0.45
    p, why = placeholder_for("Will a substitute score a goal in regulation?")
    assert p == 0.35 and "fallback" in why


def test_known_families_unchanged():
    # the data-stable families we did NOT touch stay put
    assert placeholder_for("Will X score a goal (excluding own goals)?")[0] == 0.18
    assert placeholder_for("Will both teams have at least 1 shot on target?")[0] == 0.85

"""WC_SOT_THRESH_ANCHOR: SOT-threshold base anchor (derive._apply_sot_anchor).

The flag pulls 'N-or-more shots on target' alpha prices toward a base rate (our
raw Poisson survival under-prices them), while leaving the level-invariant
team-vs-team SOT race untouched. These lock in detection + blend arithmetic so a
refactor can't silently re-anchor the race or flip the no-op-when-off contract.
"""
import derive


THRESHOLD_TEXTS = [
    "Will there be 4 or more total shots on target in the second half?",
    "Will Iraq have 2 or more shots on target in the second half?",
    "Will there be 8 or more total shots on target?",
]
RACE_TEXTS = [
    "Will Austria have more shots on target than Argentina in the second half?",
    "In the second half, will Tunisia have more shots on target than Japan?",
]
# 'both teams >=1 SOT' is a SOT count question but is ALREADY anchored to ~0.68 in
# qmodel.py — the flag must NOT touch it (double-anchoring), so it's out of scope.
ALREADY_ANCHORED_TEXTS = [
    "At halftime, will both teams have at least 1 shot on target?",
    "Will both teams have at least 1 shot on target?",
]
NON_SOT_TEXTS = [
    "Will Austria be caught offside 2 or more times?",
    "Will there be 10 or more total corners?",
]


def test_detects_thresholds_excludes_race_anchored_and_non_sot():
    assert all(derive.is_sot_threshold(t) for t in THRESHOLD_TEXTS)
    assert not any(derive.is_sot_threshold(t) for t in RACE_TEXTS)
    assert not any(derive.is_sot_threshold(t) for t in ALREADY_ANCHORED_TEXTS)
    assert not any(derive.is_sot_threshold(t) for t in NON_SOT_TEXTS)


def test_both_teams_untouched_when_on(monkeypatch):
    # regression: live anchor scope must match the gate's — never re-anchor the
    # already-calibrated 'both teams >=1 SOT' price even with the flag ON.
    monkeypatch.setattr(derive, "SOT_THRESH_ANCHOR_ON", True)
    res = (0.68, "qmodel", "both>=1 SOT 1.90/1.40 raw=0.71")
    assert derive._apply_sot_anchor(ALREADY_ANCHORED_TEXTS[0], res) == res


def test_off_is_a_no_op(monkeypatch):
    monkeypatch.setattr(derive, "SOT_THRESH_ANCHOR_ON", False)
    res = (0.40, "qmodel", "total SOT counted lam=3.0")
    assert derive._apply_sot_anchor(THRESHOLD_TEXTS[0], res) == res


def test_blend_arithmetic_on_threshold(monkeypatch):
    monkeypatch.setattr(derive, "SOT_THRESH_ANCHOR_ON", True)
    monkeypatch.setattr(derive, "SOT_ANCHOR", 0.65)
    monkeypatch.setattr(derive, "SOT_BETA", 0.5)
    prob, tier, reason = derive._apply_sot_anchor(
        "Will Iraq have 2 or more shots on target in the second half?",
        (0.59, "anchored", "team sot lam=2.0"))
    assert abs(prob - (0.5 * 0.59 + 0.5 * 0.65)) < 1e-9   # -> 0.62
    assert tier == "anchored"          # tier preserved
    assert "sotanchor" in reason       # provenance recorded


def test_race_untouched_when_on(monkeypatch):
    monkeypatch.setattr(derive, "SOT_THRESH_ANCHOR_ON", True)
    res = (0.43, "qmodel", "SOT race Skellam 2.1 vs 2.4")
    assert derive._apply_sot_anchor(RACE_TEXTS[0], res) == res


def test_none_price_is_passthrough(monkeypatch):
    monkeypatch.setattr(derive, "SOT_THRESH_ANCHOR_ON", True)
    assert derive._apply_sot_anchor(THRESHOLD_TEXTS[0], None) is None

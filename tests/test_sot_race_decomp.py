"""WC_SOT_RACE_DECOMP: 2H SOT-race de-compression (derive._apply_race_decomp).

The matchday-3 locked-email harvest falsified the old "compress toward 0.5 vs an
over-dispersed field" thesis for the team-vs-team SOT race: the field is well
calibrated and our double-damped price is squashed toward 0.5 on both sides. This
flag de-compresses the final race price away from 0.5 by gamma. These lock in the
scope predicate, the no-op-when-off contract, the blend arithmetic, the clip, and
the mutual exclusivity with the SOT-threshold anchor so a refactor can't silently
de-compress a threshold or double-fire both post-processors.
"""
import derive


RACE_TEXTS = [
    "Will Austria have more shots on target than Argentina in the second half?",
    "In the second half, will Tunisia have more shots on target than Japan?",
]
# Must NOT be de-compressed: thresholds (anchored elsewhere), both-teams, non-SOT,
# and a FULL-MATCH "more than" comparison (the contest only ever asks the 2H race,
# but the predicate must still gate on 'second half' so it can't bleed).
NOT_RACE_TEXTS = [
    "Will Iraq have 2 or more shots on target in the second half?",
    "Will both teams have at least 1 shot on target?",
    "Will Austria be caught offside 2 or more times?",
    "Will Spain have more corner kicks than Cape Verde in the second half?",
]


def test_detects_only_the_2h_sot_race():
    assert all(derive.is_sot_race(t) for t in RACE_TEXTS)
    assert not any(derive.is_sot_race(t) for t in NOT_RACE_TEXTS)


def test_scopes_are_mutually_exclusive():
    # the two chained post-processors must never both fire on one question
    for t in RACE_TEXTS + NOT_RACE_TEXTS:
        assert not (derive.is_sot_race(t) and derive.is_sot_threshold(t))


def test_gamma_one_is_a_no_op(monkeypatch):
    monkeypatch.setattr(derive, "RACE_DECOMP", 1.0)
    res = (0.43, "qmodel", "SOT race Skellam 4.25 vs 4.25")
    assert derive._apply_race_decomp(RACE_TEXTS[0], res) == res


def test_decompresses_away_from_half(monkeypatch):
    monkeypatch.setattr(derive, "RACE_DECOMP", 1.5)
    # favorite at 0.58 -> 0.5 + 1.5*0.08 = 0.62 (pushed UP, toward the field)
    prob, tier, reason = derive._apply_race_decomp(
        RACE_TEXTS[0], (0.58, "anchored", "sot race share=0.60 damped0.6"))
    assert abs(prob - (0.5 + 1.5 * (0.58 - 0.5))) < 1e-9   # 0.62
    assert tier == "anchored"               # tier preserved
    assert "racedecomp" in reason           # provenance recorded
    # underdog at 0.42 -> 0.38 (pushed DOWN, also away from 0.5)
    p2, _, _ = derive._apply_race_decomp(RACE_TEXTS[0], (0.42, "anchored", "x"))
    assert abs(p2 - (0.5 + 1.5 * (0.42 - 0.5))) < 1e-9     # 0.38


def test_clips_to_floor_and_ceiling(monkeypatch):
    monkeypatch.setattr(derive, "RACE_DECOMP", 3.0)
    hi, _, _ = derive._apply_race_decomp(RACE_TEXTS[0], (0.80, "t", "r"))  # ->1.4
    lo, _, _ = derive._apply_race_decomp(RACE_TEXTS[0], (0.20, "t", "r"))  # ->-0.4
    assert hi == 0.99 and lo == 0.01


def test_non_race_untouched_even_when_on(monkeypatch):
    monkeypatch.setattr(derive, "RACE_DECOMP", 1.5)
    res = (0.40, "qmodel", "total SOT counted lam=3.0")
    assert derive._apply_race_decomp(NOT_RACE_TEXTS[0], res) == res


def test_none_price_is_passthrough(monkeypatch):
    monkeypatch.setattr(derive, "RACE_DECOMP", 1.5)
    assert derive._apply_race_decomp(RACE_TEXTS[0], None) is None


def test_goal_share_routing_gate(monkeypatch):
    # WC_SOT_RACE_GS routes ONLY the race past qmodel (to the goal-share pricer);
    # when off, qmodel still prices everything; never touches non-race questions.
    monkeypatch.setattr(derive, "RACE_GS_ON", False)
    assert not any(derive._route_race_to_goal_share(t) for t in RACE_TEXTS)
    monkeypatch.setattr(derive, "RACE_GS_ON", True)
    assert all(derive._route_race_to_goal_share(t) for t in RACE_TEXTS)
    assert not any(derive._route_race_to_goal_share(t) for t in NOT_RACE_TEXTS)

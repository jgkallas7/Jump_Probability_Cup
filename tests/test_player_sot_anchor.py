"""WC_PLAYER_SOT_ANCHOR: player '>=1 shot on target' over-pricing anchor
(forecast.is_player_sot_over + the blend applied in forecast.run).

The book + field both over-price player ">=1 SOT" props; the flag shades them
DOWN toward a base rate. These lock in the scope predicate so the flag never
touches team SOT totals (which share the bucket) or higher-N lines, and so the
team-subject exclusion works for both the live (two-team) and gate (all-team)
call styles.
"""
import forecast

MATCH_TEAMS = ("Scotland", "Morocco")
ALL_TEAMS = {"Scotland", "Morocco", "Czechia", "Germany", "Argentina", "Austria"}


def test_detects_player_over_one():
    assert forecast.is_player_sot_over(
        "Will Patrik Schick have at least 1 shot on target?", ("Czechia", "Germany"))
    # works with the offline gate's all-teams set too (Schick is not a team)
    assert forecast.is_player_sot_over(
        "Will Patrik Schick have at least 1 shot on target?", ALL_TEAMS)


def test_excludes_team_subject():
    # team SOT total sharing the bucket — subject is one of the match's teams
    assert not forecast.is_player_sot_over(
        "Will Scotland have 3 or more shots on target?", MATCH_TEAMS)
    # a real team '>=1' line (e.g. "Will Haiti have at least 1 SOT in the 2H")
    # must be excluded — a team's >=1 SOT is ~0.8, the opposite of the 0.30 anchor
    assert not forecast.is_player_sot_over(
        "Will Scotland have at least 1 shot on target?", MATCH_TEAMS)
    # gate style: all-teams set excludes any team subject
    assert not forecast.is_player_sot_over(
        "Will Morocco have at least 1 shot on target?", ALL_TEAMS)


def test_excludes_aliased_team_subject():
    # alias divergence: SP text 'Türkiye'/'United States' vs Odds-API 'Turkey'/'USA'.
    # Must still be recognized as a team and NOT shaded down (was a real bug).
    assert not forecast.is_player_sot_over(
        "Will Türkiye have at least 1 shot on target?", {"Turkey", "Paraguay"})
    assert not forecast.is_player_sot_over(
        "Will United States have at least 1 shot on target?", {"USA", "Paraguay"})
    # a genuine player is still anchored even if a teammate shares the match
    assert forecast.is_player_sot_over(
        "Will Christian Pulisic have at least 1 shot on target?", {"USA", "Paraguay"})


def test_excludes_higher_thresholds():
    # only the validated '>=1' line is in scope; 2+/3+ player lines are not
    assert not forecast.is_player_sot_over(
        "Will Lionel Messi have 2 or more shots on target?", ("Argentina", "Austria"))


def test_blend_is_downward_and_preserves_ordering():
    # the arithmetic the flag applies in run(): (1-beta)*final + beta*anchor
    anchor, beta = forecast.PLAYER_SOT_ANCHOR, forecast.PLAYER_SOT_BETA
    blend = lambda p: (1 - beta) * p + beta * anchor
    # an over-priced 0.49 prop is pulled down toward the anchor...
    assert blend(0.49) < 0.49
    # ...but a high-signal striker still prices above a role player (ordering kept)
    assert blend(0.70) > blend(0.35)

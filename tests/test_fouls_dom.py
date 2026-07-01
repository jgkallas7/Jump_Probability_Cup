"""WC_FOULS_DOM: game-state tilt on the fouls_race Skellam (qmodel._foul_dom).

The fouls_race bucket ("Will X commit more fouls than Y?") was the single biggest
alpha leak vs the field (-128 pts / 55 settled Qs). Root cause: qmodel priced it
from each team's OWN counted foul rate, which is ~symmetric, so a clear
favourite/underdog matchup landed ~0.46 (the Skellam tie split) regardless of who
was favoured. But the underdog reliably commits MORE fouls (less possession, more
chasing) — a game-state effect the crowd prices and we ignored. WC_FOULS_DOM tilts
the foul rates by market goal-share: underdog up, favourite down. These lock in the
no-op-when-off contract, the direction, monotonicity, and the _foul_dom centring so
a refactor can't silently flip the sign or leak the tilt when the flag is off.
"""
import qmodel

# symmetric base foul rate for both teams -> the tilt is the ONLY differentiator
RATES = {"_tournament": {"fouls": 13.0}}
# Spain a clear favourite (goal-lambda 2.4) over Malta (0.8); Malta is the underdog
FAV_LAM = (2.4, 0.8, 3.2)   # (home=Spain, away=Malta, total)
UNDERDOG_Q = "Will Malta commit more fouls than Spain?"
FAVOURITE_Q = "Will Spain commit more fouls than Malta?"


def _price(text, slope, monkeypatch):
    monkeypatch.setattr(qmodel, "FOUL_DOM_SLOPE", slope)
    return qmodel.price_question(text, "Spain", "Malta", RATES, FAV_LAM)[0]


def test_off_is_symmetric_no_op(monkeypatch):
    # slope 0 -> flat multipliers -> a lopsided matchup still prices ~0.46 (the
    # symmetric Skellam tie split), exactly the pre-fix production behaviour.
    p = _price(UNDERDOG_Q, 0.0, monkeypatch)
    assert abs(p - 0.46) < 0.05


def test_underdog_priced_above_favourite(monkeypatch):
    p_underdog = _price(UNDERDOG_Q, 0.20, monkeypatch)
    p_favourite = _price(FAVOURITE_Q, 0.20, monkeypatch)
    assert p_underdog > 0.5 > p_favourite
    # complementary up to the Skellam tie mass (both can't-be-more-than each other)
    assert 0.85 < p_underdog + p_favourite < 1.0


def test_tilt_monotonic_in_slope(monkeypatch):
    assert (_price(UNDERDOG_Q, 0.0, monkeypatch)
            < _price(UNDERDOG_Q, 0.15, monkeypatch)
            < _price(UNDERDOG_Q, 0.30, monkeypatch))


def test_foul_dom_helper_direction(monkeypatch):
    monkeypatch.setattr(qmodel, "FOUL_DOM_SLOPE", 0.20)
    assert qmodel._foul_dom(0.8, 2.4) > 1.0    # underdog (goal-share .25) fouls more
    assert qmodel._foul_dom(2.4, 0.8) < 1.0    # favourite (goal-share .75) fouls less
    assert qmodel._foul_dom(1.5, 1.5) == 1.0   # parity -> centred at 1.0
    assert qmodel._foul_dom(0.0, 0.0) == 1.0   # zero-total guard


def test_other_buckets_untouched_by_flag(monkeypatch):
    # the tilt must only reach the fouls_race branch, not e.g. offsides
    monkeypatch.setattr(qmodel, "FOUL_DOM_SLOPE", 0.20)
    off = qmodel.price_question("Will Spain be caught offside 2 or more times?",
                                "Spain", "Malta", RATES, FAV_LAM)
    assert off is not None and off[1].startswith("offsides")

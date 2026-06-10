"""parse_games against a real GetSchedule shape (captured via the cockpit's
/book-fv/raw-game diagnostic, Mexico vs South Africa opener, 2026-06-10)."""

from bookmaker_client import parse_games


def _payload(lines, htm="Mexico", vtm="South Africa"):
    return {"Schedule": {"Data": {"Leagues": {"League": {
        "IdLeague": "12641", "Description": "MATCHES", "IdSport": "TNT",
        "dateGroup": {"game": {
            "idgm": "9001", "htm": htm, "vtm": vtm,
            "gmdt": "2026-06-11", "gmtm": "19:00",
            "Derivatives": {"line": lines},
        }},
    }}}}}


REAL_LINES = [
    {"s_sp": 1, "s_tot": 1, "s_ml": 0, "ovoddst": "-242", "ovt": "1.5",
     "unoddst": "188", "unt": "1.5"},
    {"s_sp": 1, "s_tot": 1, "s_ml": 1, "hoddst": "-250", "drawoddst": "371",
     "voddst": "782", "ovoddst": "-101", "ovt": "2.25", "unoddst": "-116",
     "unt": "2.25"},
    {"s_sp": 1, "s_tot": 1, "s_ml": 0, "ovoddst": "126", "ovt": "2.5",
     "unoddst": "-152", "unt": "2.5"},
]


def test_real_shape_moneyline_and_totals():
    games = parse_games(_payload(REAL_LINES))
    assert len(games) == 1
    g = games[0]
    assert g["kind"] == "match"
    assert g["event_label"] == "South Africa vs Mexico"
    sels = {s["label"]: s["american"] for s in g["selections"]}
    assert sels == {"Mexico": "-250", "Draw": "371", "South Africa": "782"}
    points = {t["point"]: (t["over"], t["under"]) for t in g["totals"]}
    assert points[2.5] == ("126", "-152")
    assert len(points) == 3


def test_missing_draw_skips_moneyline_keeps_totals():
    lines = [{"s_ml": 1, "hoddst": "-250", "voddst": "782",
              "ovoddst": "126", "ovt": "2.5", "unoddst": "-152", "unt": "2.5"}]
    games = parse_games(_payload(lines))
    # no 3-way -> no moneyline selections (never devig 2-of-3), totals survive
    assert len(games) == 1
    assert games[0]["selections"] == []
    assert len(games[0]["totals"]) == 1


def test_single_object_vs_array_coercion():
    # ASP.NET single-child-is-object quirk is already exercised above
    # (League/dateGroup/game all single objects); empty response is graceful.
    assert parse_games({}) == []

"""Question ingest: SportsPredict lobby -> questions table.

- joins the Probability Cup lobby (idempotent, 409 = already in)
- maps SP matches to our Odds API matches by normalized team names
- classifies each question text -> qtype + book market_mapping
  (NO_MARKET = alpha question, priced from base rates)

Usage: python ingest_questions.py
"""

from __future__ import annotations

import re
import sys
import unicodedata

import requests

import db
from sp_client import SPClient

QTYPE_RULES: list[tuple[str, str, str]] = [
    # (regex on question text, qtype, market_mapping) — first hit wins.
    # Order matters: specific prop patterns before the broad match-result one.
    # Priced props (per opener discovery: Pinnacle quotes cards/corners
    # spreads; half-qualified comparisons have no book market).
    (r"(halftime|first half|second half).*(corner|card)|"
     r"(corner|card).*(first half|second half)", "prop", "NO_MARKET"),
    (r"(receive|be shown) more cards than", "cards_spread", "alternate_spreads_cards"),
    (r"(have|finish with) more corner kicks than", "corners_spread", "alternate_spreads_corners"),
    # Match (not team, not half) corner/card totals: books quote these as
    # match Over/Under (alternate_totals_corners/_cards, 8/5 books) — we already
    # snapshot them, so price from consensus instead of base-rate. Half-qualified
    # variants were caught above (no 2nd-half totals market); team totals
    # ("<team> have N corners") have no book market and fall through to NO_MARKET.
    (r"\d+ or more total corner kicks", "corners_total", "alternate_totals_corners"),
    (r"\d+ or more total cards", "cards_total", "alternate_totals_cards"),
    (r"(corner|card|booking|foul|penalt|free kick|offside)", "prop", "NO_MARKET"),
    (r"shots? on target.*(first|second) half|"
     r"(halftime|first half|second half).*shots? on target", "prop", "NO_MARKET"),
    (r"will .+ have (at least \d+|\d+ or more) shots? on target", "player_sot",
     "player_shots_on_target"),
    (r"shots? on target", "prop", "NO_MARKET"),
    (r"score or assist", "player_prop", "NO_MARKET"),
    (r"score a goal \(excluding own goals\)", "player_scorer",
     "player_goal_scorer_anytime"),
    (r"(first|opening) goal", "prop", "NO_MARKET"),
    (r"score in the second half", "team_total_h2", "alternate_team_totals_h2"),
    (r"score in the first half", "team_total_h1", "team_totals_h1"),
    (r"at halftime, will .+ be winning|winning at halftime", "result_h1",
     "h2h_3_way_h1"),
    (r"(tied at halftime|at halftime, will the match be tied)", "result_h1",
     "h2h_3_way_h1"),
    (r"score more goals than .+ in the second half", "result_h2",
     "h2h_3_way_h2"),
    (r"both teams .*score and", "btts_combo", "NO_MARKET"),
    (r"both teams .*score", "btts", "btts"),
    (r"clean sheet", "btts", "btts_derived"),
    # Brace: "any player score more than 1 goal" has NO book market — it is NOT a
    # match-goals total. Must precede the generic goal-totals rules below (the
    # "more than ... goal" catch-all was mis-mapping it to totals -> placeholder).
    (r"any player score (more than (1|one)|2 or more) goals?", "brace", "NO_MARKET"),
    (r"(first|second) half .*\d+ or (fewer|less|more) total goals",
     "total_half", "totals_half"),
    (r"score (at least \d+|\d+ or more total) goal", "team_total", "team_totals"),
    (r"\d+ or (fewer|less|more) total goals", "total", "totals"),
    (r"(over|under|more than|fewer than|at least) .*(goal|goals)", "total", "totals"),
    (r"advance|qualify|progress|next round", "advancement", "to_advance"),
    (r"end in a draw|finish in a draw|\bdraw\b", "match_result", "h2h"),
    (r"win the match|win in regulation", "match_result", "h2h"),
]


def classify(text: str) -> tuple[str, str]:
    t = text.lower()
    for pattern, qtype, mapping in QTYPE_RULES:
        if re.search(pattern, t):
            return qtype, mapping
    return "other", "NO_MARKET"


# Stage signal. The schedule feed (Odds API events) carries NO round, so every
# match defaults to 'group' (db.py) — which silently strips calibrate.py's 2x/3x
# knockout multiplier (STAGE_MULTIPLIER) off every knockout game. The contest's own
# advancement question is the reliable signal: it names the round a team advances
# TO, so the match's stage is the round the team is currently IN. Order matters:
# "round of N" before the generic "final" (which also matches "semi-final").
_ADV_TARGET_TO_STAGE = [
    (r"round of 32", "group"),
    (r"round of 16", "r32"),
    (r"quarter[- ]?final", "r16"),
    (r"semi[- ]?final", "qf"),
    (r"\bfinal\b", "sf"),       # advance TO the final => currently in the semis
]


def stage_from_advance(text: str) -> str | None:
    """Knockout stage of a match from its 'Will X advance to <round>?' question,
    or None if not an advancement question / round unrecognised. Returns 'group'
    for a group->R32 advance (left as the default; we only ever PROMOTE to KO)."""
    t = text.lower()
    if not re.search(r"advance|qualify|progress|reach|go through", t):
        return None
    for pat, stage in _ADV_TARGET_TO_STAGE:
        if re.search(pat, t):
            return stage
    return None


def backfill_stages(conn) -> int:
    """Promote matches to their knockout stage from existing advancement questions.
    Idempotent; never downgrades (only writes KO stages, never 'group')."""
    n = 0
    for r in conn.execute("""SELECT match_id, text FROM questions
                             WHERE market_mapping='to_advance'""").fetchall():
        st = stage_from_advance(r["text"])
        if st and st != "group":
            cur = conn.execute(
                "UPDATE matches SET stage=? WHERE match_id=? AND stage!=?",
                (st, r["match_id"], st))
            n += cur.rowcount
    conn.commit()
    return n


def norm_team(s: str) -> str:
    # NFKD first: Curaçao -> Curacao (tracker lesson L022 — stripped accents
    # otherwise silently break equality).
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


# FIFA trigram -> Odds API team name (SP match names are e.g. "MEX vs RSA").
# All 48 qualified teams, from the seeded matches table.
FIFA_CODES: dict[str, str] = {
    "ALG": "Algeria", "ARG": "Argentina", "AUS": "Australia", "AUT": "Austria",
    "BEL": "Belgium", "BIH": "Bosnia & Herzegovina", "BRA": "Brazil",
    "CAN": "Canada", "CPV": "Cape Verde", "COL": "Colombia", "CUW": "Curaçao",
    "CZE": "Czech Republic", "COD": "DR Congo", "ECU": "Ecuador",
    "EGY": "Egypt", "ENG": "England", "FRA": "France", "GER": "Germany",
    "GHA": "Ghana", "HAI": "Haiti", "IRN": "Iran", "IRQ": "Iraq",
    "CIV": "Ivory Coast", "JPN": "Japan", "JOR": "Jordan", "MEX": "Mexico",
    "MAR": "Morocco", "NED": "Netherlands", "NZL": "New Zealand",
    "NOR": "Norway", "PAN": "Panama", "PAR": "Paraguay", "POR": "Portugal",
    "QAT": "Qatar", "KSA": "Saudi Arabia", "SCO": "Scotland",
    "SEN": "Senegal", "RSA": "South Africa", "KOR": "South Korea",
    "ESP": "Spain", "SWE": "Sweden", "SUI": "Switzerland", "TUN": "Tunisia",
    "TUR": "Turkey", "USA": "USA", "URU": "Uruguay", "UZB": "Uzbekistan",
    "CRO": "Croatia", "HRV": "Croatia",
}


def sp_match_teams(sp_name: str) -> tuple[str, str] | None:
    """SP names mix codes and full names: 'MEX vs RSA', 'Haiti vs SCO',
    'GER vs Curacao'. Resolve each side independently."""
    m = re.match(r"\s*(.+?)\s+vs?\.?\s+(.+?)\s*$", sp_name)
    if not m:
        return None

    def side(tok: str) -> str:
        return FIFA_CODES.get(tok, tok) if re.fullmatch(r"[A-Z]{3}", tok) else tok

    return side(m.group(1)), side(m.group(2))


def _migrate(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    try:
        conn.execute("ALTER TABLE matches ADD COLUMN sp_match_id TEXT")
    except Exception:
        pass  # column exists
    conn.commit()


def main() -> None:
    conn = db.init()
    _migrate(conn)
    c = SPClient()

    events = c.events()
    ev = next(e for e in events if "probability" in str(e.get("type", "")).lower()
              or "Probability Cup" in str(e.get("title", "")))
    event_id = ev["id"]

    lobbies = c.lobbies(event_id)
    lobby = lobbies[0]
    lobby_id = lobby["id"]
    if not lobby.get("joined"):
        try:
            print("joining lobby:", c.join_lobby(lobby_id))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                print("already in lobby")
            else:
                raise
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('sp_event_id', ?)", (event_id,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('sp_lobby_id', ?)", (lobby_id,))
    conn.commit()

    sp_matches = c.matches(event_id, lobby_id)
    print(f"{len(sp_matches)} SP matches with open markets")

    # map SP match -> our Odds API match via FIFA trigram codes
    ours = {(norm_team(r["home"]), norm_team(r["away"])): r["match_id"]
            for r in conn.execute("SELECT match_id, home, away FROM matches")}
    sp_to_ours: dict[str, str] = {}
    unmatched = []
    for m in sp_matches:
        teams = sp_match_teams(m.get("name", ""))
        hit = None
        if teams:
            k1 = (norm_team(teams[0]), norm_team(teams[1]))
            hit = ours.get(k1) or ours.get((k1[1], k1[0]))
        if hit:
            sp_to_ours[m["id"]] = hit
            conn.execute("UPDATE matches SET sp_match_id=? WHERE match_id=?",
                         (m["id"], hit))
        else:
            # knockout fixtures appear with placeholder names ('1A vs 2B')
            # before Odds API events exist — create a synthetic match row so
            # questions NEVER orphan with match_id NULL (invisible to every
            # pipeline query). Re-ingest later relinks via sp_match_id.
            synth_id = f"sp:{m['id']}"
            teams = sp_match_teams(m.get("name", "")) or ("TBD", "TBD")
            conn.execute(
                """INSERT INTO matches(match_id, home, away, kickoff_utc,
                     status, sp_match_id) VALUES (?,?,?,?, 'scheduled', ?)
                   ON CONFLICT(match_id) DO UPDATE SET
                     home=excluded.home, away=excluded.away,
                     kickoff_utc=excluded.kickoff_utc""",
                (synth_id, teams[0], teams[1],
                 m.get("opening_time") or m.get("closing_time") or "",
                 m["id"]))
            sp_to_ours[m["id"]] = synth_id
            unmatched.append(m.get("name", ""))
    # relink any synthetic rows once a real Odds API twin exists
    for r in conn.execute("""SELECT m1.match_id synth, m2.match_id real
        FROM matches m1 JOIN matches m2 ON m1.sp_match_id = m2.sp_match_id
        WHERE m1.match_id LIKE 'sp:%' AND m2.match_id NOT LIKE 'sp:%'""").fetchall():
        conn.execute("UPDATE questions SET match_id=? WHERE match_id=?",
                     (r["real"], r["synth"]))
        conn.execute("DELETE FROM matches WHERE match_id=?", (r["synth"],))
    conn.commit()
    if unmatched:
        print(f"WARNING: {len(unmatched)} SP matches had no Odds API twin:",
              unmatched[:8], file=sys.stderr)
    else:
        print("all SP matches mapped to Odds API events")

    # one unfiltered pull (~318 KB, a single request -> kind to the 60/min cap)
    markets = c.markets(lobby_id)
    total_q = 0
    by_mapping: dict[str, int] = {}
    for mk in markets:
        sp_mid = (mk.get("match") or {}).get("id")
        match_ref = sp_to_ours.get(sp_mid)
        qtype, mapping = classify(mk.get("question", ""))
        by_mapping[mapping] = by_mapping.get(mapping, 0) + 1
        # deadline of record = KICKOFF ("markets close in the last second
        # before matches start"); SP's closing_time field is match END.
        kickoff = conn.execute(
            "SELECT kickoff_utc FROM matches WHERE match_id=?",
            (match_ref,)).fetchone() if match_ref else None
        deadline = kickoff["kickoff_utc"] if kickoff \
            else (mk.get("match") or {}).get("closing_time")
        conn.execute(
            """INSERT INTO questions(qid, match_id, qtype, text,
                                     opens_at, deadline, status, market_mapping)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(qid) DO UPDATE SET
                 status=excluded.status, deadline=excluded.deadline,
                 qtype=excluded.qtype, market_mapping=excluded.market_mapping,
                 match_id=excluded.match_id, text=excluded.text""",
            (mk["id"], match_ref, qtype, mk.get("question"),
             (mk.get("match") or {}).get("opening_time"),
             deadline, mk.get("status", "open"), mapping))
        total_q += 1
    conn.commit()

    promoted = backfill_stages(conn)
    if promoted:
        print(f"stage: promoted {promoted} match(es) to a knockout stage "
              f"from advancement questions")

    print(f"\n{total_q} questions ingested")
    print("by mapping:", dict(sorted(by_mapping.items(), key=lambda x: -x[1])))
    print("\nsample questions (first match):")
    for r in conn.execute(
            "SELECT qtype, market_mapping, text FROM questions LIMIT 12"):
        print(f"  [{r['qtype']}/{r['market_mapping']}] {r['text']}")


if __name__ == "__main__":
    main()

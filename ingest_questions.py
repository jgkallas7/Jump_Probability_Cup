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
    (r"(corner|card|booking|foul|penalt|free kick|offside)", "prop", "NO_MARKET"),
    (r"shots? on target", "prop", "NO_MARKET"),
    (r"score or assist|score a goal|assist a goal", "player_prop", "NO_MARKET"),
    (r"(first|opening) goal", "prop", "NO_MARKET"),
    (r"score in the (first|second) half", "team_prop", "NO_MARKET"),
    (r"both teams .*score", "btts", "btts"),
    (r"clean sheet", "btts", "btts_derived"),
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
            unmatched.append(m.get("name", ""))
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
             (mk.get("match") or {}).get("closing_time"),
             mk.get("status", "open"), mapping))
        total_q += 1
    conn.commit()

    print(f"\n{total_q} questions ingested")
    print("by mapping:", dict(sorted(by_mapping.items(), key=lambda x: -x[1])))
    print("\nsample questions (first match):")
    for r in conn.execute(
            "SELECT qtype, market_mapping, text FROM questions LIMIT 12"):
        print(f"  [{r['qtype']}/{r['market_mapping']}] {r['text']}")


if __name__ == "__main__":
    main()

"""Forecast engine: question -> book market -> weighted consensus -> forecast.

For every open, book-mapped question whose match has fresh snapshots:
  1. resolve the question to (market, outcome, point) via its text
  2. weighted consensus of fair_prob across whitelisted books
     (BOOK_WEIGHTS, staleness-filtered on the snapshot batch)
  3. shrink to [PROB_FLOOR, PROB_CEILING], write a forecasts row

Honest probabilities only — final_prob == consensus_prob in v1; any future
deviation REQUIRES deviation_reason (SPEC §6).

Usage:
  python forecast.py [--hours 36]      # forecast + print submission sheet
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import db
from config import (BOOK_WEIGHTS, DEFAULT_BOOK_WEIGHT,
                    THIN_MARKET_EXTRA_WEIGHTS, THIN_MARKET_PREFIXES)
from devig import shrink_extremes

# Kalshi WC crowd-mid blend (gated). Validation (2026-06-16): Kalshi goal-totals
# match the sharp book to 0.6pt (redundant but confirming); corner-totals run
# +5-7pt above book (real divergence) — so Kalshi is a conservative BLEND that
# nudges, never an override. Book stays primary. Guarded: any failure -> no blend.
KALSHI_ON = os.environ.get("WC_KALSHI", "") == "1"
# Half goal-totals blend (KXWC1HTOTAL/2HTOTAL) — targets the losing totals_half
# bucket (-22.7 vs field). Separate flag, default OFF: WC_KALSHI is already live,
# so this new behavior must forward-validate before it changes what we send.
KALSHI_HTOTAL_ON = os.environ.get("WC_KALSHI_HTOTAL", "") == "1"
KALSHI_BLEND_W = 0.35

# Confidence dampener (default OFF, WC_DEVCAP): pull h2h (match-winner / draw)
# submissions toward 0.5 by DEVCAP_BETA. Per-bucket settled test (2026-06-20):
# h2h gains strongly from shrink (our match-outcome confidence runs ahead of the
# realized upset/draw rate: +47 over 27 Qs at heavy shrink), while SOT props and
# the NO_MARKET alpha do NOT — their losses are directional, not overconfidence,
# so shrink hurts them (b=0 best). beta=0.25 is the conservative slice (the in-
# sample optimum ~0.6 overfits a matchday-1 upset run). SHIP OFF — forward-
# validate on settled h2h via parse_locked before enabling in morning.sh.
DEVCAP_ON = os.environ.get("WC_DEVCAP", "") == "1"
DEVCAP_BETA = 0.25
DEVCAP_MARKETS = ("h2h",)

# Player-SOT over-pricing anchor (default OFF, WC_PLAYER_SOT_ANCHOR). The book
# market AND the SP field both over-price "<player> has >=1 shot on target":
# settled parse_locked (2026-06-23) shows realized YES ~0.23 while we price ~0.49
# and the field ~0.46 — a market-wide longshot/rotation bias we currently over-pay
# even more than the crowd. Blend these props DOWN toward 0.30 (beta=0.5):
# expanding-window OOS +43 over n=30, helped 8/9 match-days, beats the field.
# Player-subject ">=1 SOT" ONLY — team SOT totals share this bucket (mis-mapped)
# but carry a different, thinner threshold bias, so they're EXCLUDED. beta=0.5 not
# 1.0: the honest expanding-window check loses at b=1.0, wins at 0.5. Tunable via
# WC_PLAYER_SOT_TO / WC_PLAYER_SOT_BETA. Forward-validate the gate before go-live.
PLAYER_SOT_ANCHOR_ON = os.environ.get("WC_PLAYER_SOT_ANCHOR", "") == "1"
PLAYER_SOT_ANCHOR = float(os.environ.get("WC_PLAYER_SOT_TO", "0.30"))
PLAYER_SOT_BETA = float(os.environ.get("WC_PLAYER_SOT_BETA", "0.5"))
_K_CLIENT = None
_K_BOOK: dict[tuple, dict] = {}


def is_player_sot_over(text: str, teams) -> bool:
    """True for a player '>=1 shot on target' prop — the validated over-priced
    population. `teams` is the set of names to treat as NON-player subjects: the
    match's two teams in live use, all teams in the offline gate. Either way this
    excludes team SOT totals (subject is a team) and any line other than 'at
    least 1'. Single source of truth for the flag's scope so live and gate agree.

    Team subjects are normalized through the same TEAM_ALIASES map resolve_team
    uses and compared case-insensitively, so an aliased team ('Türkiye' vs
    'Turkey', 'United States' vs 'USA') is still recognized as a team and NOT
    shaded down — a team's >=1 SOT runs ~0.8, the opposite of the 0.30 anchor."""
    if "at least 1 shot on target" not in text.lower():
        return False
    m = re.search(r"[Ww]ill (.+?) have", text)
    if not m:
        return False
    subj = m.group(1).strip()
    subj_norm = TEAM_ALIASES.get(subj.lower(), subj).lower()
    return bool(subj) and subj_norm not in {t.lower() for t in teams}


def combine_kalshi(book_prob, kalshi_mid, w=KALSHI_BLEND_W):
    """Resolve book consensus + Kalshi mid -> (consensus_rec, final_preshrink,
    blend_w, dev_reason, dev_bps). Three cases:
      book only  (no Kalshi)      -> book, w=1.0
      rescue     (no book)        -> Kalshi as sole source, w=0.0
      blend      (both)           -> book primary, nudged toward Kalshi by w
    Pure (no shrink/IO) so it's unit-testable."""
    if kalshi_mid is None:
        return book_prob, book_prob, 1.0, None, 0
    if book_prob is None:
        return kalshi_mid, kalshi_mid, 0.0, f"kalshi only (no book) mid={kalshi_mid:.3f}", 0
    blended = (1 - w) * book_prob + w * kalshi_mid
    return (book_prob, blended, 1 - w,
            f"kalshi blend w={w} mid={kalshi_mid:.3f}",
            round((blended - book_prob) * 10000))


def _kalshi_mid(home, away, date, text):
    global _K_CLIENT
    try:
        import kalshi_wc
        if _K_CLIENT is None:
            _K_CLIENT = kalshi_wc.KalshiRO()
        key = (home, away, date)
        if key not in _K_BOOK:
            _K_BOOK[key] = kalshi_wc.match_book(_K_CLIENT, home, away, date,
                                                extras=KALSHI_HTOTAL_ON)
        res = kalshi_wc.price_question(text, _K_BOOK[key])
        return res[0] if res else None
    except Exception:
        return None


MIN_BOOKS = 2          # never forecast off a single book...
SOLO_BOOK_MIN_WEIGHT = 2.0  # ...unless it's a heavyweight sharp (pinnacle/betfair)
MAX_SNAP_AGE_MIN = 90  # snapshot batch must be this fresh

# Yes-only markets (anytime scorer) have no pair to devig against; raw
# implied prob carries the book's full margin. Haircut approximates a
# one-sided devig (typical anytime-scorer margin 6-10%).
YES_ONLY_MARKETS = {"player_goal_scorer_anytime": 0.93}

# SP question names that differ from Odds API team names.
TEAM_ALIASES = {
    "united states": "USA",
    "türkiye": "Turkey",
    "turkiye": "Turkey",
    "south korea": "South Korea",
    "ivory coast": "Ivory Coast",
    "czechia": "Czech Republic",
    "bosnia and herzegovina": "Bosnia & Herzegovina",
    "curacao": "Curaçao",
}


def resolve_team(name: str, home: str, away: str) -> str | None:
    """Map a team name from question text to the Odds API team string."""
    n = name.strip().lower()
    n = TEAM_ALIASES.get(n, name.strip())
    for t in (home, away):
        if n.lower() == t.lower():
            return t
    return None


def map_question(text: str, mapping: str, home: str, away: str):
    """-> (market, outcome, point) or None if unmappable."""
    t = text.strip()

    if mapping == "h2h":
        m = re.match(r"Will (.+?) win the match\?", t)
        if m:
            team = resolve_team(m.group(1), home, away)
            return ("h2h", team, None) if team else None
        if re.search(r"end in a draw|finish in a draw", t, re.I):
            return ("h2h", "Draw", None)
        return None

    if mapping == "totals":
        m = re.search(r"match have (\d+) or (fewer|less|more) total goals", t, re.I)
        if m:
            n, direction = int(m.group(1)), m.group(2)
            if direction in ("fewer", "less"):
                return ("totals", "Under", n + 0.5)
            return ("totals", "Over", n - 0.5)
        return None

    if mapping == "totals_half":
        m = re.search(r"(first|second) half have (\d+) or (fewer|less|more) total goals",
                      t, re.I)
        if m:
            half = "totals_h1" if m.group(1).lower() == "first" else "totals_h2"
            n, direction = int(m.group(2)), m.group(3)
            if direction in ("fewer", "less"):
                return (half, "Under", n + 0.5)
            return (half, "Over", n - 0.5)
        return None

    if mapping in ("alternate_totals_corners", "alternate_totals_cards"):
        # "Will there be N or more total corner kicks/cards?" -> match Over (N-0.5).
        # Books quote these match totals as Over/Under (no team side).
        unit = "corner kicks" if mapping.endswith("corners") else "cards"
        m = re.search(rf"(\d+) or more total {unit}", t, re.I)
        if m:
            return (mapping, "Over", int(m.group(1)) - 0.5)
        return None

    if mapping == "team_totals":
        m = re.match(r"Will (.+?) score (?:at least (\d+)|(\d+) or more total) "
                     r"goals?\?", t)
        if m:
            team = resolve_team(m.group(1), home, away)
            n = int(m.group(2) or m.group(3))
            if team:
                return ("team_totals", f"{team} Over", n - 0.5)
        return None

    if mapping == "btts":
        if re.search(r"both teams .*score", t, re.I) and " and " not in t.lower():
            return ("btts", "Yes", None)
        return None

    if mapping == "alternate_spreads_cards":
        m = re.match(r"Will (.+?) receive more cards than (.+?)\?", t)
        if m:
            team = resolve_team(m.group(1), home, away)
            # strictly more cards == team -0.5 on the cards handicap
            return ("alternate_spreads_cards", team, -0.5) if team else None
        return None

    if mapping == "alternate_spreads_corners":
        m = re.match(r"Will (.+?) (?:have|finish with) more corner kicks than (.+?)\?", t)
        if m:
            team = resolve_team(m.group(1), home, away)
            return ("alternate_spreads_corners", team, -0.5) if team else None
        return None

    if mapping == "alternate_team_totals_h2":
        m = re.match(r"Will (.+?) score in the second half\?", t)
        if m:
            team = resolve_team(m.group(1), home, away)
            return ("alternate_team_totals_h2", f"{team} Over", 0.5) if team else None
        return None

    if mapping == "team_totals_h1":
        m = re.match(r"Will (.+?) score in the first half\?", t)
        if m:
            team = resolve_team(m.group(1), home, away)
            return ("team_totals_h1", f"{team} Over", 0.5) if team else None
        return None

    if mapping == "h2h_3_way_h1":
        if re.search(r"match be tied|tied at halftime", t, re.I):
            return ("h2h_3_way_h1", "Draw", None)
        m = re.search(r"will (.+?) be winning", t, re.I)
        if m:
            team = resolve_team(m.group(1), home, away)
            return ("h2h_3_way_h1", team, None) if team else None
        return None

    if mapping == "h2h_3_way_h2":
        m = re.match(r"Will (.+?) score more goals than (.+?) in the second half\?", t)
        if m:
            team = resolve_team(m.group(1), home, away)
            return ("h2h_3_way_h2", team, None) if team else None
        return None

    if mapping == "player_shots_on_target":
        m = re.match(r"Will (.+?) have (?:at least )?(\d+)(?: or more)? "
                     r"shots? on target\?", t)
        if m:
            player, n = m.group(1).strip(), int(m.group(2))
            return ("player_shots_on_target", f"{player} Over", n - 0.5)
        return None

    if mapping == "player_goal_scorer_anytime":
        m = re.match(r"Will (.+?) score a goal", t)
        if m:
            tokens = [x for x in re.split(r"[\s\-]+", m.group(1).strip())
                      if len(x) > 2]
            return ("player_goal_scorer_anytime",
                    "tokens:" + "|".join(tokens), None)
        return None

    return None


def consensus(conn, match_id: str, market: str, outcome: str,
              point: float | None, now: datetime):
    """Weighted consensus of latest fresh fair_prob per whitelisted book.

    Returns (prob, n_books, detail) or (None, 0, reason)."""
    point_clause = "AND point IS NULL" if point is None else "AND point = ?"
    params: list = [match_id, market]
    if outcome.startswith("tokens:"):
        # player names vary in order across books ('Heung-Min Son' vs
        # 'Son Heung-min') — require every token, and the Yes side only.
        tokens = outcome[len("tokens:"):].split("|")
        outcome_clause = " AND ".join(["outcome LIKE ?"] * len(tokens)) \
            + " AND outcome LIKE '% Yes'"
        params += [f"%{t}%" for t in tokens]
    else:
        outcome_clause = "outcome = ?"
        params.append(outcome)
    if point is not None:
        params.append(point)
    cutoff = (now - timedelta(minutes=MAX_SNAP_AGE_MIN)).isoformat()
    params.append(cutoff)
    rows = conn.execute(f"""
        SELECT book, fair_prob, divergence_pts, MAX(ts) ts FROM market_snapshots
        WHERE match_id = ? AND market = ? AND {outcome_clause} {point_clause}
          AND ts >= ?
        GROUP BY book""", params).fetchall()

    weights = dict(BOOK_WEIGHTS)
    if market.startswith(THIN_MARKET_PREFIXES):
        for b, w in THIN_MARKET_EXTRA_WEIGHTS.items():
            weights.setdefault(b, w)

    haircut = YES_ONLY_MARKETS.get(market)
    weighted = []
    for r in rows:
        w = weights.get(r["book"], DEFAULT_BOOK_WEIGHT)
        if w <= 0:
            continue
        p = r["fair_prob"]
        # divergence_pts == -1 marks an unpaired (vig-included) quote;
        # haircut those, never the properly devigged pairs.
        if haircut is not None and r["divergence_pts"] == -1.0:
            p *= haircut
        weighted.append((w, p, r["book"]))
    if len(weighted) < MIN_BOOKS:
        # thin additional markets: accept a lone heavyweight sharp quote
        if not (len(weighted) == 1 and weighted[0][0] >= SOLO_BOOK_MIN_WEIGHT):
            return None, len(weighted), "insufficient books"
    tot = sum(w for w, _, _ in weighted)
    prob = sum(w * p for w, p, _ in weighted) / tot
    detail = {b: round(p, 4) for _, p, b in sorted(weighted, reverse=True)}
    return prob, len(weighted), detail


def run(conn, hours: float = 36) -> list[dict]:
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=hours)).isoformat()
    qs = conn.execute("""
        SELECT q.qid, q.text, q.market_mapping, q.deadline, q.match_id,
               m.home, m.away, m.kickoff_utc
        FROM questions q JOIN matches m ON m.match_id = q.match_id
        WHERE q.status = 'open' AND q.market_mapping NOT IN ('NO_MARKET')
          AND m.kickoff_utc <= ? AND m.kickoff_utc >= ?
        ORDER BY m.kickoff_utc, q.qid""",
        (horizon, (now - timedelta(hours=3)).isoformat())).fetchall()

    ts = now.isoformat()
    sheet, skipped = [], []
    for q in qs:
        target = map_question(q["text"], q["market_mapping"], q["home"], q["away"])
        if target is None:
            skipped.append((q["text"], "unparseable"))
            continue
        market, outcome, point = target
        prob, n, detail = consensus(conn, q["match_id"], market, outcome, point, now)
        km = _kalshi_mid(q["home"], q["away"], (q["kickoff_utc"] or "")[:10],
                         q["text"]) if KALSHI_ON else None
        if prob is None and km is None:
            skipped.append((q["text"], f"{detail} ({market}/{outcome}/{point})"))
            continue
        # book-only / conservative-blend / Kalshi-rescue (see combine_kalshi)
        consensus_rec, pre, blend_w, dev_reason, dev_bps = combine_kalshi(prob, km)
        final = shrink_extremes(pre)
        if DEVCAP_ON and market in DEVCAP_MARKETS:
            damped = (1 - DEVCAP_BETA) * final + DEVCAP_BETA * 0.5
            dev_reason = (f"{dev_reason} | " if dev_reason else "") + \
                f"devcap {market} {final:.3f}->{damped:.3f}"
            final = damped
            dev_bps = round((final - consensus_rec) * 10000)
        if PLAYER_SOT_ANCHOR_ON and q["market_mapping"] == "player_shots_on_target" \
                and is_player_sot_over(q["text"], (q["home"], q["away"])):
            damped = (1 - PLAYER_SOT_BETA) * final + PLAYER_SOT_BETA * PLAYER_SOT_ANCHOR
            dev_reason = (f"{dev_reason} | " if dev_reason else "") + \
                f"psotanchor {final:.3f}->{damped:.3f}"
            final = damped
            dev_bps = round((final - consensus_rec) * 10000)
        if prob is None:                             # rescued — Kalshi is the source
            n, detail = 0, {"kalshi": round(km, 4)}
        conn.execute("""
            INSERT INTO forecasts(qid, ts, consensus_prob, blend_w, final_prob,
                                  deviation_bps, deviation_reason)
            VALUES (?,?,?,?,?,?,?)""",
            (q["qid"], ts, round(consensus_rec, 5), blend_w, round(final, 5),
             dev_bps, dev_reason))
        sheet.append({"qid": q["qid"], "text": q["text"],
                      "match": f"{q['home']} vs {q['away']}",
                      "kickoff": q["kickoff_utc"],
                      "market": f"{market}/{outcome}"
                                + (f"@{point}" if point is not None else ""),
                      "prob": final, "submit_int": int(round(final * 100)) or 1,
                      "n_books": n, "books": detail})
    conn.commit()
    return sheet, skipped


if __name__ == "__main__":
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) \
        if "--hours" in sys.argv else 36
    conn = db.init()
    sheet, skipped = run(conn, hours)
    print(f"=== submission sheet: {len(sheet)} priced questions ===")
    cur_match = None
    for row in sheet:
        if row["match"] != cur_match:
            cur_match = row["match"]
            print(f"\n{cur_match}  (ko {row['kickoff']})")
        print(f"  {row['submit_int']:3d}  {row['text']}")
        print(f"       <- {row['market']}  n={row['n_books']} {row['books']}")
    if skipped:
        print(f"\n=== skipped: {len(skipped)} ===")
        for text, why in skipped[:15]:
            print(f"  [{why}] {text}")

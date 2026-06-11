"""Derived/alpha pricing engine: NO_MARKET questions -> market-anchored
Poisson/Skellam derivations, base rates only where no related market exists.

Tiers (per derivation, recorded in deviation_reason):
  [derived]  built from devigged market quantities on the tape
  [anchored] market proxy with a basis adjustment (e.g. cards spread -> fouls)
  [base]     UNVERIFIED base-rate lambda (training-knowledge values, flagged;
             replaced by FBref current-squad build per ROADMAP #1)

Usage:
  python derive.py [--hours 30] [--submit] [--dry-run]
With --submit: batch-submits never-submitted questions; PATCHes already-
submitted ones whose derived value moved >= ALPHA_REVISE_PTS.
"""

from __future__ import annotations

import math
import re
import sys
from datetime import datetime, timedelta, timezone

import db
from devig import shrink_extremes
from forecast import consensus, resolve_team

ALPHA_REVISE_PTS = 3   # alpha derivations churn more than book consensus

# ---- base rates: UNVERIFIED (ROADMAP #1 replaces with counted data) ----
BASE = {
    "pen_lambda": 0.36,        # in-game penalties per match (WC18/22 blend)
    "red_lambda": 0.06,        # red cards per match
    "cards_lambda": 3.8,       # total cards per match fallback
    "corners_lambda": 9.5,     # total corners fallback
    "sot_lambda": 8.2,         # total shots on target per match
    "goals_lambda": 2.6,       # reference total-goals lambda
    "offside_base": 1.3,       # team offsides lambda = base + slope*p_win
    "offside_slope": 0.8,
    "h2_goal_share": 0.55,
    "h2_card_share": 0.60,
    "h2_corner_share": 0.56,
    "h2_sot_share": 0.54,
    "h1_corner_share": 0.44,
}


# ---- math primitives ----

def pois_pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def p_geq(lam: float, k: int) -> float:
    return 1.0 - sum(pois_pmf(lam, i) for i in range(k))


def skellam_gt(la: float, lb: float, n: int = 40) -> float:
    """P(A > B) for independent Poissons."""
    pa = [pois_pmf(la, k) for k in range(n)]
    pb = [pois_pmf(lb, k) for k in range(n)]
    return sum(pa[i] * sum(pb[:i]) for i in range(1, n))


def lam_from_over(p_over: float, line: float) -> float:
    """Solve lambda such that P(N > line) = p_over (line is x.5)."""
    lo, hi = 0.05, 25.0
    for _ in range(60):
        lam = (lo + hi) / 2
        if p_geq(lam, int(line) + 1) > p_over:
            hi = lam
        else:
            lo = lam
    return (lo + hi) / 2


# ---- market readers (consensus over whitelisted books on the tape) ----

def mprob(conn, match_id, market, outcome, point, now):
    p, n, _ = consensus(conn, match_id, market, outcome, point, now)
    return p


def match_lambdas(conn, m, now):
    """(lam_home, lam_away) from devigged totals + h2h on the tape."""
    p_u25 = mprob(conn, m["match_id"], "totals", "Under", 2.5, now)
    lam_tot = lam_from_over(1 - p_u25, 2.5) if p_u25 is not None \
        else BASE["goals_lambda"]
    p_home = mprob(conn, m["match_id"], "h2h", m["home"], None, now)
    if p_home is None:
        return lam_tot / 2, lam_tot / 2, lam_tot
    lo, hi = 0.15, 0.85
    for _ in range(40):
        s = (lo + hi) / 2
        if skellam_gt(lam_tot * s, lam_tot * (1 - s)) > p_home:
            hi = s
        else:
            lo = s
    return lam_tot * s, lam_tot * (1 - s), lam_tot


def cards_lambda(conn, m, now):
    for line in (3.5, 4.5, 2.5):
        p_over = mprob(conn, m["match_id"], "alternate_totals_cards", "Over",
                       line, now)
        if p_over is not None:
            return lam_from_over(p_over, line), "derived"
    return BASE["cards_lambda"], "base"


def corners_lambda(conn, m, now):
    for line in (9.5, 8.5, 10.5):
        p_over = mprob(conn, m["match_id"], "alternate_totals_corners", "Over",
                       line, now)
        if p_over is not None:
            return lam_from_over(p_over, line), "derived"
    return BASE["corners_lambda"], "base"


def corner_share(conn, m, team, now):
    """Team's corner share: corner spread devig when quoted, else supremacy
    fallback (style caveat — corners track style, not strength)."""
    p_race = mprob(conn, m["match_id"], "alternate_spreads_corners", team,
                   -0.5, now)
    lam, _ = corners_lambda(conn, m, now)
    if p_race is not None:
        lo, hi = 0.25, 0.75
        for _ in range(40):
            s = (lo + hi) / 2
            if skellam_gt(lam * s, lam * (1 - s)) > p_race:
                hi = s
            else:
                lo = s
        return s, "derived"
    p_win = mprob(conn, m["match_id"], "h2h", team, None, now) or 0.5
    return 0.5 + 0.20 * (p_win - 0.5) / 0.5 * 0.5, "anchored-supremacy"


def goal_share(conn, m, team, now):
    lh, la, lt = match_lambdas(conn, m, now)
    return (lh if team == m["home"] else la) / lt


# ---- question handlers: (regex, fn(match_row, regex_match, conn, now)) ----
# Each returns (prob, tier, reason) or None.

def h_offside(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    k = int(g.group(2))
    p_win = mprob(conn, m["match_id"], "h2h", team, None, now) or 0.4
    lam = BASE["offside_base"] + BASE["offside_slope"] * p_win
    return p_geq(lam, k), "base", f"offside lam={lam:.2f} (UNVERIFIED base)"


def h_team_score_and_total(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    k = int(g.group(2))
    lh, la, _ = match_lambdas(conn, m, now)
    lt_team = lh if team == m["home"] else la
    lt_other = la if team == m["home"] else lh
    # P(team >= 1 AND team+other >= k), independent poisson
    p = sum(pois_pmf(lt_team, a) * sum(pois_pmf(lt_other, b)
            for b in range(0, 25) if a + b >= k)
            for a in range(1, 25))
    return p, "derived", f"joint poisson team_lam={lt_team:.2f}"


def h_total_sot(m, g, conn, now):
    k, half = int(g.group(1)), bool(g.group(2))
    _, _, lt = match_lambdas(conn, m, now)
    lam = BASE["sot_lambda"] * (lt / BASE["goals_lambda"])
    if half:
        lam *= BASE["h2_sot_share"]
    p = 0.5 + 0.6 * (p_geq(lam, k) - 0.5)  # damped: weakest family (review)
    return p, "anchored", f"sot lam={lam:.2f} damped0.6"


def h_team_sot(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None  # player SOT questions are book-mapped, not derived
    k, half = int(g.group(2)), bool(g.group(3))
    _, _, lt = match_lambdas(conn, m, now)
    lam = BASE["sot_lambda"] * (lt / BASE["goals_lambda"]) \
        * sot_share(conn, m, team, now)
    if half:
        lam *= BASE["h2_sot_share"]
    p = 0.5 + 0.6 * (p_geq(lam, k) - 0.5)  # damped: weakest family (review)
    return p, "anchored", f"team sot lam={lam:.2f} damped0.6"


def h_pen_or_red(m, g, conn, now):
    p = 1 - math.exp(-BASE["pen_lambda"]) * math.exp(-BASE["red_lambda"])
    return p, "base", "pen+red union (UNVERIFIED base lambdas)"


def h_pen(m, g, conn, now):
    return (1 - math.exp(-BASE["pen_lambda"]), "base",
            "pen lambda 0.36 (UNVERIFIED base)")


def h_total_cards(m, g, conn, now):
    k, half = int(g.group(1)), bool(g.group(2))
    lam, tier = cards_lambda(conn, m, now)
    if half:
        lam *= BASE["h2_card_share"]
    return p_geq(lam, k), tier, f"cards lam={lam:.2f}"


def h_team_cards_h2(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    k = int(g.group(2))
    lam, tier = cards_lambda(conn, m, now)
    p_more = mprob(conn, m["match_id"], "alternate_spreads_cards", team,
                   -0.5, now)
    share = 0.5 if p_more is None else 0.5 + 0.5 * (p_more - 0.45) / 0.55 * 0.3
    return (p_geq(lam * share * BASE["h2_card_share"], k), tier,
            f"team cards lam={lam*share*BASE['h2_card_share']:.2f}")


def h_total_corners(m, g, conn, now):
    k, half = int(g.group(1)), bool(g.group(2))
    lam, tier = corners_lambda(conn, m, now)
    if half:
        lam *= BASE["h2_corner_share"]
    return p_geq(lam, k), tier, f"corners lam={lam:.2f}"


def h_team_corners(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    k = int(g.group(2))
    lam, _ = corners_lambda(conn, m, now)
    share, tier = corner_share(conn, m, team, now)
    return p_geq(lam * share, k), tier, f"team corners lam={lam*share:.2f}"


def h_corners_race(m, g, conn, now):
    half = (g.group(1) or "").lower()
    team = resolve_team(g.group(2), m["home"], m["away"])
    other = resolve_team(g.group(3), m["home"], m["away"])
    if not team or not other:
        return None
    lam, _ = corners_lambda(conn, m, now)
    share, tier = corner_share(conn, m, team, now)
    if "second" in half:
        lam *= BASE["h2_corner_share"]
    elif "halftime" in half or "first" in half:
        lam *= BASE["h1_corner_share"]
    return (skellam_gt(lam * share, lam * (1 - share)), tier,
            f"corner race share={share:.3f} lam={lam:.2f}")


def h_team_scores_half(m, g, conn, now):
    """Fallback when no weighted book quotes alternate_team_totals_h2."""
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    half_share = BASE["h2_goal_share"] if g.group(2).lower() == "second" \
        else 1 - BASE["h2_goal_share"]
    lh, la, _ = match_lambdas(conn, m, now)
    lam = (lh if team == m["home"] else la) * half_share
    return 1 - math.exp(-lam), "derived", f"team {g.group(2)}H lam={lam:.2f}"


def h_fouls_race(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    p_cards = mprob(conn, m["match_id"], "alternate_spreads_cards", team,
                    -0.5, now)
    if p_cards is not None:
        return (0.5 + 0.6 * (p_cards - 0.5), "anchored",
                f"cards spread {p_cards:.3f} shaded for fouls basis")
    p_win = mprob(conn, m["match_id"], "h2h", team, None, now) or 0.5
    return 0.5 - 0.08 * (p_win - 0.5) / 0.5, "base", "underdogs foul more (weak)"


def sot_share(conn, m, team, now):
    """Shot volume regresses toward even vs goal share (finishing asymmetry
    is not volume asymmetry) — dampen the fitted goal share by 0.5."""
    gs = goal_share(conn, m, team, now)
    return 0.5 + 0.5 * (gs - 0.5)


def h_sot_race_h2(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    _, _, lt = match_lambdas(conn, m, now)
    share = sot_share(conn, m, team, now)
    lam = BASE["sot_lambda"] * (lt / BASE["goals_lambda"]) * BASE["h2_sot_share"]
    p = 0.5 + 0.6 * (skellam_gt(lam * share, lam * (1 - share)) - 0.5)
    return p, "anchored", f"sot race share={share:.3f} damped0.6"


def h_h2_gt_h1(m, g, conn, now):
    _, _, lt = match_lambdas(conn, m, now)
    l1 = lt * (1 - BASE["h2_goal_share"])
    l2 = lt * BASE["h2_goal_share"]
    return (skellam_gt(l2, l1), "derived",
            f"h2 vs h1 goals lam {l2:.2f}/{l1:.2f}")


def h_first_goal_h2(m, g, conn, now):
    team = resolve_team(g.group(1), m["home"], m["away"])
    if not team:
        return None
    _, _, lt = match_lambdas(conn, m, now)
    share = goal_share(conn, m, team, now)
    p_any = 1 - math.exp(-lt * BASE["h2_goal_share"])
    return share * p_any, "derived", f"first 2H goal share={share:.3f}"


HANDLERS = [
    (r"Will (.+?) be caught offside (\d+) or more", h_offside),
    (r"Will (.+?) score AND the match have (\d+) or more total goals",
     h_team_score_and_total),
    (r"Will both teams score AND the match have (\d+) or more", None),  # special
    (r"Will there be (\d+) or more total shots on target( in the second half)?",
     h_total_sot),
    (r"Will (.+?) have (\d+) or more shots on target( in the second half)?",
     h_team_sot),
    (r"penalty kick be awarded OR a red card", h_pen_or_red),
    (r"Will a penalty kick be awarded( in the match)?\?", h_pen),
    (r"Will there be (\d+) or more total cards shown( in the second half)?",
     h_total_cards),
    (r"Will (.+?) receive at least (\d+) cards? in the second half",
     h_team_cards_h2),
    (r"Will there be (\d+) or more total corner kicks( in the second half)?",
     h_total_corners),
    (r"Will (.+?) have (\d+) or more corner kicks", h_team_corners),
    (r"(At halftime|In the second half)?,? ?[Ww]ill (.+?) have more corner "
     r"kicks than (.+?)\?", h_corners_race),
    (r"Will (.+?) score in the (second|first) half\?", h_team_scores_half),
    (r"Will (.+?) commit more fouls than", h_fouls_race),
    (r"Will (.+?) have more shots on target than .+? in the second half",
     h_sot_race_h2),
    (r"Will the second half have more (?:total )?goals than the first half",
     h_h2_gt_h1),
    (r"Will (.+?) score the first goal of the second half", h_first_goal_h2),
]


def h_btts_and_total(m, k, conn, now):
    lh, la, _ = match_lambdas(conn, m, now)
    p = sum(pois_pmf(lh, a) * sum(pois_pmf(la, b)
            for b in range(1, 25) if a + b >= k)
            for a in range(1, 25))
    return p, "derived", f"btts+O{k-0.5} joint poisson {lh:.2f}/{la:.2f}"


def derive_question(conn, q, now):
    text = q["text"]
    m = {"match_id": q["match_id"], "home": q["home"], "away": q["away"]}
    g = re.search(r"Will both teams score AND the match have (\d+) or more", text)
    if g:
        return h_btts_and_total(m, int(g.group(1)), conn, now)
    for pattern, fn in HANDLERS:
        if fn is None:
            continue
        g = re.search(pattern, text)
        if g:
            return fn(m, g, conn, now)
    return None


def run(conn, hours: float = 30, submit_mode: bool = False, dry: bool = False):
    from sp_client import SPClient
    from submit import _lobby, _to_int
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=hours)).isoformat()
    # NO_MARKET only: book-mapped questions belong to forecast/consensus —
    # derive must NEVER PATCH its cruder Poisson over a sharp consensus
    # value (review finding: ping-pong with derive winning at close).
    qs = conn.execute("""
        SELECT q.qid, q.text, q.match_id, m.home, m.away
        FROM questions q JOIN matches m USING(match_id)
        WHERE q.status='open' AND q.market_mapping = 'NO_MARKET'
          AND m.kickoff_utc > ? AND m.kickoff_utc <= ?
        ORDER BY m.kickoff_utc""",
        ((now - timedelta(hours=2)).isoformat(), horizon)).fetchall()

    prev = {r["qid"]: r["submitted_prob"] for r in conn.execute("""
        WITH s AS (SELECT qid, submitted_prob, sp_prediction_id,
                   ROW_NUMBER() OVER (PARTITION BY qid ORDER BY submitted_at DESC) rn
                   FROM forecasts WHERE submitted_at IS NOT NULL)
        SELECT qid, submitted_prob FROM s WHERE rn=1""")}
    pred_ids = {r["qid"]: r["sp_prediction_id"] for r in conn.execute("""
        WITH s AS (SELECT qid, sp_prediction_id,
                   ROW_NUMBER() OVER (PARTITION BY qid ORDER BY submitted_at DESC) rn
                   FROM forecasts WHERE submitted_at IS NOT NULL)
        SELECT qid, sp_prediction_id FROM s WHERE rn=1""")}

    ts = now.isoformat()
    new, patches, skipped = [], [], 0
    for q in qs:
        res = derive_question(conn, q, now)
        if res is None:
            skipped += 1
            continue
        prob, tier, reason = res
        prob = shrink_extremes(prob)
        full_reason = f"derive.v1 [{tier}] {reason}"
        old = prev.get(q["qid"])
        new_int = _to_int(prob)
        if old is None:
            if not dry:  # dry-run must be DRY (review finding)
                conn.execute(
                    """INSERT INTO forecasts(qid, ts, blend_w, final_prob,
                       deviation_bps, deviation_reason) VALUES (?,?,0,?,0,?)""",
                    (q["qid"], ts, round(prob, 5), full_reason))
            new.append((q["qid"], new_int, q["text"]))
        elif abs(new_int - round(old * 100)) >= ALPHA_REVISE_PTS \
                and pred_ids.get(q["qid"]):
            patches.append((q["qid"], pred_ids[q["qid"]], round(old * 100),
                            new_int, q["text"], prob, full_reason))
    conn.commit()

    print(f"derive: {len(qs)} open questions in window; {len(new)} new, "
          f"{len(patches)} patch-candidates, {skipped} no-handler")
    if dry or not submit_mode:
        for qid, p, t in new[:25]:
            print(f"  NEW   {p:3d}  {t[:75]}")
        for _, _, o, n_, t, *_ in patches[:15]:
            print(f"  PATCH {o:3d} -> {n_:3d}  {t[:70]}")
        return

    c = SPClient()
    lobby = _lobby(conn)
    for i in range(0, len(new), 50):
        chunk = new[i:i + 50]
        resp = c.submit_batch([{"market_id": qid, "lobby_id": lobby,
                                "probability": p} for qid, p, _ in chunk])
        ok = {x["market_id"]: x for x in resp.get("results", [])
              if x.get("success")}
        for qid, p, t in chunk:
            if qid in ok:
                conn.execute(
                    """UPDATE forecasts SET submitted_at=?, submitted_prob=?,
                       sp_prediction_id=? WHERE qid=? AND ts=?""",
                    (ts, p / 100, (ok[qid].get("trade") or {}).get("id"),
                     qid, ts))
                print(f"  ok {p:3d}  {t[:70]}")
            else:
                print(f"  FAIL  {t[:70]}")
        conn.commit()
    for qid, pid, old, n_, t, prob, reason in patches:
        try:
            c.revise(pid, n_)
        except Exception as e:
            print(f"  PATCH FAIL {t[:60]}: {e}")
            continue
        conn.execute(
            """INSERT INTO forecasts(qid, ts, blend_w, final_prob, deviation_bps,
               deviation_reason, submitted_at, submitted_prob, sp_prediction_id)
               VALUES (?,?,0,?,0,?,?,?,?)""",
            (qid, ts, round(prob, 5), reason, ts, n_ / 100, pid))
        conn.commit()
        print(f"  patched {old:3d} -> {n_:3d}  {t[:65]}")


if __name__ == "__main__":
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) \
        if "--hours" in sys.argv else 30
    conn = db.init()
    run(conn, hours, submit_mode="--submit" in sys.argv,
        dry="--dry-run" in sys.argv)

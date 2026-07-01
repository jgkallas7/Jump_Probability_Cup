"""Daily calibration review — the deterministic (no-LLM) half of the
review -> validate -> calibrate loop. Runs in routines/review.sh on a timer.

Reads the realized field-vs-us P&L from the locked-email corpus (kept fresh by
harvest_locked.py) and emits a dated markdown report covering:
  1. Realized edge vs the consensus clone, overall + per bucket (worst-first).
  2. FLAG VALIDATION GATE — for each flag-gated feature pending forward proof,
     re-run its acceptance test on SETTLED data and print APPROVE / HOLD with the
     numbers. This is decision SUPPORT only: a human flips the flag (charter:
     "weight changes are a human decision"). Never auto-edits morning.sh.
  3. New losing buckets to investigate.

Writes data/reviews/<date>.md and appends a one-line pointer to
data/improvement_log.md. Safe, free, no secrets, no network beyond the DB.

Usage: python review_report.py
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone

import db
import parse_locked

REVIEW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reviews")
IMPROVE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "improvement_log.md")
MIN_N = 8   # below this a bucket/flag verdict is "thin — no opinion"


def _clip(p, lo=0.01, hi=0.99):
    return min(hi, max(lo, p))


def _rel(p, o, fab):
    return 100.0 * (fab - (p - o) ** 2)


def _records(conn):
    """One row per settled email question: (bucket, our_p, field_p, o, fab, text)."""
    rows = parse_locked.load_all()
    idx = parse_locked._match_index(conn)
    by_source = defaultdict(list)
    for r in rows:
        by_source[r["source"]].append(r)
    out = []
    for src, erows in by_source.items():
        mid, hits = parse_locked._identify_match(erows, idx)
        if not mid or hits < len(erows) // 2:
            continue
        qmap = idx[mid]
        for r in erows:
            hit = qmap.get(parse_locked._norm(r["question"]))
            if not hit:
                continue
            qid, o, brier = hit
            if o is None or brier is None:
                continue
            rr = r["if_yes"] if o == 1 else r["if_no"]
            fab = brier + rr / 100.0
            mm = conn.execute("SELECT market_mapping FROM questions WHERE qid=?",
                              (qid,)).fetchone()[0] or "NULL"
            out.append((mm, r["you"] / 100.0, r["field"] / 100.0, o, fab, r["question"]))
    return out


def _bucket_edges(recs):
    agg = defaultdict(lambda: [0, 0.0, 0.0])  # n, ours, clone
    for mm, our, fld, o, fab, _txt in recs:
        a = agg[mm]
        a[0] += 1
        a[1] += _rel(our, o, fab)
        a[2] += _rel(fld, o, fab)
    return agg


def _shrink_gain(recs, mm_filter, beta):
    """Realized rel for a bucket: as-sent vs shrunk toward 0.5 by beta."""
    sub = [(our, o, fab) for mm, our, fld, o, fab, _txt in recs if mm in mm_filter]
    if not sub:
        return 0, 0.0, 0.0
    sent = sum(_rel(our, o, fab) for our, o, fab in sub)
    shrunk = sum(_rel(_clip((1 - beta) * our + beta * 0.5), o, fab) for our, o, fab in sub)
    return len(sub), sent, shrunk


def _sot_anchor_gain(recs, anchor=0.65, beta=0.5):
    """WC_SOT_THRESH_ANCHOR gate: realized rel on SOT-threshold questions, as-sent
    vs blended toward `anchor`. Scope is derive.is_sot_threshold itself — the SAME
    predicate the live flag uses — so the gate can't drift from what ships (it
    already excludes the SOT race and the already-anchored 'both teams >=1')."""
    import derive
    # NO_MARKET only — the flag lives in derive.py, which prices ONLY alpha
    # questions; the book-mapped player_shots_on_target bucket shares the
    # "shots on target" text but is priced by forecast.py and never anchored.
    sub = [(our, o, fab) for mm, our, fld, o, fab, txt in recs
           if mm == "NO_MARKET" and derive.is_sot_threshold(txt)]
    if not sub:
        return 0, 0.0, 0.0
    sent = sum(_rel(our, o, fab) for our, o, fab in sub)
    anch = sum(_rel(_clip((1 - beta) * our + beta * anchor), o, fab) for our, o, fab in sub)
    return len(sub), sent, anch


def _player_sot_anchor_gain(conn, recs, anchor=0.30, beta=0.5):
    """WC_PLAYER_SOT_ANCHOR gate: realized rel on player '>=1 SOT' props, as-sent
    vs blended DOWN toward `anchor`. Scope is forecast.is_player_sot_over — the SAME
    predicate the live flag uses — fed the full team set so team SOT totals sharing
    the bucket are excluded (a different, thinner bias)."""
    import forecast
    teams = {r[0] for r in conn.execute(
        "SELECT home FROM matches UNION SELECT away FROM matches")}
    sub = [(our, o, fab) for mm, our, fld, o, fab, txt in recs
           if mm == "player_shots_on_target" and forecast.is_player_sot_over(txt, teams)]
    if not sub:
        return 0, 0.0, 0.0
    sent = sum(_rel(our, o, fab) for our, o, fab in sub)
    anch = sum(_rel(_clip((1 - beta) * our + beta * anchor), o, fab) for our, o, fab in sub)
    return len(sub), sent, anch


def _sot_race_decomp_gain(recs, gamma):
    """WC_SOT_RACE_DECOMP gate: realized rel on 2H SOT-race questions, as-sent vs
    de-compressed away from 0.5 by `gamma`. Scope is derive.is_sot_race — the SAME
    predicate the live flag uses. NOTE: this keys on the question text, so it also
    counts the handful of early races that fell back to a placeholder base rate (the
    flag only de-compresses derive-priced races); going forward every race is derive-
    priced, so the contamination shrinks. The clean go/no-go backtest excluded them."""
    import derive
    sub = [(our, o, fab) for mm, our, fld, o, fab, txt in recs
           if mm == "NO_MARKET" and derive.is_sot_race(txt)]
    if not sub:
        return 0, 0.0, 0.0
    sent = sum(_rel(our, o, fab) for our, o, fab in sub)
    dec = sum(_rel(_clip(0.5 + gamma * (our - 0.5)), o, fab) for our, o, fab in sub)
    return len(sub), sent, dec


def _fouls_dom_gain(conn, slope):
    """WC_FOULS_DOM gate: realized rel on the fouls_race bucket, qmodel at slope 0
    (today's symmetric counted-rate price) vs the game-state tilt at `slope`. Unlike
    the transform gates above this RE-PRICES via qmodel (the tilt needs the match
    goal-lambdas), with PRIOR-ONLY rates (foul base symmetric -> the market-goal-
    share tilt is the entire signal) and lambdas clamped to the last pre-kickoff
    snapshot, so it stays clean (no in-play / same-match look-ahead)."""
    import qmodel
    import team_rates
    from derive import match_lambdas, BASE
    from evaluate_qmodel import _match_now
    rates = {"_tournament": team_rates.build_rates().get("_tournament", {})}
    by_source = defaultdict(list)
    for r in parse_locked.load_all():
        by_source[r["source"]].append(r)
    idx = parse_locked._match_index(conn)
    n = 0
    p0_tot = p1_tot = 0.0
    old = qmodel.FOUL_DOM_SLOPE
    try:
        for src, erows in by_source.items():
            mid, hits = parse_locked._identify_match(erows, idx)
            if not mid or hits < len(erows) // 2:
                continue
            meta = conn.execute("SELECT home, away FROM matches WHERE match_id=?",
                                (mid,)).fetchone()
            if not meta:
                continue
            now = _match_now(conn, mid)
            lam = match_lambdas(conn, {"match_id": mid, "home": meta["home"],
                                       "away": meta["away"]}, now) if now else \
                (BASE["goals_lambda"] / 2, BASE["goals_lambda"] / 2, BASE["goals_lambda"])
            qmap = idx[mid]
            for r in erows:
                if "commit more fouls than" not in r["question"].lower():
                    continue
                hit = qmap.get(parse_locked._norm(r["question"]))
                if not hit:
                    continue
                qid, o, brier = hit
                if o is None or brier is None:
                    continue
                if conn.execute("SELECT market_mapping FROM questions WHERE qid=?",
                                (qid,)).fetchone()[0] != "NO_MARKET":
                    continue
                rr = r["if_yes"] if o == 1 else r["if_no"]
                fab = brier + rr / 100.0
                qmodel.FOUL_DOM_SLOPE = 0.0
                p0 = qmodel.price_question(r["question"], meta["home"], meta["away"], rates, lam)
                qmodel.FOUL_DOM_SLOPE = slope
                p1 = qmodel.price_question(r["question"], meta["home"], meta["away"], rates, lam)
                if not p0 or not p1:
                    continue
                n += 1
                p0_tot += _rel(p0[0], o, fab)
                p1_tot += _rel(p1[0], o, fab)
    finally:
        qmodel.FOUL_DOM_SLOPE = old
    return n, p0_tot, p1_tot


def _matches_with_rows(conn):
    """[(mid, meta, now, lam, [(email_row, qid, outcome, fab, mm), ...]), ...]
    for the settled locked-email corpus, with PRE-KICKOFF lambdas — shared
    scaffolding for the re-pricing gates below (each filters texts itself).
    Built once per report: match_lambdas is the expensive part."""
    from derive import match_lambdas, BASE
    from evaluate_qmodel import _match_now
    by_source = defaultdict(list)
    for r in parse_locked.load_all():
        by_source[r["source"]].append(r)
    idx = parse_locked._match_index(conn)
    out = []
    for src, erows in by_source.items():
        mid, hits = parse_locked._identify_match(erows, idx)
        if not mid or hits < len(erows) // 2:
            continue
        meta = conn.execute("SELECT home, away FROM matches WHERE match_id=?",
                            (mid,)).fetchone()
        if not meta:
            continue
        now = _match_now(conn, mid)
        lam = match_lambdas(conn, {"match_id": mid, "home": meta["home"],
                                   "away": meta["away"]}, now) if now else \
            (BASE["goals_lambda"] / 2, BASE["goals_lambda"] / 2, BASE["goals_lambda"])
        qmap = idx[mid]
        rows = []
        for r in erows:
            hit = qmap.get(parse_locked._norm(r["question"]))
            if not hit:
                continue
            qid, o, brier = hit
            if o is None or brier is None:
                continue
            mm = conn.execute("SELECT market_mapping FROM questions WHERE qid=?",
                              (qid,)).fetchone()[0] or "NULL"
            fab = brier + (r["if_yes"] if o == 1 else r["if_no"]) / 100.0
            rows.append((r, qid, o, fab, mm))
        if rows:
            out.append((mid, meta, now, lam, rows))
    return out


def _bts_half_gain(mrows, anchor):
    """WC_BTS_HALF_ANCHOR gate: both-teams->=1-SOT HALF questions re-priced via
    qmodel (prior-only rates, pre-kickoff lambdas) at the live 0.68 anchor vs
    `anchor`. Clean: the anchor is the only thing that moves."""
    import qmodel
    import team_rates
    rates = {"_tournament": team_rates.build_rates().get("_tournament", {})}
    n = 0
    p0t = p1t = 0.0
    old = qmodel.BTS_HALF_ANCHOR
    try:
        for mid, meta, now, lam, rows in mrows:
            for r, qid, o, fab, mm in rows:
                t = r["question"].lower()
                if (mm != "NO_MARKET"
                        or "both teams have at least 1 shot on target" not in t
                        or ("half" not in t and "halftime" not in t)):
                    continue
                qmodel.BTS_HALF_ANCHOR = 0.68
                p0 = qmodel.price_question(r["question"], meta["home"], meta["away"], rates, lam)
                qmodel.BTS_HALF_ANCHOR = anchor
                p1 = qmodel.price_question(r["question"], meta["home"], meta["away"], rates, lam)
                if not p0 or not p1:
                    continue
                n += 1
                p0t += _rel(p0[0], o, fab)
                p1t += _rel(p1[0], o, fab)
    finally:
        qmodel.BTS_HALF_ANCHOR = old
    return n, p0t, p1t


def _sot_split_gain(mrows, conn, a_total, a_team):
    """WC_SOT_TOTAL_ANCHOR / WC_SOT_TEAM_ANCHOR gate: SOT-threshold questions
    re-priced RAW through the live pricers (total -> qmodel counted at prior-only
    rates; team -> derive.h_team_sot, the live handler while qmodel's team-SOT is
    disabled), then blended toward the pooled 0.65 anchor vs the family-split
    anchors. Includes the team-SOT rows misfiled in player_shots_on_target
    (derive prices those too). Player rows self-exclude via resolve_team."""
    import re as _re
    import derive
    import qmodel
    import team_rates
    rates = {"_tournament": team_rates.build_rates().get("_tournament", {})}
    n = 0
    p0t = p1t = 0.0
    for mid, meta, now, lam, rows in mrows:
        m = {"match_id": mid, "home": meta["home"], "away": meta["away"]}
        for r, qid, o, fab, mm in rows:
            txt = r["question"]
            if (mm not in ("NO_MARKET", "player_shots_on_target")
                    or not derive.is_sot_threshold(txt)):
                continue
            if "total shots on target" in txt.lower():
                res = qmodel.price_question(txt, meta["home"], meta["away"], rates, lam)
                raw, cand = (res[0] if res else None), a_total
            else:
                if now is None:
                    continue
                g = _re.search(r"Will (.+?) have (\d+) or more shots on target"
                               r"( in the second half| in the first half)?", txt)
                res = derive.h_team_sot(m, g, conn, now) if g else None
                if res is None:  # 'at least N' wording -> the coverage pricer
                    g = _re.search(r"[Ww]ill (.+?) have (?:at least )?(\d+)"
                                   r"(?: or more)? shots on target", txt)
                    res = derive.h_team_sot_total(m, g, conn, now) if g else None
                raw, cand = (res[0] if res else None), a_team
            if raw is None:
                continue
            p0 = (1 - derive.SOT_BETA) * raw + derive.SOT_BETA * derive.SOT_ANCHOR
            p1 = (1 - derive.SOT_BETA) * raw + derive.SOT_BETA * cand
            n += 1
            p0t += _rel(_clip(p0), o, fab)
            p1t += _rel(_clip(p1), o, fab)
    return n, p0t, p1t


def _kalshi_soa_gain(mrows, conn):
    """WC_KALSHI_NO_SOA gate: on settled score-or-assist questions the pipeline
    priced from a Kalshi mid (sent reason tag), realized rel of the SENT price vs
    the book-union re-price (h_score_or_assist at pre-kickoff). Kalshi's orderbook
    isn't on the tape so the sent price IS the kalshi side; rows the union can't
    price are skipped (they'd fall to the placeholder either way)."""
    import re as _re
    import derive
    n = 0
    p0t = p1t = 0.0
    for mid, meta, now, lam, rows in mrows:
        m = {"match_id": mid, "home": meta["home"], "away": meta["away"]}
        for r, qid, o, fab, mm in rows:
            if "score or assist" not in r["question"].lower() or now is None:
                continue
            dr = conn.execute(
                """SELECT deviation_reason FROM forecasts
                   WHERE qid=? AND submitted_at IS NOT NULL
                   ORDER BY submitted_at DESC LIMIT 1""", (qid,)).fetchone()
            if not dr or "kalshi score-or-assist" not in (dr[0] or ""):
                continue
            g = _re.search(r"Will (.+?) score or assist a goal", r["question"])
            res = derive.h_score_or_assist(m, g, conn, now) if g else None
            if not res:
                continue
            n += 1
            p0t += _rel(r["you"] / 100.0, o, fab)
            p1t += _rel(_clip(res[0]), o, fab)
    return n, p0t, p1t


def _corner_sup_gain(mrows, conn, slope):
    """WC_CORNER_SUP_SLOPE gate: settled corner races re-priced via
    h_corners_race at the default 0.20 supremacy slope vs `slope`. Only rows
    whose share falls back to anchored-supremacy differ (the spread-ladder path
    ignores the slope), so n counts fallback rows only — thin, accumulates."""
    import re as _re
    import derive
    n = 0
    p0t = p1t = 0.0
    old = derive.CORNER_SUP_SLOPE
    try:
        for mid, meta, now, lam, rows in mrows:
            m = {"match_id": mid, "home": meta["home"], "away": meta["away"]}
            for r, qid, o, fab, mm in rows:
                if (mm != "NO_MARKET" or now is None
                        or "more corner kicks than" not in r["question"].lower()):
                    continue
                g = _re.search(r"(At halftime|In the second half)?,? ?[Ww]ill (.+?) "
                               r"have more corner kicks than (.+?)\?", r["question"])
                if not g:
                    continue
                derive.CORNER_SUP_SLOPE = 0.20
                r0 = derive.h_corners_race(m, g, conn, now)
                derive.CORNER_SUP_SLOPE = slope
                r1 = derive.h_corners_race(m, g, conn, now)
                if not r0 or not r1 or r0[1] != "anchored-supremacy":
                    continue
                n += 1
                p0t += _rel(_clip(r0[0]), o, fab)
                p1t += _rel(_clip(r1[0]), o, fab)
    finally:
        derive.CORNER_SUP_SLOPE = old
    return n, p0t, p1t


def _alpha_family(text):
    """Coarse NO_MARKET families for the review table. Order matters: check
    score-or-assist BEFORE 'own goal' — every score-or-assist question contains
    '(excluding own goals)', which a naive own-goal match swallows (that exact
    mislabelling sent the 2026-07-01 audit chasing the own-goal base rate when
    the -55 actually lived in score-or-assist pricing)."""
    t = text.lower()
    if "score or assist" in t:
        return "score_or_assist"
    if "own goal" in t:
        return "own_goal"
    if "commit more fouls" in t:
        return "fouls_race"
    if "more shots on target than" in t:
        return "sot_race"
    if "both teams have at least 1 shot on target" in t:
        return "bts_sot_half" if ("half" in t or "halftime" in t) else "bts_sot_ft"
    if "total shots on target" in t:
        return "sot_total"
    if "shots on target" in t:
        return "sot_team"
    if "more corner kicks than" in t:
        return ("corners_race_h1" if "halftime" in t or "first half" in t
                else "corners_race_h2" if "second half" in t
                else "corners_race_ft")
    if "corner" in t:
        return "corners_other"
    if "card" in t:
        return "cards"
    if "penalty" in t:
        return "pen_or_red"
    if "offside" in t:
        return "offsides"
    if "hydration" in t:
        return "hydration"
    if "first goal" in t:
        return "first_goal"
    if "half" in t:
        return "half_other"
    return "other"


def _verdict(n, gain):
    if n < MIN_N:
        return f"HOLD (thin n={n} — no opinion)"
    if gain > 3:
        return f"APPROVE (gain +{gain:.0f} over n={n})"
    if gain < -3:
        return f"REJECT (loses {gain:.0f} over n={n})"
    return f"HOLD (flat {gain:+.0f} over n={n})"


def build(conn) -> str:
    recs = _records(conn)
    edges = _bucket_edges(recs)
    tot_n = sum(a[0] for a in edges.values())
    tot_ours = sum(a[1] for a in edges.values())
    tot_clone = sum(a[2] for a in edges.values())

    L = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    L.append(f"# Calibration review — {today} (auto, review_report.py)\n")
    L.append(f"Settled email questions: {tot_n}. Realized edge vs consensus clone: "
             f"ours {tot_ours:+.0f}, clone {tot_clone:+.0f}, **edge {tot_ours-tot_clone:+.0f}**.")
    L.append("(clone = submit the field % — a strong baseline; negative edge = our "
             "deviations net-cost points. Beating it needs RIGHT deviations.)\n")

    L.append("## Realized edge by bucket (worst-first)")
    L.append("| bucket | n | ours | clone | edge |")
    L.append("|---|---|---|---|---|")
    for mm, (n, ours, clone) in sorted(edges.items(), key=lambda kv: kv[1][1] - kv[1][2]):
        L.append(f"| {mm} | {n} | {ours:+.0f} | {clone:+.0f} | {ours-clone:+.0f} |")

    # The NO_MARKET lump hides opposite-signed families (2026-07-01: corners h1
    # races +32 while h2 races -58; 'own goal' was really score-or-assist).
    L.append("\n## NO_MARKET by family (worst-first)")
    L.append("| family | n | edge | our_p | fld_p | yes% |")
    L.append("|---|---|---|---|---|---|")
    fam = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0])
    for mm, our, fld, o, fab, txt in recs:
        if mm != "NO_MARKET":
            continue
        a = fam[_alpha_family(txt)]
        a[0] += 1
        a[1] += _rel(our, o, fab) - _rel(fld, o, fab)
        a[2] += our
        a[3] += fld
        a[4] += o
    for f in sorted(fam, key=lambda k: fam[k][1]):
        n, e, po, pf, ny = fam[f]
        L.append(f"| {f} | {n} | {e:+.0f} | {po/n:.2f} | {pf/n:.2f} | {100*ny/n:.0f}% |")

    L.append("\n## Flag-validation gate (decision support — a human flips the flag)")
    # WC_DEVCAP: h2h shrink toward 0.5 at the shipped beta=0.25
    n, sent, shrunk = _shrink_gain(recs, {"h2h"}, 0.25)
    L.append(f"- **WC_DEVCAP** (h2h shrink beta=0.25): sent {sent:+.0f} vs shrunk "
             f"{shrunk:+.0f} -> {_verdict(n, shrunk - sent)}")
    # WC_SOT_THRESH_ANCHOR: SOT-threshold base anchor at the shipped anchor/beta
    import derive
    n, sent, anch = _sot_anchor_gain(recs, derive.SOT_ANCHOR, derive.SOT_BETA)
    L.append(f"- **WC_SOT_THRESH_ANCHOR** (SOT-threshold -> {derive.SOT_ANCHOR:.2f} "
             f"beta={derive.SOT_BETA}, excl already-fixed): sent {sent:+.0f} vs "
             f"anchored {anch:+.0f} -> {_verdict(n, anch - sent)}")
    # WC_PLAYER_SOT_ANCHOR: player '>=1 SOT' props shaded down at the shipped anchor/beta
    import forecast
    n, sent, anch = _player_sot_anchor_gain(conn, recs, forecast.PLAYER_SOT_ANCHOR,
                                            forecast.PLAYER_SOT_BETA)
    L.append(f"- **WC_PLAYER_SOT_ANCHOR** (player >=1 SOT -> {forecast.PLAYER_SOT_ANCHOR:.2f} "
             f"beta={forecast.PLAYER_SOT_BETA}): sent {sent:+.0f} vs "
             f"anchored {anch:+.0f} -> {_verdict(n, anch - sent)}")
    # WC_SOT_RACE_DECOMP: 2H SOT-race de-compression. Evaluate at the live gamma if
    # the flag is on, else at the 1.5 conservative candidate so the gate stays
    # decision-useful while OFF (the OOS optimum is at the grid edge — ship gentle).
    cand = derive.RACE_DECOMP if derive.RACE_DECOMP != 1.0 else 1.5
    n, sent, dec = _sot_race_decomp_gain(recs, cand)
    L.append(f"- **WC_SOT_RACE_DECOMP** (2H SOT-race de-compress gamma={cand:g}"
             f"{', LIVE' if derive.RACE_DECOMP != 1.0 else ', candidate'}): sent "
             f"{sent:+.0f} vs de-compressed {dec:+.0f} -> {_verdict(n, dec - sent)}")
    # WC_FOULS_DOM: fouls-race game-state tilt (underdog fouls more). Re-priced at
    # the live slope if on, else the 0.15 candidate (the joint OOS peak, prior-only
    # + full rates) so the gate stays decision-useful while OFF.
    import qmodel
    fcand = qmodel.FOUL_DOM_SLOPE if qmodel.FOUL_DOM_SLOPE else 0.15
    n, s0, tl = _fouls_dom_gain(conn, fcand)
    L.append(f"- **WC_FOULS_DOM** (fouls-race underdog tilt slope={fcand:g}"
             f"{', LIVE' if qmodel.FOUL_DOM_SLOPE else ', candidate'}): symmetric "
             f"{s0:+.0f} vs tilted {tl:+.0f} -> {_verdict(n, tl - s0)}")

    # re-pricing gates below share the (expensive) per-match scaffolding
    mrows = _matches_with_rows(conn)
    # WC_BTS_HALF_ANCHOR: both-teams >=1 SOT half anchor (0.68 overshoots a ~65%
    # family; candidate 0.63)
    bcand = qmodel.BTS_HALF_ANCHOR if qmodel.BTS_HALF_ANCHOR != 0.68 else 0.63
    n, p0, p1 = _bts_half_gain(mrows, bcand)
    L.append(f"- **WC_BTS_HALF_ANCHOR** (both-teams SOT half anchor 0.68->{bcand:g}"
             f"{', LIVE' if qmodel.BTS_HALF_ANCHOR != 0.68 else ', candidate'}): "
             f"at 0.68 {p0:+.0f} vs at {bcand:g} {p1:+.0f} -> {_verdict(n, p1 - p0)}")
    # WC_SOT_TOTAL_ANCHOR / WC_SOT_TEAM_ANCHOR: split the pooled 0.65 anchor
    # (total-SOT settles 84% YES, team-SOT 39% — opposite biases)
    tot_c = derive.SOT_TOTAL_ANCHOR or 0.78
    team_c = derive.SOT_TEAM_ANCHOR or 0.42
    live = bool(derive.SOT_TOTAL_ANCHOR or derive.SOT_TEAM_ANCHOR)
    n, p0, p1 = _sot_split_gain(mrows, conn, tot_c, team_c)
    L.append(f"- **WC_SOT_TOTAL/TEAM_ANCHOR** (split 0.65 -> total {tot_c:g} / team "
             f"{team_c:g}{', LIVE' if live else ', candidate'}): pooled {p0:+.0f} vs "
             f"split {p1:+.0f} -> {_verdict(n, p1 - p0)}")
    # WC_KALSHI_NO_SOA: thin-book Kalshi mids vs the book union on score-or-assist
    n, p0, p1 = _kalshi_soa_gain(mrows, conn)
    L.append(f"- **WC_KALSHI_NO_SOA** (score-or-assist: kalshi mid vs book union"
             f"{', LIVE' if derive.KALSHI_NO_SOA else ', candidate'}): kalshi-sent "
             f"{p0:+.0f} vs book-union {p1:+.0f} -> {_verdict(n, p1 - p0)}")
    # WC_CORNER_SUP_SLOPE: corner-race supremacy fallback strength
    scand = derive.CORNER_SUP_SLOPE if derive.CORNER_SUP_SLOPE != 0.20 else 0.50
    n, p0, p1 = _corner_sup_gain(mrows, conn, scand)
    L.append(f"- **WC_CORNER_SUP_SLOPE** (corner-race supremacy fallback 0.20->"
             f"{scand:g}{', LIVE' if derive.CORNER_SUP_SLOPE != 0.20 else ', candidate'}): "
             f"at 0.20 {p0:+.0f} vs at {scand:g} {p1:+.0f} -> {_verdict(n, p1 - p0)}")

    # WC_KALSHI_HTOTAL: is the totals_half bucket beating the clone yet?
    th = edges.get("totals_half")
    if th:
        n, ours, clone = th
        L.append(f"- **WC_KALSHI_HTOTAL** (totals_half bucket): edge {ours-clone:+.0f} "
                 f"-> {_verdict(n, ours - clone)}")
    else:
        L.append("- **WC_KALSHI_HTOTAL**: no settled totals_half questions yet — HOLD")

    L.append("\n## Losing buckets to investigate (edge < -5, n >= MIN_N)")
    losers = [(mm, n, ours - clone) for mm, (n, ours, clone) in edges.items()
              if ours - clone < -5 and n >= MIN_N]
    if losers:
        for mm, n, edge in sorted(losers, key=lambda x: x[2]):
            L.append(f"- `{mm}`: {edge:+.0f} over {n} Qs")
    else:
        L.append("- none above threshold")
    return "\n".join(L) + "\n"


def main():
    conn = db.connect()
    report = build(conn)
    os.makedirs(REVIEW_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(REVIEW_DIR, f"{today}.md")
    with open(path, "w") as f:
        f.write(report)
    # one-line pointer in the improvement log
    first_metric = report.split("**edge")[1].split("**")[0].strip() if "**edge" in report else "?"
    try:
        with open(IMPROVE_LOG, "a") as f:
            f.write(f"\n## {today} — auto review (review_report.py)\n"
                    f"Realized edge vs consensus clone: {first_metric}. "
                    f"Flag-validation gate + per-bucket edges in `data/reviews/{today}.md`.\n")
    except OSError:
        pass
    print(report)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

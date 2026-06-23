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

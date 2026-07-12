"""WC_PH_COVERAGE gate: re-price settled classifier-dropped questions at kickoff
(look-ahead-free) and compare realized relative points vs the placeholder we sent.

The flag is APPROVED to flip only when coverage beats the placeholder on settled
questions that have field data (field_avg_brier, from a harvested locked email).
Until those settle this prints a DEMO (coverage price vs placeholder vs field) on
a live-DB copy so the numbers can be eyeballed (the same "sane on a copy" bar the
WC_TO_ADVANCE_H2H router shipped under).

Usage:  WC_DB_PATH=/copy/wc_cup.db python validate_ph_coverage.py
"""

from __future__ import annotations

import re
from datetime import datetime

import db
import derive
from devig import shrink_extremes

# Reuse the live router's exact regexes (their capture groups feed the handlers).
TARGETS = [(fn.__name__.replace("h_", ""), pat, fn)
           for pat, fn in derive.COVERAGE_HANDLERS]


def _placeholder_prob(conn, qid):
    """The flat value placeholders.py would send (latest placeholder forecast)."""
    r = conn.execute(
        """SELECT final_prob FROM forecasts
           WHERE qid=? AND deviation_reason LIKE 'placeholder%'
           ORDER BY ts DESC LIMIT 1""", (qid,)).fetchone()
    return r["final_prob"] if r else None


def run(conn):
    rows = conn.execute("""
        SELECT q.qid, q.text, q.match_id, m.home, m.away, m.kickoff_utc,
               o.outcome, o.field_avg_brier, o.relative_points
        FROM questions q JOIN matches m USING(match_id)
        LEFT JOIN outcomes o ON o.qid = q.qid
        ORDER BY m.kickoff_utc""").fetchall()

    graded, demo = [], []
    for r in rows:
        for label, pat, fn in TARGETS:
            if not re.search(pat, r["text"], re.I):
                continue
            ko = r["kickoff_utc"]
            now = datetime.fromisoformat(ko.replace("Z", "+00:00"))
            m = {"match_id": r["match_id"], "home": r["home"], "away": r["away"]}
            g = re.search(pat, r["text"], re.I)
            res = fn(m, g, conn, now)              # priced AS OF kickoff
            cov = shrink_extremes(res[0]) if res else None
            ph = _placeholder_prob(conn, r["qid"])
            rec = {"label": label, "text": r["text"][:50], "ko": ko[:10],
                   "cov": cov, "ph": ph, "out": r["outcome"],
                   "fab": r["field_avg_brier"], "relpts": r["relative_points"]}
            if cov is not None and r["outcome"] is not None \
                    and r["field_avg_brier"] is not None:
                o = r["outcome"]
                cov_rel = 100 * (r["field_avg_brier"] - (cov - o) ** 2)
                ph_rel = r["relative_points"]  # what we actually realized (placeholder)
                rec["cov_rel"], rec["ph_rel"] = cov_rel, ph_rel
                graded.append(rec)
            else:
                demo.append(rec)
            break

    if graded:
        # ph is None when no placeholder forecast exists for the row: either it
        # was sent by a real pricer (qmodel/HANDLERS — the production router is
        # fallback-only and never fires on those; verified 2026-07-04: the bulk
        # are 'pen+red union'/'qmodel pen|red'/'anchored sot' sends) or, post
        # 2026-06-28 flip, by coverage itself. Neither answers "coverage vs
        # placeholder", so the gate verdict uses only the placeholder-sent rows.
        print("=== GRADED (settled + field data): coverage vs placeholder ===")
        print(" label              ko          cov   ph    out  cov_rel  ph_rel   delta")
        d_gate, n_gate, d_fwd, n_fwd = 0.0, 0, 0.0, 0
        for r in graded:
            delta = r["cov_rel"] - r["ph_rel"]
            if r["ph"] is not None:
                d_gate += delta
                n_gate += 1
            else:
                d_fwd += r["ph_rel"]   # realized rel pts of the sent coverage price
                n_fwd += 1
            ph_s = "%.2f" % r["ph"] if r["ph"] is not None else "sent"
            print("  %-17s %s  %.2f %s  %s  %+7.1f %+7.1f %+7.1f" %
                  (r["label"], r["ko"], r["cov"], ph_s, r["out"],
                   r["cov_rel"], r["ph_rel"], delta))
        if n_gate:
            verdict = "APPROVE" if d_gate > 0 else "HOLD/REJECT"
            print(f"\n  gate (placeholder-sent rows): n={n_gate}  delta "
                  f"(coverage - placeholder) = {d_gate:+.1f}  -> {verdict}")
        if n_fwd:
            print(f"  non-placeholder rows (sent by qmodel/handlers or, post-flip, "
                  f"coverage — context only, not the gate): n={n_fwd}  "
                  f"realized rel pts = {d_fwd:+.1f}")
    else:
        print("=== no settled coverage targets with field data yet — gate PENDING ===")
        print("    (run after the next /harvest-locked brings these emails into the corpus)")

    if demo:
        print("\n=== DEMO (unsettled / no field data): re-priced at kickoff ===")
        print(" label              ko          placeholder -> coverage   | question")
        for r in demo:
            cov = f"{r['cov']:.2f}" if r["cov"] is not None else " -- (no snap)"
            ph = f"{r['ph']:.2f}" if r["ph"] is not None else " -- "
            print("  %-17s %s   %s     -> %s   | %s" %
                  (r["label"], r["ko"], ph, cov, r["text"]))


if __name__ == "__main__":
    conn = db.connect()
    derive.PH_COVERAGE_ON = True   # exercise the handlers regardless of env
    run(conn)

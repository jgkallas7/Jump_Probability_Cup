"""Score the counted-rate qmodel against (a) what we actually submitted and
(b) the field-consensus clone, on settled alpha questions — in the contest's
relative-points metric.

For each settled NO_MARKET question that qmodel can price:
  field_avg_brier = our_brier + realized_rel/100      (exact, from the email)
  rel(p)          = 100 * (field_avg_brier - (p - outcome)**2)
  old_rel  = realized_rel        (what derive submitted actually scored)
  new_rel  = rel(qmodel_price)
  clone_rel= rel(field_prob)

CAVEAT: team rates are CURRENT (they include the very match being priced), so
this is a methodology check with mild look-ahead, NOT a clean OOS result. The
true OOS test is going-forward on matches not yet in the rate feed. Reported
honestly per the derive.py shrink-lesson.

Usage: python evaluate_qmodel.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import defaultdict

import db
import parse_locked
import qmodel
import team_rates
from derive import match_lambdas, BASE


def _match_now(conn, match_id):
    """Use the latest snapshot ts for this match as 'now' so the freshness
    filter in match_lambdas picks up that match's pre-kickoff tape."""
    row = conn.execute("SELECT MAX(ts) t FROM market_snapshots WHERE match_id=?",
                       (match_id,)).fetchone()
    if not row or not row["t"]:
        return None
    return datetime.fromisoformat(row["t"])


def main(prior_only: bool = False):
    conn = db.connect()
    rates = team_rates.build_rates()
    if prior_only:
        # strip per-team rates -> every lookup falls to the tournament prior.
        # This is the CLEAN (no look-ahead) pricing for matches where the team
        # had no prior data at kickoff (e.g. all of matchday-1). Compare its
        # edge to the FULL-rates run to size the look-ahead contamination.
        rates = {"_tournament": rates.get("_tournament", {})}
    erows = parse_locked.load_all()
    idx = parse_locked._match_index(conn)
    by_source = defaultdict(list)
    for r in erows:
        by_source[r["source"]].append(r)

    agg = defaultdict(lambda: {"n": 0, "old": 0.0, "new": 0.0, "clone": 0.0})
    priced = unpriced = 0
    examples = []
    for src, rows in by_source.items():
        mid, _ = parse_locked._identify_match(rows, idx)
        if not mid:
            continue
        qmap = idx[mid]
        meta = conn.execute("SELECT home, away FROM matches WHERE match_id=?",
                            (mid,)).fetchone()
        if not meta:
            continue
        now = _match_now(conn, mid)
        lam = match_lambdas(conn, {"match_id": mid, "home": meta["home"],
                                   "away": meta["away"]}, now) if now else \
            (BASE["goals_lambda"]/2, BASE["goals_lambda"]/2, BASE["goals_lambda"])
        for r in rows:
            hit = qmap.get(parse_locked._norm(r["question"]))
            if not hit:
                continue
            qid, outcome, brier = hit
            if outcome is None or brier is None:
                continue
            # only NO_MARKET questions qmodel targets
            mm = conn.execute("SELECT market_mapping FROM questions WHERE qid=?",
                              (qid,)).fetchone()[0]
            if mm != "NO_MARKET":
                continue
            res = qmodel.price_question(r["question"], meta["home"], meta["away"],
                                        rates, lam)
            if res is None:
                unpriced += 1
                continue
            new_p, _reason = res
            priced += 1
            realized_rel = r["if_yes"] if outcome == 1 else r["if_no"]
            fab = brier + realized_rel / 100.0
            new_rel = 100.0 * (fab - (new_p - outcome) ** 2)
            clone_rel = 100.0 * (fab - (r["field"] / 100.0 - outcome) ** 2)
            bucket = _reason.split()[0]
            a = agg[bucket]
            a["n"] += 1; a["old"] += realized_rel
            a["new"] += new_rel; a["clone"] += clone_rel
            ag = agg["TOTAL"]
            ag["n"] += 1; ag["old"] += realized_rel
            ag["new"] += new_rel; ag["clone"] += clone_rel
            examples.append((new_rel - realized_rel, r["question"][:48],
                             r["you"], round(new_p*100), r["field"],
                             "YES" if outcome else "NO"))

    print(f"priced {priced} settled alpha questions; {unpriced} qmodel couldn't price\n")
    print(f"{'bucket':22s} {'n':>3} {'OLD(sent)':>10} {'NEW(qmodel)':>12} "
          f"{'CLONE(field)':>13} {'NEW-OLD':>9}")
    print("-" * 76)
    for b in sorted(agg, key=lambda k: (k != "TOTAL", k)):
        a = agg[b]
        print(f"{b:22s} {a['n']:>3} {a['old']:>+10.1f} {a['new']:>+12.1f} "
              f"{a['clone']:>+13.1f} {a['new']-a['old']:>+9.1f}")
    print("\nbiggest qmodel improvements vs what we sent (Δrel, q, you%, new%, field%, result):")
    for d, q, you, new, field, res in sorted(examples, reverse=True)[:8]:
        print(f"  {d:+6.1f}  {q:48s} you={you:>2} new={new:>2} fld={field:>2} {res}")
    print("\nbiggest qmodel regressions:")
    for d, q, you, new, field, res in sorted(examples)[:5]:
        print(f"  {d:+6.1f}  {q:48s} you={you:>2} new={new:>2} fld={field:>2} {res}")


if __name__ == "__main__":
    import sys
    # --prior-only = CLEAN no-look-ahead run (the honest gate for flipping
    # WC_QMODEL). On matchday-2+ data with rates built from PRIOR games only,
    # a clean win here is the green light. Matchday-1 cannot validate (no priors).
    main(prior_only="--prior-only" in sys.argv)

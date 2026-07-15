"""Parse the "🔒 Your predictions are locked" emails -> field model + P&L.

The contest API withholds the per-question field average Brier (RULES.md).
The locked-predictions email sent at each kickoff DOES expose it, as a table:

    Question | You | Field | If Yes | If No | Swing

where If-Yes / If-No are the realized relative points for each outcome and
Field is the crowd's average probability. Once a question settles we know the
outcome, so:

    relative_points = If-Yes  if outcome == 1 else If-No        (exact, theirs)
    field_avg_brier = our_brier + relative_points / 100         (exact; from
                      rel = 100 * (field_avg_brier - our_brier))

This backfills outcomes.field_avg_brier / .relative_points (NULL until now)
and prints an edge-vs-field P&L: us vs a "submit-the-consensus" clone.

Emails are saved as raw HTML under data/emails/*.html (fetch via the Gmail
MCP in a Claude session; the kickoff email arrives AFTER market close, so this
is a retrospective calibration signal, never a live input).

Usage:
  python parse_locked.py            # report only (read-only)
  python parse_locked.py --backfill # also write field_avg_brier/relative_points
  python parse_locked.py --backfill --dry-run
"""
from __future__ import annotations

import glob
import html
import os
import re
import sys

import db

EMAIL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "emails")

_PCT = re.compile(r"^(\d{1,3})%$")
_FLT = re.compile(r"^[+-]\d+(?:\.\d+)?$")
_INT = re.compile(r"^\d{1,3}$")


def extract(path: str) -> list[dict]:
    """Pull the edge-vs-field rows out of one locked-predictions email."""
    t = open(path, encoding="utf-8", errors="replace").read()
    t = re.sub(r"(?is)<(style|head|script).*?</\1>", " ", t)   # drop noise blocks
    t = re.sub(r"(?i)</(td|tr|p|div|h[1-6]|li)>", "\n", t)     # cell/row boundaries
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))              # strip remaining tags
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in t.splitlines()]
    lines = [l for l in lines if l]
    try:
        start = next(i for i, l in enumerate(lines) if "edge vs the field" in l.lower())
    except StopIteration:
        return []
    lines = lines[start:]

    # A question line usually ends with "?", but late-slate emails append a
    # resolution clarification after it on the same line ("...stoppage time)?
    # If both teams substitute simultaneously, ..."). Treat any line containing
    # "?" as a candidate; the 5-number tail validation below is the real filter.
    rows, i = [], 0
    while i < len(lines):
        if "?" in lines[i]:
            q, nums, j = lines[i], [], i + 1
            while j < len(lines) and len(nums) < 5 and "?" not in lines[j]:
                nums.append(lines[j]); j += 1
            if (len(nums) >= 5 and _PCT.match(nums[0]) and _PCT.match(nums[1])
                    and _FLT.match(nums[2]) and _FLT.match(nums[3])):
                rows.append({
                    "question": q,
                    "you": int(nums[0][:-1]), "field": int(nums[1][:-1]),
                    "if_yes": float(nums[2]), "if_no": float(nums[3]),
                    "swing": int(nums[4]) if _INT.match(nums[4]) else None,
                })
                i = j; continue
        i += 1
    return rows


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_all() -> list[dict]:
    rows = []
    for f in sorted(glob.glob(os.path.join(EMAIL_DIR, "*.html"))):
        for r in extract(f):
            r["source"] = os.path.basename(f)
            rows.append(r)
    return rows


def _match_index(conn) -> dict[str, dict[str, tuple]]:
    """match_id -> {norm(question text): (qid, outcome, brier)}.

    Question texts are unique WITHIN a match but repeat across matches
    ("At halftime, will the match be tied?"), so qid resolution must be
    scoped to a match — never by text alone."""
    idx: dict[str, dict[str, tuple]] = {}
    for r in conn.execute(
            "SELECT q.match_id, q.qid, q.text, o.outcome, o.brier "
            "FROM questions q LEFT JOIN outcomes o USING(qid)"):
        idx.setdefault(r["match_id"], {})[_norm(r["text"])] = (
            r["qid"], r["outcome"], r["brier"])
    return idx


def _identify_match(email_rows: list[dict], idx: dict[str, dict[str, tuple]]):
    """Pick the match_id whose question set best fingerprints this email."""
    wanted = {_norm(r["question"]) for r in email_rows}
    best, best_hits = None, 0
    for mid, qmap in idx.items():
        hits = len(wanted & qmap.keys())
        if hits > best_hits:
            best, best_hits = mid, hits
    return best, best_hits


def run(conn, backfill: bool = False, dry: bool = False) -> None:
    rows = load_all()
    if not rows:
        print(f"no emails in {EMAIL_DIR}"); return
    idx = _match_index(conn)

    # group email rows by source, identify each source's match
    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)

    matched = unmatched = written = 0
    by_match: dict[str, dict] = {}
    agg = {"ours": 0.0, "clone": 0.0, "book": [0.0, 0.0], "alpha": [0.0, 0.0]}

    for src in sorted(by_source):
        erows = by_source[src]
        mid, hits = _identify_match(erows, idx)
        if mid is None or hits < len(erows) // 2:
            print(f"  [no match] {src} (best fingerprint hits={hits})")
            unmatched += len(erows); continue
        qmap = idx[mid]
        for r in erows:
            hit = qmap.get(_norm(r["question"]))
            if hit is None:
                unmatched += 1
                print(f"  [unmatched] {src}: {r['question'][:60]}"); continue
            qid, outcome, brier = hit
            matched += 1
            if outcome is None or brier is None:
                continue  # not settled yet — field % known, points not realizable

            rel = r["if_yes"] if outcome == 1 else r["if_no"]
            field_avg_brier = brier + rel / 100.0
            # a clone that submitted the field consensus % would realize:
            f = r["field"] / 100.0
            clone_brier = (f - outcome) ** 2
            clone_rel = 100.0 * (field_avg_brier - clone_brier)

            is_book = bool(conn.execute(
                "SELECT 1 FROM questions WHERE qid=? AND market_mapping NOT IN ('NO_MARKET') "
                "AND market_mapping IS NOT NULL", (qid,)).fetchone())
            bucket = "book" if is_book else "alpha"
            agg["ours"] += rel; agg["clone"] += clone_rel
            agg[bucket][0] += rel; agg[bucket][1] += clone_rel

            m = by_match.setdefault(src, {"ours": 0.0, "clone": 0.0, "n": 0})
            m["ours"] += rel; m["clone"] += clone_rel; m["n"] += 1

            if backfill and not dry:
                conn.execute(
                    "UPDATE outcomes SET field_avg_brier=?, relative_points=? WHERE qid=?",
                    (round(field_avg_brier, 5), round(rel, 2), qid))
                written += 1
    if backfill and not dry:
        conn.commit()

    print(f"\nmatched {matched}/{matched + unmatched} email questions to qids"
          + (f"; wrote {written} outcomes rows" if backfill and not dry else "")
          + (" (DRY RUN)" if dry else ""))

    print("\n=== realized relative points: us vs a submit-the-consensus clone ===")
    print(f"{'match':36s} {'n':>2} {'ours':>9} {'clone':>9} {'edge':>9}")
    for src, m in sorted(by_match.items()):
        print(f"{src[:36]:36s} {m['n']:2d} {m['ours']:+9.2f} {m['clone']:+9.2f} "
              f"{m['ours']-m['clone']:+9.2f}")
    print(f"{'TOTAL':36s} {'':2} {agg['ours']:+9.2f} {agg['clone']:+9.2f} "
          f"{agg['ours']-agg['clone']:+9.2f}")

    print("\n=== by question source (where the edge is won/lost) ===")
    for k in ("book", "alpha"):
        ours, clone = agg[k]
        print(f"  {k:5s}: ours {ours:+8.2f}   consensus-clone {clone:+8.2f}   "
              f"our edge {ours - clone:+8.2f}")
    print("\nclone > 0 everywhere: following the crowd consensus is +EV vs the field "
          "\n(the average field member is worse than the consensus, by Jensen). "
          "\nour edge < 0 means our deviations are net-costing points vs just "
          "submitting the crowd number.")


if __name__ == "__main__":
    conn = db.connect()
    run(conn, backfill="--backfill" in sys.argv, dry="--dry-run" in sys.argv)

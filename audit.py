"""Self-improvement DISCOVERY loop — answers "what's available that we're not
using, and what are we losing on?" so improvements stop depending on a human
noticing them.

Three scans, written to data/opportunities.md as a ranked, dated backlog:

  1. COVERAGE   — question market_mappings with no pricing handler / no forecast
  2. KALSHI     — KXWC* series that exist but aren't wired into pricing
  3. LOSS       — per-bucket relative-points-vs-field from settled questions
                  (the buckets actually costing us rank)

Deterministic (no LLM). Safe to run every morning. The scheduled improvement
agent reads opportunities.md and acts on the top item (see IMPROVEMENT_CHARTER.md).

Usage: python audit.py            # prints + writes data/opportunities.md
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import db

OUT = Path(__file__).parent / "data" / "opportunities.md"

# market_mappings that DO get priced today (forecast consensus / derive / qmodel).
# Anything in questions outside this set is an unhandled coverage gap.
HANDLED_MAPPINGS = {
    "h2h", "totals", "totals_half", "team_totals", "team_totals_h1",
    "alternate_team_totals_h2", "btts", "alternate_spreads_cards",
    "alternate_spreads_corners", "alternate_totals_corners",
    "alternate_totals_cards", "h2h_3_way_h1", "h2h_3_way_h2",
    "player_shots_on_target", "player_goal_scorer_anytime",
    # NO_MARKET is handled by derive/qmodel per-question; check at the text level
    "NO_MARKET",
}

# Kalshi WC series -> whether kalshi_wc prices it (corners/totals/score-or-assist).
# Everything else is a wiring opportunity. (descriptions for the agent.)
KALSHI_WIRED = {"KXWCCORNERS", "KXWCTOTAL", "KXWCSOA"}
KALSHI_HIGH_VALUE = {
    "KXWCSOG": "shots on goal total -> our total-SOT questions",
    "KXWCTEAMSOG": "team shots on goal -> our SOT-comparison questions (72 Qs)",
    "KXWCTCORNERS": "team corners -> our team/half corner-comparison questions",
    "KXWC1HTOTAL": "1st-half total goals -> our totals_half questions",
    "KXWC2HTOTAL": "2nd-half total goals -> our 2H total questions",
    "KXWCTTSF": "team to score first -> our first-goal questions",
    "KXWCBTTS": "both teams to score -> our btts questions (cross-check)",
    "KXWC1HBTTS": "1st-half BTTS",
    "KXWCSPREAD": "goal spread -> our h2h / handicap questions",
}


def coverage_gaps(conn) -> list[str]:
    rows = conn.execute(
        "SELECT market_mapping, COUNT(*) n, "
        "SUM(CASE WHEN qid IN (SELECT qid FROM forecasts WHERE submitted_at IS NOT NULL) "
        "THEN 1 ELSE 0 END) sub FROM questions GROUP BY market_mapping").fetchall()
    out = []
    for r in rows:
        mm = r["market_mapping"]
        if mm not in HANDLED_MAPPINGS:
            out.append(f"UNHANDLED mapping `{mm}` — {r['n']} Qs, {r['sub']} submitted "
                       f"(no pricing handler; classified to 'other'?)")
        elif r["sub"] < r["n"] * 0.9:
            out.append(f"LOW COVERAGE `{mm}` — only {r['sub']}/{r['n']} submitted "
                       f"(book consensus missing? add Kalshi rescue / a handler)")
    return out


def kalshi_gaps() -> list[str]:
    """Live: which high-value KXWC series exist + are unwired."""
    try:
        import kalshi_wc
        c = kalshi_wc.KalshiRO()
        # cheap: one series-list call each (cached); just confirm they have markets
        out = []
        for tk, desc in KALSHI_HIGH_VALUE.items():
            try:
                n = len(c.markets(tk))
            except Exception:
                n = -1
            if n != 0:
                out.append(f"WIRE Kalshi `{tk}` ({n} mkts) — {desc}")
        return out
    except Exception as e:
        return [f"(kalshi audit skipped: {type(e).__name__})"]


def loss_buckets(conn) -> list[str]:
    """Per-bucket realized relative-points vs field, from settled emails."""
    try:
        import parse_locked
    except Exception:
        return ["(parse_locked unavailable)"]
    rows = parse_locked.load_all()
    if not rows:
        return ["(no locked-prediction emails yet — no field feedback)"]
    idx = parse_locked._match_index(conn)
    bys = defaultdict(list)
    for r in rows:
        bys[r["source"]].append(r)
    agg = defaultdict(lambda: {"ours": 0.0, "clone": 0.0, "n": 0})
    for src, erows in bys.items():
        mid, _ = parse_locked._identify_match(erows, idx)
        if not mid:
            continue
        for r in erows:
            hit = idx[mid].get(parse_locked._norm(r["question"]))
            if not hit:
                continue
            qid, outcome, brier = hit
            if outcome is None or brier is None:
                continue
            mm = conn.execute("SELECT market_mapping FROM questions WHERE qid=?",
                              (qid,)).fetchone()[0]
            rel = r["if_yes"] if outcome == 1 else r["if_no"]
            fab = brier + rel / 100.0
            clone = 100.0 * (fab - (r["field"] / 100.0 - outcome) ** 2)
            a = agg[mm]
            a["ours"] += rel; a["clone"] += clone; a["n"] += 1
    out = []
    for mm, a in sorted(agg.items(), key=lambda kv: kv[1]["ours"] - kv[1]["clone"]):
        edge = a["ours"] - a["clone"]
        if edge < -2 and a["n"] >= 2:
            out.append(f"LOSING bucket `{mm}` — {edge:+.1f} pts vs field over {a['n']} Qs "
                       f"(ours {a['ours']:+.0f} vs crowd-clone {a['clone']:+.0f})")
    return out or ["(no bucket losing >2pts vs field — model is at/above crowd)"]


def run(conn) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cov, kal, loss = coverage_gaps(conn), kalshi_gaps(), loss_buckets(conn)
    lines = [f"# Improvement opportunities — {now}", "",
             "_Auto-generated by audit.py. The scheduled improvement agent works the",
             "top item per IMPROVEMENT_CHARTER.md. Highest-leverage first._", "",
             "## 1. Coverage gaps (questions we can't price)"]
    lines += [f"- {x}" for x in (cov or ["- none"])]
    lines += ["", "## 2. Available-but-unwired Kalshi markets"]
    lines += [f"- {x}" for x in kal]
    lines += ["", "## 3. Buckets losing points vs the field (ranked worst-first)"]
    lines += [f"- {x}" for x in loss]
    text = "\n".join(lines) + "\n"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    return text


if __name__ == "__main__":
    print(run(db.connect()))
    print(f"\n-> written to {OUT}")

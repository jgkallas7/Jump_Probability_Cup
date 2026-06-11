#!/bin/bash
# Sunday review (19:00 CT): calibration + book Brier + deviation P&L.
# REPORT-ONLY — weight changes are a human decision (trading discipline).
set -uo pipefail
REPO="/mnt/c/Users/jgkal/OneDrive/Jump_Probability_Cup"
PY="/home/jgkal/.wc_cup_venv/bin/python"
LOG="/home/jgkal/wc_logs/weekly.log"
mkdir -p /home/jgkal/wc_logs
cd "$REPO"

{
  echo "===== weekly review $(date -u +%F) ====="
  $PY calibrate.py sync
  $PY calibrate.py report
  $PY - <<'EOF'
import db
conn = db.connect()
# deviation P&L: rows where final != consensus, settled
rows = conn.execute("""
  WITH last_sub AS (
    SELECT f.*, ROW_NUMBER() OVER (PARTITION BY qid ORDER BY submitted_at DESC) rn
    FROM forecasts f WHERE submitted_at IS NOT NULL)
  SELECT l.deviation_reason, l.consensus_prob, l.submitted_prob, o.outcome
  FROM last_sub l JOIN outcomes o ON o.qid = l.qid
  WHERE l.rn=1 AND l.consensus_prob IS NOT NULL
    AND ABS(l.submitted_prob - l.consensus_prob) > 0.005""").fetchall()
if rows:
    dev_b = sum((r["submitted_prob"] - r["outcome"])**2 for r in rows) / len(rows)
    con_b = sum((r["consensus_prob"] - r["outcome"])**2 for r in rows) / len(rows)
    print(f"deviation P&L: n={len(rows)} deviations; our brier {dev_b:.4f} "
          f"vs pure-consensus {con_b:.4f} ({'EARNING' if dev_b < con_b else 'COSTING'})")
else:
    print("deviation P&L: no settled deviations yet")
print("\nREMINDER: review book Brier ranking above — BOOK_WEIGHTS changes "
      "are yours to make in config.py (report-only by design)")
EOF
} >> "$LOG" 2>&1

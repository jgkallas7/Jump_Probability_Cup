#!/bin/bash
# Review routine (daily 06:30 CT, before wc-morning): the deterministic half of
# the review -> validate -> calibrate loop. Keeps the locked-email corpus fresh,
# re-grades realized field P&L, runs the OOS gate, and writes a dated review with
# a FLAG-VALIDATION verdict (APPROVE/HOLD per flag). It does NOT flip any flag —
# a human reads data/reviews/<date>.md and decides (charter: weight changes are a
# human decision; also keeps a code-capable LLM out of the loop for credential safety).
set -uo pipefail
REPO="/mnt/c/Users/jgkal/OneDrive/Jump_Probability_Cup"
PY="/home/jgkal/.wc_cup_venv/bin/python"
LOG="/home/jgkal/wc_logs/review.log"
mkdir -p /home/jgkal/wc_logs
cd "$REPO"
# source the live flags so the review's gate lines label LIVE vs candidate
# truthfully (found 2026-07-01: a flag-free review rendered the live
# WC_SOT_RACE_DECOMP=1.5 as "candidate" — the exact drift flags.sh exists to stop)
source "$REPO/routines/flags.sh"

{
  echo "===== review $(date -u +%FT%H:%M) ====="
  # 1) keep the corpus current (IMAP; no-op + notice if creds absent)
  $PY harvest_locked.py --days 7
  # 2) re-grade realized field-vs-us P&L from the (now fresh) emails
  $PY parse_locked.py --backfill | tail -3
  # 3) settle + reliability (idempotent; morning also does this)
  $PY calibrate.py sync
  # 3b) referee schedule backfill (WC_REF_CARDS data; FBref scrape is flaky —
  # hard timeout + failure keeps the old cache, never blocks the review)
  timeout 300 $PY -c "import ref_rates; print('ref rows:', ref_rates.refresh())" || true
  # 4) clean no-look-ahead OOS gate for the alpha pricer
  echo "----- OOS gate (evaluate_qmodel --prior-only) -----"
  $PY evaluate_qmodel.py --prior-only 2>&1 | head -20
  # 5) the consolidated review + flag-validation verdict (writes data/reviews/)
  echo "----- calibration review (review_report.py) -----"
  $PY review_report.py
} >> "$LOG" 2>&1

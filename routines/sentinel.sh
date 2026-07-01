#!/bin/bash
# Pre-match sentinel wrapper (every 15 min via systemd timer).
set -uo pipefail
REPO="/mnt/c/Users/jgkal/OneDrive/Jump_Probability_Cup"
PY="/home/jgkal/.wc_cup_venv/bin/python"
LOG="/home/jgkal/wc_logs/sentinel.log"
mkdir -p /home/jgkal/wc_logs
cd "$REPO"
# WC_* feature flags — MUST match morning.sh, else sentinel (the last writer
# before close) re-prices flag-free and strips morning's flagged value. Shared
# source of truth so they can't drift again (see routines/flags.sh).
source "$REPO/routines/flags.sh"
# timestamp each pass — the log was previously undated, which made the Jun-27
# PATCH-400 retry loop (market locked server-side pre-kickoff) impossible to
# date during the Jul-01 forensic audit. morning.sh already stamps its runs.
echo "===== sentinel $(date -u +%FT%H:%M) =====" >> "$LOG"
$PY sentinel.py >> "$LOG" 2>&1

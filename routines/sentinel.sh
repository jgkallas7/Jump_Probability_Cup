#!/bin/bash
# Pre-match sentinel wrapper (every 15 min via systemd timer).
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${WC_PY:-$HOME/.wc_cup_venv/bin/python}"
LOG="$HOME/wc_logs/sentinel.log"
mkdir -p $HOME/wc_logs
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

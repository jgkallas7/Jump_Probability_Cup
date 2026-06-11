#!/bin/bash
# Pre-match sentinel wrapper (every 15 min via systemd timer).
set -uo pipefail
REPO="/mnt/c/Users/jgkal/OneDrive/Jump_Probability_Cup"
PY="/home/jgkal/.wc_cup_venv/bin/python"
LOG="/home/jgkal/wc_logs/sentinel.log"
mkdir -p /home/jgkal/wc_logs
cd "$REPO"
$PY sentinel.py >> "$LOG" 2>&1

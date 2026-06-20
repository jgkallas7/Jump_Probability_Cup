#!/usr/bin/env bash
# Read-only / dry-run smoke test for the Jump Probability Cup pipeline.
#
# WHY THIS EXISTS: this repo is a LIVE contest bot. routines/morning.sh runs
# `submit.py submit` and `derive.py --submit`, which POST real predictions to
# the SportsPredict "Jump Trading Probability Cup". Running those by mistake
# corrupts a live leaderboard entry and burns Odds-API credits. This driver
# exercises the SAME pricing+submission code, but:
#   1. against a THROWAWAY COPY of the live DB (via WC_DB_PATH), and
#   2. only on --dry-run / read-only command paths (never a POST/PATCH).
# So you see real numbers for today's matches with zero risk.
#
# Usage:
#   .claude/skills/run-jump-probability-cup/smoke.sh          # offline smoke
#   .claude/skills/run-jump-probability-cup/smoke.sh live     # +1 live API read
#
# Env overrides:
#   WC_PY          python to use   (default: /home/jgkal/.wc_cup_venv/bin/python)
#   WC_LIVE_DB     DB to copy from  (default: /home/jgkal/wc_cup.db)
#   WC_SMOKE_HOURS forecast window  (default: 720 — wide, to price everything)
#   WC_SMOKE_LINES output cap/step  (default: 20)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="${WC_PY:-/home/jgkal/.wc_cup_venv/bin/python}"
LIVE_DB="${WC_LIVE_DB:-/home/jgkal/wc_cup.db}"
HOURS="${WC_SMOKE_HOURS:-720}"
LINES="${WC_SMOKE_LINES:-20}"
MODE="${1:-smoke}"
cd "$REPO"

fail=0
out="$(mktemp)"
trap 'rm -f "$out"' EXIT

step() {  # step "label" cmd...
  local label="$1"; shift
  echo "===== $label ====="
  if "$@" >"$out" 2>&1; then
    head -n "$LINES" "$out"
    echo "  [ok]"
  else
    local rc=$?
    head -n "$LINES" "$out"
    echo "  [FAILED rc=$rc]"
    fail=1
  fi
  echo
}

if [ ! -x "$PY" ]; then
  echo "python not found/executable at: $PY (set WC_PY)" >&2
  exit 2
fi

if [ "$MODE" = "live" ]; then
  # The one network call we DO make: read-only GET /events. Proves the
  # SP_API_KEY env var is set and the contest API is reachable. No writes.
  step "LIVE read-only: SportsPredict GET /events" "$PY" sp_client.py
  [ $fail -eq 0 ] && echo "LIVE CHECK PASSED" || echo "LIVE CHECK FAILED"
  exit $fail
fi

# ---- offline smoke (default) -------------------------------------------
TMP="$(mktemp -d /tmp/wc_cup_smoke.XXXXXX)"
trap 'rm -rf "$TMP"; rm -f "$out"' EXIT
HAVE_DATA=0
if [ -f "$LIVE_DB" ]; then
  cp "$LIVE_DB" "$TMP/wc_cup.db"
  HAVE_DATA=1
  echo "# driving a COPY of the live DB ($(du -h "$LIVE_DB" | cut -f1)) at $TMP/wc_cup.db"
else
  echo "# no live DB at $LIVE_DB -- using a fresh empty schema DB (data steps skipped)"
fi
export WC_DB_PATH="$TMP/wc_cup.db"
echo

step "deps importable" "$PY" -c "import requests, curl_cffi, mcp, pytest; print('deps ok')"
step "db.py -- build schema, list tables" "$PY" db.py
step "forecast.py --hours $HOURS -- consensus pricing (pure, no network)" \
     "$PY" forecast.py --hours "$HOURS"

if [ $HAVE_DATA -eq 1 ]; then
  step "submit.py submit --dry-run --hours $HOURS -- submission sheet (NO POST)" \
       "$PY" submit.py submit --dry-run --hours "$HOURS"
  step "derive.py --dry-run --hours $HOURS -- alpha engine (NO POST)" \
       "$PY" derive.py --dry-run --hours "$HOURS"
  step "calibrate.py report -- settlement / Brier readout (read-only)" \
       "$PY" calibrate.py report
else
  echo "# skipping submit/derive/calibrate -- need a populated DB (set WC_LIVE_DB)"
  echo
fi

step "pytest" "$PY" -m pytest -q

if [ $fail -eq 0 ]; then
  echo "ALL SMOKE STEPS PASSED"
else
  echo "SMOKE FAILED -- see above"
fi
exit $fail

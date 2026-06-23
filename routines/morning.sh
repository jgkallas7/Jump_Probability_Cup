#!/bin/bash
# Morning routine (daily 07:00 CT): settle yesterday, ingest new questions,
# forecast + submit defaults for today's matches.
set -uo pipefail
REPO="/mnt/c/Users/jgkal/OneDrive/Jump_Probability_Cup"
PY="/home/jgkal/.wc_cup_venv/bin/python"
LOG="/home/jgkal/wc_logs/morning.log"
mkdir -p /home/jgkal/wc_logs
cd "$REPO"

# Counted-rate quant pricer for alpha (NO_MARKET) questions. Enabled after a
# CLEAN no-look-ahead OOS (evaluate_qmodel.py --prior-only): clean qmodel beat
# what we send (+3.6) and forward use is look-ahead-free (price time only sees
# prior games). Team-rate edge grows as the tournament progresses. Refresh the
# rate feed first so qmodel prices off current data; revalidate per task #9.
export WC_QMODEL=1
# Kalshi WC crowd mids: BLEND into book totals/corners (book stays primary; goal
# totals validated to 0.6pt vs sharp line, corners +5-7pt so blended not
# overridden) and RESCUE book-mapped Qs the sportsbook can't price. Guarded —
# any Kalshi failure leaves book pricing untouched.
export WC_KALSHI=1
# Half goal-totals (Kalshi KXWC1HTOTAL/2HTOTAL -> totals_half bucket).
# DISABLED 2026-06-20: was flipped on 06-16 ahead of its own gate — "validated"
# only vs the full-match Kalshi ladder (another Kalshi number), never against a
# sharp sportsbook half line or settled-results OOS, and totals_half was the
# worst bucket (-22.7 vs field). Re-enable ONLY after parse_locked shows the
# totals_half bucket improving on SETTLED questions (or a sportsbook cross-check).
export WC_KALSHI_HTOTAL=0
# h2h confidence dampener: pull match-winner/draw submissions 25% toward 0.5.
# ENABLED 2026-06-22 after its own gate cleared — review_report._shrink_gain on
# the SETTLED locked-email corpus: as-sent +73 vs shrunk +107 = +34 over n=32
# (APPROVE). This is the parse_locked forward-proof forecast.py asked for before
# go-live. Scope is h2h ONLY (DEVCAP_MARKETS); beta=0.25 is the conservative
# slice (in-sample optimum ~0.6 overfit a matchday-1 upset run). Our match-
# outcome confidence runs ahead of the realized upset/draw rate; SOT/alpha losses
# are DIRECTIONAL not overconfidence, so they stay out of scope. Watch the h2h
# bucket on parse_locked — disable if the gate flips to REJECT.
export WC_DEVCAP=1
# SOT-threshold base anchor: blend NO_MARKET "N-or-more shots on target" prices
# toward 0.65 at beta=0.5 (race pricer untouched — it's level-invariant + a
# confirmed edge). ENABLED 2026-06-22 after its gate cleared: review_report
# _sot_anchor_gain on SETTLED data (NO_MARKET-scoped, excl already-fixed both>=1)
# = sent +40 vs anchored +78 = +38 over n=21 (APPROVE); helped 6/9 match-days,
# both sub-templates positive. beta=0.5 not 1.0 ON PURPOSE: in-sample favours
# higher beta but it doubles worst-case single-Q loss (-22 vs -10) and overfits
# high-N thresholds; 0.5 is the bias-variance center. WATCH: the win is back-
# loaded/concentrated (n=21, one +30 day) — if the nightly gate slides to
# HOLD/REJECT as more settle, flip this OFF. Tunable: WC_SOT_ANCHOR/WC_SOT_BETA.
export WC_SOT_THRESH_ANCHOR=1
$PY team_rates.py refresh >/dev/null 2>&1 || echo "team_rates refresh failed (qmodel falls back to prior)"

{
  echo "===== morning $(date -u +%FT%H:%M) ====="
  # surface any overnight failures FIRST
  if [ -s /home/jgkal/wc_logs/FAILURES.log ]; then
    echo "!!! FAILURES SINCE LAST CHECK !!!"
    tail -10 /home/jgkal/wc_logs/FAILURES.log
  fi
  # heartbeat: sentinel should have ~96 entries/day
  SENT=$(grep -c "sentinel" /home/jgkal/wc_logs/sentinel.log 2>/dev/null || echo 0)
  echo "sentinel log lines to date: $SENT"
  $PY -c "import db, submit; print('reconcile:', submit.reconcile(db.init()), 'records')"
  $PY calibrate.py sync          # grade yesterday's settlements
  $PY calibrate.py report | tail -30
  $PY fetch_schedule.py | tail -3   # free; picks up knockout fixtures later
  $PY ingest_questions.py | grep -E "ingested|mapping|WARNING"
  $PY snapshot.py pinnacle --hours 30 | tail -2
  $PY forecast.py --hours 30 | head -50
  $PY submit.py submit --hours 30
  $PY derive.py --hours 30 --submit
  # INSURANCE NET (last): family base-rate placeholders for anything still
  # unpriced after forecast+derive. A blank scores 0 relative points (the worst
  # field-relative outcome); a placeholder is ~break-even. Fills gaps only —
  # never revises a real forecast (it skips already-submitted qids).
  $PY placeholders.py --submit --hours 30
  $PY -c "import db, sheet; sheet.write_for_window(db.connect(), hours=30, push=False)"
  # loud flag for the derive.py gap: alpha questions due today w/o submission
  $PY - <<'EOF'
import db
from datetime import datetime, timedelta, timezone
conn = db.connect()
hz = (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat()
n = conn.execute("""SELECT COUNT(*) FROM questions q JOIN matches m USING(match_id)
    WHERE q.status='open' AND m.kickoff_utc <= ?
    AND q.qid NOT IN (SELECT qid FROM forecasts WHERE submitted_at IS NOT NULL)""",
    (hz,)).fetchone()[0]
print(f"ALPHA GAP: {n} questions due within 30h have no submission" if n
      else "all questions due within 30h are submitted")
last = conn.execute("SELECT used, remaining FROM credit_log ORDER BY id DESC LIMIT 1").fetchone()
if last: print(f"credits: {last['used']} used, {last['remaining']} remaining")
EOF
  # DISCOVERY loop: regenerate the ranked opportunity backlog (unused markets,
  # unwired Kalshi series, buckets losing vs field) for the improvement agent.
  echo "----- opportunities (audit.py) -----"
  $PY audit.py | tail -n +5
} >> "$LOG" 2>&1

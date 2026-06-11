#!/bin/bash
# Morning routine (daily 07:00 CT): settle yesterday, ingest new questions,
# forecast + submit defaults for today's matches.
set -uo pipefail
REPO="/mnt/c/Users/jgkal/OneDrive/Jump_Probability_Cup"
PY="/home/jgkal/.wc_cup_venv/bin/python"
LOG="/home/jgkal/wc_logs/morning.log"
mkdir -p /home/jgkal/wc_logs
cd "$REPO"

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
} >> "$LOG" 2>&1

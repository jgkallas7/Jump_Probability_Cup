#!/bin/bash
# Daily self-improvement agent (autonomy: auto-implement, human approves go-live).
# Runs AFTER morning.sh/settlement. Reads docs/IMPROVEMENT_CHARTER.md + the fresh
# opportunity backlog, implements ONE validated improvement behind a flag (OFF),
# commits it to the auto/improvements branch (master untouched), and writes an
# APPROVE/HOLD recommendation to data/improvement_log.md for the human.
#
# Safety envelope (why headless autonomy is acceptable here):
#   - work happens on branch auto/improvements; master's live pipeline is untouched
#   - every change is flag-gated OFF, so it is INERT until the human flips it
#   - the charter forbids touching submission flow w/o tests, and forbids trading
#   - pytest must pass before commit
#
# Requires the `claude` CLI authenticated in this environment. Wire into the same
# scheduler that runs morning.sh, ~1h later (after settlement grading).
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WT="$HOME/wc-improve-tree"      # dedicated git worktree — NEVER the live tree
PY="${WC_PY:-$HOME/.wc_cup_venv/bin/python}"
LOG="$HOME/wc_logs/improve.log"
export WC_DB_PATH="$HOME/wc_cup.db"

{
  echo "===== improve $(date -u +%FT%H:%M) ====="
  command -v claude >/dev/null 2>&1 || { echo "claude CLI not found — skipping"; exit 0; }

  # The agent works in an ISOLATED worktree on branch auto/improvements; the live
  # pipeline keeps running from $REPO (master) untouched. Created from master's
  # latest commit, refreshed each run. Inert until the human merges + flips a flag.
  cd "$REPO" || exit 1
  if [ ! -e "$WT/.git" ]; then
    git worktree add -B auto/improvements "$WT" master 2>/dev/null \
      || { echo "worktree create failed (commit master baseline first?) — skipping"; exit 0; }
  fi
  cd "$WT" || { echo "no worktree — skipping"; exit 0; }
  git merge -q master 2>/dev/null || true      # build on the latest live code

  $PY audit.py >/dev/null 2>&1 || echo "audit.py failed (agent still runs off last backlog)"

  PROMPT="You are the daily improvement loop for this World Cup forecasting bot.
Read docs/IMPROVEMENT_CHARTER.md and data/opportunities.md in this repo, then execute
EXACTLY ONE improvement following the charter to the letter. Autonomy level:
auto-implement + validate, but you MUST NOT enable any flag (the human approves
go-live). Implement behind a flag default-OFF, validate out-of-sample, ensure
'python -m pytest -q' passes, then append a dated APPROVE/HOLD entry with the
numeric evidence and the exact command to enable it to data/improvement_log.md.
Do not touch master submission behavior. Do not place any order. Never read or
print credentials/.env/key files. If the top item is risky or ambiguous (per the
charter's escalate list), implement nothing and just log it for human review."

  # SECURITY (security-review HIGH, 2026-06-16): NOT bypassPermissions. Scrub
  # secrets from the agent env, and allowlist tools to the minimum (no push, no
  # arbitrary network). NOTE residual risk: the agent runs python for validation,
  # and python can read any user-readable file — so credentials elsewhere on
  # this host are NOT fully protected by an allowlist alone. True isolation needs
  # a container / restricted user without those secrets mounted (see
  # docs/IMPROVEMENT_CHARTER.md "isolation"). Until then keep this conservative.
  unset KALSHI_API_KEY KALSHI_API_SECRET ODDS_API_KEY SP_API_KEY
  export GIT_TERMINAL_PROMPT=0
  claude -p "$PROMPT" --permission-mode acceptEdits \
    --allowedTools "Edit,Write,Read,Grep,Glob,Bash(git add:*),Bash(git commit:*),Bash(git checkout:*),Bash(git merge:*),Bash(git diff:*),Bash(git status:*),Bash($PY:*)" \
    2>&1 | tail -40

  $PY -m pytest -q >/dev/null 2>&1 && echo "pytest: PASS" || echo "pytest: FAIL — do not merge"
  git add -A && git commit -q -m "auto: daily improvement $(date -u +%F)" 2>/dev/null \
    && echo "committed to auto/improvements (worktree $WT)" || echo "nothing to commit"
  echo "--- review: cat data/improvement_log.md ; git -C $WT diff master..auto/improvements ---"
} >> "$LOG" 2>&1

---
description: Harvest new "predictions are locked" emails via the Gmail connector, then report our realized points vs the field
argument-hint: "[days, default 4]"
allowed-tools: Bash, Read, mcp__claude_ai_Gmail__search_threads, mcp__claude_ai_Gmail__get_thread
---

# /harvest-locked — the daily calibration chore (Claude-driven)

Pull the SportsPredict "🔒 predictions are locked" emails the contest sends at each
kickoff (they expose the **field consensus %**, which the API hides), then re-grade
us vs the field. Runs from a Claude session because the Gmail connector can't be
reached headlessly (the IMAP `harvest_locked.py` path needs `~/.gmail_app_password`,
which isn't set — this command is the deliberate manual replacement).

Lookback window: **$1** days (default 4 if empty).

Paths: repo cwd; `PY=~/.wc_cup_venv/bin/python`; `WC_DB_PATH=~/wc_cup.db`.

## Steps

1. **Find candidate emails.** Call `mcp__claude_ai_Gmail__search_threads` with
   query `from:noreply@sportspredict.com subject:locked newer_than:Nd` (N = the
   lookback above), `view: THREAD_VIEW_MINIMAL`, `pageSize: 50`. The snippet of
   each thread names the matchup ("Prediction Locked! HOME vs AWAY is live").

2. **Diff against the corpus.** Run `ls data/emails/*.html` and match each inbox
   thread's `HOME vs AWAY` to existing files (names are `DATE_Home_v_Away.html`,
   accent-stripped). Keep only threads with **no** file on disk. If none are
   missing, say so and skip to step 5 (still worth re-grading).

3. **Fetch the missing threads.** For each missing thread, call
   `mcp__claude_ai_Gmail__get_thread` with `messageFormat: FULL_CONTENT`. These
   emails are ~70 KB, so the harness will reject the inline result and **save it to
   a tool-results `.txt` file** — copy that path from each error message. (Do NOT
   try to read the HTML into context; the next step parses the files directly.)

4. **Extract to the corpus.** Pass the saved file paths to the helper, which writes
   each `htmlBody` to `data/emails/` with the canonical naming + dedup:
   ```
   $PY harvest_locked_mcp.py <saved_file_1> <saved_file_2> ...
   ```
   Or point it at the whole tool-results dir (idempotent — globs get_thread files):
   `$PY harvest_locked_mcp.py --dir <tool-results-dir>`

5. **Settle + re-grade.** (DB env var required.)
   ```
   WC_DB_PATH=~/wc_cup.db $PY calibrate.py sync
   WC_DB_PATH=~/wc_cup.db $PY parse_locked.py --backfill
   ```

6. **Report vs the field — not vs the outcome.** From the `parse_locked` table,
   report per **new** match and the season TOTAL as **ours / consensus-clone / edge**
   (edge = ours − clone). Positive edge = our deviation beat the crowd; negative =
   it cost us. NEVER grade predictions hit/miss against what actually happened —
   right-side bets can lose to the field and wrong-side bets can win. Call out the
   biggest edge winners and losers among the newly settled games.

Don't commit the emails (`data/emails/` is gitignored — personal mail; the signal
already lives in the DB + `data/reviews/`). Committing is a separate, explicit ask.

---
name: review-gates
description: Nightly flag-gate review for the Probability Cup bot — read data/reviews/<date>.md, judge each WC_* flag's APPROVE/HOLD/REJECT gate, flip flags in routines/flags.sh with evidence inline, verify the flip scores, and log the decision same turn. Use when asked to do the nightly/daily review, check the gates, decide or execute a flag flip ("should we flip WC_X?"), check the watch list, or interpret review_report.py output.
---

# Review gates: nightly flag review → flip → record

The deterministic half already ran: `wc-review.timer` → `routines/review.sh`
(06:30 CT, before wc-morning) refreshes the locked-email corpus, re-grades
realized P&L, and has `review_report.py` write `data/reviews/<YYYY-MM-DD>.md`
with per-bucket edge tables and a **flag-validation gate** (APPROVE/HOLD/REJECT
per flag). This skill is the judgment half: read it, decide, flip, verify,
record. Every step's output must land in a durable artifact **the same turn**
— prose-only findings evaporate (flag-drift was diagnosed Jun 17, went
unrecorded, and was re-bought Jun 23).

**Governance (user policy, re-affirmed 2026-07-01): flipping is the agent's
job** — in an interactive session, once the gate clears, with evidence inline.
The report's "a human flips the flag" header text predates this policy; don't
be deterred by it. The one absolute exception: the DISABLED autonomous
improve-loop (`wc-improve`) must never edit `routines/flags.sh`.

## Step 0 — get today's review

```bash
cat data/reviews/$(date +%F).md
```

If it's missing, the timer hasn't run or failed (check
`~/wc_logs/review.log`). To regenerate by hand — safe: reads the DB
and email corpus, writes only `data/reviews/` + one pointer line to
`data/improvement_log.md`, no POSTs, no credits:

```bash
PY=~/.wc_cup_venv/bin/python
bash -c 'source routines/flags.sh && '"$PY"' review_report.py'
```

**Always source `routines/flags.sh` first when hand-running** — a flag-free
environment renders LIVE flags as "candidate" in the gate lines (the exact
mislabeling observed 2026-07-01). Do **not** run `calibrate.py sync` or
`harvest_locked.py` casually to "freshen" inputs: sync spends paid Odds-API
credits, and the email path is the `/harvest-locked` slash command (Gmail
connector; review.sh's IMAP call is an intentional no-op without creds). If
the corpus is stale, run `/harvest-locked` first, then regenerate.

## Step 1 — read the gate lines

Verdicts come from `review_report._verdict` (`MIN_N = 8`):

| verdict | meaning |
|---|---|
| `APPROVE (gain +G over n=N)` | alternative beats what-we-sent by > +3 pts, n ≥ 8 |
| `REJECT (loses −G over n=N)` | alternative loses > 3 pts, n ≥ 8 |
| `HOLD (flat …)` | within ±3 — no signal either way |
| `HOLD (thin n=N — no opinion)` | n < 8 — do nothing, record what n it's waiting for |

Each gate line names the flag and says LIVE or candidate. Cross-check the
gate's *direction* against the NO_MARKET family table and bucket-edge table in
the same file — a gate that says APPROVE while its family row deteriorates
deserves suspicion, not a flip.

## Step 2 — decide

| state × verdict | action |
|---|---|
| candidate (OFF/unset) × APPROVE | flip ON — after the sanity checks below |
| candidate × HOLD/REJECT | leave OFF; update its waiting-for note if the number moved |
| LIVE × REJECT | flip OFF, or pull the slider back toward neutral (e.g. a slope toward 0); say which and why |
| LIVE × APPROVE / HOLD-flat | leave ON; refresh the inline WATCH note if the gain shifted materially |
| anything × HOLD-thin | no action; some flags carry their own threshold (e.g. WC_REF_CARDS waits for n ≥ 8 *effective* rows) |

Sanity checks before ANY flip — each has burned this project:

1. **Same-sample honesty.** If the flag's parameter was chosen by argmax on
   the same settled sample the gate scores (WC_FOULS_DOM 0.15, the SOT split
   anchors), the gain is an upper bound, not a forward estimate. Label it
   "same-sample" in every artifact you write. In-sample mirages are the
   project's most-repeated failure.
2. **Concentration / phantom-APPROVE.** If the gain could sit in a few rows,
   demand a drop-N-best sensitivity (the report prints one where implemented).
   WC_REF_CARDS once showed n=27 APPROVE that was really n=3 effective rows —
   caught only by drop-3.
3. **Effective scope.** Confirm the gate measures the flag's *real* scope:
   the pooled SOT anchor gate said APPROVE for months while the widened
   (true-scope) gate said REJECT −79.
4. **Family labels lie at the margins.** `_alpha_family` matches
   score-or-assist before own-goal because every SOA question contains
   "(excluding own goals)" — a past audit chased the wrong family for a −55.
   If a family row drives your decision, spot-check a few underlying
   questions.

## Step 3 — flip mechanics

Edit **`routines/flags.sh` only** — it is the single source sourced by BOTH
`morning.sh` and `sentinel.sh` (sentinel is the last writer before close; a
flag set anywhere else gets stripped off the scored value). Never put flags in
morning.sh/sentinel.sh directly.

Every flip carries its evidence inline beside the flag, matching the file's
existing style:

```bash
# <What it does, one line>. <STATE> <date> after its gate cleared:
# <gate fn> on SETTLED data = sent +X vs <alt> +Y -> APPROVE (gain +Z over n=N).
# <Same-sample / sensitivity caveats, stated honestly.>
# WATCH: <family/bucket row to watch nightly>; <pull-back condition>.
export WC_NEWFLAG=1
```

A pull-back or disable gets the same treatment — date, the gate numbers that
justified it, and what would re-enable it (see WC_KALSHI_HTOTAL for the
pattern).

## Step 4 — verify the flip scores

A flag only matters if it reaches the SUBMITTED value. After the next
submit/revise cycle (morning run, or sentinel in the T-75 window), find the
flag's tag in the submitted forecast's `deviation_reason`. **The tag is NOT
the flag name** — it's whatever reason string the pricer emits; find it by
grepping the flag's name in the pricer source (`qmodel.py` / `derive.py` /
`forecast.py`) for the reason it writes. Verified examples:
`WC_PLAYER_SOT_ANCHOR` → `psotanchor`, `WC_OFF_ENV` → `env-blend w=0.7`,
`WC_TO_ADVANCE_H2H` → `to_advance<-h2h`, kalshi blends → `kalshi blend w=`.

There is no `sqlite3` CLI on this box — use the venv python, read-only URI:

```bash
~/.wc_cup_venv/bin/python -c "
import sqlite3
c = sqlite3.connect('file:~/wc_cup.db?mode=ro', uri=True)
sql = '''SELECT qid, submitted_prob, deviation_reason FROM forecasts
  WHERE submitted_at IS NOT NULL AND deviation_reason LIKE '%<tag>%'
  ORDER BY submitted_at DESC LIMIT 5'''
for r in c.execute(sql): print(r)"
```

No rows after a cycle that should have used it → the flag isn't reaching
production; investigate before counting it as live. (Caveat: some flags tilt
a number without changing the reason string — e.g. WC_FOULS_DOM rows still
read `fouls race Skellam` — for those, verify by comparing the submitted
prob against a flag-off re-price on a DB copy instead.) **Never run `morning.sh`, `sentinel.py`, or a real
`submit.py submit` to "test" a flip** — those POST to the live contest. The
flip takes effect on the next timer run on its own.

## Step 5 — record, same turn

- **`data/improvement_log.md`**: append a dated entry — what flipped (or was
  held back and why), the gate numbers, same-sample caveats, the WATCH
  condition. This file is the durable decision record.
- **`routines/flags.sh`**: the inline evidence comment (step 3) — done at
  flip time, not later.
- Do **not** hand-edit `data/opportunities.md` — audit.py regenerates it and
  will clobber your notes.
- If the decision changes standing operational state (a LIVE feature turned
  off, a watch closed out), reflect it in `docs/HANDOFF.md`.

## Step 6 — walk the watch list

The watch list is self-maintaining — it lives in the flags file:

```bash
grep -n "WATCH" routines/flags.sh
```

For each WATCH note, find the named family/bucket row in today's review and
confirm it hasn't regressed. Regression on a LIVE flag → back to step 2
(LIVE × REJECT row). For WC_PH_COVERAGE specifically, the deeper check is
`validate_ph_coverage.py` after each `/harvest-locked`.

## Gotchas

- **The review is only as fresh as the email corpus.** "Settled email
  questions: N" at the top of the review — if N hasn't moved in days, run
  `/harvest-locked` before trusting the gates.
- **Never grade vs outcomes.** All numbers here are relative-points-vs-field
  from the locked emails. Don't reframe a decision as "we were right/wrong
  about what happened."
- **Re-running `review_report.py` the same day** appends another pointer line
  to improvement_log.md; harmless, but trim duplicates if you regenerate.
- **A HOLD is not a REJECT.** Thin-n candidates (WC_CARDS_DOM,
  WC_KALSHI_SOA_MAXSPREAD) stay parked, not deleted — the knob + gate existing
  with the flag OFF is the designed steady state until evidence arrives.
- **Slider flags flip by degrees.** For continuous flags (WC_FOULS_DOM,
  WC_OFF_ENV, WC_CORNER_SUP_SLOPE) the pull-back move is toward neutral, not
  binary OFF — and never chase a grid-edge argmax (WC_OFF_ENV ships 0.7, not
  the monotone-to-edge 1.0).

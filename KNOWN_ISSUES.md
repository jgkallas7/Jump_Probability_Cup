# Known issues: implicit state machines

Post-contest notes (written 2026-11-08, after the Probability Cup ended). The
contest is over, so these are documented rather than fixed. They are the main
design debt to address if this system is ever reused for another contest.

The common thread: several entities move through clear lifecycles (a forecast
is drafted, submitted, revised, locked, scored), but no table stores *which
phase they are in*. The phase is inferred from NULL columns, side tables and
the clock, and each consumer infers it slightly differently.

## 1. Forecast submission has no status column (highest impact)

**Where:** `submit.py` (`_pending`, `cmd_revise`), `db.py`
(`locked_predictions`, `mark_prediction_locked`), `sentinel.py` (unanswered
check), `derive.py` (`submitted_at` writes).

**How the phase is encoded today**

| Phase | Encoded as |
|---|---|
| draft (never submitted) | `forecasts.submitted_at IS NULL` on the latest row (`ROW_NUMBER() ... rn = 1`) and no other row for the qid with `submitted_at` set |
| submitted | `submitted_at`, `submitted_prob`, `sp_prediction_id` set |
| revised | a later PATCH; no marker of its own |
| locked server-side | a row in `locked_predictions` (only after a PATCH returned 400) |
| scored | `questions.status = 'settled'` |

**What went wrong:** "locked" was not a state, so the bot kept revising
questions the server had already locked: 213 futile PATCHes in one day (see
the comment in `db.py`). `locked_predictions` was the after-the-fact patch.

**Remaining problems**

- Only a 400 response marks a prediction locked. 5xx and network errors stay
  retryable forever, with no attempt count or backoff.
- `submit._pending` (keys off `questions.deadline`) and the sentinel's
  "unanswered" alert (keys off `matches.kickoff_utc`) use different
  definitions of "needs a submission", so they can disagree.
- A question can be `status='open'` locally while its deadline has passed and
  it is locked on the server, so the sentinel can page about a question that
  can no longer be answered.
- `locked_predictions` is created lazily inside `mark_prediction_locked` as
  well as in the schema.

**Recommended fix:** add `forecasts.state` (`draft` / `submitted` / `locked` /
`scored`) plus `attempts` and `last_error`, with one `transition()` function
that every writer calls. Derive "needs a submission" in exactly one query that
both `submit.py` and `sentinel.py` use.

## 2. Match status is advanced by the clock

**Where:** `db.py` (`matches.status DEFAULT 'scheduled'`),
`snapshot.py:73-75`.

The only transition is `scheduled -> completed`, applied when kickoff is more
than 3.5 hours in the past. There is no `live` or `postponed` state.

**Problems**

- Extra time and penalties, or a delayed kickoff, can be marked completed
  while the match is still being played.
- A postponed match is marked completed and never reset, so it drops out of
  every snapshot window.
- The transition only happens as a side effect of `snapshot.py` running.

**Recommended fix:** `scheduled / live / finished / postponed`, advanced from
real results (the results feed), not elapsed time.

## 3. Question status is overwritten on re-ingest

**Where:** `ingest_questions.py:327-335`, `calibrate.py:82`.

`questions.status` is copied from the API on every ingest
(`status = excluded.status`), so a locally set `settled` can be overwritten
back to the API's value. There is no "closed, awaiting result" state, which is
why every consumer filters on `status = 'open'` and locked-but-unsettled
questions are counted as open (see issue 1).

**Recommended fix:** keep the API's status in its own column and derive the
local lifecycle state separately; never let ingest overwrite a terminal local
state.

## 4. Tournament stage is inferred from question text

**Where:** `ingest_questions.py:103-180` (`_ADV_TARGET_TO_STAGE`,
`_KO_CALENDAR`, `backfill_stages`).

`matches.stage` defaults to `group` and is promoted by regex on question text
plus a hard-coded calendar. On 2026-07-01, 13 of 21 knockout matches were
still `group` and scored at 1x (noted in the code). The stage multiplier is
also frozen into `outcomes.multiplier` at settle time and had to be rewritten
afterwards.

**Recommended fix:** one `stage_of(match)` function, and derive the multiplier
from the stage at read time instead of storing it.

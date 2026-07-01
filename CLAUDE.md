# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **live contest bot** (not an app) for the SportsPredict "Probability Cup" — a
public forecasting competition over FIFA World Cup 2026 (~104 matches, 1,000+
binary questions, Jun 11–Jul 19 2026). The bot ingests questions, prices each
from a weighted consensus of devigged sportsbook odds (plus a quant engine for
questions no book prices), and POSTs integer 1–99 probabilities to the contest
API. Production runs headless on systemd user timers; you drive it for
inspection and changes.

**Read these first, in order:** `HANDOFF.md` (current operational state, what's
live, dead-ends, open tasks), then `SPEC.md` (architecture rationale), `RULES.md`
(resolved contest-API facts), `IMPROVEMENT_CHARTER.md` (standing instructions for
model changes). The auto-loaded `memory/MEMORY.md` indexes durable facts.

## The one mental model — scoring is RELATIVE to the field

Points per question = `(field_average_brier − your_brier) × multiplier × 100`
(group 1×, knockout 2×, final 3×). **Edge means being right where the crowd is
wrong, not being right in absolute terms.** A 50% answer only loses points when
the field is confidently elsewhere; a "correct-side" forecast can still *lose*
relative points, and a "wrong-side" one can *win* them. Consequences:

- **Never grade predictions hit/miss against the outcome.** The realized metric
  is relative-points-vs-field, recoverable only from the post-close "predictions
  are locked" emails (`parse_locked.py` over `data/emails/`). Report results that
  way, never as right/wrong vs. what happened.
- Honest sharp consensus is the optimal *per-question* forecast (Brier is a
  proper scoring rule). The field model changes *posture* (variance control when
  leading/trailing on high-multiplier questions), not the honest number.
- There is **no leaderboard/rank API** (confirmed). Rank lives only in the web
  UI; use cumulative relative-points (`parse_locked`) as the standing proxy.

## ⚠️ This bot spends money and writes to a live scored contest

- `routines/morning.sh` runs `submit.py submit` and `derive.py --submit` — **real
  POSTs to the live leaderboard.** `sentinel.py` PATCHes real predictions.
  Never run these "to test."
- `forecast.py` and `derive.py` **INSERT rows into whatever DB `WC_DB_PATH`
  points at** — including the live `/home/jgkal/wc_cup.db`. Always run them
  against a *copy* when experimenting.
- `snapshot.py pinnacle`, `fetch_schedule.py`, `ingest_questions.py`,
  `calibrate.py sync` spend **paid Odds-API credits** (a pool shared with the
  separate kalshi-tracker project).

**Safe way to exercise the real code** — copies the live DB to a tmpdir, points
`WC_DB_PATH` at it, runs only read-only/`--dry-run` paths, never constructs a
POSTing client:

```bash
.claude/skills/run-jump-probability-cup/smoke.sh         # offline, all stages
.claude/skills/run-jump-probability-cup/smoke.sh live    # + GET /events (read-only auth check)
```

To poke one stage by hand, copy the DB first:

```bash
PY=/home/jgkal/.wc_cup_venv/bin/python
TMP=$(mktemp -d); cp /home/jgkal/wc_cup.db "$TMP/wc_cup.db"; export WC_DB_PATH="$TMP/wc_cup.db"
$PY forecast.py --hours 720                 # consensus pricing (pure, no network)
$PY submit.py submit --dry-run --hours 720  # submission sheet, NO POST
$PY derive.py --dry-run --hours 720         # alpha engine, NO POST
$PY calibrate.py report                      # settlement / Brier readout (read-only)
rm -rf "$TMP"
```

`--dry-run` is genuinely dry: the SPClient is only built *after* the dry-run
branch returns.

## Environment & commands

- **Always use the venv python** (deps are venv-only): `/home/jgkal/.wc_cup_venv/bin/python`.
  System `python3` will `ModuleNotFoundError` on `requests`/`curl_cffi`/`mcp`.
- **Run from the repo root** — scripts use flat imports (`import db, config`) and
  `__file__`-relative paths (`sheet.py` writes `data/sheets/`).
- `WC_DB_PATH` defaults to the **live** `/home/jgkal/wc_cup.db` (on ext4, never
  OneDrive — WAL corrupts on DrvFs). Override it to a copy when testing.
- Keys resolve from env then a dotfile, never the repo: `ODDS_API_KEY` /
  `~/.odds_api_key`, `SP_API_KEY` / `~/.sp_api_key`, `~/.apifootball_key`.

```bash
PY=/home/jgkal/.wc_cup_venv/bin/python
$PY -m pytest -q                          # full suite (pure-math + parsing tests)
$PY -m pytest tests/test_forecast.py -q   # one file
$PY -m pytest tests/test_qprice.py::test_name -q   # one test
WC_DB_PATH=/home/jgkal/wc_cup.db $PY evaluate_qmodel.py --prior-only  # OOS gate (clean, no look-ahead)
WC_DB_PATH=/home/jgkal/wc_cup.db $PY parse_locked.py   # realized us-vs-field P&L
WC_DB_PATH=/home/jgkal/wc_cup.db $PY audit.py          # ranked opportunity backlog
```

## Pipeline architecture

```
ingest_questions.py   SP lobby -> questions table; classifies each text ->
                      (qtype, market_mapping) or NO_MARKET (= alpha question)
        |
snapshot.py           bookmaker gateway (free, continuous) + pinnacle (Odds API,
                      rationed) -> market_snapshots tape; both devig methods stored
        |
   +----+----------------------------+
   |                                 |
forecast.py                       derive.py
 book-mapped questions:            NO_MARKET (alpha) questions:
 map_question() text->            market-anchored Poisson/Skellam, or qmodel.py
 (market,outcome,point) ->        (counted team rates + qprice closed forms),
 weighted consensus() ->          or base rates. Tiered: [derived]/[anchored]/[base].
 shrink_extremes()
   |                                 |
   +----------------+----------------+
                    |
placeholders.py     insurance: any still-unpriced open question gets a
                    family base-rate placeholder (a blank scores 0 relative pts)
                    |
submit.py           latest unsubmitted forecast per question -> batch POST (<=50);
                    revise = re-forecast + PATCH moves >= 2 pts before close
                    |
calibrate.py sync   GET /results -> outcomes table -> Brier; report = per-book
                    closing-line Brier + calibration buckets + reliability
```

**Data layer is an append-only tape + state tables** in SQLite (`db.py`
schema): `matches`, `questions`, `market_snapshots` (one row per book/market/
outcome with *both* power and multiplicative fair probs, so divergence is
SQL-queryable), `forecasts`, `outcomes`, `calibration_buckets`, `credit_log`.
Note `ingest_questions.py` — not `db.py` — creates the `meta` table that holds
`sp_lobby_id`; a `db.py`-only DB crashes submit/calibrate with `no such table:
meta`.

**Consensus engine (`config.py` + `forecast.py`):** fair value is
**whitelist-only** — only books with `BOOK_WEIGHTS > 0` (pinnacle heaviest,
exchanges, sharp offshore) price; the 45+ soft books are snapshotted as a crowd
proxy but excluded from pricing. Devig is **power-primary** with a multiplicative
sanity check (`devig.py`); for "Will X win?" the draw is deliberately *not*
renormalized away — that's a standing edge over a field that underweights draws.
Never submit 0/100 (`shrink_extremes`, floor/ceiling 1/99%).

**Alpha / quant layer:** `qprice.py` (pure scipy closed forms: Poisson survival,
Skellam, bivariate Poisson, first-goal) ← `qmodel.py` (routes each alpha
question to a pricer using counted team rates + market goal-λ) ← `team_rates.py`
(per-team counted rates, empirical-Bayes shrunk toward the tournament mean).
`kalshi_wc.py` is an **unauthenticated, read-only** Kalshi orderbook reader (no
trading credential ever touched — deliberate, see security note) providing a
live crowd-mid that `forecast.combine_kalshi` blends (book primary) or uses to
rescue book-unpriced questions.

## Conventions that bite

- **Feature-flag discipline.** Every behavior change ships behind a `WC_*` env
  flag, **default OFF**, and must beat what-we-currently-send on a clean
  **out-of-sample** check (`evaluate_qmodel.py --prior-only`, or `parse_locked`
  on settled questions) *before* being enabled. In-sample wins are mirages and
  have burned this project repeatedly. Flags live in `routines/flags.sh`
  (single source, gate evidence inline per flag). Flips happen in an
  interactive session at the user's direction once the `review_report` gate
  clears (user policy, re-affirmed 2026-07-01: "flipping is the agent's job");
  the DISABLED autonomous improve-loop must never edit flags.
- **Submissions are integers 1–99**; keep floats internally, round only at
  submit. **Read-back asymmetry:** you POST `75`, the API returns `0.75`
  (`_norm_prob` handles it).
- **`submit.py submit --dry-run` printing `nothing to submit` is success** — on
  live data everything in the window is already submitted.
- Submissions are **revisable until market close** (PATCH); the latest value at
  close is scored. `submit.py revise` / `sentinel.py` exploit the T-75 lineup
  window. `reconcile()` re-syncs local bookkeeping from server truth after 409s.

## Dead ends — do not redo (all validated and rejected; see HANDOFF §4)

- **Blanket shrink-to-50** — in-sample mirage, fails OOS.
- **Team-specific historical stat priors** (FBref club-form, raw intl_form,
  API-Football historical) — failed 3× to beat a calibrated flat tournament
  prior. Lesson: for micro-stat buckets the signal is **environment-level, not
  team-level**. The qmodel win is calibrated math beating crowd overconfidence,
  not team-specificity.

## Security note

The self-improvement *agent* loop (`routines/improve.sh`, `wc-improve.timer`) is
**deliberately disabled**: running a code-capable agent as the user would expose
the trading credential in the sibling kalshi-tracker project (shared mount,
un-blockable). The discovery half (`audit.py`) and the deterministic review
(`review_report.py`) are safe and stay on. Keep all Kalshi access in this repo
read-only and unauthenticated.

## Skills & slash commands

- `/run-jump-probability-cup` (skill) — run/smoke/sanity-check the pipeline.
- `sportspredict` (skill) — contest API conventions for question ingest,
  submission/revision, scoring readback.
- `/harvest-locked` (command) — the recurring "email" chore: pull new
  locked-prediction emails via the Gmail connector → `data/emails/` corpus →
  re-grade us vs. field. IMAP auto-harvest is intentionally off.

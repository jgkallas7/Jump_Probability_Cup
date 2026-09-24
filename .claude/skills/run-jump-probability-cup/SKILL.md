---
name: run-jump-probability-cup
description: Run, smoke-test, drive, or sanity-check the Jump Probability Cup pipeline — the SportsPredict "Probability Cup" forecasting bot (consensus pricing, forecast, dry-run submit, settlement/Brier report). Use when asked to run/start/test the WC cup bot, price questions, check a forecast or submission sheet, verify a change to forecast.py/submit.py/derive.py/calibrate.py, or confirm the contest API is reachable.
---

# Run: Jump Probability Cup

A **live contest bot**, not an app with a window. It ingests SportsPredict
"Probability Cup" questions, prices them from a weighted consensus of sportsbook
odds (devigged), and POSTs integer 1–99 probabilities to the contest. The
production driver is cron (`routines/*.sh`); the **agent driver is
`smoke.sh`**, which exercises the real pricing/submission code **read-only and
dry-run** so you never touch the live leaderboard entry.

> ⚠️ This repo POSTs to a live, scored contest and spends paid Odds-API
> credits. `routines/morning.sh` runs `submit.py submit` and
> `derive.py --submit` — **real submissions.** Never run those to "test."
> The smoke below is the safe way to see the bot work.

All paths are relative to the repo root (`<unit>/`). The driver lives at
`.claude/skills/run-jump-probability-cup/smoke.sh`.

## Prerequisites

No apt packages — pure Python 3.12 + SQLite (stdlib). It runs from a venv:

```bash
# venv already exists here at ~/.wc_cup_venv (deps verified importable).
# To recreate on a clean machine:
/usr/bin/python3 -m venv ~/.wc_cup_venv
~/.wc_cup_venv/bin/pip install -r requirements.txt
```

Deps (`requirements.txt`): `requests`, `curl_cffi`, `mcp`, `pytest`.

Environment the scripts read (`config.py`, `sp_client.py`):
- `WC_DB_PATH` — DB location; **defaults to `~/wc_cup.db`** (the live
  DB; lives on ext4, never OneDrive — WAL corrupts there). The driver overrides
  this to a throwaway copy.
- `SP_API_KEY` — SportsPredict bearer key (needed only for `live` mode / any
  real submit). Already set in this environment.
- `ODDS_API_KEY` or `~/.odds_api_key` — only the network scripts need it; the
  smoke does not.

## Run (agent path) — the driver

```bash
# Offline smoke: drives a COPY of the live DB through every read-only/dry-run
# surface (db, forecast, dry-run submit, dry-run derive, calibrate report, pytest).
.claude/skills/run-jump-probability-cup/smoke.sh

# Add the one live, read-only network call (GET /events — proves API + auth):
.claude/skills/run-jump-probability-cup/smoke.sh live
```

What you'll see (verified this container, 2026-06-13): a priced submission sheet
for today's matches (e.g. `Brazil vs Morocco  55  Will Brazil win the match?`),
`derive` listing ~188 patch-candidates, the per-book closing Brier table
(`pinnacle 0.2293 …`), `16 passed` from pytest, and `ALL SMOKE STEPS PASSED`.
`live` prints `1 events visible … Jump Trading Probability Cup`.

How it stays safe: `smoke.sh` copies `~/wc_cup.db` to a tmpdir and
exports `WC_DB_PATH` at it, so every write lands on the copy; and it only calls
`--dry-run` / read-only paths, which never construct a network client that POSTs.
The tmpdir is removed on exit.

Useful overrides: `WC_PY=<python>`, `WC_LIVE_DB=<db to copy>`,
`WC_SMOKE_HOURS=<window>` (default 720, wide enough to price everything),
`WC_SMOKE_LINES=<output cap per step>`.

## Direct invocation — poke one stage

To inspect a single stage by hand, **first copy the DB** (forecast/derive write
rows), point `WC_DB_PATH` at the copy, and use the venv python:

```bash
PY=~/.wc_cup_venv/bin/python
TMP=$(mktemp -d); cp ~/wc_cup.db "$TMP/wc_cup.db"; export WC_DB_PATH="$TMP/wc_cup.db"
$PY forecast.py --hours 720                 # consensus pricing (pure, no network)
$PY submit.py submit --dry-run --hours 720  # submission sheet, NO POST
$PY derive.py --dry-run --hours 720         # alpha engine, NO POST
$PY calibrate.py report                      # settlement / Brier readout (read-only)
$PY sp_client.py                             # LIVE read-only: GET /events (needs SP_API_KEY)
rm -rf "$TMP"
```

`--dry-run` is genuinely dry: `submit.py` / `derive.py` don't build an `SPClient`
until *after* the dry-run branch returns (there's an explicit "dry-run must be
DRY" guard in `derive.py`).

## Production pipeline (LIVE — the smoke deliberately avoids these)

The cron routines. Listed so you recognize the destructive commands — **not run
by the smoke, and not to be run as a test.** Each spends credits and/or writes
to the live contest:

- `routines/morning.sh` — settle → ingest → snapshot → forecast →
  **`submit.py submit`** + **`derive.py --submit`** (real POSTs).
- `routines/sentinel.sh` — `sentinel.py` every 15 min; revises predictions in
  the T-75 close window (real PATCHes).
- `routines/weekly.sh` — `calibrate.py sync` + `report`; report-only by design.

Network/credit-spending scripts to know: `snapshot.py`, `fetch_schedule.py`,
`ingest_questions.py`, `calibrate.py sync`, plus the `mcp/odds_api_server.py`
MCP server (launched by Claude Code via `.mcp.json`, stdio).

## Test

```bash
~/.wc_cup_venv/bin/python -m pytest -q   # 16 passed in <1s
```

## Gotchas

- **`db.py` does NOT create the `meta` table** — `ingest_questions.py` does
  (it holds `sp_lobby_id`). A fresh `db.py`-only DB crashes `submit`/`calibrate`
  with `no such table: meta`. The smoke copies the populated live DB to avoid
  this; its empty-DB fallback skips the data-dependent steps.
- **`submit.py submit --dry-run` printing `nothing to submit` is success**, not a
  failure — on the live data every question in the window is already submitted.
  A populated dry-run sheet needs *unsubmitted* forecasts.
- **`forecast.py`/`derive.py` mutate whatever DB `WC_DB_PATH` points at** (they
  INSERT forecast rows). Always run them against a copy, never the live DB, when
  experimenting.
- **Use the venv python, not system `python3`** — deps are in the venv only
  (`include-system-site-packages = false`). `bin/python` is a symlink to
  `python3` but it's a real venv.
- **Run from the repo root** — scripts use flat imports (`import db, config`) and
  `__file__`-relative paths (`sheet.py` writes `data/sheets/`).
- **Read-back asymmetry:** you submit integer `75`; the API reads it back as
  decimal `0.75` (`calibrate._norm_prob` / `sp_client.my_predictions` handle it).
- **Noisy book:** `forecast.py` may show one book wildly off (seen:
  `marathonbet 0.1224` for Brazil-to-win vs `~0.58` everywhere else) — a single
  book's devig/mapping artifact, diluted by the weighted consensus, not a crash.

## Troubleshooting

- `no such table: meta` / `no sp_lobby_id in meta` → you ran a data step against a
  `db.py`-only DB. Copy the live DB to `WC_DB_PATH` (or run `ingest_questions.py`).
- `ModuleNotFoundError: requests/curl_cffi/mcp` → you used system `python3`. Use
  `~/.wc_cup_venv/bin/python` (or set `WC_PY`).
- `SportsPredict key missing: set SP_API_KEY` → only `live` mode / real submits
  need it; export `SP_API_KEY` or write `~/.sp_api_key`.
- `ODDS_API_KEY not found …` → only the network scripts need it; the smoke
  doesn't. Set the env var or write `~/.odds_api_key`.

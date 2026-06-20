# Handoff — Jump Probability Cup bot (2026-06-16)

Resume doc for a fresh session. Read this + the auto-loaded `MEMORY.md`. The bot
competes in the SportsPredict "Probability Cup" (FIFA WC 2026), one forecast per
contest question, scored **relative to the field** (NOT absolute Brier).

---

## 0. THE ONE MENTAL MODEL (don't relitigate)
- **Scoring is relative to the crowd.** Edge = being right where the field is wrong.
  A 50% guess only loses when the field is confidently elsewhere. Metric =
  realized relative-points-vs-field (see `parse_locked.py`).
- **Validate OUT-OF-SAMPLE before going live.** Every change ships behind a
  `WC_*` flag (default OFF) and must beat what-we-send on a clean OOS check first.
  In-sample wins are mirages — this has burned the project repeatedly (see §4).

## 1. WHAT'S LIVE IN PRODUCTION (`routines/morning.sh`, systemd user timers)
- `export WC_QMODEL=1` — counted-rate quant pricer for alpha (NO_MARKET) questions.
  **Validated**: clean OOS +110 vs what we sent, +28 vs field over 61 alpha Qs.
- `export WC_KALSHI=1` — Kalshi crowd mids blended/rescued into forecast.py for
  book-mapped totals/corners + score_or_assist via derive. **Validated**: totals
  match sharp book to 0.6pt; corners run +5-7pt high so they're blended (book
  primary, w=0.35), never overridden.
- `team_rates.py refresh` + `audit.py` run daily in morning.sh.
- Timers (`systemctl --user list-timers`): wc-morning 07:00, wc-sentinel /15min,
  wc-weekly Sun 19:00, **wc-apifootball 09:00** (historical data accumulation).
  **wc-improve is DISABLED** (security — see §5).
- 31 tests pass (`python -m pytest -q`). Branch `master`, **31 files UNCOMMITTED**
  (the whole session's work — user gates commits; the self-improvement loop needs
  a committed baseline to function, see §5).

## 2. THE PRICING ENGINE (new this session)
- `qprice.py` — pure scipy pricers (Poisson survival, Skellam, bivariate Poisson,
  first-goal, penalty-or-red). Tested in `tests/test_qprice.py`.
- `qmodel.py` — routes each alpha question → a qprice form with counted team
  rates (`team_rates.py`) + market goal-λ (`derive.match_lambdas`). `team SOT` is
  in `QMODEL_DISABLED_PREFIXES` (regressed). The WIN is calibrated math beating
  crowd/old-engine overconfidence, NOT team-specificity (see §4).
- `kalshi_wc.py` — UNAUTHENTICATED Kalshi reader (orderbook is public; no key, no
  trading credential touched). `forecast.combine_kalshi` = book-only / blend / rescue.
- `derive.py` — alpha engine; `_qmodel_price` (WC_QMODEL) + `_kalshi_price`
  (WC_KALSHI) preempt the old handlers.
- `evaluate_qmodel.py [--prior-only]` — the OOS gate (relative-pts vs field on
  settled questions, using `parse_locked` ground truth). `--prior-only` = clean
  no-look-ahead (strips per-team rates to the flat 2026-tournament mean).
- `parse_locked.py` — harvests "Prediction Locked" emails (`data/emails/`, 16 now)
  → field % + realized points. Fetch new ones via the Gmail MCP (search
  `"Prediction Locked" newer_than:Nd`, get_thread FULL_CONTENT, extract `htmlBody`).

## 3. DATA SOURCES (see `IMPROVEMENT_CHARTER.md` "External data sources" + memory)
- **Odds API** (MCP) — sportsbook tape, goal-λ anchors. Live, in use.
- **martj42 international_results** → `intl_form.py` — national-team attack/defence
  ratings from real qualifiers+friendlies (CC0 CSV, current). BUILT, **ON HOLD**:
  small nations break it (Germany-Curaçao λ 5.44). Needs non-FIFA filter +
  small-sample shrinkage + blend, then OOS. This is the GOALS layer (scores only).
- **Kalshi** — public orderbook mids, LIVE (see §2).
- **API-Football** (`~/.apifootball_key`, 0600) — `apifootball.py` (cached/
  guarded/throttled) + `apifootball_history.py` (accumulator+aggregator).
  **FREE TIER = seasons 2022-2024 ONLY** (no 2025/2026), 100/day + 10/min.
  68 fixtures cached so far; wc-apifootball.timer accumulates nightly. Gives
  historical team stat rates + **referee tendencies**.
- soccerdata FBref — flaky browser scrape; international = WC/Euro only (qualifiers
  NOT discoverable via read_leagues; FBref 403s direct). Don't rely on it.

## 4. DEAD ENDS — DO NOT REDO (all validated-and-rejected)
- **Blanket shrink-to-50** — in-sample mirage, fails OOS (pre-existing lesson in
  `derive.py:30`).
- **Team-specific historical stat priors** — FAILED 3× to beat a calibrated flat
  prior: (a) FBref club-form aggregation (browser scrape kept erroring), (b)
  intl_form raw (small-nation breakage), (c) API-Football historical team prior
  (naive −68; level-calibrated +193; flat-2026-mean still wins +223). **Lesson:
  for these micro-stat buckets the signal is ENVIRONMENT-level, not team-level.**
  `WC_APIF_PRIOR` exists but stays OFF.

## 5. SELF-IMPROVEMENT LOOP (built; the agent half is paused for security)
- `audit.py` → `data/opportunities.md` — daily discovery (unwired markets, losing
  buckets vs field). Runs in morning.sh. Safe, free, no secrets. KEEP.
- `IMPROVEMENT_CHARTER.md` — standing instructions (encodes all the lessons).
- `routines/improve.sh` + `wc-improve.timer` — daily headless `claude -p` agent,
  autonomy = "auto-implement behind a flag + OOS-validate, HUMAN approves go-live"
  (writes `data/improvement_log.md`). **TIMER DISABLED**: running a code-capable
  agent as the user exposes the Kalshi **trading credential** (`[redacted]`,
  on a shared mount → a restricted user can't be blocked). Re-enable ONLY after
  isolation (Docker mounting repo+DB but NOT kalshi-tracker is the one reliable
  option in this WSL). Cost is NOT a factor (Max subscription covers claude -p).

## 6. OPEN TASKS / NEXT STEPS
- **#9 — matchday-2 team-rate check (LIVE in-tournament rates, ≠ the rejected
  historical priors).** All 24 teams still have 1 WC game. When matchday-2 settles
  + locked emails arrive: harvest emails (Gmail MCP), `team_rates.py refresh`,
  `python evaluate_qmodel.py --prior-only`. If a bucket regresses → add to
  `QMODEL_DISABLED_PREFIXES`. (Expectation modest given §4, but current-tournament
  form ≠ stale history.)
- **#10 — REFEREE feature (the remaining promising lever; team-stat prior was
  rejected, §4).** Join each WC2026 ref (live, from FBref schedule — Tello/Vinčič/
  Sampaio etc.) to that ref's historical cards/fouls-per-match (API-Football
  2022-2024, year-insensitive; `apifootball_history.aggregate()['referees']`) as a
  multiplier on the cards/fouls/penalty buckets in qmodel. Flag-gated, OOS-validated.
  Shrink thin ref samples toward the comp mean.
- **intl_form.py hardening** — non-FIFA filter + small-sample shrinkage toward
  market + blend weight; then OOS-validate the goals/totals layer.
- **2025 gap (open decision)** — free API-Football excludes 2025 (most relevant
  pre-WC year); only the paid tier unlocks 2025 + live-2026 stats/referees. User's
  money call. Goals/results already current free via martj42.

## 7. QUICK COMMANDS
```
WC_DB_PATH=/home/jgkal/wc_cup.db /home/jgkal/.wc_cup_venv/bin/python -m pytest -q
WC_DB_PATH=/home/jgkal/wc_cup.db .../python evaluate_qmodel.py --prior-only   # OOS gate
WC_DB_PATH=/home/jgkal/wc_cup.db .../python audit.py                          # opportunity backlog
WC_DB_PATH=/home/jgkal/wc_cup.db .../python parse_locked.py                   # us vs field P&L
.../python apifootball_history.py aggregate                                   # ref/team historical rates (0 reqs)
python = /home/jgkal/.wc_cup_venv/bin/python   ;   DB = /home/jgkal/wc_cup.db
```

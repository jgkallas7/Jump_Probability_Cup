# Handoff — Jump Probability Cup bot (2026-06-16, refreshed 2026-07-01)

Resume doc for a fresh session. Read this + the auto-loaded `MEMORY.md`. The bot
competes in the SportsPredict "Probability Cup" (FIFA WC 2026), one forecast per
contest question, scored **relative to the field** (NOT absolute Brier).

> **2026-07-01 refresh.** Knockouts underway (R32 since Jun 28; 2× multiplier
> live via ingest_questions stage backfill). Realized standing (parse_locked,
> 701 settled email Qs): ours +1526 vs field-clone +1860 → **edge −335, of
> which −318 is the NO_MARKET (alpha) bucket** — see the per-FAMILY table now
> emitted by review_report.py (the lump hid opposite-signed families). The §2
> "+110/+28 validated" qmodel claim below is the original tiny-sample result
> and did NOT generalise — treat `data/alpha_audit_2026-07-01.md` (+ its §5
> corrections) and `data/improvement_log.md` (2026-07-01 entries) as current
> truth. Fixes built + committed (214b62e), gates in the nightly review:
> WC_FOULS_DOM=0.15 (+123/n=55), WC_SOT_TOTAL_ANCHOR=0.78 + WC_SOT_TEAM_ANCHOR
> =0.42 (+125/n=47), WC_KALSHI_NO_SOA=1 (+28/n=5 thin, structural),
> WC_CORNER_SUP_SLOPE=0.5 — **ALL FLIPPED LIVE 2026-07-01 evening** at the
> user's direction (governance policy updated in CLAUDE.md + flags.sh: the
> agent flips once the gate clears; the disabled improve-loop never does).
> Post-flip the SOT-anchor gate reads APPROVE +87 on the flag's full scope
> (was REJECT −79 pooled). WC_BTS_HALF_ANCHOR deliberately NOT set (0.68 is
> the optimum). WATCH the family table + gates nightly; pull back any flag
> whose gate slides. Everything is committed — the "31 files UNCOMMITTED"
> note in §1 is obsolete.

---

## 0. THE ONE MENTAL MODEL (don't relitigate)
- **Scoring is relative to the crowd.** Edge = being right where the field is wrong.
  A 50% guess only loses when the field is confidently elsewhere. Metric =
  realized relative-points-vs-field (see `parse_locked.py`).
- **Validate OUT-OF-SAMPLE before going live.** Every change ships behind a
  `WC_*` flag (default OFF) and must beat what-we-send on a clean OOS check first.
  In-sample wins are mirages — this has burned the project repeatedly (see §4).

## 1. WHAT'S LIVE IN PRODUCTION (systemd user timers; flags in `routines/flags.sh`
##    — the SINGLE source, sourced by BOTH morning.sh and sentinel.sh)
- Live flags as of 2026-07-01: WC_QMODEL, WC_KALSHI, WC_DEVCAP,
  WC_SOT_THRESH_ANCHOR, WC_PLAYER_SOT_ANCHOR, WC_SOT_RACE_GS(+DECOMP=1.5),
  WC_TO_ADVANCE_H2H, WC_PH_COVERAGE; WC_KALSHI_HTOTAL=0 (rejected). Each flag's
  gate + rationale is inline in flags.sh; the nightly review_report re-runs
  every gate on settled data.
- `export WC_QMODEL=1` — counted-rate quant pricer for alpha (NO_MARKET) questions.
  **Validated**: clean OOS +110 vs what we sent, +28 vs field over 61 alpha Qs.
  ⚠️ 2026-07-01: that early aggregate did NOT hold up — realized per-family it
  wins some buckets (pen_or_red +50, offsides ~0) and lost badly on others
  (fouls_race −128 pre-fix). Family-level, not engine-level, judgments now.
- `export WC_KALSHI=1` — Kalshi crowd mids blended/rescued into forecast.py for
  book-mapped totals/corners + score_or_assist via derive. **Validated**: totals
  match sharp book to 0.6pt; corners run +5-7pt high so they're blended (book
  primary, w=0.35), never overridden.
- `team_rates.py refresh` + `audit.py` run daily in morning.sh.
- Timers (`systemctl --user list-timers`): wc-morning 07:00, wc-sentinel /15min,
  wc-weekly Sun 19:00, **wc-apifootball 09:00** (historical data accumulation).
  **wc-improve is DISABLED** (security — see §5).
- 107 tests pass (`python -m pytest -q`). Branch `master`, fully committed
  through 214b62e (2026-07-01) — the committed baseline §5's improvement loop
  needs now exists.

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
- ~~#11 flag flips~~ DONE 2026-07-01 evening (all live, see refresh note).
- ~~#12 final/3rd-place stage~~ DONE: `_KO_CALENDAR` date fallback in
  `ingest_questions.backfill_stages` covers every KO round incl. third/final;
  13 mistagged matches + 101 settled outcome multipliers repaired.
- ~~#13 sentinel PATCH-400 loop~~ DONE: `locked_predictions` table — a 400
  (market locked server-side, can precede kickoff) marks the prediction final
  locally; submit.cmd_revise + derive.run skip it thereafter. 5xx/network stay
  retryable. Open refinement: read the market's REAL close time from the API
  so the last pre-lock revision isn't lost (Jun-27 lost 56→63 / 30→26).
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

# Jump Probability Cup — Forecast & Submission Architecture

**Contest:** Jump Trading Probability Cup (SportsPredict.com)
**Window:** June 11 – July 19, 2026 · 104 matches · 1,000+ probability questions
**Scoring:** Relative Brier — points per question = (field average Brier − your Brier) × 100. Multipliers: Group 1×, Elimination 2×, Final 3×. Cumulative points rank the leaderboard. You can lose points by being worse than the field. Questions are binary (0–100 → YES/NO outcome).
**Prize:** Jump Trading Probability Fellowship, Chicago HQ

---

## 1. Design Principles

1. **Market consensus is the baseline, not the model.** Sharp sportsbook prices are the strongest known predictor of soccer outcomes. The pipeline's job is to reproduce devigged consensus reliably, then earn marginal points where the consensus is weak (thin props, stale lines, late team news).
2. **Consistency beats brilliance.** 1,000+ questions means variance washes out. Never miss a submission deadline; a default consensus answer beats a blank or a rushed guess.
3. **Calibration discipline.** Track your own Brier decomposition (calibration + resolution) weekly during group stage. This is the golf post-tournament review loop, pointed at yourself.
4. **Honest probabilities first, deviation second.** Brier is a proper scoring rule — your expected score is maximized by your true belief. Deviate from consensus only with a documented reason, and log every deviation for review.

---

## 2. Pipeline Overview

```
┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌─────────────┐
│ Question     │→ │ Data Layer    │→ │ Forecast      │→ │ Submission   │
│ Ingest       │  │ (odds, news)  │  │ Engine        │  │ + Audit Log  │
│ (SportsPredict│  │ Odds API +    │  │ devig → blend │  │ manual or    │
│  scrape/API) │  │ alt sources   │  │ → shrink      │  │ automated    │
└─────────────┘   └──────────────┘   └──────────────┘   └─────────────┘
                                            ↓
                                   ┌──────────────────┐
                                   │ Calibration Loop  │
                                   │ (weekly review)   │
                                   └──────────────────┘
```

SQLite DB: `wc_cup.db` (same pattern as golf_ws.db — append-only event log + state tables).

---

## 3. Question Ingest

**Unknown until you read the platform:** does SportsPredict expose an API, or is it web-form only?

- **If API/JSON exists:** poller pulls open questions, deadlines, and question types into `questions` table. Sentinel fires when new questions open or deadlines approach.
- **If web-only:** Claude in Chrome or a manual daily routine. Worst case, a morning + pre-match checklist. Build the pipeline to emit a *submission sheet* (question → probability → rationale) you can key in fast on your phone.

Question taxonomy to expect (~10 per match):
- Match result (3-way: home/draw/away)
- Totals (over/under 2.5 goals, etc.)
- Both teams to score
- Possibly: cards, corners, first goal timing, player-level
- Possibly: tournament-level futures (group winners, advancement)

Tag each question with its market mapping (or `NO_MARKET` if no book prices it — these are flagged as **alpha questions**).

---

## 4. Data Layer

### Odds API (primary)
- Sport key: `soccer_fifa_world_cup` (confirm via FREE `/sports` endpoint)
- `/events` polling is FREE — poll liberally for schedule + event IDs
- **Region strategy:** add `eu` to get Pinnacle — the sharpest soccer book. Consensus should weight Pinnacle heavily; soccer is a European market and US books are followers here.
- Core fetch: `h2h,totals` for all matches in the next 48h window
- Props: `/events/{id}/markets` (1 credit) to discover, then targeted `/events/{id}/odds` — props appear close to kickoff
- **Credit budget:** 104 matches × ~2 pulls each × 2-3 markets × 2 regions ≈ comfortably over 500/month free tier. Decide early: upgrade tier (~$30/mo for 20K credits) or ration to one pull near deadline per match. Given the prize, upgrade — this is not the place to economize.

### Devig method
Use **power method or Shin's method**, not plain multiplicative — soccer has a real favorite-longshot bias and 3-way markets make this material. For 3-way h2h:
- Multiplicative devig as fallback/sanity check
- Power devig as primary: solve `Σ pᵢ^k = 1` for k
- Log both; if they diverge >1.5 points on any outcome, flag for manual look

### Secondary sources (for alpha questions + tiebreaks)
- Kalshi + Polymarket World Cup markets (you have the Kalshi MCP already) — cross-check, and prediction markets sometimes lead books on news
- Lineup/news feed: confirmed XIs drop ~60–75 min pre-kickoff; lines move on them. If submission deadlines fall *after* lineup release, this is a systematic edge over entrants who submit the night before.
- A simple model layer (optional, week 2+): Elo or Poisson goals model fit on international match history — not to beat the books on match result, but to price questions books don't quote.

---

## 5. Forecast Engine

```
fair = devig(weighted_consensus(books, weights={pinnacle: 3, dk: 1, fd: 1, ...}))
prior = model_estimate (Poisson/Elo) where available, else category base rate
forecast = w · fair + (1 − w) · prior        # w ≈ 0.85–0.95 when sharp prices exist
forecast = shrink_extremes(forecast)          # never submit 0.00 or 1.00; floor/ceiling ~0.5%/99.5%
```

Rules of thumb:
- **Priced questions:** trust the market. w ≥ 0.9. Your value-add is *timing* (fetch as late as the deadline allows) and clean devig.
- **Unpriced/thin questions:** this is where ranks are won. Build base-rate tables from historical World Cup data (e.g., P(red card in match), P(0-0 draw), goal timing distributions). DataGolf taught you calibration > accuracy — same here.
- **Never round to 0/100.** Brier punishes confident wrongness quadratically. One 99%-er that misses costs more than dozens of well-calibrated answers earn.

### Strategy under relative scoring (the real rules)

Expected points per question = E[field Brier] − E[your Brier]. Your own Brier is minimized by your honest probability — so **honest sharp consensus remains the optimal per-question forecast regardless of what the field does.** The field model doesn't change your forecast; it changes *posture*:

1. **Answer every priced question.** Consensus beats a recreational field in expectation on essentially all of them; skipping leaves points on the table. Cumulative scoring means volume × small edge compounds.
2. **The field's soft spots are your yield:** draws underestimated (binary "Will X win?" questions — NO includes the draw; devig 3-way, then map to binary carefully), brand-name favorites overpriced, extreme submissions (90+ when truth is ~70). Hard, divided questions pay the most relative points; easy questions pay near zero — don't sweat them.
3. **Build a crowd model from feedback.** Field-average Brier per question is published after settlement. Log it (`field_avg_brier` column in `outcomes`). Within a week you'll know empirically how miscalibrated the field is by question type — that tells you where points concentrate and how much your consensus pipeline is actually earning vs. the market alone.
4. **Multiplier-aware variance control.** Group stage (1×) is the accumulation + calibration phase: pure honest consensus, max volume. Knockouts (2×) and Final (3×) are where leaderboards swing. Late-game posture depends on position:
   - **Leading:** your relative score freezes if you submit the *field average* (edge → 0). You can't observe it directly, but your crowd model approximates it — blending your forecast slightly toward modeled-crowd on 3× questions reduces the variance of your lead at small EV cost. Defensive convergence.
   - **Trailing:** you need divergence. Take your honest forecast and lean *into* your highest-conviction disagreements with the modeled crowd on 2–3× questions — not fabricated extremes (proper scoring still punishes lying), but full-size honest positions where the crowd is most wrong.
5. **Knockout question semantics:** no draws after extra time/pens — confirm whether questions resolve on 90-minute result or advancement. Books quote both; matching the wrong one is an unforced error.
6. **Alpha questions (no book pricing):** the field is guessing; a calibrated base-rate table likely beats them. Answer these too unless you genuinely have nothing.

---

## 6. Database Schema (wc_cup.db)

```sql
questions(qid PK, match_id, qtype, text, opens_at, deadline, weight, status)
market_snapshots(id PK, qid, ts, book, raw_odds_json, devig_method, fair_prob)
forecasts(id PK, qid, ts, consensus_prob, model_prob, blend_w, final_prob,
          deviation_bps, deviation_reason, submitted_at, submitted_prob)
outcomes(qid PK, resolved_at, outcome, brier, field_avg_brier, relative_points, multiplier)
calibration_buckets(bucket, n, mean_forecast, hit_rate, updated_at)
```

The `deviation_reason` column is mandatory whenever `final_prob != consensus_prob`. This is your agent-decision audit trail — same value-add-vs-baseline review you run on the golf system.

---

## 7. Agent Loop (Claude Code orchestration)

- **Morning agent (daily ~7am, before the train):** pull today's matches + open questions, fetch overnight line moves, emit submission sheet for anything due before evening.
- **Pre-match sentinel (T-90 to T-45 min per match):** fetch final odds + confirmed lineups, finalize forecasts for that match's questions, alert phone if a line moved >3 points since last snapshot.
- **Settlement agent (post-match):** grade outcomes, compute running weighted Brier, update calibration buckets.
- **Weekly review (Sunday):** calibration plot by bucket, deviation P&L (did your deviations beat consensus?), leaderboard position → adjust deviation aggressiveness per §5.

The `/loop` pattern fits the sentinel; the morning/weekly jobs are plain cron + Claude Code headless.

---

## 8. Open Questions (still unresolved)

1. **Question resolution semantics** — knockout matches: 90-minute result or advancement? Confirm per question type before mapping to book markets.
2. **Submission deadline per question** — kickoff? Earlier? Can you *revise* until deadline? (If yes, submit early defaults, revise at T-60 with lineups.)
3. **Is there an API or bulk-submit?** Determines ingest architecture (§3).
4. **Eligibility fine print** — employer conflict check. BofA likely fine for a free contest with no wagering, but read it; and think about whether the *fellowship* itself triggers anything with your current employment.
5. **One account per person / household rules** — determines whether the "we" from LinkedIn can run differentiated entries legally. **If multiple entries among friends are allowed, run distinct deviation theses per entrant — that's a free portfolio of rank outcomes.**
6. **Tiebreakers** at the top of the leaderboard.
7. **Is field-average Brier published per question?** (FAQ implies yes post-settlement.) If so, the crowd model in §5 is fully data-driven.

---

## 9. Build Order

**Tonight (after rules read):**
1. Register, confirm question format + deadlines → resolve §8
2. `/sports` call to confirm `soccer_fifa_world_cup` key + `/events` pull for full schedule (FREE)
3. Decide Odds API tier upgrade
4. Stand up `wc_cup.db` + snapshot fetcher for tomorrow's openers

**Day 1–3 (group stage opens):**
5. Devig module (power + multiplicative) with divergence flagging
6. Submission sheet generator (manual keying is fine week 1)
7. Settlement + calibration tracking

**Week 2:**
8. Sentinel automation, lineup-news trigger
9. Base-rate tables for alpha questions
10. First weekly review → tune blend weight + deviation policy


# Contest Rules — Resolved Facts (SPEC §8 answers)

Sources: sportspredict.com/probabilitycup{,/api,/scoring,/faq}, probability-cup-terms.
Fetched 2026-06-10. Quotes are verbatim from the published docs.

## Resolved

| # | SPEC §8 question | Answer |
|---|------------------|--------|
| 2 | Revise until deadline? | **YES.** `PATCH /predictions/{id}` — "the latest value at market close is what gets scored." Markets close "in the last second before matches start." Submit early defaults, revise at T-60 after lineups. |
| 3 | API / bulk submit? | **YES.** REST API, bearer auth (`sp_live_*`, shown once at creation), `POST /predictions/batch` (1-50, independently validated), 60 req/min per IP, up to 2 bots per account. "Use any tool, model, or bot. We score forecasts, not methods." There is also an MCP server. |
| 7 | Field-avg Brier per question? | Partially. Relative points formula confirmed: `(field_avg_brier − your_brier) × 100`, multipliers group 1×/elim 2×/final 3×. Whether the per-question field average is published is still unconfirmed — check `GET /results` payload after first settlements. |
| 7b | Third-place match multiplier? | **2× (elimination), not 3×.** Scoring docs re-fetched 2026-07-16: "Group Stage: 1× • Elimination Rounds: 2× • Final: 3×" — no separate bronze tier, so the bronze final falls under elimination. Matches `config.STAGE_MULTIPLIER` (`third: 2.0`). The 2026-07-16 "The Final is Worth 3x!" newsletter refers to the final only. Definitive per-match confirmation = the ⚡ multiplier banner in the Jul-18 locked email. |
| — | Submission format | Integers **1-99 inclusive**. One prediction per market per user. ~10 binary markets per match. |
| — | Question ingest | `GET /events` → `/lobbies` → `/matches` → `/markets` (binary yes/no per match). Clean API ingest; no scraping needed. |

## Resolved in second pass (full API docs, 2026-06-10)

- **§8 Q1 (knockout semantics):** question text disambiguates — sample reads
  "Will Mexico win the match **in regulation**?" Map per question text, not
  per stage. 90-min questions -> 3-way h2h; advancement -> to-advance markets.
- **§8 Q5 (multi-entry):** VERBATIM from /probabilitycup/api (re-fetched
  2026-06-12): "Each API key is a separate leaderboard entry, and your manual
  app picks are a separate entry from those." "Up to 2 active bots per user
  (each bot has one API key)." So 2 bots + manual = 3 entries. The "one
  prediction per market per user" sentence sits next to the PATCH-to-revise
  instruction — it's revision guidance (don't re-POST, PATCH), per entry,
  not an account-wide cap. Still confirm live with key #2 on one cheap
  market before strategizing (and before key #2 touches any pipeline
  script — overwrite risk if this reading is wrong).
- Deadline of record: each market's `closing_time` field (poll it; don't
  assume kickoff).
- Read-back gotcha: write integer 75, read back decimal 0.75.
- Field-avg Brier is NOT exposed in `/results` — crowd model must be inferred
  (own Brier vs leaderboard movement) unless the platform publishes it later.
- **BUT (found 2026-06-11): the "Your N locked predictions" email sent at
  kickoff exposes per-question field consensus % and If-Yes/If-No relative
  points** (from which field_avg_brier per question is exactly recoverable:
  rel = (field_avg_brier − yours) × 100). Parse these from Gmail per match —
  this is the crowd model the API withholds.
- No webhooks/pagination; poll-based settlement via `GET /results`.
- **No leaderboard/standings/rank endpoint exists (confirmed 2026-06-16 from the
  `/probabilitycup/api` docs + a full path probe).** Docs verbatim: "Your aggregate
  Smart Rating / Relative Brier Points … is computed by the leaderboard service and
  surfaces in the SportsPredict leaderboards rather than this endpoint." Neither the
  REST API nor the platform MCP exposes a rank; guessed paths 500 (route-absent, not
  bad-params). Rank is **UI-only**: `play.sportspredict.com/probability-events/{event}/leaderboard`
  (needs the account login, NOT the sp_live_ bot key). `GET /results` is your own
  settled briers only. Track standing via our cumulative relative-points (parse_locked),
  not an API call. Do not re-probe for a leaderboard endpoint.

## Prizes (fetched 2026-06-11, sportspredict.com/probabilitycup)

- **#1:** 10-week paid fellowship at Jump Trading, Chicago — "help trade a
  $1,000,000 sports-related portfolio."
- **#2–5:** Apple iPad Pro. **#6–10:** $200 Ticketmaster gift card.
- **Per-match "Top Forecaster" gift exists** (discovered 2026-07-13 via a
  "YOU WON THE MATCH" email from jose.medina@sportspredict.com): the #1
  forecaster on a single match gets a gift. We won it once (Argentina vs
  Switzerland QF). Separate from the season leaderboard — winnable on any
  single match regardless of overall rank.
- Leaderboard is unified humans+bots; bots get a "BOT" label. Extremely
  top-heavy payout → variance-seeking (decorrelated second entry) is +EV
  for P(top finish); a pure consensus-follower converges to field-average
  relative points by construction.

## Still open

| # | Question | Plan |
|---|----------|------|
| 4 | Employer conflict | Terms only require a free SportsPredict account; no employment language found. User judgment. |
| 5b | Two bots, same market, different probs? | Live test once both keys exist. |
| 6 | Tiebreakers | "Determined as described in the Official Rules" — not published. Ignore; play for clear first. |

## Architecture consequences

- Full automation pipeline is sanctioned: API poller for question ingest,
  batch submit defaults early, PATCH-revise near kickoff with final odds +
  confirmed lineups. The phone-keying submission sheet is unnecessary.
- Submissions are integer percent: keep floats internally, `round()` at
  submission, floor/ceiling 1/99.
- LLM benchmark entrants (Claude/GPT/Gemini) receive match context at T-30
  but **no live odds** — a devigged-consensus pipeline should beat them.
- Deadline at kickoff (not earlier) means the lineup window (T-60..T-75)
  is fully usable — a systematic edge over night-before submitters.

# Contest Rules — Resolved Facts (SPEC §8 answers)

Sources: sportspredict.com/probabilitycup{,/api,/scoring,/faq}, probability-cup-terms.
Fetched 2026-06-10. Quotes are verbatim from the published docs.

## Resolved

| # | SPEC §8 question | Answer |
|---|------------------|--------|
| 2 | Revise until deadline? | **YES.** `PATCH /predictions/{id}` — "the latest value at market close is what gets scored." Markets close "in the last second before matches start." Submit early defaults, revise at T-60 after lineups. |
| 3 | API / bulk submit? | **YES.** REST API, bearer auth (`sp_live_*`, shown once at creation), `POST /predictions/batch` (1-50, independently validated), 60 req/min per IP, up to 2 bots per account. "Use any tool, model, or bot. We score forecasts, not methods." There is also an MCP server. |
| 7 | Field-avg Brier per question? | Partially. Relative points formula confirmed: `(field_avg_brier − your_brier) × 100`, multipliers group 1×/elim 2×/final 3×. Whether the per-question field average is published is still unconfirmed — check `GET /results` payload after first settlements. |
| — | Submission format | Integers **1-99 inclusive**. One prediction per market per user. ~10 binary markets per match. |
| — | Question ingest | `GET /events` → `/lobbies` → `/matches` → `/markets` (binary yes/no per match). Clean API ingest; no scraping needed. |

## Resolved in second pass (full API docs, 2026-06-10)

- **§8 Q1 (knockout semantics):** question text disambiguates — sample reads
  "Will Mexico win the match **in regulation**?" Map per question text, not
  per stage. 90-min questions -> 3-way h2h; advancement -> to-advance markets.
- **§8 Q5 (multi-entry):** "each bot is a separate leaderboard entry," 2 bots
  per account. CONFLICTS with "one prediction per market per user" — verify
  live with both keys on one cheap market before strategizing on it.
- Deadline of record: each market's `closing_time` field (poll it; don't
  assume kickoff).
- Read-back gotcha: write integer 75, read back decimal 0.75.
- Field-avg Brier is NOT exposed in `/results` — crowd model must be inferred
  (own Brier vs leaderboard movement) unless the platform publishes it later.
- No webhooks/pagination; poll-based settlement via `GET /results`.

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

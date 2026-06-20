---
name: sportspredict
description: SportsPredict Probability Cup API conventions — question ingest, prediction submission/revision, scoring readback. Use whenever calling the contest API (sp_client.py), the sportspredict MCP server, or building pipeline steps that touch questions, predictions, or results.
---

# SportsPredict Contest API

Base: `https://api.sportspredict.com/api/v1` · Bearer `sp_live_*` key
(resolution: `SP_API_KEY` env, then `~/.sp_api_key`). Repo client:
`sp_client.py`. MCP server at `/api/v1/mcp` (same key, same rate pool).

## Hard constraints (violations are 4xx)

- Probabilities WRITE as integers 1-99. They READ BACK as 0-1 decimals
  (`75` in, `0.75` out). Never compare write-form to read-form.
- Batch = 1-50 predictions; failures are per-entry, no rollback — always
  check `results[].success`, never assume all-or-nothing.
- 60 req/min per IP shared across REST + MCP. Group stage has ~720 markets:
  full-lobby market scans are 318 KB and fine for scripts, but ALWAYS filter
  `match_id` in LLM/MCP contexts.
- One prediction per market; revise with `PATCH /predictions/{id}` until
  market close. Latest value at close is scored. 409 = already predicted
  (recover via GET /predictions to find the id, then PATCH).
- No pagination, no webhooks. Poll `GET /results?lobby_id=` for settlements.

## Flow

1. `GET /events` → Probability Cup event id (`type: "probability"`)
2. `GET /lobbies?event_id=` → single shared lobby; `POST /lobbies/{id}/join` once
3. `GET /matches?event_id=&lobby_id=` → matches with `open_market_count`,
   `opening_time`, `closing_time`. Predictions lock at `opening_time`
   (= kickoff; confirmed 2026-06-12 by the "locked predictions" email
   arriving at kickoff). `closing_time` is the expected final whistle —
   NOT the prediction deadline; never assume in-play revision works.
4. `GET /markets?lobby_id=&match_id=` → binary questions (~10/match)
5. `POST /predictions/batch` early defaults → `PATCH` near close with
   final odds + lineups

## Question semantics

- Match-result questions say "in regulation" explicitly — map to 90-minute
  3-way book h2h (NO includes the draw). Knockout advancement questions, if
  they appear, map to to-advance markets instead. Read the question text;
  never assume by stage.
- Tag every market on ingest with its book mapping or NO_MARKET (alpha
  question — price from base rates).

## Bots / entries

2 active keys per account, created in UI: Probability Cup event → Profile &
History → My Bots → Generate New Bot. Key shown ONCE. Docs state each bot is
a separate leaderboard entry, but also "one prediction per market per user" —
UNVERIFIED whether two bots can hold different probs on the same market.
Test on a cheap group-stage market before building strategy on it.

## Scoring readback

`brier_score` is null until settled; relative points = (field_avg_brier −
yours) × 100, multipliers 1×/2×/3× by stage. Field-average Brier per question
is NOT in `/results` — log our own Brier and infer field strength from
leaderboard deltas until/unless the platform exposes it.

## Errors

`{statusCode, message}` shape. 409 = duplicate (predict/join), 422 =
validation (prob out of 1-99, bad UUID), 429 = back off (exponential),
500 = retry with backoff.

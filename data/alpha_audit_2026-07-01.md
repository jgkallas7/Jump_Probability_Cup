# Alpha (NO_MARKET) audit — 2026-07-01

Session goal: audit the workflow/pricing for mistakes, hand-waves, and
contradictions, and improve the non-sportsbook (NO_MARKET / alpha) questions.
Driven by the realized-edge collapse: the review's `edge vs consensus clone`
fell from **−186 (06-28) → −335 (07-01)** as the knockouts (2× multiplier) began,
and **−318 of that −335 is the single NO_MARKET bucket**.

## 1. The leak, broken down by question FAMILY (new — `review_report` lumps all NO_MARKET)

Realized relative points on the settled locked-email corpus, `ours − clone`
(clone = submit the field %). Worst-first, alpha only:

| family | n | edge | our_p | fld_p | yes% | read |
|---|---|---|---|---|---|---|
| **fouls_race** | 55 | **−128** | 0.48 | 0.53 | 53 | symmetric price; underdog fouls more, we miss it → **FIXED this session** |
| sot_race | 45 | −96 | 0.49 | 0.52 | 36 | mostly PRE-fix history (GS flag 06-25, leading-form 06-29); under-differentiated favourite |
| both_teams_sot | 16 | −94 | 0.60 | 0.65 | 69 | 0.45 sent = PRE-fix placeholder catch-all; post-fix half anchor 0.68 still ~4pt under field |
| own_goal | 22 | −55 | 0.31 | 0.34 | 23 | sent = stale 0.35 catch-all; new 0.07 handler (06-30) untested + maybe too low |
| corners | 32 | −36 | 0.42 | 0.47 | 53 | SAME shape as fouls: we under-price, field higher — candidate for a game-state tilt |
| half_other | 31 | −31 | 0.48 | 0.49 | 32 | mixed halftime props |
| sot_total_threshold | 18 | −25 | 0.60 | 0.62 | **89** | near-certainty we price 0.60 → systematic under-confidence on totals |
| offside_Nplus | 58 | +13 | 0.45 | 0.48 | 40 | ~neutral now (was flagged low; counted rates have caught up) |
| pen_or_red | 30 | +50 | 0.29 | 0.37 | 23 | winning — counted union pricer works |

The `fouls_race` fix alone addresses the largest slice. `sot_race`,
`both_teams_sot`, and `own_goal` are dominated by **stale pre-fix submissions**
(the fixes shipped 06-25…06-30 but can't rescore already-locked questions), so
their forward-relevant edge is much smaller than the cumulative number.

## 2. Mistakes / hand-waves / contradictions found

1. **Thesis contradiction (headline).** CLAUDE.md + memory assert "qmodel =
   calibrated math beating crowd overconfidence." On REALIZED data the alpha
   bucket is **−318 vs the field** — the crowd is *better*, not worse. The
   original +110 / +28-vs-field claim (2026-06-16) was a 61-question, partly
   look-ahead-contaminated early sample that did not generalise. The per-family
   split shows it's not uniform: some pricers win (pen_or_red +50, offsides ~0),
   one loses catastrophically (fouls_race −128). "qmodel beats the crowd" was
   over-generalised from a good aggregate on a tiny sample.

2. **A counted-rate "upgrade" that was a regression (fouls_race).** The prior
   `derive.h_fouls_race` returned `0.5 − 0.08·(p_win−0.5)/0.5` — labelled
   *"underdogs foul more (weak)"*: correct SIGN, too-small coefficient. qmodel
   replaced it with a **symmetric** counted-rate Skellam (no game-state term),
   and because `WC_QMODEL=1` preempts the derive handler, production ships the
   *directionless* price. So the "quant" pricer is strictly worse than the weak
   heuristic it shadows on this bucket. → **fixed via `WC_FOULS_DOM`** (restores
   the underdog tilt at a validated strength, +123 OOS).

3. **`corners` shows the identical unfixed pattern** (−36): our_p 0.42 < field
   0.47, resolves YES 53%. Corners, like fouls, track game-state (the trailing/
   dominated team wins more corners late) and qmodel/derive price them
   ~symmetrically. Strong candidate for the same goal-share tilt — teed up, not
   yet built (would want its own OOS sweep; corner-share sign vs fouls differs —
   the *dominant* team gets more corners, so it's `_dom`-direction, not inverse).

4. **`sot_total_threshold` under-confidence** (−25, priced 0.60 while resolving
   **89% YES**). "N+ total shots on target" is a near-certainty we price like a
   coin-flip-plus — the summed SOT λ is too low and/or Poisson under-weights the
   upper tail. Field (0.62) is barely better, so the whole market underrates it;
   a calibrated bump (or an overdispersion guard like the both-teams-SOT 0.80
   anchor) is a clean win. Teed up.

5. **Stale-submission masking.** Several big cumulative bucket numbers
   (`both_teams_sot` −94, `own_goal` −55) are dominated by prices locked BEFORE
   the relevant handler shipped — `placeholders.run()` only fills UNsubmitted
   questions, so a fixed base rate never rescores an already-locked one. The
   cumulative review therefore over-states the LIVE deficit. Not a code bug, but
   a reporting hand-wave: "−318 NO_MARKET" reads worse than the forward reality.

6. **`own_goal` handler may have over-corrected.** The 06-30 fix set own-goal to
   0.07, but the settled corpus shows a 23% YES rate on the family (n=22, small /
   possibly regex-broad). 0.07 could now be too LOW. Needs a settled-outcome check
   before trusting — flagged, not changed.

## 3. Shipped this session

**`WC_FOULS_DOM`** (default 0.0 = OFF = unchanged production). Tilts the
fouls_race Skellam by market goal-share (underdog up, favourite down), the
inverse of qmodel's existing `_dom()` shot scaling. Clean prior-only OOS peak at
slope **0.15**: +123 realized pts vs the current symmetric price over 55 Qs, and
it *beats the field-consensus clone* (+55) — the "beat the crowd" result the
qmodel thesis promised, restored. 5 new tests; review_report gate added; full
suite 93 pass. **Recommend the human set `export WC_FOULS_DOM=0.15` in
routines/flags.sh** (details + enable command in `data/improvement_log.md`).

## 4. Ranked next targets (evidence above, not yet built)

1. **corners game-state tilt** (−36, `_dom`-direction) — same mechanism, own sweep.
2. **sot_total_threshold bump / overdispersion guard** (−25, 89% YES vs 0.60).
3. **both_teams_sot half anchor 0.68 → ~0.72** (field 0.72–0.76 on the halves; thin n).
4. **own_goal 0.07 sanity-check** against the settled YES rate before trusting it.

---

## 5. CORRECTIONS (same day, round 2 — per-question pricing attribution + full
## outcomes table; three of the four "next targets" above were wrong or misaimed)

- **§1 `own_goal` row and §2.6 are a MISCLASSIFICATION.** The family regex
  matched "own goal" inside "…score or assist a goal **(excluding own goals)**",
  so 21 of the 22 rows are player score-or-assist questions. The real own-goal
  handler has n=1 settled (a NO — 0.07 stands). The −55 belongs to
  **score_or_assist**: kalshi-mid −40/n=5 (no spread guard on thin player books
  → mid pinned ~0.5), placeholder −17/n=9, book-union −5/n=6. The union rarely
  fired because `derive._player_prob` still had the pre-cc4a453 tokenizer
  (kept "(Belgium)", dropped accents) — fixed now; plus `WC_KALSHI_NO_SOA`
  routes SOA off Kalshi (recommend ON despite thin-n HOLD; structural defect).
- **§4.1 corners tilt: refuted as a blanket.** Family split: h1 races **+32**
  (7/7 NO), team corners **+21**, h2 races −58 (8/9 YES, worst rows = the
  `anchored-supremacy` fallback whose tilt caps at ±0.10 vs ladder-implied
  ±0.17). Shipped `WC_CORNER_SUP_SLOPE` (fallback only) + two coverage handlers
  ("at least N corner kicks", "corners before the first hydration break") —
  NOT a family-wide tilt, which would have destroyed the winning h1 side.
- **§4.3 bts anchor: direction was BACKWARDS.** Post-fix rows sat ABOVE the
  field (0.70 vs 0.63) on a family settling 62–67% YES; sweep says 0.68 is
  already optimal (0.72 → −2.2). Knob shipped (`WC_BTS_HALF_ANCHOR`); no flip.
- **§4.2 sot_total: confirmed and generalized.** Total-SOT settles 84% YES
  (n=19) but team-SOT settles **39%** (n=33) — the pooled 0.65 anchor is wrong
  in OPPOSITE directions. Split anchors (total 0.78 / team 0.42) gate
  **APPROVE +125 over n=47**. See improvement_log 2026-07-01 (later).

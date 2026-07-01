# Improvement log

Daily entries from the self-improvement agent (`routines/improve.sh`, autonomy:
auto-implement + validate, human approves go-live). Each entry: what was built,
the out-of-sample evidence, the exact command to enable it, and an APPROVE/HOLD
recommendation. **You read this each morning and flip the flag if APPROVE.**

Review a day's work:  `git diff master..auto/improvements`

---

## 2026-06-16 — bootstrap (human)
Self-improvement loop installed: `audit.py` (discovery, runs in morning.sh),
`IMPROVEMENT_CHARTER.md` (standing instructions), `routines/improve.sh` +
`wc-improve.timer` (daily 08:00 agent). Flags currently live & pending forward
validation: `WC_QMODEL=1`, `WC_KALSHI=1` (both enabled in morning.sh this turn).
First agent run: 2026-06-17 08:00.

## 2026-06-16 — CONFIRM WC_QMODEL (clean OOS, 16 matches / 61 alpha Qs)
Harvested the 9 missing locked emails (7→16 matches, 80→158 graded Qs) via Gmail
MCP. Re-ran the clean no-look-ahead eval (`evaluate_qmodel.py --prior-only`):
  - clean qmodel = +223.6 rel pts vs +113.3 sent (+110) and vs +195.1 field (+28.5)
  - per-bucket: offsides +50, total +34, btts +30, pen|red +29, penalty +17,
    fouls +17 all beat sent; only `team SOT` (n=1) regresses and is already in
    QMODEL_DISABLED_PREFIXES.
  - the win is from calibrated market-anchored math (Poisson/Skellam/bivariate),
    NOT team rates (stripped in prior-only) — so it's legitimately clean.
RECOMMENDATION: **APPROVE keeping WC_QMODEL=1** (already live in morning.sh). No
bucket to disable. PENDING: team-rate layer (per-team offsides/fouls/SOT) clean
validation still needs matchday-2+ (teams with a 2nd game); it's additive-only.

## 2026-06-16 — Stage 1 (national-team form) + data-source research
Research agent mapped free soccer data (see IMPROVEMENT_CHARTER.md "External data
sources"). Key: soccerdata already wraps Football-Data.co.uk + ClubElo (plain CSV);
martj42/international_results gives clean intl results; API-Football is the one-stop
for national-team per-fixture stats + referees (needs free key).
PIVOT: abandoned club-player aggregation via FBref (browser scrape kept FAILING) for
`intl_form.py` — national-team attack/defence Poisson ratings from martj42's recent
qualifiers+friendlies (~40-50 matches/team, the user's idea, instant CSV). WORKING:
sensible ratings (Germany/Brazil top attacks; Iran-NZ 2.03/0.64). NOT flip-ready —
cross-check vs market avg |Δtotal|=0.73 goals but small-nation failures (Germany-
Curaçao intl 5.44 vs market 2.60). TODO before wiring: non-FIFA filter + small-sample
shrinkage toward market + blend weight, then evaluate_qmodel OOS. HOLD.

## 2026-06-16 — API-Football wired (historical stats + referee tendencies)
Key stored ~/.apifootball_key (0600). Built apifootball.py (cached/guarded/throttled
client) + apifootball_history.py (budget-capped accumulator + aggregator). FREE TIER
= seasons 2022-2024 only (no live 2026) — so it's the HISTORICAL prior source:
national-team offsides/fouls/corners + referee cards/fouls per match. Live 2026 ref
ASSIGNMENT comes from FBref schedule (Tello/Vinčić/Sampaio ref both years). Verified
working (Orsato 6 cards/30 fouls; Argentina 10 off/7 fouls/9 corners). wc-apifootball.timer
accumulates daily 09:00 (cap 85, self-limits once cached). NEXT: once coverage builds
(~days, 1 req/fixture, 10/min), wire (a) historical team stat rates as the team_rates
PRIOR, (b) ref tendency × FBref assignment as a card/foul/pen feature in qmodel — both
behind flags, OOS-validated.

## 2026-06-16 — API-Football historical TEAM prior: validated, REJECTED (HOLD)
Wired API-Football 2022-2024 historical team rates as the team_rates prior
(WC_APIF_PRIOR flag). Clean OOS (61 alpha Qs, no look-ahead):
  - naive historical prior: -68.3 (DISASTER — 2026 env much tamer: offsides x0.65,
    red cards x0.29 vs 2022-24 tournaments)
  - level-calibrated historical-relative: +193.2 (beats sent +113, ~ties field)
  - but the FLAT 2026-tournament-mean prior still WINS at +223.6
CONCLUSION: team-specific historical rates do NOT beat a calibrated flat 2026 prior
for these micro buckets (3rd time team-specificity has lost: club-form, intl_form,
now API-history). The signal is environment-level, not team-level. WC_APIF_PRIOR
stays OFF. Caveat: only WC2022+Euro2024 cached so far; re-check after qualifiers/
friendlies accrue, but expectation is low. PIVOT #10 to the REFEREE feature (a
different, orthogonal axis; ref tendencies year-insensitive so the data is valid).

## 2026-06-16 — Kalshi HALF-TOTALS blend + score-or-assist accent fix (WC_KALSHI_HTOTAL)
Alpha market audit (`data/alpha_market_audit.md`): the −92 alpha leak is ~80%
INHERENTLY BOOKLESS (offsides/SOT/fouls/penalty have no market anywhere — Kalshi
KXWCSOG/TEAMSOG are empty shells, books do only player props). The genuine
markets-first wins are: (a) half goal-totals, (b) score-or-assist.
BUILT (kalshi_wc.py):
  - `totals_half()` reads KXWC1HTOTAL/KXWC2HTOTAL (224 mkts each, '...-N' == P(N+)).
    Wired into match_book(extras=) + a price_question branch; forecast.py gates it
    on **WC_KALSHI_HTOTAL** (NEW flag, default OFF — WC_KALSHI is already live so
    this can't change what-we-send until forward-validated). Targets the losing
    `totals_half` bucket (−22.7 vs field, n=3).
  - score-or-assist: FIXED a latent anchor miss — `[^a-z]` name-keying dropped
    accents inconsistently ('Gyökeres'→gykeres vs Kalshi 'gyokeres'), so accented
    stars (Núñez/Díaz/Kökçü/Gyökeres) silently missed their KXWCSOA mid. Added
    NFKD accent-fold (`_norm_name`) on BOTH sides. (Anchor itself already fires
    forward via derive `_kalshi_price` when WC_KALSHI=1; the −32.8 Gyökeres loss
    predated the flag.)
VALIDATION (Kalshi is live-only, so no settled-outcome OOS is possible — same
constraint under which WC_KALSHI itself shipped; used the equivalent live checks):
  - half ladder vs the TRUSTED full-match Kalshi ladder: mean |Δ| = 1.7pp (max 3.0).
  - live run on upcoming slate: sane 2H P(2+) mids 0.37–0.52; correctly halves;
    book snapshot absent for those (future matches) so Kalshi RESCUES 5/6 — pure
    coverage gain (was bookless → now priced). Blend (book primary w=0.65) applies
    only once book data exists.
  - tests: 36 pass (was 31; +5 in tests/test_kalshi.py).
RECOMMENDATION: **APPROVE enabling** — mechanism is the already-validated totals
reader, it's coverage-positive (rescues otherwise-bookless half-totals), and the
target bucket is losing. Caveat: totals_half n=3 is thin and the blend impact can't
be measured on settled Qs; the real proof is forward. Enable + re-check
`parse_locked.py` totals_half line after ~5 settle; disable if it regresses.
ENABLE: add `export WC_KALSHI_HTOTAL=1` to routines/morning.sh (next to WC_KALSHI).

## 2026-06-20 — auto review (review_report.py)
Realized edge vs consensus clone: -195. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-20.md`.

## 2026-06-20 — auto review (review_report.py)
Realized edge vs consensus clone: -195. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-20.md`.

## 2026-06-21 — auto review (review_report.py)
Realized edge vs consensus clone: -195. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-21.md`.

## 2026-06-22 — auto review (review_report.py)
Realized edge vs consensus clone: -199. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-22.md`.

## 2026-06-23 — auto review (review_report.py)
Realized edge vs consensus clone: -199. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-23.md`.

## 2026-06-24 — auto review (review_report.py)
Realized edge vs consensus clone: -199. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-24.md`.

## 2026-06-25 — auto review (review_report.py)
Realized edge vs consensus clone: -199. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-25.md`.

## 2026-06-25 — WC_SOT_RACE_DECOMP (2H SOT-race de-compression) — APPROVE (conservative γ)
After the matchday-3 locked-email harvest (corpus 36→53, +149 settled Qs), re-graded
us-vs-field. The 2H team-vs-team SOT race REVERSED from a believed contrarian edge to a
big leak (see memory `sot-race-contrarian-edge`, now corrected). parse_locked decomposition:
  - all 2H SOT-race Qs: ours +20 vs field-clone +87 → **−67** (the headline).
  - DERIVE-PRICED only (the flag's true scope — excludes 4 placeholder/reconciled flukes
    that scored +104 and MASKED the leak): n=32, ours **−94.7 (negative!)** vs clone +76.8
    → **−171**. The field is well-calibrated (race favorites win ~70%, field says ~68%);
    our double-damped price (`sot_share` ×0.5 then `h_sot_race_h2` ×0.6) sits at ~53% on
    favorites / ~38% on underdogs — squashed toward 0.5 on both sides.
BUILT: `derive._apply_race_decomp` post-processor (mirrors `_apply_sot_anchor`), flag
`WC_SOT_RACE_DECOMP` = γ (default 1.0 = no-op/OFF). `p' = clip(0.5 + γ·(p−0.5))` on the
FINAL race price; pricer-agnostic (wraps the dispatch → covers both `h_sot_race_h2` and the
`qmodel` raw-Skellam branch). Scope = `derive.is_sot_race` (single source of truth, shared
with the review_report gate; mutually exclusive with `is_sot_threshold`). +7 tests
(`tests/test_sot_race_decomp.py`); full suite 54 pass.
OOS EVIDENCE (clean, no look-ahead, derive-priced scope):
  - γ sweep is MONOTONIC: ours −94.7 (γ1.0) → −53.8 (γ1.4) → −37.1 (γ1.6) → −11.0 (γ2.0)
    → +15.5 (γ3.0). Broad plateau, not a knife-edge.
  - honest expanding-window (γ* chosen per-match from STRICTLY-earlier dates): ours +3.0
    vs as-sent −94.7 = **+97.7 banked pts recovered**.
  - review_report gate (placeholder-contaminated scope, n=36): sent +20 vs de-compressed
    +64 → **APPROVE (+45) at γ=1.5**.
RECOMMENDATION: **APPROVE at a CONSERVATIVE γ=1.5** (recovers ~+50 banked pts). The OOS
optimum sits at the grid edge (γ≥3) — a tail-risk/overfit flag — so ship gentle (mirrors the
`WC_SOT_BETA=0.5`-over-1.0 caution). WATCH: (1) even max de-comp stays ~−62 behind the
field-clone — our race MODEL is genuinely weaker than the crowd; de-comp is damage control,
the deeper fix is better favorite identification (market goal-share, not sparse counted SOT
rates). (2) On undifferentiated races (share→0.5, e.g. early-rate knockout fixtures) de-comp
only sharpens the tie-mass NO; with the 2–3× knockout multiplier, re-check the nightly gate
through the bracket. n=32 thin; gate re-runs nightly — if it slides to HOLD/REJECT, lower γ
or disable.
ENABLE: add `export WC_SOT_RACE_DECOMP=1.5` to `routines/flags.sh` (next to the SOT flags);
tune γ via the same var. Code never auto-edits flags.sh — a human flips it.

## 2026-06-25 — "both teams ≥1 SOT" (−96) examined → HOLD, NO FLAG (mostly a fixed bug)
The −96 leak (the 2nd-worst SOT pattern in the breakdown) splits cleanly on the 2026-06-20
placeholder-regex fix (`2e0e7c3`):
  - PRE-fix (n=6): hit `placeholder.v0`'s 0.45 catch-all ("shot" singular missed the "shots"
    SOT regex) → priced 45 on events hitting 83% → edge **−68**. That code path is GONE.
  - POST-fix (n=6): `qmodel` anchors the half variant to 0.68 (`0.30·p_raw + 0.70·0.68`),
    pricing ~70 — slightly ABOVE the field's ~63 → edge −28 on a 50%-realized (3/6) sample.
The post-fix sign is OPPOSITE the "under-pricing" headline: we now marginally OVER-price, so
the tempting "anchor up" would be backwards. n=6 is far too thin and lowering 0.68 toward the
field risks the relative-scoring trap. HOLD — re-examine only if the post-fix half-both-teams
sample grows and the gate shows a consistent same-side miss. Lesson (again): a big −pts bucket
is often stale bug damage, not a live bias. Much of the cumulative −178 us-vs-clone edge is
pre-fix history; the forward-relevant edge is better than the headline.

## 2026-06-25 — FORWARD-EDGE recompute → the −178 is NOT stale bugs (CORRECTS the line above)
Split realized us-vs-clone edge by submission-date regime (which fixes were live when the
SCORED value was set):
  - R1 (< 06-20, pre placeholder-fix & anchors): n=363  edge −64.3
  - R2 (06-20..06-23 12:49, placeholder fixed):  n=82   edge −51.6
  - R3 (>= 06-23 12:49, all fixes + drift live): n=73   edge −61.6
The edge is STABLE across regimes — the bot is NOT closing the gap — and placeholder-bug Qs
net only **−3.0 over n=83** (they win some, lose some). So "much of −178 is fixed-bug damage"
was WRONG: ALL-minus-placeholder is still −174.5. The deficit is persistent and almost entirely
NO_MARKET: **R3 NO_MARKET edge −94.0** (the whole current-regime deficit); every R3 book bucket
is ~neutral-to-positive.
CRITICAL re WC_SOT_RACE_DECOMP — the race pricing regime SHIFTED mid-tournament. EARLY races
were derive-priced via market goal-share (DIFFERENTIATED favorite, shares 0.63/0.67) — that's
where de-comp recovered ~+50. CURRENT races (R3) flow through the qmodel RAW-Skellam branch on
counted SOT rates that DON'T differentiate (share→0.5, price ~0.44); there the problem is
failure-to-identify-the-favorite (wrong-side), which de-comp can't fix and can worsen. On the
R3 race sample (n=8) de-comp adds only **+4.9** (−69.3→−64.5). The +50 backtest is era-specific.
REVISED: de-comp is validated + harmless on history but LOW-VALUE forward ALONE. SEQUENCE it
AFTER favorite-identification — route the qmodel race through market goal-share (differentiated,
right-side) THEN de-comp amplifies a correct signal. Enabling `WC_SOT_RACE_DECOMP` now (on
undifferentiated qmodel prices) is premature → HOLD until goal-share routing lands. NEXT BUILD:
blend/replace the qmodel race rate-Skellam with derive's goal-share price; OOS-validate; then
re-test de-comp on top.

## 2026-06-25 — WC_SOT_RACE_GS (goal-share routing) — APPROVE as a COMBO with de-comp
Built the favorite-identification fix the forward analysis pointed to. The race has two
pricers: derive `h_sot_race_h2` (market goal-share → Skellam, DIFFERENTIATES the favorite) and
qmodel raw-Skellam on counted SOT rates (cluster ~4.25 → share≈0.5 → 0.44 for EVERYONE). qmodel
runs first, so the undifferentiated price shadows the better one once teams log games — the live
forward regime. `WC_SOT_RACE_GS` (default OFF) skips the qmodel race branch (`derive.
_route_race_to_goal_share`) so the race falls through to the goal-share pricer.
RE-PRICING BACKTEST (now=kickoff, no look-ahead; reproduction err 0.057 on derive-priced races;
forward-path numbers identical at now=deadline vs now=kickoff → stable):
  - qmodel-raw races (forward path), edge vs clone: sent **−79.4 → gs −38.3 → gs+decomp1.5 −21.6**
  - R3 current regime (n=8):                          sent **−88.3 → gs −41.0 → gs+decomp1.5 −19.7**
  goal-share moves prices toward the field on BOTH sides (fav 0.43→0.55, dog 0.47→0.29). Smoke
  (DB copy): Panama→0.29, Colombia→0.55, Ghana/Eng→0.38 vs qmodel's flat 0.43.
VALIDATED SEQUENCE: goal-share gets the SIDE right; de-comp then amplifies a now-correct signal.
De-comp ALONE was premature (HOLD, +5 forward); the COMBO recovers ~+69 banked pts on the R3
races (sent OURS ~−69 → ~0). +1 test (`_route_race_to_goal_share` gating); suite 55 pass.
CAVEATS: (1) this is a RE-PRICING backtest, not a sent-price rescale gate — FORWARD-validate by
watching the SOT-race bucket edge in review_report over coming matchdays. (2) reproduction is
imperfect on a few EARLY races (spotty snapshots) but the forward target is stable. (3) even the
combo still trails the field-clone ~−20 (our race model remains weaker than the crowd) — it cuts
the deficit ~75%, not to zero. (4) knockout multiplier 2–3× — re-check through the bracket.
RECOMMENDATION: **APPROVE the combo.** Enable BOTH together (order matters — GS makes de-comp safe):
  `export WC_SOT_RACE_GS=1`  and  `export WC_SOT_RACE_DECOMP=1.5`  in routines/flags.sh.
This SUPERSEDES the earlier "HOLD WC_SOT_RACE_DECOMP": de-comp is approved *as part of the combo*,
not standalone. Code never auto-edits flags.sh — a human flips both.

## 2026-06-25 — review fix (scope) + discovered pre-existing leading-form bug
Independent review of PR #3 flagged that `is_sot_race` (keyword-based) matched the leading
"In the second half, will X have more SOT than Y?" phrasing, but h_sot_race_h2's dispatch regex
is TRAILING-only — so with WC_SOT_RACE_GS on, that form would be blocked from qmodel AND missed by
the handler → stranded at the placeholder. Real-data check: the contest uses BOTH forms (45
trailing + **9 leading** of 54). FIX: narrowed `is_sot_race` to the handler's exact scope (trailing
'more shots on target than ... in the second half'), so is_sot_race ⟹ h_sot_race_h2 can price it;
routing can never strand. The 45 trailing races (= what the re-pricing validation actually scored)
are unaffected. +2 tests (leading-form deferral, full-match exclusion); suite 56 pass.
FOLLOW-UP (separate, NOT this PR): the 9 leading-form 2H SOT races are mispriced to placeholder
TODAY — qmodel's regex is case-sensitive 'Will' and the handler's is trailing-only, so neither
matches "In the second half, will...". Fixing needs a broadened dispatch (handle both word orders +
case) in BOTH derive HANDLERS and qmodel — but that CHANGES production for flags-OFF (placeholder →
goal-share), so it can't ride in this no-op-until-flipped PR. Track as its own change; mirror the
corners-race handler which already accepts the leading form.

## 2026-06-26 — auto review (review_report.py)
Realized edge vs consensus clone: -178. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-26.md`.

## 2026-06-27 — auto review (review_report.py)
Realized edge vs consensus clone: -186. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-27.md`.

## 2026-06-28 — auto review (review_report.py)
Realized edge vs consensus clone: -186. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-28.md`.

## 2026-06-29 — auto review (review_report.py)
Realized edge vs consensus clone: -322. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-29.md`.

## 2026-06-30 — auto review (review_report.py)
Realized edge vs consensus clone: -335. Flag-validation gate + per-bucket edges in `data/reviews/2026-06-30.md`.

## 2026-07-01 — auto review (review_report.py)
Realized edge vs consensus clone: -335. Flag-validation gate + per-bucket edges in `data/reviews/2026-07-01.md`.

## 2026-07-01 — WC_FOULS_DOM (fouls-race game-state tilt) — APPROVE (the biggest single alpha fix)
Broke the −318 NO_MARKET leak down by question FAMILY (not just the lumped bucket):
the single biggest leak is **fouls_race −128 over 55 Qs** ("Will X commit more fouls
than Y?"). ROOT CAUSE: qmodel priced it from each team's OWN counted foul rate — a
~symmetric Skellam that lands ~0.46 for EVERY matchup regardless of who is favoured
(the tie split). But the underdog reliably commits MORE fouls (less possession, more
chasing) — a game-state effect the crowd prices (field ~0.55–0.62 on underdog-first
questions, resolving YES) and we threw away. Two contradictions this exposes: (a) the
"qmodel = calibrated math beating crowd overconfidence" thesis is REVERSED here — the
crowd is better-calibrated because it prices a real effect we ignore; (b) the OLD
derive handler `h_fouls_race` had the right sign ("underdogs foul more (weak)") but a
±0.08 coefficient — AND `WC_QMODEL=1` preempts it, so the symmetric qmodel shadows the
directionally-correct heuristic. Net: the counted-rate "upgrade" was a regression on
this bucket.
BUILT (`qmodel._foul_dom`, flag `WC_FOULS_DOM` = tilt slope, default 0.0 = today's
symmetric price): tilt the two foul rates by market goal-share — underdog ×(1+slope·
(0.5−share)·2) up, favourite down — the INVERSE of the existing `_dom()` shot-volume
scaling. Pricer-level, no new data source (goal-share is the market λ we already read).
+5 tests (`tests/test_fouls_dom.py`); full suite 93 pass. Gate wired into review_report
(`_fouls_dom_gain`, re-prices at the live/candidate slope, PRIOR-ONLY = clean).
OOS EVIDENCE (re-pricing the 55 settled fouls_race Qs; goal-λ clamped to the last
pre-kickoff snapshot = no in-play look-ahead; realized relative points):
  - CLEAN prior-only (foul base symmetric → the goal-share tilt is the ENTIRE signal):
    slope 0.00 → **+54** (−68 vs field-clone) ; 0.10 → +161 ; **0.15 → +177 (+123 vs
    slope-0, and +55 ABOVE the field-clone)** ; 0.20 → +170 ; 0.25 → +146 ; 0.40 → +11.
  - FULL counted rates (mild look-ahead) agree: joint peak at slope 0.15 (+102 vs
    slope-0, +114 vs field-clone). Peak is INTERIOR (not a grid-edge overfit) and the
    0.10–0.20 plateau is flat.
  - per-question at 0.20: 31 improve / 23 worsen (regressions are upsets where the
    field ALSO lost, e.g. Norway>Iraq YES); not outlier-driven.
  - review_report gate: symmetric +54 vs tilted +177 → APPROVE (gain +123 over n=55) —
    the LARGEST approved gain of any live-or-candidate flag.
This is the legitimate "beat the crowd" result the qmodel thesis promised (we exceed
the field-clone at the optimum), restored by adding the one signal the crowd uses.
RECOMMENDATION: **APPROVE at slope 0.15** (joint OOS peak, gentle, interior; anything
in 0.10–0.20 captures ~the same gain). Ship gentle here means NOT over-tilting past
the peak (≥0.45 flips to a loss). WATCH: re-check the fouls_race bucket edge in
review_report over the next matchdays; if the WC_FOULS_DOM gate slides to HOLD, lower
the slope. Note the KNOCKOUT 2× multiplier makes this bucket worth double — high value
through the bracket.
ENABLE: add `export WC_FOULS_DOM=0.15` to `routines/flags.sh` (next to the SOT flags).
Code never auto-edits flags.sh — a human flips it.

## 2026-07-01 (later) — alpha-family audit round 2: the audit's own errors, + 5 fixes
Re-derived the family breakdown with per-question pricing attribution (which
HANDLER priced each settled row) and the full outcomes table (every settled Q,
not just the email corpus). TWO findings in the morning audit itself were wrong:

1. **"own_goal −55" was a MISCLASSIFIED family.** Every score-or-assist question
   contains "(excluding own goals)", which the family regex swallowed. The real
   own-goal handler has n=1 settled (a NO; 0.07 is fine). The −55 lives in
   **score_or_assist**, split: kalshi-mid-priced −40/n=5 (sent ~0.49 on questions
   resolving 20% YES — `kalshi_wc.mid()` has NO spread guard, and thin player
   books pin the mid near 0.5), book-union −5/n=6, placeholder −17/n=9.
   ROOT CAUSE the union rarely fires: `derive._player_prob` still used the naive
   tokenizer — cc4a453 added `_player_tokens` (strip "(Belgium)", fold accents)
   to forecast.py but MISSED the derive twin, so every KO-worded score-or-assist
   failed book matching and fell to kalshi/placeholder. **FIXED** (derive now
   uses forecast._player_tokens) + **WC_KALSHI_NO_SOA** flag routes SOA away
   from kalshi mids to the book union (gate: kalshi-sent −24 vs union +4, n=5
   thin → formal HOLD, but the defect is structural; recommend ON).
2. **"raise the both-teams-SOT half anchor 0.68 → 0.72" was BACKWARDS.** Post-fix
   submissions averaged 0.70 vs field 0.63 on a family settling 62-67% YES.
   Swept 0.58-0.72: **0.68 is already the optimum** (0.63 −0.8, 0.72 −2.2).
   Shipped the knob (WC_BTS_HALF_ANCHOR, default 0.68) + gate; recommend NO flip.

And three real improvements, all flag-gated default-current, gates in review_report:

3. **WC_SOT_TOTAL_ANCHOR / WC_SOT_TEAM_ANCHOR — the big one (+125, n=47).**
   The pooled 0.65 SOT anchor averages two families with OPPOSITE biases:
   total-SOT thresholds settle **84% YES** (n=19; the contest writes the lines
   low) while team-SOT settles **39%** (n=33). Split-anchor sweep (raw prices
   re-computed prior-only, blended per family): total 0.78 → +49 (sens-drop3
   +36), team 0.42 → +76 (sens +36); combined gate **APPROVE +125 over n=47** —
   as large as WC_FOULS_DOM. Grid note: total's curve is monotone to 0.85 (grid
   edge) — 0.78 is the deliberately conservative pick, same convention as
   RACE_DECOMP. ENABLE: `export WC_SOT_TOTAL_ANCHOR=0.78` +
   `export WC_SOT_TEAM_ANCHOR=0.42` in flags.sh.
4. **Corner races are NOT "same tilt as fouls" (morning audit hand-wave).**
   Split: h1 races **+32** (7/7 NO — tie-dense half, we're correctly low), team
   corners +21, h2 races **−58** (8/9 YES). The h2 bleed concentrates in the
   `anchored-supremacy` fallback (2 settled rows, −32.5): its tilt caps at ±0.10
   while spread-ladder-implied shares run ±0.17 — the same right-sign-too-weak
   defect as old h_fouls_race. Shipped **WC_CORNER_SUP_SLOPE** (default 0.20 =
   bit-identical old behaviour; candidate 0.50 → +17 on n=2, gate HOLD-thin,
   accumulates as KOs settle). A blanket corners tilt would have destroyed the
   winning h1 side — evidence beats mechanism-by-analogy.
5. **Two corner coverage gaps → WC_PH_COVERAGE handlers** (fallback-only, never
   override a pricer): "N or more corner kicks before the first hydration break"
   (OPEN question today; was placeholder-bound) and "Will X have at least N
   corner kicks [in the half]" ('at least' defeated h_team_corners's 'N or more'
   regex — the settled Argentina example bled −27 vs field 0.83).

WORKFLOW: review_report now prints a **NO_MARKET by-family table** (worst-first,
with our_p/field_p/yes%) — the single lumped bucket is what let fouls_race hide
for two weeks and mislabelled own_goal today — and `_alpha_family` classifies
score-or-assist BEFORE own-goal so the mislabel can't recur. All re-pricing
gates share `_matches_with_rows` scaffolding (pre-kickoff lambdas, prior-only
rates). Tests: +14 (tests/test_alpha_fixes.py), suite 107 pass.

## 2026-07-01 — auto review (review_report.py)
Realized edge vs consensus clone: -335. Flag-validation gate + per-bucket edges in `data/reviews/2026-07-01.md`.

## 2026-07-01 — auto review (review_report.py)
Realized edge vs consensus clone: -335. Flag-validation gate + per-bucket edges in `data/reviews/2026-07-01.md`.

## 2026-07-01 (evening) — forensic session/commit audit → 8 live fixes + honesty pass
Five parallel auditors read every transcript Jun 17–Jul 1 + every commit Jun
20–30. Durable record: `data/session_audit_2026-07-01.md`. Highlights: the
widened WC_SOT_THRESH_ANCHOR gate (now scoring the flag's REAL scope incl.
misfiled team-SOT) flips its verdict to **REJECT −79/n=60** — the live pooled
0.65 anchor is net-negative; the split anchors are the fix. 13/21 KO matches
were still stage='group' (1× internal multiplier) → KO-calendar fallback in
backfill_stages + 101 settled outcomes repaired to 2×. h_total_shots_match was
threshold-blind (flat 0.58 for every N) → Normal survival. review.sh now
sources flags.sh (was mislabeling LIVE flags as "candidate"). Honesty labels
added to the WC_FOULS_DOM and split-anchor gates (same-sample parameter
selection; re-priced-not-as-sent baselines). Meta-lesson: the recurring failure
mode is ARCHIVAL — write findings to durable artifacts the same turn.

## 2026-07-01 — auto review (review_report.py)
Realized edge vs consensus clone: -335. Flag-validation gate + per-bucket edges in `data/reviews/2026-07-01.md`.

## 2026-07-01 (night) — GO-LIVE: all validated flags flipped + remaining defects fixed
User directive ("fix all defects, optimize it to win") + governance policy
resolved: flips are the agent's job once the gate clears (CLAUDE.md + flags.sh
headers updated; the disabled improve-loop still never edits flags).
FLIPPED LIVE in flags.sh, each with gate evidence inline:
  - WC_SOT_TOTAL_ANCHOR=0.78 + WC_SOT_TEAM_ANCHOR=0.42 (gate +125/n=47; the
    pooled 0.65 was REJECT −79 on its real scope — post-flip the live config
    re-gates APPROVE +87/n=60)
  - WC_FOULS_DOM=0.15 (+123/n=55, same-sample caveat labeled; 0.10–0.25 plateau)
  - WC_KALSHI_NO_SOA=1 (structural: wide-spread mids aren't probabilities)
  - WC_CORNER_SUP_SLOPE=0.5 (fallback-only scope, thin-n HOLD, forward-watch)
  - WC_BTS_HALF_ANCHOR deliberately NOT set (0.68 is the sweep optimum).
DEFECTS FIXED: PATCH-400 retry loop (locked_predictions table — a 400 marks the
prediction final; submit+derive skip it; Jun-27 burned 213 futile PATCHes);
dual team-SOT pricers consolidated onto one shared body (wordings "N or more"
vs "at least N" priced 0.232 vs 0.028 for the same question — raw-share variant
was +15/n=29 anchored but same-sample-thin, so consolidation is on the
incumbent formula for consistency, not a formula switch).
VERIFIED: 113 tests pass; DB-copy dry-run shows all effects (team-SOT down ~11,
total-shots line-aware 20+→78/22+→68 with a PATCH queued over the old flat 58,
SOA off kalshi); review regenerated with LIVE labels. The 15-min sentinel
applies everything to the open slate (USA–Bosnia next); morning.sh covers the
full slate. WATCH nightly: family table + every gate; pull back any slider.

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

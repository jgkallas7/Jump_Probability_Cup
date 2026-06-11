# Build Roadmap (set 2026-06-11, post-opener-submission)

Direction from user review of alpha sheet v0: remembered tournament-average
base rates are not acceptable inputs. Replace with current-squad data,
market anchors, and simulation. Priority order:

## 1. Base-rate engine v1 — CURRENT-SQUAD rates (week 1, blocks alpha quality)
- Source: FBref team match logs (or API-Football) for all 48 teams,
  2024-26 window: qualifiers + friendlies + continental tournaments.
- Per-team, opponent-adjusted rates: fouls committed/drawn, corners for/
  against, cards, offsides, goals by half, SOT.
- Schema: team_stats(team, window, matches, fouls_pg, corners_for_pg,
  corners_against_pg, cards_pg, offsides_pg, goals_h1_pg, goals_h2_pg,
  sot_pg, updated_at) + per-match raw rows for distribution fitting.
- Replaces every "remembered lambda" in alpha sheet v0. The four flagged
  submissions (offsides x2, pen, pen-or-red) get re-derived and PATCHed.
- 2026 caveat: semi-automated offside tech + new referee directives mean
  even 2022 counted data needs a shrinkage prior, not point reuse.

## 2. Match simulation engine (week 1-2)
- Bivariate Poisson / Dixon-Coles, team attack/defense rates calibrated to
  devigged market lines (totals + h2h as the anchor, NOT to out-predict
  books), empirical goal-timing curves for half splits.
- Outputs every derived question consistently from ONE joint distribution:
  btts-and-over combos, team-scores-in-half, half-vs-half comparisons,
  race props with tie probabilities.
- Lineup-conditioned adjustments at T-60 (scorer props especially).

## 3. Model priors (Elo layer)
- Nate Silver PELE (natesilver.net) — PAYWALLED; user may have sub, would
  provide ratings + per-match projections updated daily.
- Free: eloratings.net national Elo. Blend weight small (SPEC: w 0.85-0.95
  on market) — only matters for thin/alpha markets and futures.

## 4. Historical odds calibration (NOT YET RUN — redesigned 2026-06-11)
User direction: recent matches over WC2022 (3.5y stale, n=64 too small to
separate devig methods; book microstructure has drifted). Two tiers:
- FREE bulk tier: football-data.co.uk CSVs — Pinnacle closing 3-way odds +
  results, thousands of recent club matches (EPL/UCL/etc), zero credits.
  Settles power vs multiplicative vs shin at real sample size.
- Odds API historical tier (10x cost, spend where source-identical matters):
  Euro 2024 (51) + Copa America 2024 (32) + 2024-25 WC qualifiers/Nations
  League through OUR exact pipeline — same Pinnacle scrape/delay, same
  exchange quotes, same parser. Per-book closing accuracy beyond Pinnacle,
  international-match level check vs the club-data baseline.
- Results source for internationals: FBref/fixturedownload (historical
  /scores only reaches 3 days back).
- Extend to totals/btts closers for prop-adjacent calibration if tier-1
  shows method choice matters there.

## 5. derive.py — productionize alpha derivations
- Tonight's inline scripts (skellam corner/SOT races, poisson card/team-goal
  splits, scorer-assist union) become a module driven by question taxonomy,
  auto-run in the forecast pass for all 104 matches.
- Revision loop currently PATCHes only book-mapped consensus questions —
  derive.py must hook alpha questions into the same T-60 re-derive + PATCH.

## 6. Bot 2 divergence book (week 2, after calibration data exists)
- Live-test two-bots-one-market first (RULES.md 5b).
- MrShaw runs SPEC §5 posture: crowd-divergent on high-multiplier rounds.

## Operational cadence (until sentinel automation)
- Morning: snapshot --hours 30 -> forecast -> submit (new questions)
- T-60 per match: snapshot --hours 3 -> submit revise + manual alpha recheck
- Post-settlement: calibrate sync && calibrate report
- Sunday: weekly review — calibration buckets, deviation P&L, book Brier

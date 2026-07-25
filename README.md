# Jump Probability Cup — an autonomous forecasting system for a live prediction contest

A production bot that competed in the SportsPredict **Probability Cup**, a public
forecasting competition over FIFA World Cup 2026: ~104 matches, 1,000+ binary
questions, six weeks (Jun 11 – Jul 19, 2026), 4,013 entrants.

**Final rank: 104th of 4,013 (top 2.6%).** Fully autonomous in production —
priced, submitted, and revised every question on systemd timers, with humans in
the loop only for model governance.

---

## The game

Each question ("Will both teams score?", "Will Messi record 2+ shots on
target?") takes an integer probability 1–99, revisable until kickoff. Scoring is
**relative Brier**:

```
points = (field_average_brier − your_brier) × multiplier × 100
```

with 1× group / 2× knockout / 3× final multipliers. Three properties drive the
whole design:

1. **You are scored against the crowd, not against reality.** A "correct-side"
   forecast can lose points and a "wrong-side" one can win them. Edge means
   being right where the field is wrong.
2. **Brier is a proper scoring rule**, so the honest probability maximizes
   expected points *per question*.
3. **The contest pays rank, not points** — the payoff is convex. This is
   tournament logic, not cash-game logic (see [The two ledgers](#the-two-ledgers)).

## Headline numbers

| Strategy | Season relative points | Approx. finish |
|---|---|---|
| Average entrant | ~0 (by construction) | ~2,000th |
| **This system** | **+3,419** | **104th** |
| Crowd-consensus clone (counterfactual) | +3,924 | top ~50 |
| Leader entering the final | +5,186 | 1st |

Every number above comes from the repo's own settlement ledger
(`parse_locked.py` over the contest's post-close emails, which disclose the
field consensus the API hides), regraded nightly.

## The two ledgers

The honest way to read this project is with two ledgers at once.

**Ledger 1 — expected points.** The hardest baseline in a relative-scored
contest is not the average entrant; it is *submitting the field's average
probability on every question*. By Jensen's inequality the crowd mean beats
almost every individual in the crowd (the Brier of the average forecast is less
than the average of the Briers), so the clone is an elite strategy — top ~1-2%
here. Against it, this system's deviations netted **−566 points**: the sharp-book
consensus machinery carried the rank, and the alpha layer on top, in aggregate,
cost expected points (**−505** over the season). That number is recorded
permanently in `data/improvement_log.md` and every nightly review, with
per-family attribution.

**Ledger 2 — rank.** A contest pays out on rank, and the payoff is convex. The
clone's ceiling is wherever the consensus lands — it has essentially zero
probability of finishing top-10, because you cannot beat the field by copying
it. The only path to the top is deviating from the crowd and being right, which
makes deviations lottery tickets: negative realized E[points] can coexist with
positive option value on rank. Running deviations was the correct strategy for
the contest's actual objective; the ledger just prices what the tickets cost.

The discipline was in keeping the two ledgers separate: every deviation
mechanism had to clear an out-of-sample gate on *expected* points before going
live, and the ones that failed were pulled — while accepting that the season's
aggregate deviation P&L is the entry fee for competing for the top rather than
locking in a safe top-50.

## What actually made money

Validated deviations from the crowd, each with its gate evidence inline in
`routines/flags.sh`:

- **Books and the crowd over-price player shots-on-target** (longshot/rotation
  bias). Anchoring "player ≥1 SOT" props down toward the realized base:
  **+92 points / n=61**.
- **The field over-prices substitute involvement.** A calibrated 0.24 base rate
  vs a field averaging 0.55: **+35 / n=5**.
- **The field under-prices draws.** For "Will X win?" questions the draw is
  deliberately *not* renormalized away — a standing structural edge in the h2h
  bucket (+33).
- **Underdogs commit more fouls.** A game-state tilt on fouls-race questions
  the symmetric model missed: **+123 / n=55**.
- **Split anchors for total-SOT vs team-SOT thresholds** (the contest writes
  total-SOT lines low — 84% settle YES): **+149 / n=72**.
- **Final-day manual repricing** (Jul 19, 3× multiplier): a live audit found six
  questions submitted as information-free placeholders — including "Will
  Argentina win the World Cup?" — repriced them from market data (draw-no-bet
  h2h, player-SOT ladder Skellam, tournament penalty base rates) and PATCHed
  under deadline. The final graded **+62 vs the clone — the best single match
  of the season** (five of the six fixes beat the field, +180 combined);
  jumped 126th → 104th on the one match.

And the honestly-documented losers: the quant layer's early-tournament
mispricings (fouls/SOT races before the fixes above), a stale-cache bug that
served 18-day-old team rates, and a half-totals data source that failed its
gate and was disabled. All in the log; none redacted.

## Architecture

```
ingest_questions.py   contest lobby -> questions; classifies text ->
                      (qtype, market mapping) or NO_MARKET (= alpha question)
        |
snapshot.py           Pinnacle + whitelisted sharp books via Odds API ->
                      append-only market_snapshots tape (both devig methods)
        |
   +----+----------------------------+
   |                                 |
forecast.py                       derive.py
 book-mapped questions:            NO_MARKET questions:
 text -> (market,outcome,point)    market-anchored Poisson/Skellam closed
 -> weighted whitelist consensus   forms (qprice.py), counted team rates
 -> extreme shrinkage              (team_rates.py), Kalshi crowd mids
   |                                 |
   +----------------+----------------+
                    |
placeholders.py     insurance: any still-unpriced open question gets a
                    family base-rate placeholder (a blank scores 0)
                    |
submit.py           batch POST; revisions PATCH until close
                    |
sentinel.py         every 15 min near kickoff: fresh snapshot -> re-price ->
                    PATCH moves >= 2 pts (post-lineup information capture)
                    |
calibrate.py        settlement sync -> Brier, calibration buckets
parse_locked.py     the real ledger: us-vs-field from post-close emails
review_report.py    nightly per-bucket edge + per-flag validation gates
```

**Pricing engine:** fair value is whitelist-only — Pinnacle (heaviest),
exchanges, sharp offshore books; 45+ soft books are snapshotted as a crowd
proxy but excluded from pricing. Devig is power-method-primary with a
multiplicative cross-check, both stored per snapshot row so divergence is
SQL-queryable. Submissions never touch 0/100.

**Data layer:** SQLite, append-only tape + state tables. Every forecast row
records its consensus input, model input, blend weight, and a
`deviation_reason` audit tag — every submitted number is explainable after the
fact.

## Research process

The process is the part that transfers beyond this contest:

- **Feature-flag governance.** Every behavior change ships behind a flag,
  default OFF, and must beat what-we-currently-send on a clean out-of-sample
  check before being enabled. Gate evidence lives inline beside each flag in
  `routines/flags.sh`.
- **Same-sample honesty.** Parameters chosen by argmax on the sample that
  scores them are labeled as upper bounds, not forward estimates, in every
  artifact. In-sample mirages burned this project early and the rule exists
  because of it.
- **Nightly attribution.** An automated review regrades the season each
  morning: per-bucket edge vs the clone, per-family breakdown of the quant
  layer, and an APPROVE/HOLD/REJECT gate per live flag
  (`data/reviews/*.md`).
- **Documented dead ends.** Team-specific historical priors failed the gate
  three separate ways and are recorded as do-not-redo, with the lesson
  (micro-stat signal is environment-level, not team-level). Negative results
  are kept, not deleted.
- **Post-mortems that found real bugs.** A revision daemon silently stripping
  feature flags off scored values; an HTTP cache serving 18-day-old data to a
  "daily" refresh; a question-classifier gap that sent the two highest-stakes
  match-winner questions of the knockouts out as flat placeholders (caught and
  hand-repriced on the final). All diagnosed from the ledger, fixed, and
  written down (`data/improvement_log.md`).

## Repo map

| Path | What it is |
|---|---|
| `SPEC.md` | architecture rationale |
| `HANDOFF.md` | operational state, open tasks, dead ends |
| `RULES.md` | resolved contest-API facts |
| `config.py` | book whitelist + weights, devig settings |
| `devig.py`, `qprice.py` | pure math (power/multiplicative devig; Poisson/Skellam/bivariate closed forms) |
| `forecast.py`, `derive.py`, `qmodel.py`, `team_rates.py` | pricing engines |
| `submit.py`, `sentinel.py`, `placeholders.py` | execution layer |
| `calibrate.py`, `parse_locked.py`, `review_report.py`, `audit.py` | settlement, ledger, nightly review, opportunity discovery |
| `routines/` | systemd timer entrypoints + the flag file |
| `data/reviews/`, `data/improvement_log.md` | the nightly record and decision log, unredacted |
| `tests/` | pure-math + parsing suite |

## Running it

The contest is over, so the live endpoints are dead, but the whole pipeline
runs offline against a database copy:

```bash
.claude/skills/run-jump-probability-cup/smoke.sh   # offline smoke of all stages
python -m pytest -q                                # math + parsing suite
python parse_locked.py                             # the season ledger
```

---

*Built and operated June–July 2026. The model-governance loop (gates → flag
flips → nightly watches) was driven interactively with Claude Code; every
decision and its evidence is in the commit history and
`data/improvement_log.md`.*

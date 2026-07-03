# WC_* feature flags — SINGLE SOURCE OF TRUTH.
#
# Sourced by BOTH routines/morning.sh (daily forecast+submit) AND
# routines/sentinel.sh (T-75 pre-kickoff revision). They MUST agree: sentinel is
# the LAST writer before a market closes, so any flag it doesn't set is stripped
# back off whatever morning submitted. This file exists because they drifted —
# sentinel.sh set no flags, silently re-pricing every near-kickoff revision with
# the entire feature set OFF and PATCHing the flag-free value over morning's
# flagged one (found 2026-06-23 on Portugal-Uzbekistan: morning devcap'd the h2h
# 0.811->0.733, sentinel re-priced 0.857 flag-free and submitted it).
# GOVERNANCE (user policy, re-affirmed 2026-07-01): flips are made in an
# interactive session at the user's direction once the review_report gate
# clears, with the gate evidence recorded inline beside the flag. The DISABLED
# autonomous improve-loop (wc-improve) must NEVER edit this file. Each flag's
# gate/validation lives in the comment beside it.

# Counted-rate quant pricer for alpha (NO_MARKET) questions. Enabled after a
# CLEAN no-look-ahead OOS (evaluate_qmodel.py --prior-only): clean qmodel beat
# what we send (+3.6) and forward use is look-ahead-free (price time only sees
# prior games). ⚠️ HONEST UPDATE 2026-07-01 (post leak-fix c61455c, n=273):
# qmodel +488 vs sent +398 vs field-clone +707 — it beats what-we'd-otherwise-
# send (+90) but does NOT beat the field in aggregate. Kept ON as the better
# fallback; judge it PER-FAMILY via review_report's NO_MARKET family table
# (pen_or_red/offsides win; fouls_race lost -128 until WC_FOULS_DOM). The old
# "+110/+28 vs field" claim was a contaminated early sample — do not cite it.
export WC_QMODEL=1
# Kalshi WC crowd mids: BLEND into book totals/corners (book stays primary; goal
# totals validated to 0.6pt vs sharp line, corners +5-7pt so blended not
# overridden) and RESCUE book-mapped Qs the sportsbook can't price. Guarded —
# any Kalshi failure leaves book pricing untouched.
export WC_KALSHI=1
# Half goal-totals (Kalshi KXWC1HTOTAL/2HTOTAL -> totals_half bucket).
# DISABLED 2026-06-20: was flipped on 06-16 ahead of its own gate — "validated"
# only vs the full-match Kalshi ladder (another Kalshi number), never against a
# sharp sportsbook half line or settled-results OOS, and totals_half was the
# worst bucket (-22.7 vs field). Re-enable ONLY after parse_locked shows the
# totals_half bucket improving on SETTLED questions (or a sportsbook cross-check).
export WC_KALSHI_HTOTAL=0
# h2h confidence dampener: pull match-winner/draw submissions 25% toward 0.5.
# ENABLED 2026-06-22 after its own gate cleared — review_report._shrink_gain on
# the SETTLED locked-email corpus: as-sent +73 vs shrunk +107 = +34 over n=32
# (APPROVE). This is the parse_locked forward-proof forecast.py asked for before
# go-live. Scope is h2h ONLY (DEVCAP_MARKETS); beta=0.25 is the conservative
# slice (in-sample optimum ~0.6 overfit a matchday-1 upset run). Our match-
# outcome confidence runs ahead of the realized upset/draw rate; SOT/alpha losses
# are DIRECTIONAL not overconfidence, so they stay out of scope. Watch the h2h
# bucket on parse_locked — disable if the gate flips to REJECT.
export WC_DEVCAP=1
# SOT-threshold base anchor: blend NO_MARKET "N-or-more shots on target" prices
# toward 0.65 at beta=0.5 (race pricer untouched — it's level-invariant + a
# confirmed edge). ENABLED 2026-06-22 after its gate cleared: review_report
# _sot_anchor_gain on SETTLED data (NO_MARKET-scoped, excl already-fixed both>=1)
# = sent +40 vs anchored +78 = +38 over n=21 (APPROVE); helped 6/9 match-days,
# both sub-templates positive. beta=0.5 not 1.0 ON PURPOSE: in-sample favours
# higher beta but it doubles worst-case single-Q loss (-22 vs -10) and overfits
# high-N thresholds; 0.5 is the bias-variance center. WATCH: the win is back-
# loaded/concentrated (n=21, one +30 day) — if the nightly gate slides to
# HOLD/REJECT as more settle, flip this OFF. Tunable: WC_SOT_ANCHOR/WC_SOT_BETA.
# ⚠️ 2026-07-01: the gate, widened to the flag's REAL scope (b8c4a7d also
# anchors team-SOT rows misfiled in player_shots_on_target), says the POOLED
# 0.65 anchor is REJECT (−79/n=60): total-SOT settles 84% YES, team-SOT 39% —
# one anchor is wrong for both. The split anchors below are the fix; this
# master switch stays ON (it gates the whole anchor mechanism).
export WC_SOT_THRESH_ANCHOR=1
# Family-split SOT anchors (2026-07-01). Settled base rates: total-SOT 84% YES
# (n=19; the contest writes those lines low), team-SOT 39% (n=33). Split gate
# (_sot_split_gain, re-priced prior-only, pre-kickoff λ): pooled +93 vs split
# +218 = APPROVE +125 over n=47; drop-3-best sensitivity +36 each side. Anchors
# chosen from the outcome COUNTS (0.78 deliberately below the +points argmax
# 0.85; 0.42 ≈ the 39% base) — same-sample caveat labeled in the gate, so WATCH
# the nightly verdict + the sot_total/sot_team family rows; pull back if it
# slides. ENABLED 2026-07-01 (user-directed session flip).
export WC_SOT_TOTAL_ANCHOR=0.78
export WC_SOT_TEAM_ANCHOR=0.42
# Fouls-race game-state tilt (qmodel._foul_dom): the underdog commits more
# fouls; the symmetric counted Skellam missed it (fouls_race was the single
# biggest alpha leak, −128/n=55 — and the old h_fouls_race had the right sign
# but WC_QMODEL preempted it). Gate (_fouls_dom_gain, prior-only, pre-kickoff
# λ): re-priced symmetric +54 vs tilted +177 = APPROVE +123/n=55, beats the
# field-clone at the 0.10–0.25 plateau. HONESTY: 0.15 is the argmax on the same
# settled sample (interior peak, smooth curve — not a grid edge) and the
# baseline is re-priced, not as-sent; treat the magnitude as same-sample until
# post-flip fouls Qs settle. WATCH the fouls_race family row nightly; lower the
# slope if the gate slides. ENABLED 2026-07-01 (user-directed session flip).
export WC_FOULS_DOM=0.15
# Score-or-assist OFF Kalshi (2026-07-01): kalshi_wc.mid() has no spread guard
# and player-prop books are thin — a wide book pins the mid ~0.5 regardless of
# truth (kalshi-priced SOA realized −40/n=5, sent ~0.49 on 20%-YES questions).
# With this ON the family falls through to the book-union h_score_or_assist
# (whose KO name-matching bug is fixed — derive now uses _player_tokens) or the
# 0.24 family placeholder. Gate thin (n=5, +28) but the defect is structural:
# a wide-spread mid is not a probability. ENABLED 2026-07-01.
export WC_KALSHI_NO_SOA=1
# Corner-race supremacy-fallback slope (2026-07-01): the fallback (used only
# when NO corner-spread ladder is quoted) capped its tilt at ±0.10 while
# ladder-implied shares run ±0.17 — the h_fouls_race right-sign-too-weak defect
# class. 0.50 doubles-plus the tilt (max ±0.25). Gate thin (n=2, +17, HOLD) —
# enabled on mechanism + bounded scope (fallback-only), same basis as
# WC_TO_ADVANCE_H2H/WC_PH_COVERAGE. WATCH corners_race families; revert to 0.20
# if they regress. ENABLED 2026-07-01.
export WC_CORNER_SUP_SLOPE=0.5
# WC_BTS_HALF_ANCHOR deliberately NOT set: the 0.68 default is already the
# sweep optimum (0.63 −0.8, 0.72 −2.2 — the morning audit's "raise it" was
# backwards). The knob + gate exist for future evidence.
# Referee cards multiplier (HANDOFF #10, built 2026-07-02): scales card-level
# lambdas by the assigned ref's historical cards/match (API-Football 2022-24,
# shrunk n/(n+6), clamped 0.75-1.35) on the tiers with NO book cards line
# (43/53 settled card-level rows priced bookless). Races cancel the ref;
# derived tier is market-priced — untouched. OFF pending its review_report
# gate (_ref_cards_gain): the FBref schedule cache only carries refs for the
# early matchdays so far (refresh in review.sh backfills nightly); flip when
# the gate clears at n >= 8.
export WC_REF_CARDS=0
# Player-SOT over-pricing anchor: shade book-mapped player ">=1 shot on target"
# props DOWN toward 0.30 at beta=0.5 (player-subject only; mis-mapped team SOT
# totals excluded). ENABLED 2026-06-23 after its gate cleared: review_report
# _player_sot_anchor_gain on SETTLED data = sent +54 vs anchored +158 = +104 over
# n=30 (APPROVE). HONEST forward number is the look-ahead-free expanding-window
# +43 (the +104 anchor value is informed by the realized 0.23 base); helped 8/9
# days, beats the field. Contrarian book edge — books+crowd over-price player SOT
# (longshot/rotation bias), we shade past them. beta=0.5 not 1.0 (expanding-window
# LOSES at 1.0). WATCH the gate nightly; if the deeper cause is rotation, lineup
# data is the better long-term fix. Tunable: WC_PLAYER_SOT_TO/WC_PLAYER_SOT_BETA.
export WC_PLAYER_SOT_ANCHOR=1
# 2H SOT-race goal-share routing + de-compression (the combo, enabled together —
# GS gets the favorite's side right, then DECOMP amplifies the now-correct signal;
# DECOMP alone is premature). The matchday-3 harvest REVERSED the old "race is a
# contrarian edge, compress toward 0.5" thesis: the field is well-calibrated (race
# favorites win ~70%, field ~68%) while our double-damped price sat ~53%/~38% —
# squashed both sides — losing. GS skips qmodel's undifferentiated raw-Skellam
# (counted SOT rates cluster ~4.25 -> 0.44 for everyone) so the race uses market
# goal-share, which identifies the favorite. ENABLED 2026-06-26. Validation is a
# RE-PRICING backtest (now=kickoff, no look-ahead, reproduction err 0.057), NOT a
# clean rescale gate — same constraint WC_KALSHI shipped under, so forward-validate:
# R3 current-regime edge -88 -> -41 (GS) -> -20 (GS+decomp1.5), ~+69 banked pts, but
# the forward sample is THIN (n=8) and it still trails the field-clone ~-20 (our race
# model stays weaker than the crowd; this is damage-control, ~75% of the gap). WATCH
# the SOT-race bucket nightly (review_report carries a WC_SOT_RACE_DECOMP gate); if it
# regresses or through the 2-3x knockout multiplier, flip OFF. Tunable: WC_SOT_RACE_DECOMP
# (gamma, default 1.0 = no-op; in-sample optimum is at the grid edge -> ship gentle 1.5).
# Leading-form races ("In the second half, will...") stay on placeholder — see issue #4.
export WC_SOT_RACE_GS=1
export WC_SOT_RACE_DECOMP=1.5
# Knockout "Will X advance?" router (default OFF, WC_TO_ADVANCE_H2H). These
# to_advance questions have no snapshot market AND no pricer branch, so today they
# fall to a ~50% base-rate placeholder — scored at the 2x/3x knockout multiplier.
# The flag prices them off the h2h market as the 2-way (draw-no-bet) devig
# P(advance)=P(win)/(1-P(draw)); falls back to the placeholder if h2h isn't up yet.
# No settled advancement data exists to OOS-gate (KO-only), so verification is
# "sane numbers on a DB copy", not a backtest — done 2026-06-27 (prices span
# 0.03-0.97 by favorite strength, P(home adv)+P(away adv)=1.000 exactly).
# ENABLED 2026-06-27 ahead of R32 (2026-06-28). Safe today: no to_advance questions
# exist until KO, so it's a no-op for group games. Still confirm the question wording
# matches _ADVANCE_RE against the first real R32 advancement question (else it falls
# back to the placeholder — no harm — until the regex is widened).
export WC_TO_ADVANCE_H2H=1
# Placeholder-coverage router (default OFF, WC_PH_COVERAGE). Late-slate wording
# (knockout "in regulation (90 min + stoppage)", "hydration break", brace) defeats
# the classifier, so a growing share of NO_MARKET questions skip qmodel+handlers and
# fall to the flat 0.45 placeholder (rate spiked ~15% -> 37% on 2026-06-28; settled
# placeholder Qs realize ~2x the negative edge/Q of priced ones). The router fills the
# cheap, high-confidence gaps with pricers we ALREADY own (derive.COVERAGE_HANDLERS):
# "end in a tie" -> h2h draw (our un-renormalized draw edge); "ahead at halftime" ->
# h2h_3_way_h1; "any player 2+ goals" -> a brace off team goal-lambdas. Runs as a
# FALLBACK (never overrides qmodel/a HANDLER). DB-copy demo (2026-06-28): SA-Canada
# brace 0.45->0.26 (field 0.25), tie 0.45->0.28 (field 0.30), ahead-HT 0.45->0.36
# (field 0.44 — follows the sharp h1 market AWAY from the crowd, the real edge case).
# ENABLED 2026-06-28 on the same basis as WC_TO_ADVANCE_H2H / WC_KALSHI / WC_SOT_RACE_GS
# (sane numbers + forward-watch, NOT a clean settled gate — market-derived prices have no
# rescale gate). Justification on SETTLED data: the placeholder bucket it replaces is the
# WORST we have (−0.86 edge/Q vs −0.42 priced) and is information-free (flat 0.45); the
# markets it routes to are already validated (h2h_3_way_h1 +3.46/Q over n=17; h2h draw is
# our standing edge); brace reproduces the field. Leaving placeholders is now 2x costly at
# the KO multiplier. WATCH: run validate_ph_coverage.py after each /harvest-locked once
# these settle, and the coverage bucket in parse_locked — flip OFF if it regresses (esp.
# ahead-HT, which moves AWAY from the crowd toward the sharp h1 market). 66/66 tests pass.
export WC_PH_COVERAGE=1
# Spread-aware Kalshi SOA (2026-07-02, refines WC_KALSHI_NO_SOA): Kalshi SOA is
# a TWO-SIDED exchange book — tight books (stars, ~1-4c) are a real crowd
# probability, arguably the best fair for a family sportsbooks only quote
# one-sided; only the WIDE books (longshots, 15c+) produced the -40 thin-mid
# loss. Setting WC_KALSHI_SOA_MAXSPREAD (e.g. 0.08) re-admits books tighter
# than the threshold; wide ones stay banned. UNSET for now: historical spreads
# were never stored so there is NO backtest — validation is forward-only
# (flip it, then watch the score_or_assist family row for a week; revert on
# regression).
# export WC_KALSHI_SOA_MAXSPREAD=0.08
# Match-total offsides environment blend (WC_OFF_ENV, default 0 = pure counted).
# The 'N or more offside calls' KO wording is an ENVIRONMENT stat (semi-auto
# offside tech; realized 5/7 YES at N=3-4 while we sent 26-35 vs a ~49 field;
# outcome-MLE lambda 4.4 vs counted ~2.5). Root causes fixed same day: (1)
# soccerdata served a Jun-15 cached FBref page to every daily team_rates
# refresh for 18 days (no_cache=True now forced — rates were matchday-1
# totals), (2) even fresh counted sums under-read the calls environment.
# Blend: lam = (1-w)*counted_sum + w*2*tournament_mean (fresh mean 1.73/team
# -> env lam 3.46, auto-updates with each refresh). Gate 2026-07-03: env
# pricing on the 7 settled match-total rows +72 (w=1) / +58 (w=0.7), still
# +10 after dropping the 2 best rows; team-level wording LOSES -51/n=58 under
# env, so the blend applies ONLY to the match-total branch. Same-sample n=7,
# thin by the REF_CARDS standard, but the deviation is TOWARD the field
# (w=0.7 prices ~55/N3, ~43/N4 vs field ~49) and replaces a diagnosed
# artifact (flat 0.35 / stale-data counted). w=0.7 not 1.0: the grid is
# monotone to the edge — don't chase it. WATCH the offsides family row
# nightly; pull toward 0 if the match-total rows regress.
export WC_OFF_ENV=0.7

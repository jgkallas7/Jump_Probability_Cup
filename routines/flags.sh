# WC_* feature flags — SINGLE SOURCE OF TRUTH.
#
# Sourced by BOTH routines/morning.sh (daily forecast+submit) AND
# routines/sentinel.sh (T-75 pre-kickoff revision). They MUST agree: sentinel is
# the LAST writer before a market closes, so any flag it doesn't set is stripped
# back off whatever morning submitted. This file exists because they drifted —
# sentinel.sh set no flags, silently re-pricing every near-kickoff revision with
# the entire feature set OFF and PATCHing the flag-free value over morning's
# flagged one (found 2026-06-23 on Portugal-Uzbekistan: morning devcap'd the h2h
# 0.811->0.733, sentinel re-priced 0.857 flag-free and submitted it). A human
# flips a flag here; code never auto-edits it. Each flag's gate/validation lives
# in the comment beside it.

# Counted-rate quant pricer for alpha (NO_MARKET) questions. Enabled after a
# CLEAN no-look-ahead OOS (evaluate_qmodel.py --prior-only): clean qmodel beat
# what we send (+3.6) and forward use is look-ahead-free (price time only sees
# prior games). Team-rate edge grows as the tournament progresses.
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
export WC_SOT_THRESH_ANCHOR=1
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

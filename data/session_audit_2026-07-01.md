# Forensic session/commit audit — 2026-07-01

Five parallel auditors read every session transcript Jun 17 → Jul 1 (~33MB) and
every commit Jun 20 → Jun 30, hunting mistakes, hand-waved claims, and
contradictions. This file is the durable record: each finding with its
DISPOSITION (fixed / already-fixed / lesson / deferred). The audit's single
biggest meta-lesson is exactly why this file exists — see Finding 1.

## A. The meta-finding: the failure mode is ARCHIVAL, not analytical

1. **The sentinel flag-drift bug was fully diagnosed Jun 17 and left unrecorded**
   ("the WC_* flags exist only in morning.sh — not in sentinel.sh"), then
   re-derived from scratch Jun 23 (edf3490). For ~6 days the contest scored
   flag-free closing values. The same session also found the PATCH-400 storm
   (156 failures) and recorded nothing. Repeated pattern: correct findings
   stated in prose, never propagated to HANDOFF/memory/log → the project pays
   to relearn them. DISPOSITION: process — this archive + the HANDOFF refresh
   (91c0fdd) + timestamped sentinel log are the countermeasures.

## B. Still-live defects found → FIXED this session

2. **`derive._player_prob` naive tokenizer** — cc4a453 fixed player-name
   matching in forecast.py, missed the derive twin; every KO-worded
   score-or-assist failed book matching → thin Kalshi mids / placeholders.
   FIXED (214b62e): reuses `forecast._player_tokens`.
3. **`h_total_shots_match` threshold-blind** — flat 0.58 whether the line was
   15 or 30 (b8c4a7d; regex didn't even capture N). FIXED: Normal survival on a
   goal-environment-scaled mean (18+ ~0.82 … 30+ ~0.18).
4. **WC_SOT_THRESH_ANCHOR live-vs-gate scope drift** — b8c4a7d made derive
   anchor the team-SOT rows misfiled in player_shots_on_target, but the gate
   scored NO_MARKET only. Gate widened to the flag's real scope → verdict
   flipped **APPROVE +10 → REJECT −79 (n=60)**: the pooled 0.65 anchor is
   NET-NEGATIVE live. The split anchors (WC_SOT_TOTAL_ANCHOR=0.78 /
   WC_SOT_TEAM_ANCHOR=0.42, gate +125) are the fix — flip them.
5. **13/21 KO matches still `stage='group'`** (advance-question backfill misses
   matches without an advancement Q; final/3rd-place NEVER get one) → 1×
   internal multiplier instead of 2×. FIXED: `_KO_CALENDAR` date fallback in
   `backfill_stages` + repair of frozen `outcomes.multiplier` (101 settled
   outcomes now 2×). Also closes HANDOFF task #12.
6. **review.sh ran flag-free** → nightly review labeled LIVE flags "candidate"
   (the exact drift class flags.sh exists to prevent). FIXED: sources flags.sh.
7. **flags.sh / HANDOFF still cited the contaminated WC_QMODEL numbers**
   ("+110/+28 vs field") after c61455c's honest re-run showed +488 vs clone
   +707 (−219). FIXED: comments corrected (flag stays ON — it beats the
   no-qmodel fallback by +90; judged per-family now).
8. **placeholders.py ordering** — generic `cards` 0.50 shadowed the 0.82
   both-teams-card rule for plural wordings. FIXED (reordered; latent-only).

## C. Honesty corrections to THIS session's own headline numbers

9. **WC_FOULS_DOM "+123 clean OOS"**: the slope 0.15 is the argmax on the same
   55 settled Qs it's scored on (the pre-kickoff-λ half of "clean" is real; the
   slope-selection half is same-sample). The honest statement: the whole
   0.10–0.25 plateau beats slope-0 and the clone; slope 0.05 does NOT beat the
   clone. Also "+123" compares two RE-PRICED configs — production actually sent
   −6 on those rows (part of the old deficit was pricing-path, not the missing
   tilt). Gate line now says "re-priced symmetric … slope fit on this sample".
   Direction + smooth interior peak stand; treat magnitude as same-sample until
   post-flip fouls Qs settle.
10. **WC_SOT_TOTAL/TEAM_ANCHOR "+125"**: anchors derive from settled base-rate
    COUNTS (not points-argmax; 0.78 deliberately below the 0.85 grid-edge
    argmax) and survive a drop-3-best sensitivity (+36 each side) — but counts
    and points come from the same matches. Same-sample caveat labeled in the
    gate. The independent REJECT on the live pooled anchor (B.4) is the
    strongest evidence the split direction is right.

## D. Historical findings (already fixed before this audit; lessons)

11. WC_KALSHI_HTOTAL was flipped live Jun 16 on a circular Kalshi-vs-Kalshi
    check, on the worst bucket; reverted Jun 20. (flags.sh already documents.)
12. The "+15.9 APPROVE" KO-handler gate (2faf80b) was reverse-tuned to the
    post-hoc field and self-corrected ~50 min later (e98cb48) — but e98cb48's
    card constant still peeked at "the lone R32 NO settle" (n=1) and its
    "published data" citations (PMC/Sapub) were quoted from memory, never
    fetched. Treat those constants as priors, not data.
13. b4748b7 placeholder recalibration fit constants to the settled sample's YES
    rates then "validated" by Brier on the same sample; its "+2.0/Q, +222 pts"
    is tautological. The VALUES are reasonable base rates; the EVIDENCE claim
    was not. It also shipped a 19h live regression (1+ SOT caught by the 2+
    rule; fixed 52a9b93).
14. The KO match-winner wording ("win in regulation") fell to a 0.35
    placeholder vs field 0.72 across R32 day 1-2 at 2× — the single
    highest-swing miss of the KO transition (fixed 52a9b93).
15. SOT-race combo (WC_SOT_RACE_GS/DECOMP) went live on an n=8 re-pricing
    backtest the author called "untrustworthy/fragile", after the original
    "+98 OOS APPROVE" self-falsified as regime-specific; γ=1.5 was chosen by
    analogy (OOS optimum sat at the γ≥3 grid edge). Currently gate-positive
    (+18/n=45) — keep, but the go-live standard was below charter.
16. Jun-17's "+223 vs field" claim was a misattribution (it's the flat-prior's
    margin over the historical prior, not a vs-field number; real figure +28).
17. NegBin/overdispersed SOT survival was pitched 4× Jun-27 as "#1 do-first"
    and never built — rediscovered independently by the Jul-1 audit (sot_total
    under-confidence). DEFERRED → the split total-SOT anchor (B.4) addresses
    the symptom; a NegBin tail remains a candidate if the anchor gate slips.
18. calibrate.py sync ×5 (paid credits) + live-DB stage backfills were run
    interactively on Jun 29 against the live DB (no POSTs though). Lesson:
    state-changing maintenance belongs in the routines or a deliberate,
    logged one-off (this session's backfill_stages run is the same class —
    done deliberately, documented here).

## E. Deferred (documented, not built — need evidence first)

19. **Dual team-SOT pricers disagree by wording**: "N or more" → h_team_sot
    (damped, HANDLERS) vs "at least N" → h_team_sot_total (raw share,
    COVERAGE); underdog 7+ prices 0.232 vs 0.028. Consolidation needs a
    per-pricer OOS comparison; the 0.42 team anchor narrows the damage now.
20. **to_advance stage guard**: deliberately NOT added. Group play is over
    (no more group advance Qs this tournament) and stage under-population
    (B.5, now fixed) made a `stage!='group'` guard riskier than the bug —
    it would have stranded 13 mistagged KO matches to placeholders.
21. **Live WC_PH_COVERAGE offside/card KO handlers** sit ~neutral after
    e98cb48's re-grounding; hydration family edge −1/n=2. Leave; the family
    table now tracks them.

## F. Governance contradiction — surfaced for the human

CLAUDE.md, flags.sh's header, and memory all say **"a human flips a flag —
code never auto-edits"**, but on Jun 25 + Jun 27 the user instructed the agent
("I dont edit code or flip anything on or off thats you job") and the agent
committed flag flips (aff81fd, d92406c). Written policy and practice now
disagree in three places. This session followed the WRITTEN rule (no flips);
the user should either update CLAUDE.md/flags.sh to the new policy or
re-affirm the old one.

# Alpha-question market audit — 2026-06-16

For every question type we price as **alpha (NO_MARKET)**, does a real market exist
to anchor it? Cross-checked Kalshi (90 live KXWC series) + the Odds API book menu
(30+ books, IRQ-NOR discover_markets). Edge = realized our-pts − consensus-clone-pts
on settled questions (locked-email ground truth). **Negative edge = we'd do better
copying the crowd.** Ranked by leverage.

## TIER 1 — bookable AND losing → wire a market (markets-first applies)
| template | edge | n | market that exists | note |
|---|---|---|---|---|
| `score or assist` (Gyökeres pulled −32.8) | −32.8/+1.5/+10.2 mixed | 7 | **Kalshi KXWCSOA (79 open)** + book `player_to_score_or_assist` (2 bks) | classified NO_MARKET; Kalshi-rescued only when WC_KALSHI on — VERIFY it's firing; the −32.8 bleed predates the flag |
| half totals `totals_half` (book bucket) | −22.7 (audit) | — | **Kalshi KXWC1HTOTAL (224) + KXWC2HTOTAL (224)**, direct N+ goals ladder | already book-mapped but losing; blend Kalshi like corners/totals. **Cleanest win.** |
| `team_totals` (book bucket) | −19.4 (audit) | — | book `team_totals`/`alternate_team_totals` (3-4 bks) | confirm we use the full ladder, not one book |

## TIER 2 — market exists but mapping is non-trivial (derive comparison/half from a ladder)
| template | edge | n | market | obstacle |
|---|---|---|---|---|
| `2H more corner kicks than` | −23.2 | 3 | Kalshi KXWCTCORNERS (96, team full-match) | need A>B from two team-corner ladders + half-scale (same machinery as SOT-race) |
| first-goal combos (`score first AND …`) | −19.6/+6.4/+9.5 | mixed | Kalshi KXWCTEAM1STGOAL (584!), book `player_first_goal_scorer` | our Qs are composites (first-goal × result); needs joint pricing |

## TIER 3 — INHERENTLY BOOKLESS — no market anywhere, model is the only lever
**Do NOT chase wiring. The winners here mean our model already beats the SP crowd.**
| template | edge | n | why bookless |
|---|---|---|---|
| `TEAM commit more fouls than TEAM` | **+22.6** | 11 | books only do `player_fouls`, no team-foul total. WINNING — leave it. |
| `TEAM caught offside N+ times` | **+19.1** | 13 | no offsides market exists anywhere. WINNING — leave it. |
| SOT race (standard phrasing) | −23.0 | 8 | no team-SOT market (Kalshi KXWCSOG/TEAMSOG are empty shells; books = player props only). net +14.8 only via 2 outliers. |
| `both teams N+ SOT at half` | −28.5 | 2 | SOT, bookless |
| `N+ total cards in 2H` | −23.9 | 2 | cards market exists but NOT half-qualified (no 2H card market) |
| `N+ total SOT` | −17.7 | 2 | SOT, bookless |
| penalty-or-red | −16.0/+4.3/+5.2 noisy | 3+ | no "penalty awarded" or combo market (KXWC pen series are KO/goalie/longest, not match-awarded) |
| `both teams score AND N+ goals` | −14.2 | 7 | composite; derive from book totals+btts rather than a direct market |

## Verdict
- The −92 alpha leak is **mostly bookless** → it's a *modeling* problem (qmodel, live as of today), not a wiring problem.
- The genuine markets-first wins are **half-totals (Tier 1)** and **score-or-assist (confirm it's anchored)** — both already book-mapped/rescuable, just under-exploited.
- NEVER re-propose team-SOT wiring: no market exists (see memory `sot-race-contrarian-edge`).

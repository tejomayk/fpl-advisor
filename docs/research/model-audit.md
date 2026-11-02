# fpl-advisor model and decision audit (branch model-analysis, 2026-11-01)

Scope: src/fpl_advisor/{model,optimise,chips,league,advise,rules,prices,signals,squad,world,odds,elite,serialize}.py and scripts/backtest.py, checked against the live public FPL API (bootstrap-static game_config.scoring, game_settings, chips, phases; event/7 and event/8 live; fixtures) and against a re-run of the backtest (GW3-8, 2,463 player-matches, same filter as scripts/backtest.py; my run reproduces the reported 5-6 bin: 5.40 predicted vs 3.93 actual).

Nothing in the repo was edited. All scripts and data live in this folder (`bt.py`, `an1-4.py`, `exp.py`, `lam.py`, `prom.py`, `dec.py`, `el.py`, `top.py`). They ran on a copy of `.cache/` plus fresh downloads of bootstrap, fixtures and event/{7,8}/live.

## What is right (checked against the live API)

- All scoring constants in rules.py match `game_config.scoring`: goals 10/6/5/4, assist 3, CS 4/4/1/0, -1 per 2 GC (GKP/DEF), 1 pt per 3 saves, yellow -1, DC 2 pts for DEF/MID/FWD and none for GKP.
- The DC thresholds are confirmed from event/7 and event/8 live `explain`. No DEF scored DC points below 10 (the highest DEF without points was on 9). No MID scored below 12 (the highest without points was on 11). No FWD reached 12 in those two GWs.
- Chips: there are 2 of each, and the windows are read from the API rather than hard-coded. WC and FH run GW2-19 and GW20-38. BB and TC run GW1-19 and GW20-38. One chip per GW is enforced.
- FT cap is 5 (`max_extra_free_transfers: 4`). The sell-on fee is 0.5 (`transfers_sell_on_fee`), and `selling_price` matches it.
- There is no assistant-manager chip in 2026/27 (all `mng_*` scoring is 0).
- Promoted sides are Ipswich, Hull and Coventry. No blank or double GWs are scheduled in the fixtures yet.

---

## Gaps, ranked by likely impact on points

### 1. Start-rate model: shrinks too hard, and counts injured weeks as dropped weeks (high)

**Now:**
- model.py:200-241 decays this season's starts with a half-life of 8 matches.
- It adds a prior worth 3 pseudo-games at `prior_start = past starts / 38` (model.py:231), or 0.5 when there is no past.
- Every 0-minute row counts as a non-start, including weeks the player was injured or suspended (model.py:202-215).
- The same absence is then discounted again by `availability()` (model.py:328).

**Reality:**
- Regular starters start almost every match they are fit for.
- Last season's starts/38 mixes up injury with selection. Calafiori's 22/38 is an injury record, not rotation.

**Evidence:**
- Start calibration is too flat:

  | Predicted start | Actual start |
  |---|---|
  | 0.16 | 0.105 |
  | 0.24 | 0.14 |
  | 0.55 | 0.63 |

- Players who started all of their last 3: predicted 0.805, actual 0.861. Their points: predicted 2.96, actual 3.40.
- Players who started none of their last 3: predicted 0.235, actual 0.133.
- The Brier score of the model's start rate (0.160) is worse than a naive "did he start last GW" rule (0.156).
- Calafiori (8 of 9 starts) is modelled at 0.77, and still at 0.75 with no past at all, because the 0.5 prior is worth 3 games.
- Palmer (9 of 9) is modelled at 0.88, Muharemović (9 of 9, promoted side) at 0.84.
- GKs are hit hardest. GK starters have a mean modelled start rate of 0.84, and their points are under-predicted by 1.15 per match (2.62 vs 3.78). GK overall bias is -0.72, the largest of any position.
- Backup GKs are over-predicted: predicted 1.59, actual 0.00 in 22 cases. The sub-appearance prior of 0.3 is also applied to GKs.

**Impact:**
- Nailed players are under-projected by about 0.3-0.45 pts per GW, and nailed GKs by about 1 pt.
- Rotation risks and recently benched players are over-projected by about 0.4-0.9.
- This pushes the optimiser toward the wrong cheap defenders and GKs.

**Fix:**
- Use a half-life of 3-4 matches and a prior of k=1.
- Base the prior on starts per available match (for example, past starts / (past minutes / 85)) instead of /38.
- Drop rows where the player was unavailable. Use a 0-minute row while flagged as a proxy, and store the flag history going forward.
- Give GKs their own model: first-choice GK start ~0.97, and the backup's sub-appearance rate ~0.

**Backtest (exp.py), half-life 4, k=1, prior = starts per appearance:**

| | corr | MAE | top-30 pts | 2-3 bin |
|---|---|---|---|---|
| Before | 0.378 | 1.849 | 4.11 | 2.47 vs 2.80 |
| After | 0.399 | 1.843 | 4.19 | 2.50 vs 2.55 |

### 2. Transfers are one-shot and held 5 GWs, and the FT value is flat, so the plan takes too many hits (high)

**Now:**
- optimise.py:108-161 makes all transfers now and holds the squad for the 5-GW horizon (weights sum to 3.70).
- It credits a hit against all 5 GWs of gain.
- `ROLL_VALUE = 1.0` per banked FT, flat.
- chips.py:16 uses a different FT value (`FUTURE_FT_VALUE = 1.5`).

**Reality:** A move can usually be made next week with that week's free transfer. So the value of making it now with a hit is roughly next GW's gain minus 4, not 5 weeks of gain minus 4.

**Evidence:**
- On a cached real squad with 1 FT, the solver chose 3 transfers for -8 (dec.py).
- Tzolis → Le Fée shows a weighted gain of +9.1 (+12.2 unweighted over 5 GWs), but only +2.7 in GW10.
- Waiting one week loses about 2.7 and saves 4. The same holds for João Pedro → Evanilson (+4.2 in GW10).

**Impact:** Probably the largest decision leak: several -4 hits a season that a multi-week plan would have avoided, likely 10-25 pts.

**Fix:**
- Make the plan multi-period: transfer variables per GW in the horizon, with FT state (ft_{g+1} = min(5, ft_g - n_g + 1)), hits per GW, and a squad per GW.
- A cheaper approximation: credit a hit only with the gain earned before the next FT could make the same move.
- Make the roll value concave (for example, 2nd FT ≈ 1.5-2, 5th ≈ 0.3) and use one constant in both files.

### 3. Team strength has no pre-season prior, so promoted sides start as league average (high early in the season, medium now)

**Now:**
- model.py:103-137 builds ratings only from this season's finished fixtures.
- It shrinks them toward the league average with 4 pseudo-games (`TEAM_PRIOR_GAMES`).
- Before GW1 every team equals `LEAGUE_GOALS_PER_TEAM`.

**Reality:**
- Last season's ratings and the bookmakers' season prices are strong priors.
- Promoted sides are typically about 1.0-1.1 goals for and 1.7-1.9 against per match.

**Evidence:**
- In GW1 every fixture was projected at exactly 1.51 home / 1.21 away. Coventry vs Arsenal was projected as Arsenal scoring 1.51; it finished 0-3.
- As of GW3, Ipswich vs Liverpool projected Ipswich to score 2.10 after two noisy games; they lost 0-2.
- The pre-season squad build (`initial`) and the first ~5 GWs of horizons run on flat ratings.
- Today the ratings are still compressed by a third toward the mean, because 8 games sit against 4 pseudo-games.

**Impact:** Large for the pre-season squad and GW1-6 transfers (fixture picks are close to random). Smaller after GW10.

**Fix:**
- Seed ratings from last season's team xG for and against, with a decay.
- Seed promoted teams with a "typical promoted side" prior, or from the bootstrap `teams[].strength_*` fields.
- Optionally blend in season outright or handicap odds.
- Replace the fixed 4-game shrink with a prior that starts strong and fades.

### 4. Goal expectancy is too high and clean sheets too low: home advantage overstated, plain Poisson (medium-high)

**Now:**
- `HOME_ADV = 1.12` multiplies the home side and divides the away side (model.py:30, 145-146), so the implied home/away ratio is 1.25.
- `XG_SHARE = 0.7`.
- P(CS) = exp(-λ) for a full match (model.py:370).

**Reality:** This season home/away goals run 1.20 and xG 1.14. Recent PL seasons were 1.1-1.25.

**Evidence:**
- In GW3-8 the mean projected λ was 1.70 home and 1.34 away, against actual 1.40 and 1.27.
- Projected P(CS) was 0.231, actual 0.300.
- Over the season the CS rate is 0.2875, while Poisson at the true mean (1.39) gives 0.250, so even a perfect mean under-counts zeros.
- DEF with 60+ minutes kept a clean sheet 0.31 of the time against 0.23 predicted. That is about 0.3 pts per DEF/GK start.
- Away players are under-predicted by 0.22 per match; home players are over by 0.07.

**Impact:** Defenders and keepers are systematically under-projected by about 0.3-0.5 per start, and away assets are too.

**Fix:**
- Set HOME_ADV to about 1.07 (equivalently, ratio 1.14).
- Re-fit `XG_SHARE` and the overall level against actual goals.
- Use a Dixon-Coles or zero-inflated / negative-binomial correction for P(0).
- Use exp(-λ·m/90) for "no goal while on the pitch".
- Backtest: HOME_ADV 1.06-1.08 alone barely moves corr (0.378→0.383). The bigger gain is in CS calibration, which corr does not show.

### 5. Attacking rates are not regressed toward the population, and FPL assists are under-counted (medium-high)

**Now:**
- model.py:222-237 shrinks per-90 xG and xA only toward the player's own last season (up to 540 prior minutes), or toward a pooled position prior.
- There is no shrinkage toward a position × price population.
- Assists are modelled as xA × 3.

**Reality:**
- Early-season xG per 90 regresses strongly.
- FPL assists are broader than Opta xA: they include rebounds, won penalties and deflections.

**Evidence:**
- For players projected at 4.5 or more (n=50), predicted points were 5.15 against 4.62 actual.
- In that group the model's goal-point term (roughly 1.97) is well above the xG they actually produced in those matches (1.25).
- The 5-6 bin over-prediction (5.40 vs 3.93) is n=15 with a standard error of about 0.9 (about 1.6 SE), so it is mostly noise on top of this shrinkage problem. It is not a separate bug.
- In the same matches bonus was over by 0.35 and DC by 0.07.
- League-wide in GW3-8, FPL assists were 144 against xA of 104 (1.38x).

**Impact:**
- Premium and hot-start attackers are over-projected by about 0.5 pt per GW, which hurts captaincy and buys.
- Creative players are under-projected by about 0.1-0.2 per match.

**Fix:**
- Add a population prior by position × price band, with about 600-900 minutes of weight, on top of the own-history prior.
- Scale xA by a fitted FPL-assist/xA ratio (about 1.2-1.35; refit each season).
- Apply the same xA scaling inside the odds blend, since bookmaker assist markets also follow Opta assists.

### 6. The elite-ownership nudge is large, herds the plan, and leaks into captaincy (medium-high)

**Now:**
- model.py:520-526 adds `ELITE_WEIGHT × (elite_own − mean)` points to next-GW xp. The mean is about 0.022, taken over all 670 players.
- That xp is the "raw" value the captain is chosen on (optimise.py:151-156) and the value shown to users.

**Evidence (signals.json, GW10):**

| Player | Model xp | Nudge | Change |
|---|---|---|---|
| Haaland | 5.38 | +0.98 | +18% |
| Groß | 3.51 | +0.89 | +25% |
| Rogers | 4.06 | +0.90 | |
| Konsa | 4.76 | +0.86 | |
| Calafiori | 4.17 | +0.80 | |

- Ownership counts the elite's whole 15-man squads, bench included, from last GW.
- The supporting backtest (elite_backtest.py, GW3-8) is in-sample: the elite are drawn from this season's top 50k.
- The nudge mostly double-counts what xG already sees.

**Impact:**
- About 0.9 pt per GW on one week is enough to flip close transfers and captain picks toward the template.
- In a mini-league that is the opposite of what a chaser wants.

**Fix:**
- Cut it to about 0.2-0.3, or make it multiplicative and capped (for example ±5%).
- Use only XI ownership; keep captaincy share separate.
- Exclude the nudge from the captain choice and from displayed xp.
- Re-validate on a past season with elite picked from earlier seasons only.

### 7. Bench, auto-subs and vice-captain are crude (medium)

**Now:**
- optimise.py:17 and 152-158 give every bench player a weight of 0.08, GK2 included.
- Auto-subs are not modelled.
- The vice gets 0.05 × xp.
- Bench order is xp-sorted (optimise.py:186).
- XI choice uses availability × points, so a 50% doubtful premium is valued at half.

**Reality:**
- With about 5% of named starters getting 0 minutes, P(at least one auto-sub) ≈ 1−0.95^10 ≈ 40%.
- Bench slot 1 is therefore worth about 0.3-0.4 × its xp, slot 2 about 0.1, slot 3 about 0.04, and GK2 about 0.03.
- A doubtful starter is worth p·x + (1−p)·(bench-1 xp), not p·x.

**Evidence:** In the backtest 9% of 0.9+ "nailed" players did not start. The model's 0.08 is 4-5x too low for the first sub.

**Impact:**
- Under-values a playing first sub (about 1 pt per GW).
- Bench-and-wait decisions on doubtful premiums go wrong.
- Bench Boost value is mis-estimated.

**Fix:**
- Use bench weights that depend on bench order, and enforce formation-valid bench order.
- Value XI slots with explicit auto-sub expectation.
- Make the vice weight P(captain blanks) ≈ 1 − P(captain plays).

### 8. Chip logic is myopic and uses a static squad (medium)

**Now (chips.py):**
- TC and BB are valued on the current base squad and played if now ≥ best later − 0.2 (TC) or − 0.3 (BB). Those margins are far inside projection noise.
- WC is compared against base plus `FUTURE_FT_VALUE × Σw[1:]` (+4.05 pts credited only to the base), and needs 15.
- FH needs 12, or 5 when it is near expiry.
- There is no plan for the order of the 4 first-half chips. All four expire after GW19, and the code only forces one per week once chips ≥ weeks left (chips.py:239-252).

**Reality:**
- Good play schedules the chips together: WC to set up a BB or TC week, FH for blank or double GWs.
- A WC held until the forced cascade (GW16-19) wastes most of its value, since WC2 opens in GW20.
- TC and BB are mostly DGW plays, and DGWs are not scheduled yet. Projecting "best later week" on single-GW means just chases noise.

**Impact:** Easily 10-20 pts a season in chip value.

**Fix:**
- Run a small DP or enumeration over chip-to-week assignments in the window, with the squad re-optimised under each chip.
- Add an explicit WC1 "use by about GW12-15 unless clearly bad" prior, and treat the GW19 expiry as a hard constraint.
- Use a symmetric baseline for WC: both paths get future FTs.
- Re-run chip advice when FPL publishes DGWs and BGWs.

### 9. Transfer display: arbitrary pairing and an unweighted per-pair "gain" explain the live "+0.0" (medium; trust and UX, which drive what the user actually does)

**Now:**
- advise.py:262-271 (`_pair_transfers`) pairs each sell with the first buy of the same position in `plan.buys` order, which is pool id order.
- transfer_lines (advise.py:367-372) and serialize.py:146 show `gain = horizon_xp(in) − horizon_xp(out)`. That is an unweighted 5-GW sum. It ignores horizon weights, bench vs XI, captaincy, the rival multiplier, and budget links between pairs. It also includes the elite nudge.

**Why "+0.0" happens:** The optimiser takes moves whose value is not in that number:
1. A budget enabler. A sideways or down move frees money for an upgrade in another pair; that pair shows ~0, the other pair carries the gain.
2. A rival-ownership swap. Two players with equal xp but different rival ownership have different objective value when λ≠0.
3. FT = 5. `roll` is capped at 4 (optimise.py:106), so the first transfer costs nothing in the objective and CBC can return any zero-gain swap, including bench-for-bench.
4. A bench-to-bench move. Its value is 0.08×.

With two sells in the same position, the pairing is arbitrary, so individual gains can be misleading (for example −3 and +10 instead of +3.5 and +3.5).

**Fix:**
- Compute each move's marginal value as the objective with the plan minus the objective with that move reverted (re-solve with `max_transfers` or with the sell forced kept).
- Show the weighted value of starting-XI points.
- Pair moves with an assignment that maximises per-pair gain under the budget.
- Label enabler moves ("frees £1.2m for X").
- Add a tie-break penalty (for example 0.1 per transfer) so zero-value moves are never suggested.

### 10. The rival-ownership multiplier changes expected points (medium)

**Now:**
- league.py:108-114 multiplies xp by 1 + λ(1−2·own), with λ up to 0.2, so ±20%.
- Ownership is the average over up to 30 tracked rivals' last-GW squads (league.py:193-200).
- It applies to XI, transfers and bench, but not to the captain.

**Reality:**
- In expected-margin terms, owning a player gains you xp × (1−own) versus not owning him and losing xp × own. The difference is always xp, so ownership changes variance, not the mean.
- What matters for winning is the effective ownership (with captaincy) of the one or two managers you must pass, not the average of 30.

**Impact:** It can trade about 0.5-1 real point a week for differential-ness. That is sometimes right when chasing late, but it is applied even with a 30-GW season left.

**Fix:**
- Target the leader (or the top-k who can still win).
- Include captaincy EO.
- Scale λ with remaining GWs.
- Better still, simulate P(finish 1st) with player-score distributions (this needs gap 15).

### 11. Old "last season" data and players with no history (medium)

**Now:**
- world.py:236-237 and advise.py:117-118 take `history_past[-1]` whatever its season.
- Players with no past get the pooled position prior (all minutes in that position) and a 0.5 start prior, or 0.05 if they have no rows (model.py:243).
- `load_world` skips players with 0 minutes this season (world.py:231-233).

**Evidence:**
- In the backtest, 168 player-matches used a "past" season from 2015/16 to 2024/25, from other teams and other leagues.
- New signings and promoted players get no price information. A £9m new signing is projected as an average midfielder.
- Players with 0 minutes so far are never loaded, so they are never considered for transfers (relevant after injuries and January signings).

**Fix:**
- Use history_past only when it is the previous season (decay older seasons heavily).
- Add a position × price prior.
- Use `team_join_date`.
- Load histories for all `can_select` players above a price floor.

### 12. Fixture congestion, Europe and DGW rotation are not modelled (medium; seasonal)

**Now:** The start rate is the same in every fixture, including both legs of a DGW. There is no notion of midweek European or cup matches (FPL fixtures are PL only).

**Reality:**
- Europe-qualified squads (ARS, MCI, LIV, CHE, NEW, AVL and others) rotate around European and cup weeks.
- DGW second legs and festive congestion cut start rates for the stars.

**Fix:**
- Add an external fixture calendar (or kickoff gaps from FPL fixtures plus a hard-coded competitions list).
- Apply a start-rate discount for short rest (for example under 72 h), learnt per team or manager.
- Discount the DGW second leg.

### 13. Only means are produced, with no uncertainty (medium-low now; it blocks better decisions)

Everything is an expected value. That makes it impossible to:
- value TC by haul probability against a rival's captain;
- run risk-seeking strategies on a real P(win);
- give the user ranges;
- tell signal from noise in the chip "now vs later" margins (0.2/0.3).

**Fix:** Sample per-player match outcomes from the component model (minutes, Poisson goals/assists, CS from team λ, bonus given returns) and keep the means for the solver.

### 14. Bonus is a historical per-90 rate, not driven by BPS or match outcome (low-medium)

**Now:** model.py:382-387 uses bonus90 × √(att_mult) or a CS-based multiplier.

**Evidence:**
- Calibrated overall (predicted 0.151 vs actual 0.154 per match).
- GKs under (0.18 vs 0.27), and the top bins over (0.72 vs 0.56 at 4.5+).
- Bonus is concentrated in returns: 1.19 when a starter scores or assists, 0.09 when he blanks.

**Fix:** Model bonus as P(return) × E[bonus | return, position], plus a CS-linked term for DEF/GK (BPS now also counts CBIT and recoveries), fitted from live `bps` data.

### 15. Penalties and set pieces are ignored (low-medium)

Bootstrap `penalties_order`, `direct_freekicks_order` and `corners_and_indirect_freekicks_order` are not used. A change of penalty taker only shows up slowly through xG. In the backtest, first-choice takers were over-predicted by 0.38 (n=114, mostly the shrinkage issue in gap 5). Fix: give the first-choice taker penalty xG (team penalty rate × 0.78) and remove past penalty xG when the taker changes.

### 16. Odds blend details (low-medium)

- Fixture odds change only the first GW (model.py:473), so GW1 and GW2+ in the same horizon sit on different scales.
- The weights 0.6 and 0.5 are not backtested.
- The assist market is Opta-defined, while FPL assists run about 1.38x xA this season, so the blend pulls FPL assists down.
- The scorer adjustment changes goals but not the bonus linked to them.
- DGWs skip player odds.

**Fix:**
- Extend the odds-implied λ to later GWs with the team-rating ratio.
- Scale the assist target by the FPL/Opta factor.
- Log odds and outcomes so the weights can be fitted.

### 17. Small rules gaps in the scoring formula (low)

Not modelled:
- red card −3
- own goal −2
- penalty miss −2
- penalty save +5 (GK)

From GW3-8 data the net effect is about −0.016 pts per player-match (reds), plus about +0.004 per GK-match (penalty saves). Other small approximations:
- CS uses full-match λ rather than on-pitch λ.
- A sub gets no DC or bonus.
- Sub minutes are fixed at 20.

These are noise-level, but cheap to add for completeness.

### 18. Edge cases in purchase price and FT accounting (low; correctness)

- squad.py:143-147 takes the price from the history row with `round == start_gw`. If the player had no row that GW (he joined later, or his team blanked), it falls back to `price_start`, the season start price, which is wrong for late joiners.
- The live code does not handle a one-off FT top-up like the 2025/26 AFCON top-up to 5. `sim.py` has `ft_topup`, but `count_free_transfers` (squad.py:52-68) does not. If FPL repeats it in 2026/27, the FT count will be wrong.
- The FT count cannot see transfers already made for the next GW (this is noted to users).

### 19. Price-change timing is hard-coded (low)

prices.py:17 assumes about 01:30 UK every night. The API publishes `game_config.settings.price_change_deadlines` (currently 2026-11-03/04/05 00:00Z) and `status.price_change_last_updated` (2026-11-02 00:50Z). It also exposes `price_change_locked_until` and `price_change_calibrating` per player. Fix: read these, so "before prices update tonight" is accurate. The optimiser itself gives no value to team value, bank, or price rises (see gap 20).

### 20. Decision horizon details (low-medium)

- No end-of-horizon value. A player bought for GW10-14 is worth nothing after GW14, so short fixture swings beat long-term assets.
- Bank money has no value (optimise.py:118-121). That is fine for points, but £0.1m can matter for future moves.
- The horizon weights (0.85 per week) are a judgment call and have not been tuned in the season simulator.
- The captain is picked on raw xp (optimise.py:153-156). That is reasonable for EV, but raw xp includes the elite nudge (gap 6), and doubtful captains are not valued with the vice fallback.

**Fix:**
- Add a terminal value (for example 0.5 × next-5-GW xp for the end squad).
- Add a small bank value.
- Tune the horizon weights and the terminal weight in scripts/season_sim.py.

### 21. The backtest cannot answer the questions being asked of it (medium, as tooling)

scripts/backtest.py:
- runs with `use_availability=False`, so injured players' zeros sit in the actuals;
- has survivorship bias: only players with minutes>0 by today are loaded;
- uses today's prices and positions in `as_of`;
- covers 6 GWs with n=15 in the 5-6 bin (SE ≈ 0.9 pt);
- has no decision-level metric;
- never tests odds, news or elite.

**Fix:**
- Report calibration by position, price band, home/away and start status, as in an1.py and an2.py here.
- Report start-rate Brier and CS calibration.
- Use multiple past seasons. The untracked scripts/season_sim.py and src/fpl_advisor/sim.py seem to head this way.

---

## Numbers at a glance (GW3-8 backtest, n=2,463)

| Slice | n | pred | actual | bias |
|---|---|---|---|---|
| All | 2463 | 2.11 | 2.18 | −0.07 |
| GKP | 140 | 2.46 | 3.19 | −0.72 |
| DEF | 869 | 2.08 | 2.23 | −0.16 |
| MID | 1162 | 2.07 | 2.04 | +0.04 |
| FWD | 292 | 2.16 | 2.12 | +0.04 |
| Away | 1230 | 1.98 | 2.20 | −0.22 |
| Home | 1233 | 2.23 | 2.17 | +0.07 |
| Did not play previous GW | 615 | 1.33 | 0.65 | +0.68 |
| Played previous GW | 1848 | 2.37 | 2.69 | −0.32 |
| £7-9.9m | 121 | 3.51 | 4.15 | −0.64 |
| £10m+ | 12 | 5.97 | 4.92 | +1.05 |
| Penalty taker #1 | 114 | 3.42 | 3.04 | +0.38 |
| Promoted teams GW3-5 | 180 | 1.86 | 1.77 | +0.09 |
| Others GW3-5 | 977 | 2.23 | 2.44 | −0.21 |
| Bin 5-6 | 15 | 5.40 | 3.93 | +1.46 (SE≈0.9) |
| Bin 4.5+ | 50 | 5.15 | 4.62 | +0.53 |

Variants tried (exp.py):

| Variant | corr | MAE | top-30 |
|---|---|---|---|
| Baseline | 0.378 | 1.849 | 4.11 |
| Start half-life 4, k=1, prior per appearance | 0.399 | 1.843 | 4.19 |
| HOME_ADV 1.08 | 0.382 | 1.846 | 4.19 |
| Both (HOME_ADV 1.07) | 0.403 | 1.839 | 4.12 |

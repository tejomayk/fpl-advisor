# What our method is missing: research notes for fpl-advisor

*Written 2026-11-01 (2026/27 season, GW9). Every claim is linked to its source. Where a source gives no method, I say so.*

## 0. Where we stand

Our method projects expected points (xP) for each player in each fixture. It then runs one MILP that makes **all transfers now** and holds the squad for 5 GWs, with weights `[1, .85, .72, .61, .52]`. Each extra free transfer we save is worth a flat **+1 point**, the bank is worth **0**, and the bench counts at a flat **8%**. Nothing after the horizon has any value. We treat xP as certain: we use one number per player and have no variance, no correlation and no scenarios.

The leading tools differ from us in four ways, covered below. They plan transfers week by week across the horizon, give saved FTs and the bank a real value, make decisions that hold up under noisy inputs, and get minutes right.

## 1. What kind of problem FPL is

| Problem class | Why it fits FPL | What it gives us | Practical algorithm |
|---|---|---|---|
| **Partially observable MDP / Bayesian RL** | The squad, bank, FTs and chips form a state. Transfers are the actions. Player ability is hidden and gets learned from data. | The correct theory: today's moves should consider tomorrow's options. | [Matthews, Ramchurn & Chalkiadakis (AAAI 2012)](https://eprints.soton.ac.uk/340382) built a belief-state MDP solved with Bayesian Q-learning. It ranked around the top 1% of 2.5M players in a replay ([Southampton release](https://www.southampton.ac.uk/news/2012/07/artificial-football-manager.page)). Exact solution is intractable, so in practice people approximate it. |
| **Multi-period MILP with a rolling horizon** (= model predictive control) | You plan N weeks ahead, carry out only week 1, then re-solve when new information arrives. | Handles FT banking, sequenced moves and chip weeks within one model. | This is the practitioner standard: [sertalpbilal/FPL-Optimization-Tools](https://github.com/sertalpbilal/FPL-Optimization-Tools) and the [FPL Review solver](https://docs.fplreview.com/the-model/solvers/settings/). We already use a MILP. What we lack is **separate transfer variables for each week**. |
| **Stochastic programming / robust optimisation** | xP is a forecast with error. Minutes especially are uncertain. | Moves that survive noise. Fewer hits wasted on one noisy projection. | Approximated with sampled scenarios plus a vote. [FPL Review sensitivity analysis](https://docs.fplreview.com/the-model/solvers/sensitivity-analysis/) uses 20 runs by default (20–100 recommended), adds noise to ratings, xMins and settings, and scores each run's top moves 3/2/1. [Ramezani & Dinh (2025)](https://arxiv.org/abs/2505.02170) compare deterministic and robust MILPs. |
| **Portfolio optimisation with correlation** | Teammates' points move together: one team's goal can feed a scorer, an assister and the keeper's clean sheet. | You can choose your variance, low when protecting a lead and high when chasing. | Mean–variance or scenario-based MILP. |
| **Tournament (rank) objective**, as in DFS "GPP" contests | A mini-league pays only for 1st place, not for total points. | Maximise P(finish 1st) instead of expected points. | [Hunter, Vielma & Zaman, "Picking Winners"](https://arxiv.org/abs/1604.01455): an integer program that rewards variance and low overlap with opponents. [Haugh & Singal (Management Science 2021)](https://papers.ssrn.com/abstract=3393127) model what opponents pick with a Dirichlet-multinomial. [Newell's thesis](https://www.semanticscholar.org/paper/Optimizing-daily-fantasy-sports-contests-through-Newell/06b2c8f3c6ed200eb216ed0dcf6a2385a42cb98d) uses a stochastic integer program for DFS. |
| **Optimal stopping / option value** for chips | Each chip is a one-off option with a deadline (GW19 for the first set). | "Play now" should beat the *expected best future use*, not just the best week we can see. | Compare against a threshold: play only if this week's chip gain is greater than the expected maximum of future gains, estimated from simulated futures. |
| **Bayesian forecasting / state-space models** | Ability changes slowly. Samples are small. | Shrinkage stops over-reaction to a few hot games. | Hierarchical Poisson / Dixon–Coles team models ([AIrsenal](https://github.com/alan-turing-institute/AIrsenal/blob/main/docs/how-it-works.md) uses Dixon–Coles via `bpl`, or xG with a Conway–Maxwell–Poisson distribution). Dirichlet models divide a team's goals among its players. |

**Bottom line:** FPL is a POMDP. The workable approach is **rolling-horizon stochastic MILP (MPC) + Bayesian forecasts + a rank-aware objective**. We have the skeleton of the first and a light version of the third.

## 2. State of the art in FPL

### Academic work
- **Matthews et al. 2012**: belief-state MDP with Bayesian Q-learning, around the top percentile ([eprints](https://eprints.soton.ac.uk/340382)).
- **Ramezani & Dinh 2025** ([arXiv 2505.02170](https://arxiv.org/abs/2505.02170)): MILP selection with many forecasters, tested on 2023/24 GW27–38. ARIMA(1,0,0) with weekly re-solving scored 704 points against 635 for a weighted average. Blending model points with actual points at 1:2 helped. Chips and multi-week transfer planning were left as future work. The paper also lists earlier work: Gupta 2019 (RNN–ARIMA), Bangdiwala 2022, Pokharel 2022 (XGBoost), Venter & van Vuuren 2024 (*ORiON*, a top-4% finish), Khamsan & Maskat 2019 (price-change classification).
- **OpenFPL** ([Groos 2025, arXiv 2508.09992](https://arxiv.org/html/2508.09992v1)): open, position-specific XGBoost + Random Forest ensembles on FPL + Understat data, with features averaged over the last 1/3/5/10/38 matches. Tested prospectively against FPL Review on 2024/25. Its error (RMSE) on "haulers" was 5.14 against FPL Review's 5.17, so OpenFPL was better on the big scores that move rank. It was worse on players who scored zero (0.82 vs 0.69). The author puts that down to FPL Review's **better minutes information**.

### Practitioner tools (parameter values)
**sertalpbilal FPL-Optimization-Tools**, from [`comprehensive_settings.json`](https://raw.githubusercontent.com/sertalpbilal/FPL-Optimization-Tools/main/data/comprehensive_settings.json):
- `horizon` 8, `decay_base` 0.9
- `ft_value` 1.5, with values that depend on how many FTs you hold: `{2: 2.0, 3: 1.6, 4: 1.3, 5: 1.1}` (the 2nd FT is worth most, and later ones are worth less)
- `itb_value` 0.08 points per £1m in the bank
- `bench_weights` GK 0.03, sub 1 0.21, sub 2 0.06, sub 3 0.002; `vcap_weight` 0.1
- `no_transfer_last_gws` 2: no transfers in the last 2 GWs of the horizon, so the solver can't grab short-term gains with no thought for what comes after
- `ft_use_penalty` 0.2; `hit_cost` 4
- `randomized` with `randomization_strength`, plus `num_iterations` with `iteration_criteria`, to produce several different plans
- `xmin_lb` 300 (drops players with low expected minutes)

**FPL Review solver** ([settings docs](https://docs.fplreview.com/the-model/solvers/settings/)):
- Transfer Depth 6, set a couple of GWs short of the projection horizon. The GWs after it still count in the score, but no transfers happen in them.
- FT Value 1.75 by default; the docs cite about 2.0 as a baseline.
- Time Decay 0.85, with a suggested range of 0.80–0.95.
- Bank Value 0.10 per £1m (range 0–0.20).
- Bench weights SGK 0.03, S1 0.30, S2 0.10, S3 0.03; VC weight 0.05.
- Same-team GK/DEF limit 3, with 2 suggested for diversification.
- "Future Info Value" for saving FTs to use once more news is in.
- An experimental "Risk Position" setting based on EV × EO (−0.15 to +0.15).

**FPL Review xMins** ([docs](https://docs.fplreview.com/the-model/projections/xmins/)): average minutes over 1,000 simulations. It models specific rotation events, and expected minutes fall further into the horizon depending on how injury-prone the player is and how secure his role is. People check press-conference and leaked news by hand before the deadline. The formulas are not published.

**AIrsenal** ([docs](https://github.com/alan-turing-institute/AIrsenal/blob/main/docs/how-it-works.md)):
- Team model from xG, or Dixon–Coles.
- Dirichlet model for each player's share of the team's goals and assists.
- Minutes are sampled from recent matches (at least 3), with last season used to fill gaps.
- Players with ≤50% chance of playing get 0 minutes. Default horizon is 3 GWs.

**Fantasy Football Hub and FPL Review's "Massive Data" model:** I found no published methods. OpenFPL is the only public comparison against the Massive Data model.

## 3. Projection modelling best practice

1. **Model minutes as their own stage.**
   - Split minutes into three outcomes: start, sub appearance, unused. Combine them as P(start)·mins|start + P(sub)·mins|sub ([campbelltom/fpl-projections](https://github.com/campbelltom/fpl-projections)).
   - Shrink P(start) toward a prior when the sample is small. Model P(60+ minutes | start) separately ([kelihh/fpl-machine](https://github.com/kelihh/fpl-machine)).
   - Using average minutes understates the scores of rotation players (same source). The 60-minute appearance point, clean sheets and defensive contributions all depend on the *distribution* of minutes, not the mean.
   - OpenFPL's evidence says minutes are where the remaining gap is.
2. **Fixture congestion.** Top-six clubs carry the most congested minutes: +2,003 congested minutes per player across 2015/16–2024/25 ([Allen et al., BJSM 2025](https://bjsm.bmj.com/content/59/23/1643)). Wide midfielders and strikers get rotated most in congested spells, centre-backs least ([Huddersfield study](https://www.hud.ac.uk/news/2020/november/football-fixture-congestion-new-study/)). **Add:** lower P(start) for attackers when the gap since their previous match is under 96 hours or a European game comes next.
3. **Finishing skill: regress hard to xG.** Goals minus xG has a year-to-year correlation of only about 0.12, while shot volume is around 0.63 ([arXiv 2512.00203](https://arxiv.org/pdf/2512.00203)). Finishing ratios carry about 10% signal even past 60 shots ([AnalyticsFC](https://analyticsfc.co.uk/blog/2025/02/11/are-some-players-consistently-good-finishers/)). **Add:** project from xG and xA per 90 plus shot share, with only light credit for finishing skill. Do this through Bayesian shrinkage toward a position prior, weighted by number of shots.
4. **Team strength.** Betting odds usually beat statistical models. An Elo rating built from odds beat classic Elo ([Wunderlich & Memmert, PLOS ONE 2018](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5988281/)). Dixon–Coles models can beat bookmakers on exact scorelines, which carry a favourite–longshot bias ([Singleton & Vaughan Williams](https://www.scribd.com/document/647272519/reade-singleton-scorelines)). **Add:** blend odds-implied ratings into *all* future GWs, not only the next one. Odds are the best single input.
5. **Defensive contributions (DC) are a minutes × role model.** The DC threshold is 10 CBIT for defenders and 12 CBIRT for midfielders and forwards, worth 2 points ([PL 2025/26](https://www.premierleague.com/en/news/4362211)). These rules are unchanged for 2026/27, but BPS was tweaked to reduce overlap with DC ([PL 2026/27](https://www.premierleague.com/en/news/4679873/all-you-need-to-know-about-changes-to-fpl-for-202627)). Model P(DC) from each player's per-90 rate *and* the minutes distribution, since you can't reach the threshold in a cameo.
6. **Variance and correlation.** Our projections give only a mean. Simulating scorelines and then dividing team goals with Dirichlet shares (the AIrsenal approach) gives correlated samples for each player almost for free. That makes captain variance, stacking risk and P(rank) computable.
7. **Price changes.** FPL now publishes an official daily Price Change Predictor ([PL 2026/27](https://www.premierleague.com/en/news/4679873/all-you-need-to-know-about-changes-to-fpl-for-202627)). We should read it rather than build our own.

## 4. Season-level strategy

**Current rules (verified):**
- Two sets of the four chips: Wildcard, Free Hit, Triple Captain and Bench Boost. The first set must be used by the GW19 deadline, Sat 2 Jan, 13:30 GMT. Only one chip per GW ([PL chips 2026/27](https://www.premierleague.com/en/news/4679879/whats-happening-with-fpl-chips-in-202627)).
- FT cap is 5. There is **no AFCON top-up this season**: it happened at GW16 in 2025/26 only ([PL 2026/27](https://www.premierleague.com/en/news/4679873/all-you-need-to-know-about-changes-to-fpl-for-202627); [2025/26](https://www.premierleague.com/en/news/4362211)).
- 2025/26 widened the assist rule (for example, a won handball that is converted counts as an assist) ([Rotowire](https://www.rotowire.com/soccer/article/fpl-rule-changes-2025-26-season-assists-defensive-contributions-fantasy-premier-league-double-chips-94629)).
- 2026/27 BPS changes: defenders get 1 BPS per 3 CBI (it was per 2), and there is no longer a deduction for being tackled, per fan-site round-ups such as [Fantasy Football Fix](https://www.fantasyfootballfix.com/blog-index/fpl-2026-27-new-rules/) (the official article gives no figures). **Check `rules.py` against this.**

**Valuing FTs and the bank.**
- Practitioners value a saved FT at **1.5–2.0 points**, falling as you hold more: 2.0 → 1.6 → 1.3 → 1.1 (sertalpbilal). FPL Review's default is 1.75.
- The bank is worth **0.08–0.10 points per £1m**.
- One analyst puts a transfer's lifetime gain at about 6 points on average ([FFScout PGLT](https://www.fantasyfootballscout.co.uk/2021/10/08/points-gained-over-the-lifetime-of-a-transfer/)). That is a check on whether a hit is worth −4.
- Our flat +1 and £0 bank make us **too eager to spend FTs** and blind to the value of money.

**End of horizon.** Both tools stop *transfers* before the projection horizon ends (2 GWs early, or a depth of 6 against a horizon of 8). That way squad quality in the later weeks still counts. Our 5-GW hold treats the squad as worthless after GW+5.

**Chips.**
- Consensus says save BB and TC for double gameweeks. A BB where two bench players don't play loses about a third of its value ([FFScout](https://www.fantasyfootballscout.co.uk/2022/09/27/why-fpl-managers-should-be-thinking-about-their-bench-boost-now/)).
- Published EV numbers for chips come from vendors and are weak evidence (e.g. [fplcopilot](https://fplcopilot.com/chip-strategies)).
- Treat chips as options. Solve chip placement *inside* the multi-period MILP, as both tools do with `use_bb/use_tc/use_wc/use_fh` and chip limits, then compare against simulated futures.

**Mini-league objective.**
- What matters is **effective ownership** (EO = owners who start him + extra for captaincy and triple captaincy, which can exceed 100%), not plain ownership ([FFScout](https://www.fantasyfootballscout.co.uk/2021/03/07/how-to-use-effective-ownership-to-make-differential-fpl-decisions)).
- A differential pays only if it outscores the popular player it replaces ([FFScout](https://www.fantasyfootballscout.co.uk/2020/06/30/are-differentials-always-the-most-effective-route-to-fpl-success/)).
- The theory comes from DFS: maximise P(win) with variance that depends on your standing and with overlap against opponents counted explicitly ([Hunter et al.](https://arxiv.org/abs/1604.01455), [Haugh & Singal](https://papers.ssrn.com/abstract=3393127)).
- Our multiplier `1 + λ(1 − 2·own)` ignores **rival captaincy and chips** and is applied to xP rather than to simulated rank. The captain choice is the biggest lever on variance.

## 5. How to evaluate

- **Forecasts:** RMSE/MAE split by outcome (zeros, blanks, tickers, haulers), as OpenFPL does, plus calibration of P(start), P(clean sheet) and P(DC). Overall RMSE hides what matters, which is the hauls.
- **Decisions:** replay whole seasons GW by GW. At each deadline, use only the information available then, re-solve, apply transfers, chips and captain, and score with actual points. Report total points, points net of hits, and rank against a real ownership sample. Compare this to the "decision backtests" Ramezani & Dinh ran for 12 GWs.
- **Mini-league:** Monte Carlo the rest of the season using rivals' actual squads, and report **P(finish 1st)**, which is the number we care about.
- **Statistics:** results across seasons vary a lot. Use paired comparisons (same GWs, two policies) and at least 2–3 seasons before claiming a gain. Our `scripts/backtest.py` should report decision-level numbers alongside forecast error.

## Ranked recommendations for fpl-advisor

| # | Change | Why | Impact | Effort |
|---|---|---|---|---|
| 1 | **Transfer variables for each GW in the MILP** (buy/sell/FT state per week, FT cap 5, hits per week) instead of "everything now, hold 5" | It's what every serious tool does. Lets the solver roll, then double up on moves; plan around blanks and doubles; and sequence moves around injuries. | High | Medium |
| 2 | **Calibrated FT and bank values**: FT value depends on how many you hold (≈2.0/1.6/1.3/1.1), bank ≈0.08–0.10 per £1m. Expose both in config. | Our +1/£0 makes us spend FTs too readily and ignore money. | High | Low |
| 3 | **End-of-horizon handling**: horizon 8, decay ≈0.85–0.9, no transfers in the last 2 GWs | Stops the solver favouring short-term gains, with no regard for later weeks, at the end of the plan. | Medium–High | Low |
| 4 | **Minutes model**: P(start)/P(sub)/P(60+), shrunk toward a prior, with a congestion/Europe term and injury-return ramps. Use the minutes *distribution* for appearance, CS and DC points. | Best-documented source of forecast error (OpenFPL vs FPL Review). | High | Medium |
| 5 | **Sensitivity runs**: 20–50 solves on xP perturbed by noise in ratings, minutes and settings; recommend moves by vote | Robust moves, and gives users a "confidence" figure in plain words. | Medium–High | Low–Medium |
| 6 | **Position-specific bench weights** (GK ~0.03, S1 ~0.2–0.3, S2 ~0.06–0.1, S3 ~0.01–0.03) + vice-captain weight ~0.05–0.1 | Our flat 8% gives too much weight to the 3rd sub and too little to the 1st. | Medium | Low |
| 7 | **Odds-implied team strength for every horizon GW**, and xG-based finishing with shrinkage | Odds are the strongest public signal. Finishing skill barely persists. | Medium | Medium |
| 8 | **Simulation layer**: correlated scoreline-based samples per GW, then P(finish 1st) for the mini-league. Use EO including rival captains and chips, and choose captain and differentials to maximise P(1st). | Directly targets the user's goal, which is to win the league, not to maximise points. | High (for the league goal) | High |
| 9 | **Chips inside the MILP**, compared with simulated futures (option value) before GW19 | Better chip timing than a week-by-week comparison. | Medium | Medium |
| 10 | **Decision backtest + P(1st) metrics** in `scripts/backtest.py`, split by outcome type | Without this we can't tell whether 1–9 help. | Enabler | Medium |
| 11 | Read FPL's official price predictor; update `rules.py` for the 2026/27 BPS changes | Cheap correctness fixes. | Low–Medium | Low |

Suggested order: **2, 3, 6** (quick parameter fixes) → **10** (so later changes can be measured) → **1** → **4, 7** → **5** → **8, 9**.

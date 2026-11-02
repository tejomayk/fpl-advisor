# Model plan: from "best squad for 5 weeks" to "win the league"

Sources: [research notes](research/model-research.md), [code audit](research/model-audit.md).
Scoreboard: `uv run python scripts/season_sim.py` (whole past seasons, advice replayed week by week
with only what was known at the time, scored on real points) and `scripts/backtest.py`
(one-week prediction accuracy).

## What kind of problem this is

FPL is a **sequential decision problem under uncertainty**. The state is your squad, bank,
free transfers and chips. Each week you act (transfers, XI, captain, chip), the world reveals
points, and you act again. In theory that is a Markov decision process; in practice its state
and action spaces are far too big to solve exactly, so every strong tool uses the same
approximation:

1. **Forecast** each player's points as a distribution, not a single number
   (Bayesian/shrunk rates for minutes, goals, assists, clean sheets, defensive contributions).
2. **Plan** with a **rolling-horizon integer program** (model predictive control): plan moves for
   each of the next ~8 weeks, carrying free transfers, money and chips forward, then act only on
   this week's moves and re-plan next week. Saving a transfer or money is then worth exactly
   what the plan can do with it later, with no guessed constants.
3. **Stress-test** the plan: re-solve on many perturbed forecasts and keep the moves that survive
   (robust / scenario optimisation).
4. **Aim at the real objective.** A mini-league pays only for 1st place, so the final choice
   among good plans should maximise the chance of finishing 1st, not expected points. That is the
   tournament problem from daily fantasy sports: take variance when behind, cover the leader's
   players when ahead, weigh captains by effective ownership.

## Where we are

- Forecast: means only. Decent ranking (corr 0.378 vs 0.265 for points-per-game), but start
  rates are too low, attacking rates are not regressed, home advantage is too high, clean sheets
  too low, no team prior for promoted sides, elite ownership leaks into expected points.
- Plan: one shot, every transfer now and held for 5 weeks; a saved transfer is a flat +1 point;
  money is worth 0; nothing after week 5 counts; flat bench weight; chips judged on a static
  squad, and expiring chips are forced through.
- Objective: expected points, bent by a rival-ownership multiplier that ignores captaincy.
- Display: sells and buys are paired arbitrarily, and the per-pair "gain" ignores everything the
  optimiser actually weighed (hence the "+0.0" move).

Season sim baseline (points after hits): 2023-24 **2362**, 2024-25 **2350**, 2025-26 **2181**.

## Plan, in stages (each judged on season sim and backtest before and after)

**Stage 1 — forecast fixes** (`model.py`): start-rate model that separates "injured" from
"dropped" and shrinks less; regress xG/xA per 90 to position priors and correct FPL's assist
rate; team-strength prior from last season (promoted sides from the relegated sides' level);
calibrate home advantage and clean sheets (Dixon–Coles style); move elite ownership out of
expected points (it becomes a separate signal, never a captain driver); per-player variance so
later stages can sample.

**Stage 2 — multi-period planner** (`optimise.py`, `advise.py`, `chips.py`): transfer variables
for every week of an 8-week horizon with free-transfer state and the cap of 5; per-week hits;
bank carried forward with a small terminal value; end-of-horizon value for the final squad;
per-slot bench weights from auto-sub odds; chips as decisions inside the same plan, with expiry
handled by value rather than forced; the club limit can never make the plan infeasible. The
plan says, in plain words, which moves are this week and which are pencilled in for later.

**Stage 3 — robustness**: solve on 20–50 perturbed forecasts and recommend moves by how often
they appear; show that as confidence ("strong", "close call").

**Stage 4 — league objective**: simulate the rest of the season for you and your rivals
(their squads, captains, chips from public data), estimate P(finish 1st), and use it to choose
between near-equal plans and captains; risk on when behind, cover the leader when ahead.

**Stage 5 — explanation**: pair moves the way the plan does (including "this sale pays for that
signing"), show money cost, and give each move's gain as the plan counts it.

Rules for every stage: live advice must stay fast enough for the website (target under 10 s per
request); no paid calls in the sim; no stage ships unless season-sim totals beat the previous
stage on at least two of three seasons and do not fall on the third by more than noise.

## Stage 3 and 4 results (season sim, 1% jitter, seeds 1-6, `--time-limit 10`)

Stage 3 as built (`robust.py`, `advise.decide`): instead of re-solving on perturbed forecasts,
the fixed plans (planner's choice, hold, best plan without a hit) are scored on forecasts moved
by each player's `mean_sd` (one error per player, shared across his gameweeks). The plan value is
linear in the forecasts, so P(A beats B) is exact for that error model and costs microseconds.
Rule: take a hit only if P(beats the no-hit plan) >= 0.8; move only if P(beats holding) >= 0.7.
Tuned on 2025-26; planner constants unchanged (transfer penalty 0.3 lost 56 points, se 16;
penalty 0 and free-transfer end values x0.7 / x1.5 were within noise but lower).

| season | original | Stage 1+2 | Stage 3 | vs Stage 1+2 (se) | hits/season (was) |
|---|---|---|---|---|---|
| 2023-24 | 2367 | 2383 | 2366 | -16.5 (32.1) | 0.0 (3.5) |
| 2024-25 | 2417 | 2536 | 2549 | +12.5 (13.4) | 0.2 (2.2) |
| 2025-26 (tuning) | 2166 | 2197 | 2204 | +6.5 (19.4) | 0.5 (4.8) |

All three differences are within noise; the rule mostly removes hits. Live advice: 2-9 s.

Stage 4 as built (`leaguechoice.py`): with a league, the expected-points plan, holding, the plans
the rule set aside and the next 4 captains are compared on P(1st) with common random numbers;
switch only when the gain is over 2 standard errors (about 0.2 s). Synthetic leagues
(`scripts/league_choice_eval.py`, captain only, 150 leagues x 3 seasons x 2 seeds, GW10-38):
win rate 0.0945 (expected points) vs 0.095 (P(1st)), difference +0.0005 (se ~0.002): no
measurable gain. Captains switch in 1-5% of league-weeks.

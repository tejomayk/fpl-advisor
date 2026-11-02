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

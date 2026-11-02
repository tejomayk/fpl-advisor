"""How sure is the plan? Stress-test this week's moves against forecast error.

Expected points are estimates. `model.PointsDist.mean_sd` says how unsure each one is (a player
whose rate rests on few matches is less certain), and the error is shared across that player's
gameweeks: if we overrate him this week we overrate him next week too. So a plan is tested by
drawing one error per player, `mean + z * mean_sd` in every gameweek, and scoring the fixed plans
(squads, line-ups, captains as the planner chose them) on those forecasts.

The plan's value is linear in the forecasts, so the difference between two fixed plans is
normal: its mean is the difference in the planner's objective and its spread comes from the
players whose roles differ, each weighted by how much the plan leans on him. P(plan A beats plan
B) is then exact for this error model, with no sampling noise and in microseconds (the same as
drawing infinitely many correlated samples; we skip the samples, not the idea). Players held in
the same role by both plans cancel, which is why the test is about the moves.

Two uses:
- a decision rule: make a move (or take a hit) only when it beats holding (or the no-hit plan)
  with probability at least `MOVE_CONFIDENCE` (`HIT_CONFIDENCE`); see `advise.decide`.
- a label for users: "strong", "likely" or "close call", with the probability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import optimise as O
from . import rules as R
from .model import PointsDist
from .optimise import Move, Plan

# Make this week's moves only when they beat holding in at least this share of forecast errors.
MOVE_CONFIDENCE = 0.5
# Take a hit only when the plan with it beats the best plan without it this often.
HIT_CONFIDENCE = 0.5
# Labels for users, on the chance the recommendation is right.
STRONG = 0.85
LIKELY = 0.65


@dataclass
class Confidence:
    p: float  # chance plan A beats plan B
    mean: float  # A minus B in expected (weighted) points
    sd: float

    @property
    def level(self) -> str:
        return level(self.p)


def level(p: float) -> str:
    """'strong', 'likely' or 'close call' for the chance a recommendation is right."""
    if p >= STRONG:
        return "strong"
    if p >= LIKELY:
        return "likely"
    return "close call"


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _prob(mean: float, var: float) -> float:
    if var <= 1e-12:
        return 1.0 if mean > 1e-9 else (0.5 if abs(mean) <= 1e-9 else 0.0)
    return _phi(mean / math.sqrt(var))


def mean_sds(
    dists: dict[int, dict[int, PointsDist]], gws: list[int], after: list[int]
) -> tuple[dict[int, dict[int, float]], dict[int, float]]:
    """Per-player forecast error per gameweek, and for the weeks after the horizon (averaged)."""
    msd = {p: {g: d[g].mean_sd for g in gws if g in d} for p, d in dists.items()}
    term = {}
    for p, d in dists.items():
        vals = [d[g].mean_sd for g in (after or gws[-1:]) if g in d]
        term[p] = sum(vals) / len(vals) if vals else 0.0
    return msd, term


def loadings(
    plan: Plan,
    gws: list[int],
    msd: dict[int, dict[int, float]],
    msd_after: dict[int, float],
    multiplier: dict[int, float] | None = None,
    weights: list[float] | None = None,
    world=None,
) -> dict[int, float]:
    """How much the plan's value moves per unit of each player's forecast error (z = 1).

    Mirrors the planner's objective: weighted weeks, starters, captain (x2, x3 with Triple
    Captain), vice, bench slots by auto-sub odds (all of them with Bench Boost), and the squad
    left at the end.
    """
    mult = multiplier or {}
    w = list(weights or O.decay_weights(len(gws)))
    out: dict[int, float] = {}

    def add(p: int, x: float) -> None:
        if x:
            out[p] = out.get(p, 0.0) + x

    for i, g in enumerate(gws):
        if g not in plan.xi:
            continue
        chip = plan.chips.get(g)
        cap_mult = 2 if chip == O.TRIPLE_CAPTAIN else 1
        cap, vice = plan.captain.get(g), plan.vice.get(g)
        for p in plan.xi[g]:
            m = msd.get(p, {}).get(g, 0.0)
            role = mult.get(p, 1.0)
            if p == cap:
                role += cap_mult
            elif p == vice:
                role += O.VICE_WEIGHT
            add(p, w[i] * m * role)
        for k, p in enumerate(plan.bench.get(g, [])):
            m = msd.get(p, {}).get(g, 0.0)
            if chip == O.BENCH_BOOST:
                bw = 1.0
            elif (
                world.players[p].pos == R.GKP
                if world is not None and p in world.players
                else k == 0
            ):
                bw = O.BENCH_WEIGHTS[0]
            else:
                bw = O.BENCH_WEIGHTS[min(k, 3)]
            add(p, w[i] * m * bw * mult.get(p, 1.0))
    last = max(g for g in gws if g in plan.xi)
    w_after = w[-1] * O.DECAY
    for p in plan.xi[last]:
        add(p, O.TERMINAL_WEEKS * w_after * msd_after.get(p, 0.0) * mult.get(p, 1.0))
    return out


def compare(a: Plan, b: Plan, la: dict[int, float], lb: dict[int, float]) -> Confidence:
    """P(plan a beats plan b) when every player's forecast may be off by his mean_sd."""
    mean = a.objective - b.objective
    var = sum((la.get(p, 0.0) - lb.get(p, 0.0)) ** 2 for p in set(la) | set(lb))
    return Confidence(_prob(mean, var), round(mean, 2), round(math.sqrt(var), 2))


def move_confidence(
    m: Move,
    plan: Plan,
    gws: list[int],
    msd: dict[int, dict[int, float]],
    multiplier: dict[int, float] | None = None,
    weights: list[float] | None = None,
    cost: float = 0.0,
) -> Confidence:
    """P(this one swap gains more than `cost`, e.g. its share of a hit), on the weeks the plan
    keeps the signing, in the role the plan gives him (the same terms as `Move.gain`)."""
    mult = multiplier or {}
    w = list(weights or O.decay_weights(len(gws)))
    li = lo = 0.0
    for i, g in enumerate(gws):
        if g not in plan.squads or m.inn not in plan.squads[g]:
            if i > 0:
                break
            continue
        xi = plan.xi[g]
        if m.inn in xi:
            role = 1.0
            if plan.captain[g] == m.inn:
                role += 2 if plan.chips.get(g) == O.TRIPLE_CAPTAIN else 1
            elif plan.vice[g] == m.inn:
                role += O.VICE_WEIGHT
        elif plan.chips.get(g) == O.BENCH_BOOST:
            role = 1.0
        else:
            bench = plan.bench[g]
            k = bench.index(m.inn) if m.inn in bench else 3
            role = O.BENCH_WEIGHTS[min(k, 3)]
        li += w[i] * role * mult.get(m.inn, 1.0) * msd.get(m.inn, {}).get(g, 0.0)
        lo += w[i] * role * mult.get(m.out, 1.0) * msd.get(m.out, {}).get(g, 0.0)
    mean = m.gain - cost
    var = li**2 + lo**2
    return Confidence(_prob(mean, var), round(mean, 2), round(math.sqrt(var), 2))

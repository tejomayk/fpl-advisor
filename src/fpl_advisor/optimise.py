"""Choose transfers, starting XI, bench and captain with an integer program.

The transfers are made now and held over a short horizon. Each gameweek in the horizon has its own
best XI and captain, and later gameweeks count for less because projections get less certain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from . import rules as R
from .world import World

HORIZON_WEIGHTS = [1.0, 0.85, 0.72, 0.61, 0.52]
BENCH_WEIGHT = 0.08  # a little value for bench cover (injuries, rotation)
ROLL_VALUE = 1.0  # points of value per extra free transfer carried forward
CANDIDATES_PER_POS = {R.GKP: 12, R.DEF: 35, R.MID: 40, R.FWD: 25}
# Seconds CBC may spend per solve. scripts/season_sim.py lowers it to replay seasons faster.
SOLVER_TIME_LIMIT = 60


@dataclass
class Plan:
    squad: list[int]
    buys: list[int]
    sells: list[int]
    hits: int
    xi: dict[int, list[int]]  # gw -> starting XI
    captain: dict[int, int]
    vice: dict[int, int]
    bench: dict[int, list[int]]  # gw -> bench order (GK first)
    objective: float
    bank_after: float
    gw_points: dict[int, float] = field(
        default_factory=dict
    )  # expected points per gw, incl. captain


def solve(
    world: World,
    xp: dict[int, dict[int, float]],
    gameweeks: list[int],
    squad: list[int],
    sell_price: dict[int, float],
    bank: float,
    free_transfers: int,
    *,
    weights: list[float] | None = None,
    multiplier: dict[int, float] | None = None,
    max_hits: int = 2,
    unlimited: bool = False,  # Wildcard / Free Hit: any number of transfers at no cost
    bench_boost_gw: int | None = None,
    triple_captain_gw: int | None = None,
    forced_out: set[int] | None = None,
    banned: set[int] | None = None,
    max_transfers: int | None = None,
) -> Plan:
    weights = weights or HORIZON_WEIGHTS[: len(gameweeks)]
    mult = multiplier or {}
    owned = set(squad)
    banned = banned or set()

    # Candidate pool: everyone owned plus the best by horizon value at each position.
    def score(pid):
        return sum(w * xp[pid].get(g, 0) for g, w in zip(gameweeks, weights))

    pool = set(owned)
    for pos, n in CANDIDATES_PER_POS.items():
        ranked = sorted(
            (
                p
                for p in world.players.values()
                if p.pos == pos and p.id not in banned and p.status != "u"
            ),
            key=lambda p: -score(p.id),
        )
        pool.update(p.id for p in ranked[:n])
        # Cheap enablers matter for the budget even with low projections.
        cheap = sorted(
            (p for p in ranked if p.status == "a"),
            key=lambda p: (p.price, -score(p.id)),
        )
        pool.update(p.id for p in cheap[:3])
    pool -= banned - owned
    pool = sorted(pool)

    price = {p: (sell_price[p] if p in owned else world.players[p].price) for p in pool}
    prob = pulp.LpProblem("fpl", pulp.LpMaximize)
    x = {p: pulp.LpVariable(f"x_{p}", cat="Binary") for p in pool}
    s = {
        (p, g): pulp.LpVariable(f"s_{p}_{g}", cat="Binary")
        for p in pool
        for g in gameweeks
    }
    c = {
        (p, g): pulp.LpVariable(f"c_{p}_{g}", cat="Binary")
        for p in pool
        for g in gameweeks
    }
    v = {
        (p, g): pulp.LpVariable(f"v_{p}_{g}", cat="Binary")
        for p in pool
        for g in gameweeks
    }
    hits = pulp.LpVariable("hits", lowBound=0, cat="Integer")
    roll = pulp.LpVariable("roll", lowBound=0, upBound=R.MAX_FREE_TRANSFERS - 1)

    n_buys = pulp.lpSum(x[p] for p in pool if p not in owned)
    # Squad shape, club limit and budget.
    prob += pulp.lpSum(x.values()) == R.SQUAD_SIZE
    for pos, n in R.SQUAD_COUNT.items():
        prob += pulp.lpSum(x[p] for p in pool if world.players[p].pos == pos) == n
    for t in world.teams:
        prob += (
            pulp.lpSum(x[p] for p in pool if world.players[p].team == t)
            <= R.MAX_PER_CLUB
        )
    prob += (
        pulp.lpSum(price[p] * x[p] for p in pool)
        <= bank + sum(sell_price[p] for p in owned) + 1e-6
    )
    for p in forced_out or ():
        if p in x:
            prob += x[p] == 0

    if unlimited:
        prob += hits == 0
    else:
        prob += hits >= n_buys - free_transfers
        prob += hits <= max_hits
        prob += roll <= free_transfers - n_buys + hits
    if max_transfers is not None:
        prob += n_buys <= max_transfers

    obj = []
    for g, w in zip(gameweeks, weights):
        prob += pulp.lpSum(s[p, g] for p in pool) == 11
        prob += pulp.lpSum(c[p, g] for p in pool) == 1
        prob += pulp.lpSum(v[p, g] for p in pool) == 1
        for pos in R.POS_NAME:
            n_pos = pulp.lpSum(s[p, g] for p in pool if world.players[p].pos == pos)
            prob += n_pos >= R.XI_MIN[pos]
            prob += n_pos <= R.XI_MAX[pos]
        for p in pool:
            prob += s[p, g] <= x[p]
            prob += c[p, g] <= s[p, g]
            prob += v[p, g] <= s[p, g]
            prob += c[p, g] + v[p, g] <= 1
            raw = xp[p].get(g, 0.0)
            e = raw * mult.get(p, 1.0)
            cap_bonus = 2 if g == triple_captain_gw else 1
            bench_w = 1.0 if g == bench_boost_gw else BENCH_WEIGHT
            # The captain is chosen on raw expected points: doubling a weaker differential trades away
            # too many points for too little edge over rivals.
            obj.append(
                w * (e * s[p, g] + cap_bonus * raw * c[p, g] + 0.05 * raw * v[p, g])
            )
            obj.append(w * bench_w * e * (x[p] - s[p, g]))
    prob += (
        pulp.lpSum(obj) - R.HIT_COST * hits + (0 if unlimited else ROLL_VALUE * roll)
    )

    status = prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=SOLVER_TIME_LIMIT))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Optimiser failed: {pulp.LpStatus[status]}")

    chosen = [p for p in pool if x[p].value() > 0.5]
    plan = Plan(
        squad=chosen,
        buys=[p for p in chosen if p not in owned],
        sells=[p for p in squad if p not in chosen],
        hits=round(hits.value() or 0),
        xi={},
        captain={},
        vice={},
        bench={},
        objective=pulp.value(prob.objective),
        bank_after=round(
            bank + sum(sell_price[p] for p in owned) - sum(price[p] for p in chosen), 1
        ),
    )
    for g in gameweeks:
        xi = [p for p in chosen if s[p, g].value() > 0.5]
        xi.sort(key=lambda p: (world.players[p].pos, -xp[p].get(g, 0)))
        bench = [p for p in chosen if p not in xi]
        bench.sort(key=lambda p: (world.players[p].pos != R.GKP, -xp[p].get(g, 0)))
        plan.xi[g] = xi
        plan.bench[g] = bench
        plan.captain[g] = next(p for p in chosen if c[p, g].value() > 0.5)
        plan.vice[g] = next(p for p in chosen if v[p, g].value() > 0.5)
        cap_mult = 2 if g == triple_captain_gw else 1
        total = sum(xp[p].get(g, 0) for p in xi) + cap_mult * xp[plan.captain[g]].get(
            g, 0
        )
        if g == bench_boost_gw:
            total += sum(xp[p].get(g, 0) for p in bench)
        plan.gw_points[g] = total
    return plan

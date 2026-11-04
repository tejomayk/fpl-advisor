"""Plan transfers, starting XI, bench and captain over several gameweeks with one integer program.

The plan is rolling: it chooses moves for every gameweek of the horizon, carrying the squad, the bank
and the free-transfer count from week to week, but only this week's moves are made. Next week the
plan is made again with fresh projections. Saving a free transfer is then worth exactly what the plan
can do with it later.

Later gameweeks count for less because projections get less certain (`DECAY`). The last
`NO_TRANSFER_LAST` weeks of the horizon allow no moves, so the plan can't chase a short fixture swing
with no regard for what comes after, and the squad, bank and free transfers left at the end keep a
small value (`TERMINAL_WEEKS`, `BANK_VALUE`, `FT_END_VALUE`).

Chips are given as fixed weeks (`wildcard_gw`, ...); chips.py decides which ones to try.
To keep a solve to a few seconds, only this week's line-up is an integer choice (later weeks are
valued with the relaxed line-up, which is whole for almost every squad), and later weeks may only
buy from a short list of the best players at each position.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pulp

from . import rules as R
from .world import World

HORIZON = 8  # gameweeks planned
DECAY = 0.85  # weight of each later gameweek relative to the one before
NO_TRANSFER_LAST = 2  # weeks at the end of the horizon with no moves
# Value of the squad after the horizon, in gameweeks of its best XI's average expected points
# (weighted like the week after the horizon).
TERMINAL_WEEKS = 1.0
BANK_VALUE = 0.08  # points per £1m left in the bank at the end
# Points per free transfer still held at the end (the 1st, 2nd, ...): more held is worth less each.
FT_END_VALUE = (1.0, 0.8, 0.5, 0.3, 0.1)
TRANSFER_PENALTY = 0.1  # per transfer, so moves that gain nothing are never suggested
# Bench weights by slot: the reserve goalkeeper, then outfield subs 1-3. Roughly the chance each
# slot is brought on by an auto-sub (season sim: about 0.6 of gameweeks have one, 0.14 two).
BENCH_WEIGHTS = (0.03, 0.30, 0.10, 0.03)
VICE_WEIGHT = 0.08  # roughly how often the captain doesn't play
CANDIDATES_PER_POS = {R.GKP: 5, R.DEF: 12, R.MID: 14, R.FWD: 8}
LATER_TARGETS_PER_POS = {
    R.GKP: 3,
    R.DEF: 6,
    R.MID: 7,
    R.FWD: 4,
}  # buyable after this week
LATER_MAX_MOVES = 2  # transfers per later week (pencilled-in moves are rarely more)
WHOLE_LINEUP_WEEKS = 1  # weeks whose line-up is an integer choice
MIP_GAP = 0.002  # stop when within 0.2% of the best possible plan
SOLVER = os.environ.get("FPL_SOLVER", "auto")  # auto: HiGHS when installed, else CBC
# Seconds the solver may spend per solve. scripts/season_sim.py can change it.
SOLVER_TIME_LIMIT = 4
# Plans `advise.decide` may solve at once (the plan, holding, no hit), each on its own CPU. The
# time limit is wall-clock time, so two solves sharing one CPU could stop at a different plan:
# never run more than the CPUs this process may use. FPL_SOLVE_WORKERS overrides (1 = one at a time).
MAX_SOLVE_WORKERS = 3
# Kept for callers that still read them; the plan uses BENCH_WEIGHTS and DECAY.
BENCH_WEIGHT = BENCH_WEIGHTS[1]
HORIZON_WEIGHTS = [DECAY**i for i in range(HORIZON)]

WILDCARD, FREE_HIT, BENCH_BOOST, TRIPLE_CAPTAIN = "wildcard", "freehit", "bboost", "3xc"


@dataclass
class Move:
    """One sale paired with one purchase, as the plan sees them."""

    out: int
    inn: int
    sell_price: float
    buy_price: float
    # Weighted expected points the swap adds over the weeks the plan keeps the signing.
    gain: float = 0.0

    @property
    def money(self) -> float:
        """Change to the bank from this move (positive frees money)."""
        return round(self.sell_price - self.buy_price, 1)


@dataclass
class WeekPlan:
    gw: int
    buys: list[int]
    sells: list[int]
    hits: int
    free_transfers: int  # available before this week's moves
    chip: str | None
    bank: float  # after this week's moves
    moves: list[Move] = field(default_factory=list)
    held: list[int] = field(default_factory=list)  # squad owned after the moves


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
    weeks: list[WeekPlan] = field(default_factory=list)  # this week first
    squads: dict[int, list[int]] = field(default_factory=dict)  # gw -> squad fielded
    chips: dict[int, str] = field(default_factory=dict)  # gw -> chip played

    @property
    def moves(self) -> list[Move]:
        return self.weeks[0].moves if self.weeks else []

    @property
    def chip_now(self) -> str | None:
        return self.chips.get(min(self.xi)) if self.xi else None


def _solver(time_limit: float | None):
    limit = time_limit if time_limit is not None else SOLVER_TIME_LIMIT
    msg = bool(os.environ.get("FPL_SOLVER_LOG"))
    name = SOLVER
    if name == "auto":
        name = "highs" if "HiGHS" in pulp.listSolvers(onlyAvailable=True) else "cbc"
    if name == "highs":
        return pulp.HiGHS(msg=msg, timeLimit=limit, gapRel=MIP_GAP, threads=1)
    return pulp.PULP_CBC_CMD(msg=msg, timeLimit=limit, gapRel=MIP_GAP)


def usable_cpus() -> int:
    """CPUs this process may run on: its CPU affinity, capped by a container's CPU quota."""
    try:
        n = len(os.sched_getaffinity(0))
    except AttributeError:  # macOS and Windows have no affinity call
        n = os.cpu_count() or 1
    try:  # cgroup v2 quota, e.g. "200000 100000" is 2 CPUs; "max 100000" is no limit
        with open("/sys/fs/cgroup/cpu.max") as f:
            quota, period = f.read().split()[:2]
        if quota != "max":
            n = min(n, max(1, int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    return max(1, n)


def solve_workers() -> int:
    """How many plans to solve at once (see MAX_SOLVE_WORKERS)."""
    raw = os.environ.get("FPL_SOLVE_WORKERS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return min(MAX_SOLVE_WORKERS, usable_cpus())


def decay_weights(n: int, decay: float | None = None) -> list[float]:
    d = DECAY if decay is None else decay
    return [d**i for i in range(n)]


def pair_moves(
    world: World,
    buys: list[int],
    sells: list[int],
    sell_price: dict[int, float],
) -> list[Move]:
    """Pair each sale with a purchase in the same position, dearest with dearest.

    The squad shape forces equal numbers in and out per position, so this always pairs everyone.
    Matching by price keeps each pair's money change small and readable: the big sale is the one
    that pays for the big signing.
    """
    pl = world.players
    out: list[Move] = []
    for pos in R.POS_NAME:
        s = sorted(
            (p for p in sells if pl[p].pos == pos),
            key=lambda p: (-sell_price.get(p, pl[p].price), p),
        )
        b = sorted(
            (p for p in buys if pl[p].pos == pos), key=lambda p: (-pl[p].price, p)
        )
        for o, i in zip(s, b):
            out.append(Move(o, i, sell_price.get(o, pl[o].price), pl[i].price))
    return out


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
    unlimited: bool = False,  # building a squad from scratch: any number of moves this week, free
    bench_boost_gw: int | None = None,
    triple_captain_gw: int | None = None,
    wildcard_gw: int | None = None,
    free_hit_gw: int | None = None,
    forced_out: set[int] | None = None,
    banned: set[int] | None = None,
    max_transfers: int | None = None,  # cap on this week's transfers
    ft_cap: int = R.MAX_FREE_TRANSFERS,
    keep_fts_on_chip: bool = True,
    no_transfer_last: int | None = None,
    terminal_xp: dict[int, float]
    | None = None,  # per-player xP a week after the horizon
    time_limit: float | None = None,
) -> Plan:
    gws = list(gameweeks)
    H = len(gws)
    weights = list(weights or decay_weights(H))[:H]
    mult = multiplier or {}
    owned = set(squad)
    banned = banned or set()
    forced_out = forced_out or set()
    if no_transfer_last is None:
        no_transfer_last = min(NO_TRANSFER_LAST, max(0, H - 3))
    move_weeks = set(range(max(1, H - no_transfer_last)))  # week indices allowing moves
    chip_at: dict[int, str] = {}
    for g, name in (
        (bench_boost_gw, BENCH_BOOST),
        (triple_captain_gw, TRIPLE_CAPTAIN),
        (wildcard_gw, WILDCARD),
        (free_hit_gw, FREE_HIT),
    ):
        if g is not None and g in gws:
            chip_at[gws.index(g)] = name

    # ---------------- candidate pool: everyone owned plus the best at each position
    def score(pid):
        return sum(w * xp[pid].get(g, 0) for g, w in zip(gws, weights))

    pool = set(owned)
    later_targets: set[int] = set()
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
        later_targets.update(p.id for p in ranked[: LATER_TARGETS_PER_POS[pos]])
        # Cheap enablers matter for the budget even with low projections.
        cheap = sorted(
            (p for p in ranked if p.status == "a"),
            key=lambda p: (p.price, -score(p.id)),
        )
        pool.update(p.id for p in cheap[:3])
        later_targets.update(p.id for p in cheap[:1])
    pool -= banned - owned
    pool = sorted(pool)
    pl = world.players
    pos_of = {p: pl[p].pos for p in pool}
    buy_cost = {p: pl[p].price for p in pool}
    sell_val = {p: (sell_price[p] if p in owned else pl[p].price) for p in pool}

    prob = pulp.LpProblem("fpl_plan", pulp.LpMaximize)
    V = pulp.LpVariable

    def add(c):
        prob.addConstraint(c)

    # ---------------- squad, transfers, bank and free transfers per week
    x = {(p, i): V(f"x_{p}_{i}", cat="Binary") for p in pool for i in range(H)}
    buy: dict = {}
    sell: dict = {}
    for i in sorted(move_weeks):
        free_now = (unlimited and i == 0) or chip_at.get(i) == WILDCARD
        # Continuous is enough: the squad x is whole, so each buy and sell is too (a fake "buy and
        # sell back" only costs transfers and money).
        for p in pool:
            if i == 0:
                if p in owned:
                    sell[p, i] = V(f"sell_{p}_{i}", 0, 1)
                else:
                    buy[p, i] = V(f"buy_{p}_{i}", 0, 1)
                continue
            sell[p, i] = V(f"sell_{p}_{i}", 0, 1)
            if free_now or p in later_targets:
                buy[p, i] = V(f"buy_{p}_{i}", 0, 1)
    for p in pool:
        prev = 1 if p in owned else 0
        for i in range(H):
            add(x[p, i] == prev + buy.get((p, i), 0) - sell.get((p, i), 0))
            if (p, i) in buy and (p, i) in sell:
                add(buy[p, i] + sell[p, i] <= 1)
            prev = x[p, i]
        if p in forced_out:
            add(x[p, 0] == 0)

    # Clubs: at most 3, except a club already over the limit (a player moved there midseason):
    # that excess may stay until it is sold, as FPL allows, but no new player from it can join.
    held_by_club: dict[int, int] = {}
    for p in owned:
        held_by_club[pl[p].team] = held_by_club.get(pl[p].team, 0) + 1
    over = {t for t, n in held_by_club.items() if n > R.MAX_PER_CLUB}

    def shape_rows(var, i, legal_only=False):
        add(pulp.lpSum(var[p, i] for p in pool) == R.SQUAD_SIZE)
        for pos, n in R.SQUAD_COUNT.items():
            add(pulp.lpSum(var[p, i] for p in pool if pos_of[p] == pos) == n)
        for t in world.teams:
            members = [p for p in pool if pl[p].team == t]
            if not members:
                continue
            if t in over and not legal_only:
                for p in members:
                    if p not in owned:
                        add(var[p, i] == 0)
            else:
                add(pulp.lpSum(var[p, i] for p in members) <= R.MAX_PER_CLUB)

    n_tr: dict = {}
    bank_v: dict = {}
    hits: dict = {}
    ft: dict = {0: free_transfers}
    M = R.SQUAD_SIZE
    prev_bank = bank
    for i in range(H):
        shape_rows(x, i)
        n_tr[i] = pulp.lpSum(v for (p, ii), v in buy.items() if ii == i)
        bank_v[i] = V(f"bank_{i}", lowBound=0)
        add(
            bank_v[i]
            == prev_bank
            + pulp.lpSum(sell_val[p] * v for (p, ii), v in sell.items() if ii == i)
            - pulp.lpSum(buy_cost[p] * v for (p, ii), v in buy.items() if ii == i)
        )
        prev_bank = bank_v[i]
        chip = chip_at.get(i)
        free = (unlimited and i == 0) or chip in (WILDCARD, FREE_HIT)
        if chip == FREE_HIT:
            add(n_tr[i] == 0)  # the Free Hit squad is separate; the real one comes back
        if i == 0 and max_transfers is not None:
            add(n_tr[0] <= max_transfers)
        if i > 0 and i in move_weeks and not free:
            add(n_tr[i] <= LATER_MAX_MOVES)
        if free or i not in move_weeks:
            hits[i] = 0
        else:
            cap = max(0, max_hits if i == 0 else min(max_hits, 1))
            if i == 0:
                # This week: hits = max(0, n - ft) exactly (z: every free transfer is used).
                hits[i] = V(f"hits_{i}", 0, cap, cat="Integer")
                z = V(f"z_{i}", cat="Binary")
                add(hits[i] <= M * z)
                add(hits[i] <= n_tr[i] - ft[i] + M * (1 - z))
            else:
                # Later weeks: whole squads make n whole, so the cheapest hits are whole too.
                hits[i] = V(f"hits_{i}", 0, cap)
            add(hits[i] >= n_tr[i] - ft[i])
        # Free transfers for the next week.
        if unlimited and i == 0:
            ft[i + 1] = 1
        elif chip in (WILDCARD, FREE_HIT):
            ft[i + 1] = ft[i] if keep_fts_on_chip else 1
        elif i not in move_weeks:
            ft[i + 1] = V(f"ft_{i + 1}", 0, ft_cap)
            add(ft[i + 1] <= ft[i] + 1)
        else:
            # Continuous is enough: it takes the whole value min(cap, ft - n + hits + 1).
            ft[i + 1] = V(f"ft_{i + 1}", 0, ft_cap)
            add(ft[i + 1] <= ft[i] - n_tr[i] + hits[i] + 1)

    # ---------------- the squad that plays each week (the Free Hit squad, in its week)
    play = {}
    for i in range(H):
        if chip_at.get(i) == FREE_HIT:
            q = {(p, i): V(f"q_{p}_{i}", cat="Binary") for p in pool}
            shape_rows(q, i, legal_only=True)
            held_before = (
                {p: (1 if p in owned else 0) for p in pool}
                if i == 0
                else {p: x[p, i - 1] for p in pool}
            )
            before_bank = bank if i == 0 else bank_v[i - 1]
            add(
                pulp.lpSum(sell_val[p] * q[p, i] for p in pool)
                <= before_bank + pulp.lpSum(sell_val[p] * held_before[p] for p in pool)
            )
            for p in pool:
                play[p, i] = q[p, i]
        else:
            for p in pool:
                play[p, i] = x[p, i]

    # ---------------- line-ups, captain, bench order, chips' points
    s: dict = {}
    c: dict = {}
    v: dict = {}
    slots: dict = {}
    obj = []
    outfield = [p for p in pool if pos_of[p] != R.GKP]
    for i, g in enumerate(gws):
        w = weights[i]
        chip = chip_at.get(i)
        # This week's line-up must be whole. Later weeks only value the squad, and for a whole
        # squad the best relaxed line-up is (almost always) whole too.
        cat = (
            {"cat": "Binary"}
            if i < WHOLE_LINEUP_WEEKS
            else {"lowBound": 0, "upBound": 1}
        )
        for p in pool:
            s[p, i] = V(f"s_{p}_{i}", **cat)
            c[p, i] = V(f"c_{p}_{i}", **cat)
            v[p, i] = V(f"v_{p}_{i}", **cat)
            add(s[p, i] <= play[p, i])
            add(c[p, i] + v[p, i] <= s[p, i])
        add(pulp.lpSum(s[p, i] for p in pool) == 11)
        add(pulp.lpSum(c[p, i] for p in pool) == 1)
        add(pulp.lpSum(v[p, i] for p in pool) == 1)
        for pos in R.POS_NAME:
            n_pos = pulp.lpSum(s[p, i] for p in pool if pos_of[p] == pos)
            add(n_pos >= R.XI_MIN[pos])
            add(n_pos <= R.XI_MAX[pos])
        # Outfield bench order: slot k holds one player (continuous is enough: for a fixed squad
        # and XI this is an assignment problem, whose best answer is whole).
        slot = {(p, k): V(f"b_{p}_{i}_{k}", 0, 1) for p in outfield for k in (1, 2, 3)}
        slots[i] = slot
        for k in (1, 2, 3):
            add(pulp.lpSum(slot[p, k] for p in outfield) == 1)
        for p in outfield:
            add(pulp.lpSum(slot[p, k] for k in (1, 2, 3)) == play[p, i] - s[p, i])
        cap_mult = 2 if chip == TRIPLE_CAPTAIN else 1
        for p in pool:
            raw = xp[p].get(g, 0.0)
            e = raw * mult.get(p, 1.0)
            # The captain is chosen on raw expected points: doubling a weaker differential trades
            # away too many points for too little edge over rivals.
            obj.append(
                w
                * (e * s[p, i] + cap_mult * raw * c[p, i] + VICE_WEIGHT * raw * v[p, i])
            )
            if chip == BENCH_BOOST:
                obj.append(w * e * (play[p, i] - s[p, i]))
            elif pos_of[p] == R.GKP:
                obj.append(w * BENCH_WEIGHTS[0] * e * (play[p, i] - s[p, i]))
            else:
                obj.append(
                    w * e * pulp.lpSum(BENCH_WEIGHTS[k] * slot[p, k] for k in (1, 2, 3))
                )

    # ---------------- what is left at the end
    last = H - 1
    w_after = weights[-1] * DECAY
    if terminal_xp is None:
        tail = gws[-3:]
        terminal_xp = {
            p: sum(xp[p].get(g, 0.0) for g in tail) / len(tail) for p in pool
        }
    t = {p: V(f"t_{p}", 0, 1) for p in pool}
    for p in pool:
        add(t[p] <= x[p, last])
    add(pulp.lpSum(t.values()) == 11)
    for pos in R.POS_NAME:
        n_pos = pulp.lpSum(t[p] for p in pool if pos_of[p] == pos)
        add(n_pos >= R.XI_MIN[pos])
        add(n_pos <= R.XI_MAX[pos])
    obj.append(
        TERMINAL_WEEKS
        * w_after
        * pulp.lpSum(terminal_xp.get(p, 0.0) * mult.get(p, 1.0) * t[p] for p in pool)
    )
    obj.append(w_after * BANK_VALUE * bank_v[last])
    units = [V(f"ftu_{k}", 0, 1) for k in range(len(FT_END_VALUE))]
    add(pulp.lpSum(units) <= ft[H])
    obj.append(w_after * pulp.lpSum(val * u for val, u in zip(FT_END_VALUE, units)))
    obj.append(-R.HIT_COST * pulp.lpSum(hits.values()))
    obj.append(-TRANSFER_PENALTY * pulp.lpSum(n_tr.values()))
    prob += pulp.lpSum(obj)

    status = prob.solve(_solver(time_limit))
    # Stopping on the time limit with a good plan in hand is fine; having no plan is not.
    if x[pool[0], 0].value() is None or pulp.LpStatus[status] in (
        "Infeasible",
        "Unbounded",
    ):
        raise RuntimeError(f"Optimiser failed: {pulp.LpStatus[status]}")

    def on(var) -> bool:
        return (var.value() or 0) > 0.5

    def num(expr) -> float:
        if isinstance(expr, (int, float)):
            return float(expr)
        return float(pulp.value(expr) or 0)

    planned_chips = {gws[i]: name for i, name in chip_at.items()}
    plan = Plan(
        squad=[],
        buys=[],
        sells=[],
        hits=0,
        xi={},
        captain={},
        vice={},
        bench={},
        objective=float(pulp.value(prob.objective)),
        bank_after=0.0,
        chips=planned_chips,
    )
    prev = sorted(owned)
    ft_now = free_transfers
    for i, g in enumerate(gws):
        sq = [p for p in pool if on(x[p, i])]
        fielded = [p for p in pool if on(play[p, i])]
        b = [p for p in sq if p not in prev]
        so = [p for p in prev if p not in sq]
        plan.weeks.append(
            WeekPlan(
                gw=g,
                buys=b,
                sells=so,
                hits=round(num(hits[i])),
                free_transfers=ft_now,
                chip=planned_chips.get(g),
                bank=round(num(bank_v[i]), 1),
                moves=pair_moves(world, b, so, {p: sell_val[p] for p in so}),
                held=sq,
            )
        )
        plan.squads[g] = fielded
        ft_now = round(num(ft[i + 1]))
        prev = sq
        _read_lineup(plan, g, i, fielded, s, c, v, slots[i], xp, pos_of)

    first = plan.weeks[0]
    plan.squad = list(first.held)
    plan.buys, plan.sells, plan.hits = first.buys, first.sells, first.hits
    plan.bank_after = first.bank
    if planned_chips.get(gws[0]) == FREE_HIT:
        # The Free Hit squad is what plays; the real squad comes back afterwards.
        plan.squad = plan.squads[gws[0]]
        plan.buys = [p for p in plan.squad if p not in owned]
        plan.sells = [p for p in squad if p not in plan.squad]
        spent = sum(buy_cost[p] for p in plan.buys) - sum(
            sell_val[p] for p in plan.sells
        )
        plan.bank_after = round(bank - spent, 1)
        first.buys, first.sells = plan.buys, plan.sells
        first.moves = pair_moves(world, plan.buys, plan.sells, sell_price)
    _score_moves(plan, world, xp, gws, weights, mult)
    return plan


def _read_lineup(plan: Plan, g, i, fielded, s, c, v, slot, xp, pos_of) -> None:
    """Week i's XI, bench order, captain and expected points, read off the solution."""
    xi = sorted(fielded, key=lambda p: -(s[p, i].value() or 0))[:11]
    xi.sort(key=lambda p: (pos_of[p], -xp[p].get(g, 0)))
    rest = [p for p in fielded if p not in xi]
    gk = [p for p in rest if pos_of[p] == R.GKP]

    def slot_of(p):
        vals = [slot[p, k].value() or 0 for k in (1, 2, 3)]
        return max(range(3), key=lambda k: vals[k])

    outf = sorted(
        (p for p in rest if pos_of[p] != R.GKP),
        key=lambda p: (slot_of(p), -xp[p].get(g, 0)),
    )
    plan.xi[g] = xi
    plan.bench[g] = gk + outf
    cap = max(xi, key=lambda p: (c[p, i].value() or 0, xp[p].get(g, 0)))
    vice = max(
        (p for p in xi if p != cap),
        key=lambda p: (v[p, i].value() or 0, xp[p].get(g, 0)),
    )
    plan.captain[g], plan.vice[g] = cap, vice
    chip = plan.chips.get(g)
    cap_mult = 2 if chip == TRIPLE_CAPTAIN else 1
    total = sum(xp[p].get(g, 0) for p in xi) + cap_mult * xp[cap].get(g, 0)
    if chip == BENCH_BOOST:
        total += sum(xp[p].get(g, 0) for p in plan.bench[g])
    plan.gw_points[g] = total


def _score_moves(plan: Plan, world: World, xp, gws, weights, mult) -> None:
    """Each move's gain as the plan counts it.

    For every week the plan keeps the signing, compare what he adds in the role the plan gives him
    (starter, captain, bench slot, Bench Boost) with what the sold player would have added in that
    same role. Weeks are weighted like the plan weighs them.
    """
    pl = world.players
    for wk_i, wk in enumerate(plan.weeks):
        for m in wk.moves:
            gain = 0.0
            for i in range(wk_i, len(gws)):
                g = gws[i]
                if m.inn not in plan.squads.get(g, ()):
                    break
                chip = plan.chips.get(g)
                xi, bench = plan.xi[g], plan.bench[g]

                def worth(p, role_of=m.inn, g=g, chip=chip, xi=xi, bench=bench):
                    raw = xp.get(p, {}).get(g, 0.0)
                    e = raw * mult.get(p, 1.0)
                    if role_of in xi:
                        val = e
                        if plan.captain[g] == role_of:
                            # Without the signing the vice would have taken the armband.
                            vice_raw = xp[plan.vice[g]].get(g, 0.0)
                            bonus = raw if p == role_of else max(raw, vice_raw)
                            val += bonus * (2 if chip == TRIPLE_CAPTAIN else 1)
                        elif plan.vice[g] == role_of:
                            val += VICE_WEIGHT * raw
                        return val
                    if chip == BENCH_BOOST:
                        return e
                    if pl[role_of].pos == R.GKP:
                        return BENCH_WEIGHTS[0] * e
                    return BENCH_WEIGHTS[min(bench.index(role_of), 3)] * e

                gain += weights[i] * (worth(m.inn) - worth(m.out))
            m.gain = round(gain, 1)

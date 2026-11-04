"""What-if transfers: score the manager's own transfers for the next deadline against holding and
against the advisor's plan.

Two uses, one tool:
- trying out your own ideas ("what if I sold X for Y instead?");
- telling the advisor about transfers you've already made. FPL's public data hides them until the
  deadline passes, so without this the advice is based on last week's squad.

The draft is applied to a copy of the squad (`apply_transfers`): a signing costs his current price,
which is also his purchase price; a sale adds his selling price to the bank; each move beyond the
free transfers costs a 4-point hit. The edited squad is then planned with the same forecasts and
planner as the advice, with no further moves this week (the draft is this week's transfers), and
compared with holding and with the advisor's plan on the robustness test (robust.py) and, when a
league is set, on the chance of winning it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import robust
from . import rules as R
from .advise import Advice
from .leaguechoice import DETAIL_GWS, score_plans
from .optimise import TRANSFER_PENALTY, Plan
from .squad import Squad
from .world import World

MAX_MOVES = R.SQUAD_SIZE

Moves = list[tuple[int, int]]  # (sold, bought) player ids


class DraftError(ValueError):
    """A draft that FPL wouldn't accept, with the reason in plain words."""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _and(items: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


POS_WORDS = {
    R.GKP: "goalkeeper",
    R.DEF: "defender",
    R.MID: "midfielder",
    R.FWD: "forward",
}


def apply_transfers(squad: Squad, world: World, moves: Moves) -> tuple[Squad, int]:
    """The squad after `moves`, and the number of hits they take. Raises DraftError when FPL
    would refuse them: unknown or repeated players, wrong position counts, more than 3 from one
    club, or not enough money."""
    if len(moves) > MAX_MOVES:
        raise DraftError(f"A draft can have at most {MAX_MOVES} transfers.")
    pl = world.players
    for out, inn in moves:
        for pid in (out, inn):
            if pid not in pl:
                raise DraftError(
                    f"We don't know a player with ID {pid}. Pick players from the list."
                )
    outs = [o for o, _ in moves]
    ins = [i for _, i in moves]
    for o in outs:
        if o not in squad.players:
            raise DraftError(
                f"{pl[o].name} isn't in your squad, so you can't sell him."
            )
        if outs.count(o) > 1:
            raise DraftError(f"You're selling {pl[o].name} twice.")
    for i in ins:
        if ins.count(i) > 1:
            raise DraftError(f"You're buying {pl[i].name} twice.")
        if i in outs:
            raise DraftError(
                f"You're selling {pl[i].name} and buying him back. Just keep him."
            )
        if i in squad.players:
            raise DraftError(f"{pl[i].name} is already in your squad.")

    swap = dict(moves)
    players = [swap.get(p, p) for p in squad.players]

    counts = {pos: 0 for pos in R.SQUAD_COUNT}
    for p in players:
        counts[pl[p].pos] += 1
    if counts != R.SQUAD_COUNT:
        wrong = [
            _plural(counts[pos], POS_WORDS[pos])
            for pos in R.SQUAD_COUNT
            if counts[pos] != R.SQUAD_COUNT[pos]
        ]
        need = [_plural(n, POS_WORDS[pos]) for pos, n in R.SQUAD_COUNT.items()]
        raise DraftError(
            f"Your squad would have {_and(wrong)}. FPL needs exactly {_and(need)}. "
            "Swap each player for one in the same position."
        )

    clubs: dict[int, list[int]] = {}
    for p in players:
        clubs.setdefault(pl[p].team, []).append(p)
    for team, ps in clubs.items():
        if len(ps) > R.MAX_PER_CLUB:
            names = ", ".join(pl[p].name for p in ps)
            raise DraftError(
                f"You'd have {len(ps)} {world.teams[team].name} players ({names}). "
                f"FPL allows at most {R.MAX_PER_CLUB} from one club."
            )

    income = sum(squad.sell_price[o] for o in outs)
    cost = sum(pl[i].price for i in ins)
    bank = round(squad.bank + income - cost, 1)
    if bank < -1e-9:
        raise DraftError(
            f"These transfers cost £{-bank:.1f}m more than you have. You have "
            f"£{squad.bank:.1f}m in the bank, selling brings in £{income:.1f}m and the "
            f"new players cost £{cost:.1f}m."
        )

    sell = {p: v for p, v in squad.sell_price.items() if p not in swap}
    bought = {p: v for p, v in squad.purchase_price.items() if p not in swap}
    for i in ins:  # bought today: his price now is what he cost and what he'd sell for
        sell[i] = bought[i] = pl[i].price
    n = len(moves)
    edited = replace(
        squad,
        players=players,
        starting=[swap.get(p, p) for p in squad.starting],
        captain=None if squad.captain in swap else squad.captain,
        vice=None if squad.vice in swap else squad.vice,
        sell_price=sell,
        purchase_price=bought,
        bank=max(bank, 0.0),
        free_transfers=max(squad.free_transfers - n, 0),
    )
    return edited, max(n - squad.free_transfers, 0)


@dataclass
class Option:
    """One way to play the next weeks, scored on the same forecasts."""

    plan: Plan
    hits: int  # hits taken this week before the plan starts (the user's draft)
    points: float  # expected points over the horizon, after every hit
    value: float  # the planner's objective, after the draft's hits (for the robustness test)


@dataclass
class WhatIf:
    advice: Advice
    moves: Moves
    squad: Squad  # after the draft
    hits: int
    yours: Option
    hold: Option
    advisor: Option
    p_beats_hold: float | None  # P(your version beats holding), None for an empty draft
    win: dict[str, float] | None = None  # P(finish 1st) for "yours", "hold", "advisor"

    @property
    def empty(self) -> bool:
        return not self.moves


def _points(plan: Plan, horizon: list[int], extra_hits: int = 0) -> float:
    gws = [g for g in horizon if g in plan.gw_points]
    later_hits = sum(w.hits for w in plan.weeks)
    return round(
        sum(plan.gw_points[g] for g in gws) - R.HIT_COST * (later_hits + extra_hits), 1
    )


def _option(plan: Plan, horizon: list[int], hits: int = 0, n_moves: int = 0) -> Option:
    # The planner charges every transfer a tiny penalty; charge the draft's moves the same way so
    # the comparison with holding is like for like.
    value = plan.objective - R.HIT_COST * hits - TRANSFER_PENALTY * n_moves
    return Option(plan, hits, _points(plan, horizon, hits), value)


def _hold_plan(a: Advice) -> Plan:
    """The best plan that makes no move this week and plays no chip now."""
    dec = a.decision
    if dec.hold is not None and dec.hold.chip_now is None:
        return dec.hold
    if not dec.base.buys:
        return dec.base
    if dec.no_chip_hold is not None:  # often solved already, alongside the advice
        return dec.no_chip_hold()
    sq = a.squad
    return dec.solve(
        sq.players, sq.sell_price, sq.bank, sq.free_transfers, max_transfers=0
    )


def build_whatif(a: Advice, moves: Moves) -> WhatIf:
    """Score `moves` (made this week) against holding and the advisor's plan in `a`."""
    if a.initial or a.decision is None or a.decision.solve is None:
        raise DraftError(
            "There's no squad to change yet: pick your first squad in FPL, then come back."
        )
    edited, hits = apply_transfers(a.squad, a.world, moves)
    for _, i in moves:
        if i not in a.xp:
            raise DraftError(f"We have no forecast for {a.name(i)} yet.")
    dec = a.decision
    horizon = dec.horizon
    hold_plan = _hold_plan(a)
    hold = _option(hold_plan, horizon)
    advisor = _option(a.plan, horizon)
    if moves:
        # The draft is this week's transfers: plan from the edited squad with no more moves now.
        plan = dec.solve(
            edited.players,
            edited.sell_price,
            edited.bank,
            edited.free_transfers,
            max_transfers=0,
        )
        yours = _option(plan, horizon, hits, len(moves))
        after = dec.window[len(horizon) :]
        msd, msd_after = robust.mean_sds(dec.dists, horizon, after)

        def load(p: Plan) -> dict[int, float]:
            return robust.loadings(
                p, horizon, msd, msd_after, dec.multiplier, world=a.world
            )

        scored = replace(plan, objective=yours.value)
        p = round(robust.compare(scored, hold_plan, load(plan), load(hold_plan)).p, 3)
    else:
        yours, p = hold, None
    out = WhatIf(a, list(moves), edited, hits, yours, hold, advisor, p)
    out.win = _win_chances(a, out)
    return out


def _win_chances(a: Advice, w: WhatIf) -> dict[str, float] | None:
    """P(finish 1st) for each version, on the same simulated seasons, when a league is set."""
    view = a.league
    if view is None or a.league_choice is None or not view.rivals:
        return None
    gws = [
        g
        for g in a.horizon[:DETAIL_GWS]
        if all(g in o.plan.xi for o in (w.yours, w.hold, w.advisor))
    ]
    if not gws:
        return None
    plans = {"advisor": w.advisor.plan, "hold": w.hold.plan}
    if not w.empty:
        # The draft's hits come off this week's score.
        first = w.yours.plan.weeks[0] if w.yours.plan.weeks else None
        plan = w.yours.plan
        if first is not None and w.hits:
            plan = replace(
                plan, weeks=[replace(first, hits=first.hits + w.hits), *plan.weeks[1:]]
            )
        plans["yours"] = plan
    try:
        res, _ = score_plans(view, a.world, a.decision.dists, gws, plans)
    except (ValueError, KeyError):
        return None
    out = {k: round(v, 3) for k, v in res.p_first.items()}
    out.setdefault("yours", out["hold"])
    return out


def parse_moves(text: str | None) -> Moves:
    """'301-55,7-200' -> [(301, 55), (7, 200)]. Raises DraftError on anything else."""
    if not text or not text.strip():
        return []
    out: Moves = []
    for part in text.split(","):
        bits = part.strip().split("-")
        if len(bits) != 2 or not all(b.isdigit() and 0 < len(b) <= 6 for b in bits):
            raise DraftError(
                "That draft isn't in the expected format. Open the transfer editor and try again."
            )
        out.append((int(bits[0]), int(bits[1])))
    if len(out) > MAX_MOVES:
        raise DraftError(f"A draft can have at most {MAX_MOVES} transfers.")
    return out

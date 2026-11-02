"""Aim at the real objective: pick the plan and captain that most raise your chance of winning
your mini-league, not just your expected points.

The expected-points plan is the default. A few close alternatives (making no move, the plans the
robustness check considered, other captains) are scored on P(finish 1st) with the same simulated
seasons for every candidate (winprob.choose_plan). The advice only switches when the gain is
clearly real: more than `SWITCH_SE` standard errors. Usually that happens late in a season when
you are far behind (a differential captain is the only way to catch up) or well ahead (copying
the leader's captain protects the lead).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import rules as R
from . import winprob as W
from .optimise import BENCH_BOOST, TRIPLE_CAPTAIN, Plan

DETAIL_GWS = 6  # gameweeks simulated player by player (then each manager's form)
SIMS = 4000
CAPTAIN_OPTIONS = (
    4  # captains tried besides the plan's own (the best by expected points)
)
MAX_PLAN_ALTERNATIVES = 3
SWITCH_SE = 2.0  # switch only when P(1st) rises by more than this many standard errors


@dataclass
class LeagueChoice:
    name: str  # the candidate chosen: "plan", "hold", "planner", "no_hit" or "captain:<id>"
    plan: Plan
    switched: bool
    p_first: dict[str, float]  # every candidate's P(1st)
    gain: float  # chosen minus the expected-points plan
    gain_se: float
    odds: W.LeagueOdds  # the league with you on the chosen plan
    me: int
    reason_captain: str | None = None
    reason_plan: str | None = None


def plan_picks(plan: Plan, gws: list[int]) -> W.Picks:
    """A plan's line-ups as the win-probability simulation reads them."""
    hits = {w.gw: w.hits for w in plan.weeks}
    out: W.Picks = {}
    for g in gws:
        chip = plan.chips.get(g)
        out[g] = W.GWPick(
            list(plan.xi[g]),
            list(plan.bench[g]),
            plan.captain[g],
            plan.vice[g],
            chip if chip in (TRIPLE_CAPTAIN, BENCH_BOOST) else None,
            R.HIT_COST * hits.get(g, 0),
        )
    return out


def _with_captain(plan: Plan, g: int, cap: int, xp) -> Plan:
    """A copy of the plan with a different captain this week (the old one becomes vice)."""
    old = plan.captain[g]
    vice = old if old != cap else plan.vice[g]
    mult = 2 if plan.chips.get(g) == TRIPLE_CAPTAIN else 1
    delta = mult * (xp.get(cap, {}).get(g, 0.0) - xp.get(old, {}).get(g, 0.0))
    return replace(
        plan,
        captain={**plan.captain, g: cap},
        vice={**plan.vice, g: vice},
        gw_points={**plan.gw_points, g: plan.gw_points.get(g, 0.0) + delta},
    )


def choose(
    view,
    world,
    dists,
    xp: dict[int, dict[int, float]],
    horizon: list[int],
    plan: Plan,
    others: dict[str, Plan],
    n_sims: int = SIMS,
    seed: int | None = None,
) -> LeagueChoice | None:
    """Choose between `plan` (expected points) and `others` plus captain options by P(1st)."""
    remaining = view.remaining_gws
    if remaining <= 0 or not view.rivals:
        return None
    gws = [g for g in horizon[:DETAIL_GWS] if g in plan.xi]
    if not gws:
        return None
    g0 = gws[0]
    cands: dict[str, Plan] = {"plan": plan}
    for name, p in others.items():
        if len(cands) > MAX_PLAN_ALTERNATIVES:  # "plan" plus this many
            break
        if p is None or p is plan or not all(g in p.xi for g in gws):
            continue
        if any(_same(p, q, gws) for q in cands.values()):
            continue
        cands[name] = p
    ranked = sorted(plan.xi[g0], key=lambda p: -xp.get(p, {}).get(g0, 0.0))
    for pid in [p for p in ranked if p != plan.captain[g0]][:CAPTAIN_OPTIONS]:
        cands[f"captain:{pid}"] = _with_captain(plan, g0, pid, xp)
    picks = {n: plan_picks(p, gws) for n, p in cands.items()}
    entrants = W.entrants_from_league(view, world, dists, gws, picks["plan"])
    me = view.me.entry
    res = W.choose_plan(
        entrants,
        me,
        picks,
        dists,
        world,
        max(remaining, len(gws)),
        n_sims,
        g0 if seed is None else seed,
    )
    best, gain, se = pick(res)
    chosen = cands[best]
    out = LeagueChoice(
        name=best,
        plan=chosen,
        switched=best != "plan",
        p_first=res.p_first,
        gain=gain,
        gain_se=se,
        odds=res.candidate_odds[best],
        me=me,
    )
    if best.startswith("captain:"):
        out.reason_captain = captain_reason(
            world,
            view,
            entrants,
            me,
            plan.captain[g0],
            chosen.captain[g0],
            g0,
            res.p_first["plan"],
            res.p_first[best],
        )
    elif best != "plan":
        out.reason_plan = (
            "For your league: this plan raises your chance of finishing 1st from "
            f"{_pct(res.p_first['plan'])} to {_pct(res.p_first[best])} "
            "(the plan with the most expected points is slightly ahead on points alone)."
        )
    return out


def pick(res: W.PlanChoice, baseline: str = "plan") -> tuple[str, float, float]:
    """The candidate to follow: the baseline unless another beats it on P(1st) by more than
    SWITCH_SE standard errors (then the one that beats it by most). Returns (name, gain, se)."""
    best, gain, se = baseline, 0.0, 0.0
    for n, (d, s) in res.vs_baseline.items():
        if n != baseline and d > SWITCH_SE * s and d > gain:
            best, gain, se = n, d, s
    return best, gain, se


def _same(a: Plan, b: Plan, gws: list[int]) -> bool:
    return all(
        set(a.xi[g]) == set(b.xi[g]) and a.captain[g] == b.captain[g] for g in gws
    ) and sorted(a.buys) == sorted(b.buys)


def _pct(p: float) -> str:
    return "under 1%" if p < 0.005 else f"{p:.0%}"


def captain_reason(
    world, view, entrants, me, old: int, new: int, g: int, p_old: float, p_new: float
) -> str:
    """'Captain Saka instead of Haaland: 6 of 11 rivals are likely to captain Haaland, ...'"""
    name = world.players
    rivals = [e for e in entrants if e.key != me]
    n_old = sum(1 for e in rivals if e.picks[g].captain == old)
    n_new = sum(1 for e in rivals if e.picks[g].captain == new)
    others = [m.total for m in view.rivals]
    best = max(others) if others else view.me.total
    gap = best - view.me.total
    left = view.remaining_gws
    weeks = f"{left} week{'s' if left != 1 else ''} left"
    if gap > 0:
        standing = f"you're {gap} behind the leader with {weeks}"
        why = (
            f"{n_old} of your {len(rivals)} rivals are likely to captain {name[old].name}, so "
            f"his points barely move you; {name[new].name} is a chance to gain ground"
        )
    else:
        standing = (
            f"you lead by {-gap} with {weeks}"
            if gap < 0
            else f"you're level at the top with {weeks}"
        )
        why = (
            f"{n_new} of your {len(rivals)} rivals are likely to captain {name[new].name}, so "
            "matching them keeps your lead safe if he scores big"
        )
    return (
        f"Captain {name[new].name} instead of {name[old].name}: {why}, and {standing}. "
        f"It raises your chance of winning the league from {_pct(p_old)} to {_pct(p_new)}."
    )

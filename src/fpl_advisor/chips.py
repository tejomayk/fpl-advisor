"""When to play chips: each chip's gain this week, against what holding it is worth.

The transfer plan (optimise.solve) covers the next weeks without chips. For each chip held:

- Triple Captain and Bench Boost are valued on that plan's own squads, week by week (the captain's
  expected points; the bench's points beyond what the plan already counts for it), so a better
  week later in the horizon (say a double gameweek after two pencilled-in transfers) is seen.
- Wildcard and Free Hit are valued by solving the plan again with the chip played this week.

A chip is played this week only if its gain beats both its best later week in the horizon and its
value kept (`KEEP_VALUE`, shrinking to 0 as its deadline nears). So an expiring chip is used in its
last week only if it gains something, never forced into a week where it is worth nothing.
One chip per gameweek. Since 2025/26 there are two of each chip, one per half of the season.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import optimise
from . import rules as R
from .optimise import BENCH_WEIGHTS, Plan, solve
from .squad import Squad, chip_deadline
from .world import World

# Points that holding each chip is worth when many weeks of its window remain: roughly what a good
# later use gives (a double gameweek for Triple Captain and Bench Boost, a blank or double for Free
# Hit, a planned rebuild for Wildcard). It shrinks linearly over the last KEEP_RAMP_WEEKS weeks.
KEEP_VALUE = {"wildcard": 20.0, "freehit": 12.0, "bboost": 11.0, "3xc": 10.0}
KEEP_RAMP_WEEKS = 8
BETTER_USE = {
    "3xc": "a double gameweek for a premium player",
    "bboost": "a double gameweek with four bench players who start",
    "freehit": "a week when many teams have no match or play twice",
    "wildcard": "a rebuild when several players need replacing at once",
}
# Outside blank/double weeks, a Free Hit is only tried once its keep value is this low.
FREE_HIT_EVAL_BELOW = 6.0
# The quick Wildcard check (rebuild, then hold) is at most about this far below the full plan's.
WILDCARD_QUICK_MARGIN = 8.0


@dataclass
class ChipAdvice:
    chip: str
    play_now: bool
    value_now: float
    best_gw: int | None
    best_value: float
    expires_gw: int | None
    reason: str
    plan: Plan | None = None  # the squad to field if this chip is played now
    forced: bool = False  # played in its last valid week (it would expire otherwise)
    keep_value: float = 0.0


def best_xi(
    squad: list[int], world: World, xp_g: dict[int, float]
) -> tuple[list[int], list[int]]:
    """A greedy best XI under formation rules (good enough to value future gameweeks)."""
    by_pos = {
        pos: sorted(
            (p for p in squad if world.players[p].pos == pos),
            key=lambda p: -xp_g.get(p, 0),
        )
        for pos in R.POS_NAME
    }
    xi = by_pos[R.GKP][:1]
    for pos in (R.DEF, R.MID, R.FWD):
        xi += by_pos[pos][: R.XI_MIN[pos]]
    rest = sorted(
        (p for pos in (R.DEF, R.MID, R.FWD) for p in by_pos[pos][R.XI_MIN[pos] :]),
        key=lambda p: -xp_g.get(p, 0),
    )
    for p in rest:
        if len(xi) == 11:
            break
        pos = world.players[p].pos
        if sum(world.players[q].pos == pos for q in xi) < R.XI_MAX[pos]:
            xi.append(p)
    return xi, [p for p in squad if p not in xi]


def blank_and_double(world: World, gw: int) -> tuple[list[int], list[int]]:
    counts = {t: 0 for t in world.teams}
    for f in world.fixtures_in(gw):
        counts[f.home] += 1
        counts[f.away] += 1
    return [t for t, c in counts.items() if c == 0], [
        t for t, c in counts.items() if c >= 2
    ]


def keep_value(chip: str, g0: int, deadline: int | None) -> float:
    """What holding a chip past this week is worth: full with many weeks left, 0 in its last week."""
    full = KEEP_VALUE.get(chip, 0.0)
    if deadline is None:
        return full
    return full * max(0.0, min(1.0, (deadline - g0) / KEEP_RAMP_WEEKS))


def _bench_gain(plan: Plan, xp, g: int) -> float:
    """Bench Boost's extra points: the bench's full points minus what the plan already counts."""
    out = 0.0
    for k, p in enumerate(plan.bench[g]):
        w = BENCH_WEIGHTS[min(k, 3)]
        out += (1 - w) * xp[p].get(g, 0.0)
    return out


def advise_chips(
    world: World,
    xp: dict[int, dict[int, float]],
    window: list[int],  # gameweeks projected, starting at the next one
    horizon: list[int],
    squad: Squad,
    base: Plan,
    multiplier: dict[int, float] | None = None,
    **solve_kw,
) -> list[ChipAdvice]:
    """One verdict per chip held now. A chip played now carries the plan to field (`plan`)."""
    g0 = window[0]
    weeks = [g for g in horizon if g in base.xi]

    def name_of(pid):
        return world.players[pid].name

    def resolve(**chip_kw) -> Plan:
        return solve(
            world,
            xp,
            horizon,
            squad.players,
            squad.sell_price,
            squad.bank,
            squad.free_transfers,
            multiplier=multiplier,
            **{**solve_kw, **chip_kw},
        )

    blanks, doubles = blank_and_double(world, g0)
    out: list[ChipAdvice] = []
    for chip in squad.chips_available:
        dl = chip_deadline(world.chips, chip, g0)
        keep = keep_value(chip, g0, dl)
        valid = [g for g in weeks if dl is None or g <= dl]
        expiry = (
            f" It expires after GW{dl}." if dl is not None and dl <= horizon[-1] else ""
        )
        chip_plan = None
        best_later, later_what = 0.0, ""

        if chip in ("3xc", "bboost"):
            if chip == "3xc":
                per_gw = {g: xp[base.captain[g]].get(g, 0.0) for g in valid}
            else:
                per_gw = {g: _bench_gain(base, xp, g) for g in valid}
            now = per_gw.get(g0, 0.0)
            later = {
                g: v * optimise.DECAY ** (g - g0) for g, v in per_gw.items() if g != g0
            }
            best_g = max(later, key=later.get) if later else None
            best_later = later[best_g] if best_g else 0.0
            play = now > 0 and now >= max(keep, best_later)
            if chip == "3xc":
                what = (
                    f"Triple Captain on {name_of(base.captain[g0])} is worth about "
                    f"{now:.1f} extra points this week"
                )
                if best_g:
                    later_what = (
                        f"GW{best_g} looks better ({name_of(base.captain[best_g])}, "
                        f"{per_gw[best_g]:.1f})"
                    )
            else:
                what = f"Bench Boost adds about {now:.1f} points this week"
                if best_g:
                    later_what = (
                        f"GW{best_g} looks better ({per_gw[best_g]:.1f}) with the squad the "
                        "plan builds by then"
                    )
            if doubles:
                what += f" ({len(doubles)} teams play twice)"
            best_value = per_gw[best_g] if best_g else now
        else:
            # Wildcard and Free Hit: plan again with the chip this week and compare.
            try_it = (
                chip == "wildcard"
                or bool(blanks or doubles)
                or keep <= FREE_HIT_EVAL_BELOW
            )
            now = 0.0
            if try_it and chip == "wildcard":
                # Quick look first: rebuild now and hold that squad (fast to solve). It
                # undervalues the Wildcard (no later moves), so only a clear miss skips the full
                # plan; a clear win needs no full plan either.
                quick = resolve(wildcard_gw=g0, no_transfer_last=len(horizon) - 1)
                now = quick.objective - base.objective
                chip_plan = quick
                if keep - WILDCARD_QUICK_MARGIN < now < keep:
                    chip_plan = resolve(wildcard_gw=g0)
                    now = max(now, chip_plan.objective - base.objective)
            elif try_it:
                chip_plan = resolve(free_hit_gw=g0)
                now = chip_plan.objective - base.objective
            play = try_it and now > 0 and now >= keep
            best_g = None
            if chip == "wildcard":
                n = len(chip_plan.buys) if chip_plan else 0
                what = (
                    f"Rebuilding the squad ({n} changes) adds about {now:+.1f} points over "
                    f"the next {len(horizon)} weeks compared with your planned transfers"
                )
            else:
                if try_it:
                    what = (
                        f"A one-week squad adds about {now:+.1f} points over the plan"
                        + (f" ({len(blanks)} teams have no match)" if blanks else "")
                        + (f" ({len(doubles)} teams play twice)" if doubles else "")
                    )
                else:
                    what = "This is a normal week with no blanks or doubles"
                later = [
                    g
                    for g in window[1:]
                    if any(blank_and_double(world, g)) and (dl is None or g <= dl)
                ]
                best_g = later[0] if later else None
            best_value = now

        if play:
            reason = (
                f"{what}. That beats "
                + (
                    f"holding it (worth about {keep:.1f})"
                    if keep > 0
                    else "letting it expire unused"
                )
                + (" and every later week in the plan" if best_later else "")
                + "."
                + expiry
            )
        elif later_what and best_later >= keep and best_later > now:
            reason = f"{what}. {later_what}, so keep it for then." + expiry
        else:
            reason = (
                f"{what}. Keep it: holding it is worth about {keep:.1f} points"
                + (f" ({BETTER_USE[chip]} uses it better)." if keep > 0 else ".")
                + expiry
                + (
                    " An expiring chip is only used if it gains something."
                    if dl is not None and dl <= g0 + 1
                    else ""
                )
            )
        out.append(
            ChipAdvice(
                chip,
                play,
                round(now, 1),
                g0 if play else best_g,
                round(best_value, 1),
                dl,
                reason,
                chip_plan if play else None,
                forced=play and dl is not None and dl <= g0,
                keep_value=round(keep, 1),
            )
        )

    # Only one chip per gameweek: keep the one that gains most over holding it.
    playing = [a for a in out if a.play_now]
    if len(playing) > 1:
        pick = max(playing, key=lambda a: (a.forced, a.value_now - a.keep_value))
        for a in playing:
            if a is not pick:
                a.play_now = False
                a.forced = False
                a.plan = None
                a.reason += (
                    f" (Not this week: {R.CHIP_NAMES[pick.chip]} is worth more, and you can "
                    "only play one chip a week.)"
                )
    # Triple Captain and Bench Boost change the line-up the plan should pick: plan with them.
    for a in out:
        if a.play_now and a.plan is None:
            kw = (
                {"triple_captain_gw": g0} if a.chip == "3xc" else {"bench_boost_gw": g0}
            )
            a.plan = resolve(**kw)
    return out

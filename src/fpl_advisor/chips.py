"""When to play chips. Each chip's value now is compared with its best expected value later in its window.

Since 2025/26 there are two of each chip, one per half of the season. An unused first-half chip is lost
after its deadline gameweek, so as that deadline nears, the bar for playing it drops.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import rules as R
from .optimise import HORIZON_WEIGHTS, Plan, solve
from .squad import Squad, chip_deadline
from .world import World

FUTURE_FT_VALUE = (
    1.5  # rough points a weekly free transfer adds per week it can be used
)
WILDCARD_GAIN = 15.0  # expected points over the horizon needed to justify a Wildcard
FREE_HIT_GAIN = 12.0  # expected points in one gameweek needed to justify a Free Hit
NEAR_EXPIRY = 2  # gameweeks before the deadline at which the bar drops


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
    forced: bool = False  # must be played now or it expires unused


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


def advise_chips(
    world: World,
    xp: dict[int, dict[int, float]],
    window: list[int],  # gameweeks projected, starting at the next one
    horizon: list[int],
    squad: Squad,
    base: Plan,
    multiplier: dict[int, float] | None = None,
) -> list[ChipAdvice]:
    g0 = window[0]
    out: list[ChipAdvice] = []

    def expires(name):
        return chip_deadline(world.chips, name, g0)

    def name_of(pid):
        return world.players[pid].name

    for chip in squad.chips_available:
        dl = expires(chip)
        last = [g for g in window if dl is None or g <= dl]
        near = dl is not None and dl - g0 <= NEAR_EXPIRY

        if chip == "3xc":
            per_gw = {}
            for g in last:
                xi, _ = best_xi(
                    base.squad, world, xp_g={p: xp[p].get(g, 0) for p in base.squad}
                )
                cap = max(xi, key=lambda p: xp[p].get(g, 0))
                per_gw[g] = (cap, xp[cap].get(g, 0))
            now_cap, now_val = per_gw[g0]
            best_g = max(per_gw, key=lambda g: per_gw[g][1])
            play = now_val >= per_gw[best_g][1] - 0.2 or (dl is not None and g0 >= dl)
            _, dbl = blank_and_double(world, best_g)
            reason = (
                f"Triple Captain on {name_of(now_cap)} is worth about {now_val:.1f} extra points this week. "
                + (
                    "No week before the chip expires looks better, so use it now."
                    if play
                    else f"GW{best_g} looks better ({name_of(per_gw[best_g][0])}, {per_gw[best_g][1]:.1f})"
                    + (" because of a double gameweek" if dbl else "")
                    + ", so keep it for then."
                )
            )
            out.append(
                ChipAdvice(chip, play, now_val, best_g, per_gw[best_g][1], dl, reason)
            )

        elif chip == "bboost":
            per_gw = {}
            for g in last:
                _, bench = best_xi(
                    base.squad, world, xp_g={p: xp[p].get(g, 0) for p in base.squad}
                )
                per_gw[g] = sum(xp[p].get(g, 0) for p in bench)
            now_val = per_gw[g0]
            best_g = max(per_gw, key=per_gw.get)
            play = now_val >= per_gw[best_g] - 0.3 or (dl is not None and g0 >= dl)
            reason = (
                f"Your bench is expected to score about {now_val:.1f} this week. "
                + (
                    "That's as good as it gets before the chip expires, so play it."
                    if play
                    else f"It looks better in GW{best_g} ({per_gw[best_g]:.1f}), so wait. Bench Boost "
                    "works best in a double gameweek with four bench players who all start."
                )
            )
            out.append(
                ChipAdvice(chip, play, now_val, best_g, per_gw[best_g], dl, reason)
            )

        elif chip == "freehit":
            fh = solve(
                world,
                xp,
                [g0],
                squad.players,
                squad.sell_price,
                squad.bank,
                squad.free_transfers,
                unlimited=True,
                multiplier=multiplier,
                weights=[1.0],
            )
            # The base week includes the gain from any hit transfers, so charge their cost too.
            gain = fh.gw_points[g0] - (base.gw_points[g0] - R.HIT_COST * base.hits)
            blanks, doubles = blank_and_double(world, g0)
            bar = 5.0 if near else FREE_HIT_GAIN
            play = gain >= bar
            later = [g for g in last[1:] if any(blank_and_double(world, g))]
            reason = (
                f"A one-week squad would score about {gain:+.1f} more than your planned team this week"
                + (f" ({len(blanks)} teams have no match)" if blanks else "")
                + (f" ({len(doubles)} teams play twice)" if doubles else "")
                + ". "
                + (
                    "That's enough to use it."
                    if play
                    else "Free Hit is best saved for a week when many teams have no match or play twice"
                    + (f" (next one: GW{later[0]})" if later else "")
                    + (
                        ". It expires soon, though, so use it on your weakest week before then."
                        if near
                        else "."
                    )
                )
            )
            out.append(
                ChipAdvice(
                    chip,
                    play,
                    gain,
                    later[0] if later else None,
                    gain,
                    dl,
                    reason,
                    fh,
                )
            )

        elif chip == "wildcard":
            wc = solve(
                world,
                xp,
                horizon,
                squad.players,
                squad.sell_price,
                squad.bank,
                squad.free_transfers,
                unlimited=True,
                multiplier=multiplier,
            )
            w = HORIZON_WEIGHTS[: len(horizon)]
            # Without the Wildcard you'd still get a free transfer each later week; credit the base
            # plan for that so the Wildcard isn't flattered.
            base_val = (
                sum(wi * base.gw_points[g] for wi, g in zip(w, horizon))
                - R.HIT_COST * base.hits
                + FUTURE_FT_VALUE * sum(w[1:])
            )
            wc_val = sum(wi * wc.gw_points[g] for wi, g in zip(w, horizon))
            gain = wc_val - base_val
            bar = 4.0 if near else WILDCARD_GAIN
            play = gain >= bar
            reason = (
                f"Rebuilding your whole squad would add about {gain:+.1f} expected points over the next {len(horizon)} "
                f"weeks compared with your best normal transfers ({len(wc.buys)} changes). "
                + (
                    "That's worth it: play the Wildcard."
                    if play
                    else f"That's below the {bar:.0f}-point bar, so keep it. Wildcards are best used when you have several "
                    "problems at once, like injuries or many players with bad fixtures."
                )
                + (f" Note: it expires after GW{dl}." if near else "")
            )
            out.append(ChipAdvice(chip, play, gain, None, gain, dl, reason, wc))

    # A chip in its last valid week is forced: play it now or lose it.
    for a in out:
        if a.expires_gw is not None and a.expires_gw <= g0 and a.play_now:
            a.forced = True

    # Chips that would otherwise expire unused: with k chips sharing a deadline and only k weeks
    # left, one has to go this week. Use the one whose value now is closest to its best later value.
    by_deadline: dict[int, list[ChipAdvice]] = {}
    for a in out:
        if a.expires_gw is not None:
            by_deadline.setdefault(a.expires_gw, []).append(a)
    for dl, group in by_deadline.items():
        weeks_left = dl - g0 + 1
        if len(group) >= weeks_left and not any(a.play_now for a in group):
            pick = max(group, key=lambda a: a.value_now - a.best_value)
            pick.play_now = True
            pick.forced = True
            pick.reason += (
                f" You have {len(group)} chips that expire after GW{dl} and only {weeks_left} "
                f"week{'s' if weeks_left != 1 else ''} left, so one has to be used now."
            )

    # Only one chip per gameweek: keep the most valuable "play now" and defer the rest.
    playing = [a for a in out if a.play_now]
    if len(playing) > 1:
        keep = max(playing, key=lambda a: (a.forced, a.value_now))
        for a in playing:
            if a is not keep:
                a.play_now = False
                a.reason += (
                    f" (Not this week: {R.CHIP_NAMES[keep.chip]} is worth more, and you can only "
                    "play one chip a week.)"
                )
    return out

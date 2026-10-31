"""Turn projections, the optimiser and league context into plain-language advice."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from . import rules as R
from .api import FPLClient
from .chips import ChipAdvice, advise_chips
from .config import Config
from .creators import creator_lines
from .league import (
    LeagueError,
    LeagueView,
    load_league,
    ownership_multiplier,
    risk_lambda,
)
from .model import project
from .optimise import Plan, solve
from .prices import outlook, timing_advice
from .signals import Signals
from .squad import STARTING_BUDGET as R_BUDGET
from .squad import (
    NoHistoryError,
    Squad,
    chip_deadline,
    chips_available,
    initial_squad,
    load_squad,
)
from .world import Event, World, load_world


@dataclass
class Advice:
    world: World
    squad: Squad
    event: Event
    hours_to_deadline: float
    horizon: list[int]
    xp: dict[int, dict[int, float]]
    plan: Plan
    chips: list[ChipAdvice]
    league: LeagueView | None
    risk_lambda: float
    risk_reason: str
    timing: list[str]
    chip_now: ChipAdvice | None = None
    notes: list[str] = field(default_factory=list)
    initial: bool = False  # building a squad from scratch (pre-season or new manager)
    signals: Signals = field(default_factory=Signals)  # odds, team news, elite managers

    # ---- helpers
    def name(self, pid: int) -> str:
        return self.world.players[pid].name

    def horizon_xp(self, pid: int) -> float:
        return sum(self.xp[pid].get(g, 0) for g in self.horizon)

    def fixture_count(self, pid: int) -> int:
        """Matches the player's team plays in the next gameweek."""
        team = self.world.players[pid].team
        return sum(
            team in (f.home, f.away) for f in self.world.fixtures_in(self.event.id)
        )

    def fixtures(self, pid: int, n: int = 3) -> str:
        team = self.world.players[pid].team
        out = []
        for g in self.horizon[:n]:
            fs = [f for f in self.world.fixtures_in(g) if team in (f.home, f.away)]
            if not fs:
                out.append("-")
            for f in fs:
                opp, home = (f.away, True) if f.home == team else (f.home, False)
                out.append(f"{self.world.teams[opp].short}({'H' if home else 'a'})")
        return " ".join(out)


def build_advice(
    cfg: Config,
    client: FPLClient,
    now: datetime | None = None,
    horizon_len: int = 5,
    world: World | None = None,
    signals: Signals | None = None,
) -> Advice:
    """Advice for one manager. Pass a preloaded `world` to share it between requests.

    `signals` (odds, team news, elite managers) sharpen the next gameweek; without them the
    advice uses FPL data alone.
    """
    signals = signals or Signals()
    now = now or datetime.now(UTC)
    if world is None:
        world = load_world(client)
    else:  # this function adds histories for the user's players; don't touch the shared copy
        world = replace(world, history=dict(world.history), past=dict(world.past))
    ev = world.next_event
    if ev is None:
        raise ValueError("The season is over: there is no upcoming deadline.")
    try:
        squad = load_squad(client, world, cfg.team_id)
        initial = False
    except (
        NoHistoryError
    ):  # pre-season, or joined after the last deadline: pick a squad from scratch
        squad = initial_squad(client, cfg.team_id)
        initial = True
    # Make sure the user's own players have histories even if they've barely played.
    missing = [p for p in squad.players if p not in world.history]
    if missing:
        for pid, s in client.element_summaries(missing).items():
            world.history[pid] = s["history"]
            if s["history_past"]:
                world.past[pid] = s["history_past"][-1]

    league, league_note = None, None
    if cfg.league_id:
        try:
            league = load_league(client, world, cfg.league_id, cfg.team_id, cfg.risk)
        except LeagueError as e:  # advice still works without the league view
            league_note = f"League view skipped: {e}"
    if league:
        lam, why = league.risk_lambda, league.risk_reason
    else:
        lam, why = risk_lambda(0, [], 0, cfg.risk)

    # Project far enough to compare each chip with every week left before it expires.
    last_gw = max(e.id for e in world.events)
    deadlines = [
        chip_deadline(world.chips, c, ev.id) or last_gw for c in squad.chips_available
    ]
    window_end = min(last_gw, max([ev.id + horizon_len - 1, *deadlines]))
    window = list(range(ev.id, window_end + 1))
    horizon = window[:horizon_len]
    xp = project(world, window, signals=signals)
    own = league.ownership if league else {}
    mult = ownership_multiplier(world.players, own, lam) or None

    def plan_with(**kw) -> Plan:
        return solve(
            world,
            xp,
            horizon,
            squad.players,
            squad.sell_price,
            squad.bank,
            squad.free_transfers,
            multiplier=mult,
            max_hits=cfg.max_hits,
            unlimited=initial,
            **kw,
        )

    base = plan_with()
    chips = (
        [] if initial else advise_chips(world, xp, window, horizon, squad, base, mult)
    )
    chip_now = next((c for c in chips if c.play_now), None)
    plan = base
    if chip_now:
        if chip_now.plan is not None:
            plan = chip_now.plan
        elif chip_now.chip == "bboost":
            plan = plan_with(bench_boost_gw=ev.id)
        elif chip_now.chip == "3xc":
            plan = plan_with(triple_captain_gw=ev.id)

    hours = (ev.deadline - now).total_seconds() / 3600
    timing = timing_advice(
        [world.players[p] for p in plan.buys],
        [world.players[p] for p in plan.sells],
        hours,
    )
    advice = Advice(
        world,
        squad,
        ev,
        hours,
        horizon,
        xp,
        plan,
        chips,
        league,
        lam,
        why,
        timing,
        chip_now,
    )
    advice.notes.append(
        "FPL only shows your squad after each deadline, so transfers you've already made for this "
        "gameweek aren't counted here."
    )
    advice.initial = initial
    advice.signals = signals
    if (
        initial
    ):  # there's no existing squad, so the note about unseen transfers doesn't apply
        advice.notes.clear()
    if league_note:
        advice.notes.append(league_note)
    return advice


# ------------------------------------------------------------------ explanations


def _pair_transfers(a: Advice) -> list[tuple[int, int]]:
    pairs = []
    buys = list(a.plan.buys)
    for out in sorted(a.plan.sells, key=lambda p: a.world.players[p].pos):
        pos = a.world.players[out].pos
        inn = next((b for b in buys if a.world.players[b].pos == pos), None)
        if inn is not None:
            buys.remove(inn)
            pairs.append((out, inn))
    return pairs


def signals_live(a: Advice) -> bool:
    """Odds and news describe one gameweek; after its deadline they no longer apply."""
    return a.signals.gameweek in (None, a.event.id)


def evidence(a: Advice, pid: int, selling: bool = False) -> list[str]:
    """Plain-words support from outside signals: team news, bookmakers, elite managers."""
    sig = a.signals.players.get(pid)
    if not sig:
        return []
    out = []
    live = signals_live(a)
    if sig.news and live:
        out.append(f"team news: {sig.news.rstrip('.')}")
    if sig.p_goal is not None and not selling and live and a.fixture_count(pid) == 1:
        out.append(f"bookmakers give a {sig.p_goal:.0%} chance to score next match")
    e = a.signals.elite
    if e and e.managers and sig.elite_own is not None:
        n = round(sig.elite_own * e.managers)
        moved = sig.elite_out if selling else sig.elite_in
        if n or moved:
            out.append(
                f"{n} of the {e.managers} most consistent managers own this player"
                + (
                    f" ({moved} {'sold' if selling else 'bought'} last week)"
                    if moved
                    else ""
                )
            )
    return out


def _why_out(a: Advice, pid: int) -> str:
    p = a.world.players[pid]
    extra = "; ".join(evidence(a, pid, selling=True))
    return _why_out_base(a, p, pid) + (f"; {extra}" if extra else "")


def _why_out_base(a: Advice, p, pid: int) -> str:
    if p.status != "a" or (p.chance_next is not None and p.chance_next < 100):
        return (
            f"{p.news or 'flagged as doubtful'}, so only {a.horizon_xp(pid):.1f} expected points "
            f"over the next {len(a.horizon)} weeks"
        )
    return f"only {a.horizon_xp(pid):.1f} expected points over the next {len(a.horizon)} weeks ({a.fixtures(pid)})"


def initial_squad_lines(a: Advice) -> list[str]:
    lines = [
        f"**Pick your squad** (£{R_BUDGET:.0f}m budget, as many changes as you like before your first deadline):"
    ]
    for pos in R.POS_NAME:
        picks = [p for p in a.plan.squad if a.world.players[p].pos == pos]
        picks.sort(key=lambda p: -a.horizon_xp(p))
        lines.append(
            f"- {R.POS_NAME[pos]}: "
            + ", ".join(
                f"{a.name(p)} £{a.world.players[p].price:.1f}m ({a.horizon_xp(p):.1f})"
                for p in picks
            )
        )
    lines.append(
        f"Money left: £{a.plan.bank_after:.1f}m. Numbers in brackets are expected points over the next "
        f"{len(a.horizon)} weeks."
    )
    return lines


def transfer_lines(a: Advice) -> list[str]:
    if a.initial:
        return initial_squad_lines(a)
    lines = []
    n = len(a.plan.buys)
    ft = a.squad.free_transfers
    if a.chip_now and a.chip_now.chip in ("wildcard", "freehit"):
        lines.append(
            f"Play your **{R.CHIP_NAMES[a.chip_now.chip]}**: {n} changes, all free."
        )
    elif n == 0:
        lines.append(
            f"**No transfers this week.** You have {ft} free transfer{'s' if ft != 1 else ''}. No move adds enough "
            "expected points to beat keeping it for later (a saved transfer gives you flexibility when injuries hit)."
            + (
                " You're already at the maximum of 5, though, so one goes to waste if you don't use it."
                if ft >= R.MAX_FREE_TRANSFERS
                else ""
            )
        )
    else:
        hit = f", taking a -{R.HIT_COST * a.plan.hits} hit" if a.plan.hits else ""
        lines.append(
            f"**Make {n} transfer{'s' if n > 1 else ''}** (you have {ft} free{hit})."
        )
    for out, inn in _pair_transfers(a):
        po, pi = a.world.players[out], a.world.players[inn]
        gain = a.horizon_xp(inn) - a.horizon_xp(out)
        lines.append(
            f"- OUT **{po.name}** (£{a.squad.sell_price[out]:.1f}m), IN **{pi.name}** (£{pi.price:.1f}m) — "
            f"{gain:+.1f} expected points over {len(a.horizon)} weeks.\n"
            f"  - Why sell: {_why_out(a, out)}.\n"
            f"  - Why {pi.name}: {a.horizon_xp(inn):.1f} expected points, fixtures {a.fixtures(inn)}"
            + (
                f", owned by {a.league.ownership.get(inn, 0):.0%} of your rivals"
                if a.league
                else ""
            )
            + "".join(f"; {e}" for e in evidence(a, inn))
            + "."
        )
    if a.plan.hits:
        lines.append(
            f"About the hit: each extra transfer costs {R.HIT_COST} points. A hit is only worth it when the "
            "transfers together gain more than that over the weeks you keep the new players. Here the whole "
            f"set gains {sum(a.horizon_xp(i) - a.horizon_xp(o) for o, i in _pair_transfers(a)):+.1f} expected "
            f"points over {len(a.horizon)} weeks, for a cost of {R.HIT_COST * a.plan.hits}."
        )
    lines.append(f"Money in the bank afterwards: £{a.plan.bank_after:.1f}m.")
    return lines


def captain_lines(a: Advice) -> list[str]:
    g = a.event.id
    xi = a.plan.xi[g]
    ranked = sorted(xi, key=lambda p: -a.xp[p].get(g, 0))[:3]
    cap, vice = a.plan.captain[g], a.plan.vice[g]
    mult = 3 if (a.chip_now and a.chip_now.chip == "3xc") else 2
    lines = [
        (
            f"**Captain {a.name(cap)}** ({a.xp[cap].get(g, 0):.1f} expected points, x{mult}), "
            f"vice-captain {a.name(vice)}."
        )
    ]
    lines.append(
        "Top options: "
        + ", ".join(
            f"{a.name(p)} {a.xp[p].get(g, 0):.1f} ({a.fixtures(p, 1)})" for p in ranked
        )
        + "."
    )
    if a.league:
        share = a.league.captaincy.get(cap, 0)
        if share >= 0.5:
            lines.append(
                f"{share:.0%} of your rivals captained {a.name(cap)} last week. Captaining them too means "
                "their big score can't open a gap on you."
            )
        elif share <= 0.2:
            lines.append(
                f"Few rivals ({share:.0%}) captained {a.name(cap)} last week, so a big score here is a real chance to gain ground."
            )
    return lines


def lineup_lines(a: Advice) -> list[str]:
    g = a.event.id
    by_pos: dict[int, list[str]] = {}
    for p in a.plan.xi[g]:
        by_pos.setdefault(a.world.players[p].pos, []).append(
            f"{a.name(p)} {a.xp[p].get(g, 0):.1f}"
        )
    lines = [
        f"- {R.POS_NAME[pos]}: " + ", ".join(names)
        for pos, names in sorted(by_pos.items())
    ]
    bench = ", ".join(
        f"{'GK' if i == 0 else f'{i}.'} {a.name(p)} {a.xp[p].get(g, 0):.1f}"
        for i, p in enumerate(a.plan.bench[g])
    )
    lines.append(f"- Bench (in order, GK first): {bench}")
    lines.append(f"Expected points this week: **{a.plan.gw_points[g]:.0f}**.")
    return lines


def league_lines(a: Advice) -> list[str]:
    lg = a.league
    if not lg:
        return [
            "Add your league ID to see rivals, a points target and strategy tailored to your league."
        ]
    everyone = sorted([lg.me, *lg.rivals], key=lambda m: m.rank)
    lines = ["| # | Manager | Total | Last GW |", "|---|---|---|---|"]
    for m in everyone[:8]:
        me = " (you)" if m is lg.me else ""
        lines.append(f"| {m.rank} | {m.manager}{me} | {m.total} | {m.event_total} |")
    if lg.me not in everyone[:8]:
        lines.append(
            f"| {lg.me.rank} | {lg.me.manager} (you) | {lg.me.total} | {lg.me.event_total} |"
        )
    lines.append("")
    lines.append(
        f"**Target: about {lg.target} points** by the end of the season. That needs about "
        f"{lg.required_per_gw:.0f} a week from here (your average so far: {lg.me.total / max(1, lg.me.gws_played):.0f}). "
        "It's where the best rival is heading if they keep a realistic share of their edge over the average "
        "FPL manager (early hot streaks usually fade), plus a 10-point margin."
    )
    if lg.last_season_best:
        who, pts = lg.last_season_best
        lines.append(f"Best total last season among these managers: {pts} ({who}).")
    lines.append(f"**Strategy:** {a.risk_reason}")
    mine = set(a.plan.squad)
    threats = sorted(
        (
            p
            for p, s in lg.ownership.items()
            if s >= 0.5 and p not in mine and a.xp[p].get(a.event.id, 0) >= 2.5
        ),
        key=lambda p: -lg.ownership[p],
    )
    diffs = sorted(
        (p for p in a.plan.xi[a.event.id] if lg.ownership.get(p, 0) <= 0.25),
        key=lambda p: lg.ownership.get(p, 0),
    )
    if threats:
        lines.append(
            "Popular with rivals but not in your team (their points are your loss): "
            + ", ".join(f"{a.name(p)} {lg.ownership[p]:.0%}" for p in threats[:5])
            + "."
        )
    if diffs:
        lines.append(
            "Your differentials (few rivals own them, so their points gain you ground): "
            + ", ".join(f"{a.name(p)} {lg.ownership.get(p, 0):.0%}" for p in diffs[:5])
            + "."
        )
    rival_chips = []
    for m in lg.rivals[:5]:
        left = [
            R.CHIP_NAMES[c]
            for c in chips_available(a.world.chips, m.chips_used, a.event.id)
        ]
        if left:
            rival_chips.append(f"{m.manager}: {', '.join(left)}")
    if rival_chips:
        lines.append(
            "Chips the top rivals still have for this half of the season: "
            + "; ".join(rival_chips)
            + "."
        )
    return lines


def chip_lines(a: Advice) -> list[str]:
    if not a.chips:
        return ["No chips available right now."]
    lines = []
    for c in a.chips:
        verdict = "PLAY NOW" if c.play_now else "Keep"
        exp = f" (expires after GW{c.expires_gw})" if c.expires_gw else ""
        lines.append(f"- **{R.CHIP_NAMES[c.chip]}{exp}: {verdict}.** {c.reason}")
    return lines


def price_lines(a: Advice) -> list[str]:
    lines = list(a.timing)
    moves = []
    for pid in a.squad.players:
        o = outlook(a.world.players[pid])
        if o and o.nights == 0:
            moves.append(
                f"{a.name(pid)} likely to {'rise' if o.direction > 0 else 'fall'} tonight"
            )
    if moves:
        lines.append("Your players: " + "; ".join(moves) + ".")
    return lines


def render_markdown(a: Advice) -> str:
    s = a.squad
    when = a.event.deadline.strftime("%a %d %b %H:%M UTC")
    out = [
        f"# {a.event.id and f'GW{a.event.id}'} advice for {s.team_name}",
        (
            f"Deadline **{when}** (in {a.hours_to_deadline:.0f} hours). Total so far: {s.total_points} points. "
            f"Squad value £{s.value:.1f}m."
        ),
        "",
        "## Transfers",
        *transfer_lines(a),
        "",
        "## Captain",
        *captain_lines(a),
        "",
        "## Starting XI",
        *lineup_lines(a),
        "",
        "## Chips",
        *chip_lines(a),
        "",
        "## When to make your moves",
        *[f"- {t}" for t in price_lines(a)],
        "",
        "## Your league",
        *league_lines(a),
        "",
        "## Creator room",
        *creator_lines(
            a.signals.creators
            if a.signals.creators and a.signals.creators.get("gameweek") == a.event.id
            else None
        ),
        "",
        "---",
        *[f"_{n}_" for n in a.notes],
        (
            f"_Expected points come from a model using xG, xA, minutes, defensive contributions, bonus and "
            f"fixture difficulty, with recent weeks weighted more. They're estimates, not promises. Horizon: GW{a.horizon[0]}-GW{a.horizon[-1]}._"
        ),
    ]
    return "\n".join(out)


def summary_text(a: Advice) -> str:
    """A short version for a phone notification."""
    g = a.event.id
    parts = []
    if a.initial:
        return (
            f"Pick your squad before the GW{g} deadline (see the full report). "
            f"Captain {a.name(a.plan.captain[g])}."
        )
    if a.chip_now:
        parts.append(f"Chip: play {R.CHIP_NAMES[a.chip_now.chip]}.")
    pairs = _pair_transfers(a)
    if pairs:
        moves = "; ".join(f"{a.name(o)} → {a.name(i)}" for o, i in pairs[:4])
        more = f" (+{len(pairs) - 4} more)" if len(pairs) > 4 else ""
        hit = f" (-{R.HIT_COST * a.plan.hits})" if a.plan.hits else ""
        parts.append(f"Transfers{hit}: {moves}{more}.")
    else:
        parts.append(f"No transfers; keep your {a.squad.free_transfers} FT.")
    parts.append(f"Captain {a.name(a.plan.captain[g])}, vice {a.name(a.plan.vice[g])}.")
    if a.league and a.league.rivals:
        me, leader = a.league.me, a.league.leader
        if leader is not me:
            parts.append(
                f"League: #{me.rank}, {leader.total - me.total} behind the leader."
            )
        else:
            lead = me.total - max(m.total for m in a.league.rivals)
            parts.append(f"League: 1st, {lead} ahead.")
    return " ".join(parts)

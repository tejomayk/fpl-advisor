"""Turn projections, the optimiser and league context into plain-language advice."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from . import robust
from . import rules as R
from .api import FPLClient
from .chips import ChipAdvice, advise_chips
from .config import Config
from .creators import creator_lines
from .league import (
    LeagueError,
    LeagueView,
    load_league,
    ownership_centre,
    ownership_multiplier,
    risk_lambda,
)
from .leaguechoice import LeagueChoice
from .leaguechoice import choose as choose_for_league
from .model import PointsDist, project_dist
from .optimise import (
    BENCH_BOOST,
    FREE_HIT,
    HORIZON,
    TRIPLE_CAPTAIN,
    WILDCARD,
    Plan,
    solve,
)
from .prices import outlook, timing_advice
from .signals import Signals
from .squad import STARTING_BUDGET as R_BUDGET
from .squad import (
    NoHistoryError,
    Squad,
    chips_available,
    initial_squad,
    load_squad,
)
from .winprob import describe as describe_odds
from .world import Event, World, load_world

# Weeks after the horizon used to value the squad left at the end.
TERMINAL_LOOKAHEAD = 4


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
    # What this week's moves are worth against making none, in the plan's own terms (after hits).
    value_of_moves: float | None = None
    # Stage 3 (robust.py): P(this week's best moves beat making none); how sure we are of the
    # transfer advice (that P when it says move, 1 - P when it says hold); one per move.
    p_beats_hold: float | None = None
    confidence: float | None = None
    move_confidence: list = field(default_factory=list)
    robust_note: str | None = None  # why the planner's first choice was changed
    # Stage 4 (leaguechoice.py): the choice made on P(finish 1st), when a league is set.
    league_choice: LeagueChoice | None = None

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
    horizon_len: int = HORIZON,
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

    dec = decide(
        world,
        squad,
        initial=initial,
        max_hits=cfg.max_hits,
        multiplier=ownership_multiplier(
            world.players,
            league.ownership if league else {},
            lam,
            ownership_centre(squad.players, league.ownership) if league else 0.5,
        )
        or None,
        horizon_len=horizon_len,
        signals=signals,
    )
    choice = None
    if league and not initial:
        try:
            choice = choose_for_league(
                league,
                world,
                dec.dists,
                dec.xp,
                dec.horizon,
                dec.plan,
                {"hold": dec.hold, **dec.alternatives},
            )
        except (ValueError, KeyError) as e:  # advice still works without it
            league_note = f"League odds skipped: {e}"
        if choice and choice.switched and choice.plan.buys != dec.plan.buys:
            restate_confidence(dec, choice.plan, world)
        if choice and choice.switched:
            dec.plan = choice.plan
    horizon, xp, plan, chips, chip_now = (
        dec.horizon,
        dec.xp,
        dec.plan,
        dec.chips,
        dec.chip_now,
    )

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
    advice.value_of_moves = dec.value_of_moves
    advice.p_beats_hold = dec.p_beats_hold
    advice.confidence = dec.confidence
    advice.move_confidence = dec.move_confidence
    advice.robust_note = dec.robust_note
    advice.league_choice = choice
    if (
        initial
    ):  # there's no existing squad, so the note about unseen transfers doesn't apply
        advice.notes.clear()
    if league_note:
        advice.notes.append(league_note)
    return advice


@dataclass
class Decision:
    """What the advisor decides for the next deadline, before any explanation is written."""

    window: list[int]  # gameweeks projected (to the last chip deadline)
    horizon: list[int]  # gameweeks the transfer plan looks ahead
    xp: dict[int, dict[int, float]]
    base: Plan  # the plan without a chip
    plan: Plan  # the plan to follow (with the chip, if one is played now)
    chips: list[ChipAdvice]
    chip_now: ChipAdvice | None
    # The plan's value minus the best plan that makes no move this week: what acting now is worth,
    # after hits and the free transfer it uses. None when there is nothing to compare.
    value_of_moves: float | None = None
    dists: dict[int, dict[int, PointsDist]] = field(default_factory=dict)
    # The best plan with no move this week (same chip), when it was solved.
    hold: Plan | None = None
    # Other plans considered this week, by name: "planner" (its first choice, when the
    # robustness rule turned it down) and "no_hit" (the best plan without a hit).
    alternatives: dict[str, Plan] = field(default_factory=dict)
    # P(the planner's chosen moves beat making none), when there were moves to test.
    p_beats_hold: float | None = None
    # P(the plan followed is the right call): p_beats_hold if it moves, 1 - that if it holds.
    confidence: float | None = None
    # Per move of the plan followed: P(the swap gains more than its share of any hit).
    move_confidence: list[robust.Confidence] = field(default_factory=list)
    # Why the planner's first choice was changed, in plain words (None when it wasn't).
    robust_note: str | None = None
    p_hit: float | None = None  # P(the plan with a hit beats the best plan without one)
    multiplier: dict[int, float] | None = None


def decide(
    world: World,
    squad: Squad,
    *,
    initial: bool,
    max_hits: int,
    multiplier: dict[int, float] | None = None,
    horizon_len: int = HORIZON,
    signals: Signals | None = None,
    ft_cap: int = R.MAX_FREE_TRANSFERS,
    keep_fts_on_chip: bool = True,
    compare_hold: bool = True,
) -> Decision:
    """Projections, transfers, chips, XI and captain for `world.next_event`.

    This is the whole decision path `build_advice` uses; scripts/season_sim.py replays past
    seasons through it, so a change here is measured on season totals. `ft_cap` and
    `keep_fts_on_chip` are the season's free-transfer rules (the sim replays older seasons).

    With `compare_hold`, this week's moves are stress-tested against forecast error (robust.py):
    a hit is only taken when the plan with it beats the best plan without one in at least
    `robust.HIT_CONFIDENCE` of cases, and moves are only made when they beat making none in at
    least `robust.MOVE_CONFIDENCE`.
    """
    ev = world.next_event
    last_gw = max(e.id for e in world.events)
    horizon = list(range(ev.id, min(last_gw, ev.id + horizon_len - 1) + 1))
    # Project a few weeks past the horizon too: the squad left at the end is valued on them.
    window = list(range(ev.id, min(last_gw, horizon[-1] + TERMINAL_LOOKAHEAD) + 1))
    dists = project_dist(world, window, signals=signals)
    xp = {pid: {gw: d.mean for gw, d in v.items()} for pid, v in dists.items()}
    after = window[len(horizon) :]
    terminal = (
        {p: sum(xp[p].get(g, 0.0) for g in after) / len(after) for p in xp}
        if after
        else None
    )

    kw = {
        "multiplier": multiplier,
        "max_hits": max_hits,
        "unlimited": initial,
        "ft_cap": ft_cap,
        "keep_fts_on_chip": keep_fts_on_chip,
        "terminal_xp": terminal,
    }
    if initial:  # a squad from scratch: pick it once and hold it (much faster to solve)
        kw["no_transfer_last"] = len(horizon) - 1

    def plan_with(**more) -> Plan:
        return solve(
            world,
            xp,
            horizon,
            squad.players,
            squad.sell_price,
            squad.bank,
            squad.free_transfers,
            **{**kw, **more},
        )

    base = plan_with()
    chip_kw = {k: v for k, v in kw.items() if k != "multiplier"}
    chips = (
        []
        if initial
        else advise_chips(
            world, xp, window, horizon, squad, base, multiplier, **chip_kw
        )
    )
    chip_now = next((c for c in chips if c.play_now), None)
    plan = chip_now.plan if chip_now and chip_now.plan is not None else base
    dec = Decision(
        window,
        horizon,
        xp,
        base,
        plan,
        chips,
        chip_now,
        dists=dists,
        multiplier=multiplier,
    )
    now_chip = plan.chip_now
    if not compare_hold or initial or not plan.buys or now_chip in (WILDCARD, FREE_HIT):
        return dec
    same_chip = {
        "triple_captain_gw": horizon[0] if now_chip == TRIPLE_CAPTAIN else None,
        "bench_boost_gw": horizon[0] if now_chip == BENCH_BOOST else None,
    }
    msd, msd_after = robust.mean_sds(dists, horizon, after)

    def load(p: Plan) -> dict[int, float]:
        return robust.loadings(p, horizon, msd, msd_after, multiplier, world=world)

    hold = plan_with(max_transfers=0, **same_chip)
    dec.hold = hold
    l_hold, l_plan = load(hold), load(plan)
    first = plan
    vs_hold = robust.compare(plan, hold, l_plan, l_hold)
    notes = []
    if plan.hits:
        no_hit = plan_with(max_transfers=squad.free_transfers, **same_chip)
        if no_hit.buys:
            dec.alternatives["no_hit"] = no_hit
            l_no_hit = load(no_hit)
            vs_no_hit = robust.compare(plan, no_hit, l_plan, l_no_hit)
            dec.p_hit = round(vs_no_hit.p, 3)
            if vs_no_hit.p < robust.HIT_CONFIDENCE:
                notes.append(
                    f"The best plan took a -{R.HIT_COST * plan.hits} hit, but it beat the best "
                    f"plan without one in only {vs_no_hit.p:.0%} of tests with the forecasts "
                    "nudged up and down, so the hit isn't worth the risk."
                )
                plan, l_plan = no_hit, l_no_hit
                vs_hold = robust.compare(plan, hold, l_plan, l_hold)
    if vs_hold.p < robust.MOVE_CONFIDENCE:
        notes.append(
            f"The best moves beat saving the transfer in only {vs_hold.p:.0%} of tests with the "
            "forecasts nudged up and down, so saving it is the safer choice."
        )
        plan = hold
    if plan is not first:
        dec.alternatives["planner"] = first
        dec.robust_note = " ".join(notes)
    dec.plan = plan
    if chip_now is not None and chip_now.plan is first:
        chip_now.plan = plan
    dec.p_beats_hold = round(vs_hold.p, 3)
    if plan is hold:
        dec.confidence = round(1 - vs_hold.p, 3)
    else:
        dec.confidence = dec.p_beats_hold
        dec.value_of_moves = round(plan.objective - hold.objective, 1)
    dec.move_confidence = move_confidences(plan, horizon, msd, multiplier)
    return dec


def restate_confidence(dec: Decision, plan: Plan, world: World) -> None:
    """Confidence for a different plan than `decide` chose (the league choice switched)."""
    if dec.hold is None:
        return
    after = dec.window[len(dec.horizon) :]
    msd, msd_after = robust.mean_sds(dec.dists, dec.horizon, after)

    def load(p: Plan) -> dict[int, float]:
        return robust.loadings(
            p, dec.horizon, msd, msd_after, dec.multiplier, world=world
        )

    hold = dec.hold
    if plan.buys:
        p = robust.compare(plan, hold, load(plan), load(hold)).p
        dec.p_beats_hold = dec.confidence = round(p, 3)
        dec.value_of_moves = round(plan.objective - hold.objective, 1)
    elif dec.p_beats_hold is not None:
        dec.confidence = round(1 - dec.p_beats_hold, 3)
        dec.value_of_moves = None
    dec.move_confidence = move_confidences(plan, dec.horizon, msd, dec.multiplier)


def move_confidences(
    plan: Plan,
    horizon: list[int],
    msd: dict[int, dict[int, float]],
    multiplier: dict[int, float] | None,
) -> list[robust.Confidence]:
    """Each of this week's moves: P(the swap gains more than its share of any hit)."""
    moves = plan.moves
    cost = R.HIT_COST * plan.hits / len(moves) if moves else 0.0
    return [
        robust.move_confidence(m, plan, horizon, msd, multiplier, cost=cost)
        for m in moves
    ]


# ------------------------------------------------------------------ explanations


def _pair_transfers(a: Advice) -> list[tuple[int, int]]:
    """(out, in) pairs as the plan made them: same position, dearest sale with dearest signing."""
    return [(m.out, m.inn) for m in plan_moves(a)]


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
    moves = plan_moves(a)
    later = pencilled_weeks(a)
    if a.chip_now and a.chip_now.chip in ("wildcard", "freehit"):
        lines.append(
            f"Play your **{R.CHIP_NAMES[a.chip_now.chip]}**: {n} changes, all free."
        )
    elif n == 0 and (a.robust_note or _league_reason_plan(a)):
        lines.append(
            f"**No transfers this week.** You have {ft} free transfer{'s' if ft != 1 else ''}. "
            + (_league_reason_plan(a) or a.robust_note or "")
        )
    elif n == 0:
        nxt = later[0] if later else None
        lines.append(
            f"**No transfers this week.** You have {ft} free transfer{'s' if ft != 1 else ''}. "
            f"Planning the next {len(a.horizon)} weeks together, no move now beats saving it"
            + (
                f": the plan uses it in GW{nxt.gw} instead (see below)."
                if nxt is not None
                else " for when injuries or fixture swings call for it."
            )
            + (
                " You're already at the maximum of 5, though, so one goes to waste if you don't use it."
                if ft >= R.MAX_FREE_TRANSFERS
                else ""
            )
        )
    else:
        hit = f", taking a -{R.HIT_COST * a.plan.hits} hit" if a.plan.hits else ""
        worth = (
            f" Doing them now rather than waiting is worth {a.value_of_moves:+.1f} points in the "
            "plan (after any hit, and counting the free transfer they use)."
            if a.value_of_moves is not None
            else ""
        )
        extra = " ".join(x for x in (a.robust_note, _league_reason_plan(a)) if x)
        lines.append(
            f"**Make {n} transfer{'s' if n > 1 else ''}** (you have {ft} free{hit}).{worth}"
            + (f" {extra}" if extra else "")
        )
    conf = confidence_sentence(a)
    if conf:
        lines.append(conf)
    mc = a.move_confidence if len(a.move_confidence) == len(moves) else []
    for k, m in enumerate(moves):
        po, pi = a.world.players[m.out], a.world.players[m.inn]
        sure = (
            f" How sure: {robust.level(mc[k].p)} ({mc[k].p:.0%} of tests with the forecasts "
            "nudged say this swap pays off)."
            if mc
            else ""
        )
        lines.append(
            f"- OUT **{po.name}** (£{m.sell_price:.1f}m), IN **{pi.name}** (£{pi.price:.1f}m) — "
            f"{m.gain:+.1f} expected points as the plan counts them ({_money(m.money)}).{sure}\n"
            f"  - Why sell: {_why_out(a, m.out)}.\n"
            f"  - Why {pi.name}: {a.horizon_xp(m.inn):.1f} expected points over {len(a.horizon)} "
            f"weeks, fixtures {a.fixtures(m.inn)}"
            + (
                f", owned by {a.league.ownership.get(m.inn, 0):.0%} of your rivals"
                if a.league
                else ""
            )
            + "".join(f"; {e}" for e in evidence(a, m.inn))
            + "."
        )
    if a.plan.hits:
        lines.append(
            f"About the hit: each extra transfer costs {R.HIT_COST} points, and a move can usually wait "
            "for next week's free transfer. The plan takes the hit only because these moves gain "
            f"{sum(m.gain for m in moves):+.1f} points (weighted, over the weeks it keeps the new players) "
            "and waiting would lose more than the hit costs."
        )
    lines.append(f"Money in the bank afterwards: £{a.plan.bank_after:.1f}m.")
    if later:
        lines.append(
            "Pencilled in for later (the plan is made again each week, so these may change):"
        )
        for wk in later:
            lines.append(f"- {_week_text(a, wk)}")
    return lines


def _league_reason_plan(a: Advice) -> str | None:
    c = a.league_choice
    return c.reason_plan if c and c.switched else None


def transfer_confidence(a: Advice) -> dict | None:
    """{"level": "strong" | "likely" | "close call", "p_beats_hold": P(moves beat holding)}.

    The level is about the advice given: moving when it says move, holding when it says hold.
    None when there was nothing to compare (no move worth testing, a squad from scratch, or a
    Wildcard / Free Hit week).
    """
    if a.initial or a.confidence is None or a.p_beats_hold is None:
        return None
    return {"level": robust.level(a.confidence), "p_beats_hold": a.p_beats_hold}


def confidence_sentence(a: Advice) -> str | None:
    c = transfer_confidence(a)
    if not c:
        return None
    p = c["p_beats_hold"]
    if a.plan.buys:
        return (
            f"How sure: **{c['level']}**. We re-tested the plan with every player's expected "
            f"points nudged up or down by how unsure they are; these moves beat saving the "
            f"transfer in {p:.0%} of the tests."
        )
    return (
        f"How sure: **{c['level']}**. With every player's expected points nudged up or down by "
        f"how unsure they are, the best moves beat saving the transfer in only {p:.0%} of the tests."
    )


def _money(delta: float) -> str:
    if abs(delta) < 0.05:
        return "no money change"
    return f"frees £{delta:.1f}m" if delta > 0 else f"costs £{-delta:.1f}m"


def plan_moves(a: Advice) -> list:
    """This week's moves, paired the way the plan made them."""
    return list(getattr(a.plan, "moves", None) or [])


def pencilled_weeks(a: Advice) -> list:
    """Later weeks of the plan with a move or a chip in them."""
    weeks = getattr(a.plan, "weeks", None) or []
    return [w for w in weeks[1:] if w.moves or w.chip]


def _week_text(a: Advice, wk) -> str:
    parts = []
    if wk.chip:
        parts.append(f"play {R.CHIP_NAMES[wk.chip]}")
    for m in wk.moves:
        parts.append(f"{a.name(m.out)} → {a.name(m.inn)} ({m.gain:+.1f})")
    hit = f", -{R.HIT_COST * wk.hits} hit" if wk.hits else ""
    ft = f" ({wk.free_transfers} free transfer{'s' if wk.free_transfers != 1 else ''}{hit})"
    return f"GW{wk.gw}{ft if wk.moves else ''}: " + "; ".join(parts) + "."


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
    reason = captain_reason_league(a)
    if reason:
        lines.append(f"**For your league:** {reason}")
    return lines


def captain_reason_league(a: Advice) -> str | None:
    """Why the captain differs from the expected-points pick, when the league choice changed it."""
    c = a.league_choice
    return c.reason_captain if c and c.switched else None


def league_win_odds(a: Advice) -> dict | None:
    """{"me": P(you finish 1st), "rows": [{"entry", "name", "p_first"}], "sentence"}."""
    c = a.league_choice
    if not c:
        return None
    o = c.odds
    rows = sorted(
        (
            {"entry": int(k), "name": o.labels[k], "p_first": round(o.p_first[k], 3)}
            for k in o.keys
        ),
        key=lambda r: -r["p_first"],
    )
    return {
        "me": round(o.p_first[c.me], 3),
        "rows": rows,
        "sentence": describe_odds(o, c.me),
    }


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
    odds = league_win_odds(a)
    if odds:
        lines.append(f"**Chance of winning:** {odds['sentence']}")
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

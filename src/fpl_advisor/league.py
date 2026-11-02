"""Mini-league view: rivals, how many of them own each player, a points target and the risk level.

The risk level, lambda, adjusts how much the optimiser favours players your rivals don't own. When you
are chasing, players few rivals own are worth more to you: if they score, you gain on everyone. When you
lead, players most rivals own are safer: if they score, nobody gains on you.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .api import FPLClient, FPLError
from .world import World

GW_DIFF_SD = 14.0  # typical spread of one gameweek's points gap between two managers
RISK_PRESETS = {"low": -0.05, "medium": 0.05, "high": 0.2}
MAX_RIVALS = 30
PAGE_SIZE = 50  # managers per standings page
LONG_RUN_AVG = 52.0  # typical average FPL score per gameweek over a full season
EDGE_SHRINK_GWS = 15  # how quickly a manager's early edge over the average is trusted


class LeagueError(ValueError):
    pass


@dataclass
class Manager:
    entry: int
    team_name: str
    manager: str
    rank: int
    total: int
    event_total: int
    picks: list[int] = field(default_factory=list)
    captain: int | None = None
    chips_used: list[tuple[str, int]] = field(default_factory=list)
    last_season_total: int | None = None
    gws_played: int = 0
    projected_final: float = 0.0


@dataclass
class LeagueView:
    name: str
    me: Manager
    rivals: list[Manager]  # everyone tracked except me, sorted by rank
    ownership: dict[int, float]  # player id -> share of rivals owning
    captaincy: dict[
        int, float
    ]  # player id -> share of rivals captaining (last gameweek)
    remaining_gws: int
    target: int
    required_per_gw: float
    last_season_best: tuple[str, int] | None
    risk_lambda: float
    risk_reason: str
    total_managers: int | None = (
        None  # everyone in the league; rivals is only those tracked
    )

    @property
    def leader(self) -> Manager:
        return min([self.me, *self.rivals], key=lambda m: m.rank)


def risk_lambda(
    my_total: int, totals_others: list[int], remaining: int, setting: str = "auto"
) -> tuple[float, str]:
    if setting in RISK_PRESETS:
        return RISK_PRESETS[setting], f"Risk set to '{setting}' in your config."
    if not totals_others or remaining <= 0:
        return 0.0, "No rivals to measure against, so picks are judged on points alone."
    spread = GW_DIFF_SD * math.sqrt(remaining)
    best_other = max(totals_others)
    if best_other > my_total:
        gap = best_other - my_total
        z = gap / spread
        # Gentle by design: a gap of one typical swing gives 0.14 (a 0% owned player's points count
        # 14% more, a 100% owned player's 14% less); it never goes above 0.2.
        lam = min(0.2, 0.02 + 0.12 * z)
        how = (
            "leans slightly toward"
            if lam < 0.08
            else "moderately favours"
            if lam < 0.15
            else "strongly favours"
        )
        return lam, (
            f"You are {gap} points behind the leader with {remaining} gameweeks left. That gap is "
            f"{z:.1f} times a typical swing over that period. The advisor {how} players your rivals don't own, "
            "because you only catch up when your players outscore theirs."
        )
    margin = my_total - best_other
    z = margin / spread
    lam = max(-0.12, 0.02 - 0.12 * z)
    return lam, (
        f"You lead by {margin} points with {remaining} gameweeks left. The advisor "
        + (
            "leans toward players your rivals own, so their big weeks are your big weeks too."
            if lam < 0
            else "stays close to neutral: the lead is small, so you still need to score well."
        )
    )


def ownership_multiplier(
    player_ids, ownership: dict[int, float], lam: float, centre: float = 0.5
) -> dict[int, float]:
    """Weight on each player's expected points: 1 + 2 * lambda * (centre - rival ownership).

    It tilts the plan toward players rivals don't own (lambda > 0) or do own (lambda < 0). It is
    meant to change which players the plan prefers, not how many points the plan expects overall:
    pass `centre` = the average rival ownership of your own squad, so your current players weigh 1
    on average and a hit (-4) or a saved transfer is still judged against real expected points.
    With no rival data there is nothing to tilt toward, so no multiplier at all.
    """
    if not lam or not ownership:
        return {}
    return {pid: 1 + 2 * lam * (centre - ownership.get(pid, 0.0)) for pid in player_ids}


def ownership_centre(squad: list[int], ownership: dict[int, float]) -> float:
    """Average rival ownership of a squad: the point where the multiplier is 1."""
    if not squad:
        return 0.5
    return sum(ownership.get(p, 0.0) for p in squad) / len(squad)


def _select_rivals(results: list[dict], my_entry: int) -> list[dict]:
    if len(results) <= MAX_RIVALS:
        return results
    ranked = sorted(results, key=lambda r: r["rank"])
    mine = next((i for i, r in enumerate(ranked) if r["entry"] == my_entry), 0)
    keep = ranked[:10] + ranked[max(0, mine - 10) : mine + 5]
    seen, out = set(), []
    for r in keep:
        if r["entry"] not in seen:
            seen.add(r["entry"])
            out.append(r)
    return out


def load_league(
    client: FPLClient,
    world: World,
    league_id: int,
    my_entry: int,
    risk_setting: str = "auto",
) -> LeagueView:
    first = client.league(league_id)
    results = list(first["standings"]["results"])
    my_league = next(
        (
            lg
            for lg in client.entry(my_entry).get("leagues", {}).get("classic", [])
            if lg.get("id") == league_id
        ),
        {},
    )
    total_managers = my_league.get("rank_count")
    if not any(r["entry"] == my_entry for r in results):
        # Big league: jump straight to the user's page (and the one above) instead of paging.
        my_rank = my_league.get("entry_rank")
        if my_rank:
            page = (my_rank - 1) // PAGE_SIZE + 1
            for p in sorted({max(2, page - 1), page}):
                results.extend(client.league(league_id, p)["standings"]["results"])
    if not any(r["entry"] == my_entry for r in results):
        raise LeagueError(
            f"Team {my_entry} isn't in league {league_id}'s standings yet "
            "(new members appear after the next deadline)."
        )

    last_event = max(
        (e.id for e in world.events if e.finished or e.is_current), default=1
    )
    last_season = _previous_season_name(world)
    managers = []
    for r in _select_rivals(results, my_entry):
        m = Manager(
            entry=r["entry"],
            team_name=r["entry_name"],
            manager=r["player_name"],
            rank=r["rank"],
            total=r["total"],
            event_total=r["event_total"],
        )
        hist = client.history(m.entry)
        m.chips_used = [(c["name"], c["event"]) for c in hist["chips"]]
        m.gws_played = len(hist["current"])
        past = next((p for p in hist["past"] if p["season_name"] == last_season), None)
        m.last_season_total = past["total_points"] if past else None
        try:
            picks = client.picks(m.entry, last_event)
            m.picks = [p["element"] for p in picks["picks"]]
            m.captain = next(
                (p["element"] for p in picks["picks"] if p["is_captain"]), None
            )
        except FPLError:  # a manager who joined after that gameweek has no picks yet
            m.picks, m.captain = [], None
        managers.append(m)

    me = next(m for m in managers if m.entry == my_entry)
    rivals = sorted((m for m in managers if m.entry != my_entry), key=lambda m: m.rank)
    n = max(1, len(rivals))
    ownership: dict[int, float] = {}
    captaincy: dict[int, float] = {}
    for m in rivals:
        for pid in m.picks:
            ownership[pid] = ownership.get(pid, 0) + 1 / n
        if m.captain:
            captaincy[m.captain] = captaincy.get(m.captain, 0) + 1 / n

    remaining = 38 - last_event
    # Future weeks: the average FPL score (this season's, eased toward a typical season), plus
    # each manager's edge over that average, shrunk because hot and cold starts fade.
    finished = [e.average_score for e in world.events if e.finished and e.average_score]
    season_avg = sum(finished) / len(finished) if finished else LONG_RUN_AVG
    future_avg = (season_avg * len(finished) + LONG_RUN_AVG * EDGE_SHRINK_GWS) / (
        len(finished) + EDGE_SHRINK_GWS
    )
    for m in managers:
        g = max(1, m.gws_played)
        edge = (m.total / g - season_avg) * g / (g + EDGE_SHRINK_GWS)
        m.projected_final = m.total + (future_avg + edge) * remaining
    best_rival = max((m.projected_final for m in rivals), default=me.projected_final)
    target = int(round(best_rival / 10) * 10) + 10
    required = (target - me.total) / remaining if remaining else 0.0

    known = [(m.manager, m.last_season_total) for m in managers if m.last_season_total]
    last_best = max(known, key=lambda t: t[1]) if known else None
    lam, why = risk_lambda(me.total, [m.total for m in rivals], remaining, risk_setting)
    return LeagueView(
        name=first["league"]["name"],
        me=me,
        rivals=rivals,
        ownership=ownership,
        captaincy=captaincy,
        remaining_gws=remaining,
        target=target,
        required_per_gw=required,
        last_season_best=last_best,
        risk_lambda=lam,
        risk_reason=why,
        total_managers=total_managers or len(managers),
    )


def _previous_season_name(world: World) -> str:
    first_deadline = min(e.deadline for e in world.events)
    start_year = (
        first_deadline.year if first_deadline.month >= 7 else first_deadline.year - 1
    )
    return f"{start_year - 1}/{str(start_year)[-2:]}"

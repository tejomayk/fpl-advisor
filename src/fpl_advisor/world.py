"""A snapshot of the FPL world: players, teams, fixtures and per-match histories.

The same structure serves live advice and backtests. `World.as_of(gw)` hides everything from
gameweek `gw` onward, so a backtest only sees what was known before that deadline.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime

from .api import FPLClient


@dataclass
class Player:
    id: int
    name: str
    full_name: str
    team: int
    pos: int
    price: float  # current price in £m
    status: str = "a"  # a=available, d=doubtful, i=injured, s=suspended, u=unavailable, n=not in squad
    chance_next: int | None = None
    news: str = ""
    selected_by: float = 0.0
    price_start: float = 0.0  # price at the start of the season
    price_projection: list = field(
        default_factory=list
    )  # FPL's own price-change projections
    transfers_in_event: int = 0
    transfers_out_event: int = 0
    ep_next: float = 0.0  # FPL's own expected points (shown for comparison only)


@dataclass
class Team:
    id: int
    name: str
    short: str


@dataclass
class Fixture:
    id: int
    event: int | None
    home: int
    away: int
    kickoff: str | None
    finished: bool
    home_goals: int | None = None
    away_goals: int | None = None


@dataclass
class Event:
    id: int
    deadline: datetime
    finished: bool
    is_current: bool
    is_next: bool
    average_score: int | None = None


@dataclass
class World:
    players: dict[int, Player]
    teams: dict[int, Team]
    fixtures: list[Fixture]
    events: list[Event]
    history: dict[int, list[dict]]  # player id -> per-match rows (this season)
    past: dict[int, dict]  # player id -> last season's totals
    chips: list[dict]  # chip windows from bootstrap
    total_managers: int = 0
    cutoff: int | None = None  # if set, only data before this gameweek is visible

    @property
    def next_event(self) -> Event | None:
        if self.cutoff is not None:
            return next((e for e in self.events if e.id == self.cutoff), None)
        return next((e for e in self.events if e.is_next), None)

    def as_of(self, gw: int) -> World:
        """A view of the world as it was just before gameweek `gw`'s deadline."""
        history = {
            pid: [r for r in rows if r["round"] < gw]
            for pid, rows in self.history.items()
        }
        fixtures = [
            f
            if (f.event or 99) < gw
            else replace(f, finished=False, home_goals=None, away_goals=None)
            for f in self.fixtures
        ]
        return replace(self, history=history, fixtures=fixtures, cutoff=gw)

    def fixtures_in(self, gw: int) -> list[Fixture]:
        return [f for f in self.fixtures if f.event == gw]

    def finished_fixtures(self) -> list[Fixture]:
        return [f for f in self.fixtures if f.finished]

    # ------------------------------------------------------------ snapshot
    # The refresh job saves the world it loaded, so a web function can start from it instead of
    # fetching ~700 FPL endpoints on a cold start. JSON keys are strings; ids come back as ints.

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str | bytes) -> World:
        """Inverse of `to_json`. Raises ValueError on anything that isn't a valid snapshot."""
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict:
        return {
            "version": SNAPSHOT_VERSION,
            "players": [asdict(p) for p in self.players.values()],
            "teams": [asdict(t) for t in self.teams.values()],
            "fixtures": [asdict(f) for f in self.fixtures],
            "events": [
                {**asdict(e), "deadline": e.deadline.isoformat()} for e in self.events
            ],
            "history": {str(k): v for k, v in self.history.items()},
            "past": {str(k): v for k, v in self.past.items()},
            "chips": self.chips,
            "total_managers": self.total_managers,
            "cutoff": self.cutoff,
        }

    @classmethod
    def from_dict(cls, d: dict) -> World:
        try:
            if d.get("version") != SNAPSHOT_VERSION:
                raise ValueError(f"unknown world snapshot version {d.get('version')!r}")
            return cls(
                players={p["id"]: Player(**_known(Player, p)) for p in d["players"]},
                teams={t["id"]: Team(**_known(Team, t)) for t in d["teams"]},
                fixtures=[Fixture(**_known(Fixture, f)) for f in d["fixtures"]],
                events=[
                    Event(
                        **{
                            **_known(Event, e),
                            "deadline": datetime.fromisoformat(e["deadline"]),
                        }
                    )
                    for e in d["events"]
                ],
                history={int(k): v for k, v in d["history"].items()},
                past={int(k): v for k, v in d["past"].items()},
                chips=d["chips"],
                total_managers=d.get("total_managers", 0),
                cutoff=d.get("cutoff"),
            )
        except (KeyError, TypeError, AttributeError) as e:
            raise ValueError(f"not a valid world snapshot: {e}") from e


SNAPSHOT_VERSION = 1


def _known(cls, d: dict) -> dict:
    """Only the fields `cls` has, so a snapshot from a newer version still loads."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def load_world(
    client: FPLClient, with_history: bool = True, extra_ids: set[int] | None = None
) -> World:
    boot = client.bootstrap()
    players = {}
    for e in boot["elements"]:
        players[e["id"]] = Player(
            id=e["id"],
            name=e["web_name"],
            full_name=f"{e['first_name']} {e['second_name']}",
            team=e["team"],
            pos=e["element_type"],
            price=e["now_cost"] / 10,
            status=e["status"],
            chance_next=e["chance_of_playing_next_round"],
            news=e["news"] or "",
            selected_by=_f(e["selected_by_percent"]),
            price_start=(e["now_cost"] - e["cost_change_start"]) / 10,
            price_projection=e.get("price_change_projections") or [],
            transfers_in_event=e["transfers_in_event"],
            transfers_out_event=e["transfers_out_event"],
            ep_next=_f(e["ep_next"]),
        )
    teams = {t["id"]: Team(t["id"], t["name"], t["short_name"]) for t in boot["teams"]}
    events = [
        Event(
            id=ev["id"],
            deadline=datetime.fromisoformat(ev["deadline_time"]),
            finished=ev["finished"],
            is_current=ev["is_current"],
            is_next=ev["is_next"],
            average_score=ev.get("average_entry_score"),
        )
        for ev in boot["events"]
    ]
    fixtures = [
        Fixture(
            id=f["id"],
            event=f["event"],
            home=f["team_h"],
            away=f["team_a"],
            kickoff=f["kickoff_time"],
            finished=f["finished"],
            home_goals=f["team_h_score"],
            away_goals=f["team_a_score"],
        )
        for f in client.fixtures()
    ]
    history: dict[int, list[dict]] = {}
    past: dict[int, dict] = {}
    if with_history:
        # Players who have never played this season contribute nothing to team stats; skip them
        # unless asked for (e.g. they are in the user's squad or a new signing worth projecting).
        # Before the first match, load everyone: last season's data is all there is.
        season_started = any(ev["finished"] for ev in boot["events"])
        wanted = {
            e["id"] for e in boot["elements"] if e["minutes"] > 0 or not season_started
        } | (extra_ids or set())
        for pid, summary in client.element_summaries(sorted(wanted)).items():
            history[pid] = summary["history"]
            if summary["history_past"]:
                past[pid] = summary["history_past"][-1]
    return World(
        players=players,
        teams=teams,
        fixtures=fixtures,
        events=events,
        history=history,
        past=past,
        chips=boot.get("chips", []),
        total_managers=boot.get("total_players", 0),
    )

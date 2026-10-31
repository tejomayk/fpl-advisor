"""Outside signals that sharpen the model: bookmaker odds, team news and what the elite do.

Each source fills in its own fields and leaves the rest as None. Every source is optional: with
no keys configured, `Signals()` is empty and the model runs on FPL data alone.

Sources write here:
- odds.py   -> FixtureSignal (goal expectancy per fixture) and PlayerSignal.p_goal / p_assist
- news.py   -> PlayerSignal.p_available / p_start / news / news_source
- elite.py  -> PlayerSignal.elite_* and Signals.elite
- creators.py -> Signals.creators (what big FPL YouTube creators plan; shown only, never fed
                 into the projection model)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class PlayerSignal:
    # Bookmakers (vig removed), for the player's next fixture only.
    p_goal: float | None = None  # chance to score at least once
    p_assist: float | None = None  # chance of at least one assist
    # Team news (press conferences, reports), for the next gameweek only.
    p_available: float | None = None  # chance of being in the squad at all
    p_start: float | None = None  # chance of starting, given available
    news: str = ""  # one plain sentence, shown to users
    news_source: str = ""  # URL the sentence came from
    # Consistently good managers ("the elite"), from their last public picks.
    elite_own: float | None = None  # share of the elite who own the player (0-1)
    elite_captain: float | None = None  # share who captained the player last gameweek
    elite_in: int | None = None  # elite managers who bought the player last gameweek
    elite_out: int | None = None  # elite managers who sold the player last gameweek


@dataclass
class FixtureSignal:
    lam_home: float  # expected goals for the home side implied by the odds
    lam_away: float
    source: str = "odds"


@dataclass
class EliteSummary:
    managers: int  # how many elite managers were read
    gameweek: int  # gameweek their picks are from (picks are public after the deadline)
    seasons: list[
        str
    ]  # seasons used to pick them, e.g. ["2023/24", "2024/25", "2025/26"]
    median_rank: float | None = None  # median of their median ranks, for display


@dataclass
class Signals:
    players: dict[int, PlayerSignal] = field(default_factory=dict)
    fixtures: dict[int, FixtureSignal] = field(
        default_factory=dict
    )  # by FPL fixture id
    elite: EliteSummary | None = None
    updated: dict[str, str] = field(default_factory=dict)  # source -> ISO time fetched
    gameweek: int | None = None  # the gameweek odds and news describe (None = unknown)
    # The "Creator room" block (creators.build_room); informational only, not used by the model.
    creators: dict | None = None

    def player(self, pid: int) -> PlayerSignal:
        return self.players.setdefault(pid, PlayerSignal())

    def merge(self, other: Signals) -> Signals:
        """Combine two partial Signals; fields set in `other` win."""
        for pid, sig in other.players.items():
            mine = self.player(pid)
            for f in fields(PlayerSignal):
                v = getattr(sig, f.name)
                if v not in (None, ""):
                    setattr(mine, f.name, v)
        self.fixtures.update(other.fixtures)
        if other.elite:
            self.elite = other.elite
        self.updated.update(other.updated)
        if other.gameweek is not None:
            self.gameweek = other.gameweek
        if other.creators is not None:
            self.creators = other.creators
        return self

    # Stored as JSON in the data dir so the web server and CLI share one copy per source.
    def to_json(self) -> str:
        return json.dumps(
            {
                "players": {str(k): asdict(v) for k, v in self.players.items()},
                "fixtures": {str(k): asdict(v) for k, v in self.fixtures.items()},
                "elite": asdict(self.elite) if self.elite else None,
                "updated": self.updated,
                "gameweek": self.gameweek,
                "creators": self.creators,
            }
        )

    @classmethod
    def from_json(cls, text: str) -> Signals:
        d = json.loads(text)
        known = {f.name for f in fields(PlayerSignal)}
        return cls(
            players={
                int(k): PlayerSignal(**{kk: vv for kk, vv in v.items() if kk in known})
                for k, v in d.get("players", {}).items()
            },
            fixtures={
                int(k): FixtureSignal(**v) for k, v in d.get("fixtures", {}).items()
            },
            elite=EliteSummary(**d["elite"]) if d.get("elite") else None,
            updated=d.get("updated", {}),
            gameweek=d.get("gameweek"),
            creators=d.get("creators") if isinstance(d.get("creators"), dict) else None,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.to_json())
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> Signals:
        try:
            return cls.from_json(path.read_text())
        except (OSError, ValueError, TypeError, KeyError):
            return cls()

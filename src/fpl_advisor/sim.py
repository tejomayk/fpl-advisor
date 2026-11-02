"""Replay past seasons: game rules for each season, the manager's bookkeeping, and scoring.

scripts/season_sim.py drives the replay. This module holds the parts worth testing on their own:
free-transfer accrual, selling prices and bank, auto-substitutions and the vice-captain fallback,
plus the mapping from the public vaastav/Fantasy-Premier-League CSVs into a `World`.

Outcomes are scored with each player's real `total_points` for the gameweek, so scoring is exact
for every season even though rules.py (used for projections) follows the current rules.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from . import rules as R
from .squad import STARTING_BUDGET, selling_price
from .world import Event, Fixture, Player, Team, World

# ------------------------------------------------------------------ season rules


def _windows(*spans: tuple[str, int, int]) -> list[dict]:
    return [{"name": n, "start_event": a, "stop_event": b} for n, a, b in spans]


@dataclass(frozen=True)
class SeasonRules:
    season: str
    max_free_transfers: int
    # From 2024/25, free transfers banked before a Wildcard or Free Hit are kept (no new one is
    # added that week). Before that, the next gameweek started again from 1.
    keep_fts_on_chip: bool
    chip_windows: list[dict]
    ft_topup: dict[int, int] = field(default_factory=dict)  # gw -> topped up to N
    notes: tuple[str, ...] = ()


SEASONS: dict[str, SeasonRules] = {
    "2023-24": SeasonRules(
        "2023-24",
        max_free_transfers=2,
        keep_fts_on_chip=False,
        chip_windows=_windows(
            ("wildcard", 2, 19),
            ("wildcard", 20, 38),
            ("freehit", 2, 38),
            ("bboost", 1, 38),
            ("3xc", 1, 38),
        ),
        notes=(
            "At most 2 free transfers could be banked; the optimiser still values rolling up to 5.",
            "No defensive contribution points; history has none, so none are projected.",
        ),
    ),
    "2024-25": SeasonRules(
        "2024-25",
        max_free_transfers=5,
        keep_fts_on_chip=True,
        chip_windows=_windows(
            ("wildcard", 2, 19),
            ("wildcard", 20, 38),
            ("freehit", 2, 38),
            ("bboost", 1, 38),
            ("3xc", 1, 38),
        ),
        notes=(
            "Assistant Manager chip (GW24+) is not modelled by the advisor and is never played.",
            "No defensive contribution points; history has none, so none are projected.",
        ),
    ),
    "2025-26": SeasonRules(
        "2025-26",
        max_free_transfers=5,
        keep_fts_on_chip=True,
        chip_windows=_windows(
            ("wildcard", 2, 19),
            ("freehit", 2, 19),
            ("bboost", 1, 19),
            ("3xc", 1, 19),
            ("wildcard", 20, 38),
            ("freehit", 20, 38),
            ("bboost", 20, 38),
            ("3xc", 20, 38),
        ),
        # AFCON: every manager was topped up to 5 free transfers for GW16.
        ft_topup={16: 5},
        notes=(
            (
                "Last season's defensive contributions are unknown (2024/25 data has none), so "
                "the defensive-contribution prior starts at zero until this season builds it up."
            ),
        ),
    ),
}


def next_free_transfers(
    ft: int, used: int, chip: str | None, rules: SeasonRules, next_gw: int
) -> int:
    """Free transfers for gameweek `next_gw`, after using `used` of `ft` (and maybe a chip)."""
    if chip in ("wildcard", "freehit"):
        nxt = ft if rules.keep_fts_on_chip else 1
    else:
        nxt = min(rules.max_free_transfers, max(ft - used, 0) + 1)
    return max(nxt, rules.ft_topup.get(next_gw, 0))


def hits_for(n_transfers: int, ft: int, chip: str | None) -> int:
    if chip in ("wildcard", "freehit"):
        return 0
    return max(0, n_transfers - ft)


# ------------------------------------------------------------------ manager bookkeeping


@dataclass
class Manager:
    players: list[int]
    purchase_price: dict[int, float]
    bank: float = STARTING_BUDGET
    free_transfers: int = 1
    chips_used: list[tuple[str, int]] = field(default_factory=list)

    def sell_prices(self, price_now: dict[int, float]) -> dict[int, float]:
        return {
            p: selling_price(self.purchase_price[p], price_now[p]) for p in self.players
        }


def apply_transfers(
    m: Manager, buys: list[int], sells: list[int], price_now: dict[int, float]
) -> Manager:
    """A new manager state after selling `sells` (at selling price) and buying `buys` (at today's price)."""
    if len(buys) != len(sells):
        raise ValueError("buys and sells must match one for one")
    missing = set(sells) - set(m.players)
    if missing:
        raise ValueError(f"selling players not owned: {sorted(missing)}")
    sell = m.sell_prices(price_now)
    bank = m.bank + sum(sell[p] for p in sells) - sum(price_now[p] for p in buys)
    bank = round(bank, 1)
    if bank < -1e-9:
        raise ValueError(f"transfers overspend the bank: {bank:.1f}")
    keep = [p for p in m.players if p not in sells]
    purchase = {p: m.purchase_price[p] for p in keep}
    purchase.update({p: price_now[p] for p in buys})
    return replace(m, players=keep + list(buys), purchase_price=purchase, bank=bank)


# ------------------------------------------------------------------ scoring


@dataclass
class GWScore:
    points: int  # before hits
    counted: list[int]  # players whose points counted
    subs: list[tuple[int, int]]  # (out, in)
    multiplier_on: int | None  # player whose points were multiplied


def _formation_ok(players: list[int], pos: dict[int, int]) -> bool:
    counts = {p: 0 for p in R.POS_NAME}
    for p in players:
        counts[pos[p]] += 1
    return all(counts[p] >= R.XI_MIN[p] for p in R.POS_NAME) and counts[R.GKP] == 1


def auto_subs(
    xi: list[int], bench: list[int], minutes: dict[int, int], pos: dict[int, int]
) -> tuple[list[int], list[tuple[int, int]]]:
    """FPL automatic substitutions.

    Starters who played 0 minutes are replaced in XI order by the first bench player (in bench
    order) who played and keeps a valid formation. Only the bench goalkeeper can replace the
    goalkeeper, and he can replace no one else.
    """
    final = list(xi)
    used: set[int] = set()
    subs = []
    for out in xi:
        if minutes.get(out, 0) > 0:
            continue
        for b in bench:
            if b in used or minutes.get(b, 0) <= 0:
                continue
            if (pos[out] == R.GKP) != (pos[b] == R.GKP):
                continue
            trial = [b if p == out else p for p in final]
            if _formation_ok(trial, pos):
                final = trial
                used.add(b)
                subs.append((out, b))
                break
    return final, subs


def score_gameweek(
    xi: list[int],
    bench: list[int],
    captain: int,
    vice: int,
    points: dict[int, int],
    minutes: dict[int, int],
    pos: dict[int, int],
    chip: str | None = None,
) -> GWScore:
    """Points for one gameweek with auto-subs, captain/vice and Bench Boost/Triple Captain."""
    if chip == "bboost":
        counted, subs = list(xi) + list(bench), []
    else:
        counted, subs = auto_subs(xi, bench, minutes, pos)
    mult = 3 if chip == "3xc" else 2
    if minutes.get(captain, 0) > 0 and captain in counted:
        star = captain
    elif minutes.get(vice, 0) > 0 and vice in counted:
        star = vice
    else:
        star = None
    total = sum(points.get(p, 0) for p in counted)
    if star is not None:
        total += (mult - 1) * points.get(star, 0)
    return GWScore(total, counted, subs, star)


# ------------------------------------------------------------------ vaastav data -> World

POSITIONS = {"GK": R.GKP, "GKP": R.GKP, "DEF": R.DEF, "MID": R.MID, "FWD": R.FWD}
HISTORY_FIELDS_FLOAT = ("expected_goals", "expected_assists")
HISTORY_FIELDS_INT = (
    "minutes",
    "starts",
    "saves",
    "bonus",
    "yellow_cards",
    "total_points",
    "value",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
)
PAST_FIELDS = (
    "minutes",
    "starts",
    "expected_goals",
    "expected_assists",
    "saves",
    "bonus",
    "yellow_cards",
    "total_points",
    "defensive_contribution",
)


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _num(x, cast=float):
    try:
        return cast(float(x))
    except (TypeError, ValueError):
        return cast(0)


@dataclass
class SeasonData:
    """A full past season, from which `world_at(gw)` gives the view before gameweek `gw`."""

    name: str
    world: (
        World  # everything, including results; never hand this to the advisor directly
    )
    price: dict[int, dict[int, float]]  # pid -> gw -> price that gameweek
    team_at: dict[
        int, dict[int, int]
    ]  # pid -> gw -> club (players can move in January)
    active: dict[int, tuple[int, int]]  # pid -> (first, last) gameweek in the game
    points: dict[
        int, dict[int, int]
    ]  # pid -> gw -> actual points (summed over a double)
    minutes: dict[int, dict[int, int]]
    has_defensive_contribution: bool
    has_xg: bool

    @property
    def gameweeks(self) -> list[int]:
        return sorted(e.id for e in self.world.events)

    def _at(self, table: dict[int, float | int], gw: int):
        """Value at `gw`, else the latest before it, else the earliest after it."""
        before = [g for g in table if g <= gw]
        if before:
            return table[max(before)]
        return table[min(table)]

    def prices_at(self, gw: int) -> dict[int, float]:
        return {pid: self._at(t, gw) for pid, t in self.price.items()}

    def world_at(self, gw: int) -> World:
        """The world just before gameweek `gw`'s deadline: prices then, results before it only.

        There is no historical injury news, so every player in the game is listed as available;
        players not yet in the game (or gone from it) are 'u' and can't be bought.
        """
        players = {}
        for pid, p in self.world.players.items():
            first, last = self.active.get(pid, (99, -1))
            players[pid] = replace(
                p,
                price=self._at(self.price[pid], gw) if pid in self.price else p.price,
                team=self._at(self.team_at[pid], gw) if pid in self.team_at else p.team,
                status="a" if first <= gw <= last else "u",
            )
        return replace(self.world, players=players).as_of(gw)

    def actual(self, gw: int) -> tuple[dict[int, int], dict[int, int]]:
        pts = {pid: t[gw] for pid, t in self.points.items() if gw in t}
        mins = {pid: t[gw] for pid, t in self.minutes.items() if gw in t}
        return pts, mins


def load_season(
    name: str, season_dir: Path, prev_dir: Path | None = None
) -> SeasonData:
    """Map one season of the vaastav dataset into our structures.

    `prev_dir` (last season) supplies `World.past`, matched across seasons by the stable player `code`.
    """
    teams = {
        int(t["id"]): Team(int(t["id"]), t["name"], t["short_name"])
        for t in _read(season_dir / "teams.csv")
    }
    fixtures = []
    by_id: dict[int, Fixture] = {}
    kickoffs: dict[int, list[datetime]] = {}
    for f in _read(season_dir / "fixtures.csv"):
        if not f["event"]:
            continue
        ev = int(f["event"])
        fx = Fixture(
            id=int(f["id"]),
            event=ev,
            home=int(f["team_h"]),
            away=int(f["team_a"]),
            kickoff=f["kickoff_time"] or None,
            finished=f["finished"] == "True",
            home_goals=_num(f["team_h_score"], int) if f["team_h_score"] else None,
            away_goals=_num(f["team_a_score"], int) if f["team_a_score"] else None,
        )
        fixtures.append(fx)
        by_id[fx.id] = fx
        if fx.kickoff:
            kickoffs.setdefault(ev, []).append(datetime.fromisoformat(fx.kickoff))
    events = [
        Event(
            id=g,
            deadline=min(ks) - timedelta(minutes=90),
            finished=True,
            is_current=False,
            is_next=False,
        )
        for g, ks in sorted(kickoffs.items())
    ]

    raw = {int(r["id"]): r for r in _read(season_dir / "players_raw.csv")}
    gw_rows = _read(season_dir / "gws" / "merged_gw.csv")
    has_dc = bool(gw_rows) and "defensive_contribution" in gw_rows[0]
    history: dict[int, list[dict]] = {}
    price: dict[int, dict[int, float]] = {}
    team_at: dict[int, dict[int, int]] = {}
    points: dict[int, dict[int, int]] = {}
    minutes: dict[int, dict[int, int]] = {}
    pos_of: dict[int, int] = {}
    name_of: dict[int, str] = {}
    xg_seen = False
    for r in gw_rows:
        if r["position"] not in POSITIONS:  # e.g. "AM", the 2024/25 assistant managers
            continue
        pid, gw, fid = int(r["element"]), int(r["round"]), int(r["fixture"])
        fx = by_id.get(fid)
        if fx is None:
            continue
        home = r["was_home"] == "True"
        row = {
            "element": pid,
            "round": gw,
            "fixture": fid,
            "was_home": home,
            **{k: _num(r.get(k), float) for k in HISTORY_FIELDS_FLOAT},
            **{k: _num(r.get(k), int) for k in HISTORY_FIELDS_INT},
        }
        if has_dc:
            row["defensive_contribution"] = _num(r.get("defensive_contribution"), int)
        xg_seen = xg_seen or row["expected_goals"] > 0
        history.setdefault(pid, []).append(row)
        price.setdefault(pid, {})[gw] = row["value"] / 10
        team_at.setdefault(pid, {})[gw] = fx.home if home else fx.away
        points.setdefault(pid, {})
        points[pid][gw] = points[pid].get(gw, 0) + row["total_points"]
        minutes.setdefault(pid, {})
        minutes[pid][gw] = minutes[pid].get(gw, 0) + row["minutes"]
        pos_of[pid] = POSITIONS[r["position"]]
        name_of[pid] = r["name"]
    for rows in history.values():
        rows.sort(key=lambda x: (x["round"], x["fixture"]))

    players = {}
    for pid, gws in price.items():
        rr = raw.get(pid)
        first_gw = min(gws)
        players[pid] = Player(
            id=pid,
            name=rr["web_name"] if rr else name_of[pid],
            full_name=f"{rr['first_name']} {rr['second_name']}" if rr else name_of[pid],
            team=team_at[pid][first_gw],
            pos=int(rr["element_type"]) if rr else pos_of[pid],
            price=gws[first_gw],
            price_start=gws[first_gw],
        )
    active = {pid: (min(g), max(g)) for pid, g in price.items()}

    past: dict[int, dict] = {}
    if prev_dir is not None and (prev_dir / "players_raw.csv").exists():
        prev = {r["code"]: r for r in _read(prev_dir / "players_raw.csv")}
        for pid, rr in raw.items():
            pr = prev.get(rr["code"])
            if pid in players and pr and _num(pr.get("minutes"), int) > 0:
                # Same types as the FPL API's history_past: counts are ints, xG/xA strings.
                past[pid] = {
                    k: (pr[k] if k in HISTORY_FIELDS_FLOAT else _num(pr[k], int))
                    for k in PAST_FIELDS
                    if k in pr
                }

    world = World(
        players=players,
        teams=teams,
        fixtures=fixtures,
        events=events,
        history=history,
        past=past,
        chips=[],
    )
    return SeasonData(
        name, world, price, team_at, active, points, minutes, has_dc, xg_seen
    )

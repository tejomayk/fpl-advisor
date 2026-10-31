"""A manager's current squad: who they own, selling prices, bank, free transfers and chips left.

Only public endpoints are used, so transfers already made for the upcoming gameweek are invisible
until its deadline passes. The advice says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import rules as R
from .api import FPLClient
from .world import World

STARTING_BUDGET = 100.0


@dataclass
class Squad:
    team_id: int
    manager: str
    team_name: str
    players: list[int]  # 15 player ids
    starting: list[int]  # last gameweek's XI, in pick order
    captain: int | None
    sell_price: dict[int, float]
    purchase_price: dict[int, float]
    bank: float
    free_transfers: int
    chips_available: list[
        str
    ]  # chip keys usable at the next deadline, e.g. ["wildcard", "3xc"]
    chips_used: list[tuple[str, int]] = field(default_factory=list)
    total_points: int = 0
    based_on_event: int = 0

    @property
    def value(self) -> float:
        return round(self.bank + sum(self.sell_price.values()), 1)


def selling_price(purchase: float, now: float) -> float:
    """FPL keeps half of any rise (rounded down to £0.1m); falls are passed on in full."""
    if now <= purchase:
        return now
    gain = math.floor(round((now - purchase) * 10, 6) / 2) / 10
    return round(purchase + gain, 1)


def count_free_transfers(
    history_rows: list[dict], chips_used: list[tuple[str, int]]
) -> int:
    """Free transfers available for the next deadline, replaying the season."""
    chip_at = {gw: name for name, gw in chips_used}
    ft = None
    for row in history_rows:
        gw, used = row["event"], row["event_transfers"]
        if ft is None:  # first gameweek played: transfers before it were unlimited
            ft = 1
            continue
        if chip_at.get(gw) in ("wildcard", "freehit"):
            # Banked free transfers are kept through a Wildcard or Free Hit, but no new one is added
            # (premierleague.com: "4 saved for GW29, play Free Hit, still 4 for GW30").
            continue
        ft = min(R.MAX_FREE_TRANSFERS, max(ft - used, 0) + 1)
    return ft if ft is not None else 1


def chips_available(
    chip_windows: list[dict], chips_used: list[tuple[str, int]], next_gw: int
) -> list[str]:
    out = []
    for w in chip_windows:
        if not (w["start_event"] <= next_gw <= w["stop_event"]):
            continue
        used = any(
            n == w["name"] and w["start_event"] <= gw <= w["stop_event"]
            for n, gw in chips_used
        )
        if not used:
            out.append(w["name"])
    return out


def chip_deadline(chip_windows: list[dict], name: str, next_gw: int) -> int | None:
    """Last gameweek the current copy of a chip can be played."""
    for w in chip_windows:
        if w["name"] == name and w["start_event"] <= next_gw <= w["stop_event"]:
            return w["stop_event"]
    return None


class NoHistoryError(ValueError):
    """The manager hasn't played a gameweek yet, so there is no squad to read."""


def initial_squad(client: FPLClient, team_id: int) -> Squad:
    """An empty squad with the starting budget, for building a team from scratch."""
    entry = client.entry(team_id)
    return Squad(
        team_id=team_id,
        manager=f"{entry['player_first_name']} {entry['player_last_name']}",
        team_name=entry["name"],
        players=[],
        starting=[],
        captain=None,
        sell_price={},
        purchase_price={},
        bank=STARTING_BUDGET,
        free_transfers=0,
        chips_available=[],
    )


def load_squad(client: FPLClient, world: World, team_id: int) -> Squad:
    entry = client.entry(team_id)
    hist = client.history(team_id)
    rows = hist["current"]
    if not rows:
        raise NoHistoryError("This team has not played a gameweek yet.")
    chips_used = [(c["name"], c["event"]) for c in hist["chips"]]
    last = rows[-1]["event"]
    picks = client.picks(team_id, last)
    if picks.get("active_chip") == "freehit" and last > 1:
        picks = client.picks(team_id, last - 1)  # a Free Hit squad reverts afterwards
    ids = [p["element"] for p in picks["picks"]]

    # Purchase price: most recent transfer in (ignoring Free Hit weeks), else the price when the
    # manager joined.
    fh_weeks = {gw for n, gw in chips_used if n == "freehit"}
    bought: dict[int, float] = {}
    for t in sorted(client.transfers(team_id), key=lambda t: t["time"]):
        if t["event"] not in fh_weeks:
            bought[t["element_in"]] = t["element_in_cost"] / 10
    start_gw = entry.get("started_event", 1)
    purchase, sell = {}, {}
    for pid in ids:
        p = world.players[pid]
        if pid in bought:
            purchase[pid] = bought[pid]
        else:
            row = next(
                (r for r in world.history.get(pid, []) if r["round"] == start_gw), None
            )
            purchase[pid] = row["value"] / 10 if row else p.price_start
        sell[pid] = selling_price(purchase[pid], p.price)

    next_ev = world.next_event
    next_gw = next_ev.id if next_ev else last + 1
    return Squad(
        team_id=team_id,
        manager=f"{entry['player_first_name']} {entry['player_last_name']}",
        team_name=entry["name"],
        players=ids,
        starting=[p["element"] for p in picks["picks"] if p["position"] <= 11],
        captain=next((p["element"] for p in picks["picks"] if p["is_captain"]), None),
        sell_price=sell,
        purchase_price=purchase,
        bank=rows[-1]["bank"] / 10,
        free_transfers=count_free_transfers(rows, chips_used),
        chips_available=chips_available(world.chips, chips_used, next_gw),
        chips_used=chips_used,
        total_points=rows[-1]["total_points"],
        based_on_event=last,
    )

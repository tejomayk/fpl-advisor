"""Price-change outlook from FPL's own projections (bootstrap `price_change_projections`).

`projected_percent` is progress toward the next price change at upcoming nightly updates (offset 0 is
the next update). A value of 100 or more means a rise is expected, and -100 or less means a fall.
`likelihood` runs from -5 to 5. Prices update once a night, at about 01:30 UK time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .world import Player

UK = ZoneInfo("Europe/London")
PRICE_UPDATE_UK = time(1, 30)
LIKELY = 4  # |likelihood| at or above this counts as "likely tonight"


@dataclass
class PriceOutlook:
    player_id: int
    direction: int  # +1 rise, -1 fall
    likelihood: int
    nights: int  # 0 = tonight, 1 = tomorrow night, ...


def outlook(p: Player) -> PriceOutlook | None:
    """The next likely price change in the coming nights, if any."""
    for proj in sorted(p.price_projection, key=lambda x: x.get("offset", 0)):
        try:
            pct = float(proj.get("projected_percent", 0))
        except (TypeError, ValueError):
            continue
        lk = int(proj.get("likelihood", 0))
        if pct >= 100 and lk >= LIKELY:
            return PriceOutlook(p.id, +1, lk, int(proj.get("offset", 0)))
        if pct <= -100 and lk <= -LIKELY:
            return PriceOutlook(p.id, -1, lk, int(proj.get("offset", 0)))
    return None


def next_price_update(now: datetime) -> datetime:
    """The next nightly price update (about 01:30 UK time), as a UK-local datetime."""
    local = now.astimezone(UK)
    update = datetime.combine(local.date(), PRICE_UPDATE_UK, tzinfo=UK)
    return update if local < update else update + timedelta(days=1)


def timing_advice(
    buys: list[Player], sells: list[Player], hours_to_deadline: float
) -> list[str]:
    """When to make the planned transfers.

    The default is to wait until close to the deadline, because team news arrives late. The exception
    is when waiting costs money: a target is about to rise, or a player you are selling is about to fall.
    """
    notes = []
    for p in buys:
        o = outlook(p)
        if o and o.direction > 0 and o.nights == 0 and hours_to_deadline > 6:
            notes.append(
                f"Buy {p.name} before prices update tonight (about 01:30 UK): {p.name} is likely to rise by £0.1m, "
                "and that money would let you afford slightly better players later."
            )
    for p in sells:
        o = outlook(p)
        if o and o.direction < 0 and o.nights == 0 and hours_to_deadline > 6:
            notes.append(
                f"Sell {p.name} before prices update tonight: {p.name} is likely to drop £0.1m, which comes straight off your "
                "selling price."
            )
    if not notes:
        if hours_to_deadline > 26:
            notes.append(
                "No price changes push you to act early. Wait for the pre-match press conferences "
                "(usually 1-2 days before the deadline) so you don't buy someone who turns out to be injured."
            )
        else:
            notes.append(
                "Team news is mostly out now, so this is a good time to make your moves."
            )
    return notes

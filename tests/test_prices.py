"""Offline tests for price-change outlook and timing advice."""

from __future__ import annotations

from fpl_advisor.prices import outlook, timing_advice
from fpl_advisor.world import Player


def player(pid: int = 1, proj=None) -> Player:
    return Player(
        id=pid,
        name=f"P{pid}",
        full_name=f"Player {pid}",
        team=1,
        pos=3,
        price=5.0,
        price_projection=proj or [],
    )


def proj(offset=0, pct=100, lk=4):
    return {"offset": offset, "projected_percent": pct, "likelihood": lk}


class TestOutlook:
    def test_rise(self):
        o = outlook(player(proj=[proj(0, 100, 4)]))
        assert (o.direction, o.likelihood, o.nights) == (1, 4, 0)

    def test_fall(self):
        o = outlook(player(proj=[proj(0, -100, -4)]))
        assert (o.direction, o.likelihood, o.nights) == (-1, -4, 0)

    def test_below_thresholds(self):
        assert outlook(player(proj=[proj(0, 99.9, 5)])) is None
        assert outlook(player(proj=[proj(0, 100, 3)])) is None
        assert outlook(player(proj=[proj(0, -99.9, -5)])) is None
        assert outlook(player(proj=[proj(0, -100, -3)])) is None

    def test_mixed_sign_ignored(self):
        assert outlook(player(proj=[proj(0, 150, -5)])) is None

    def test_no_projection(self):
        assert outlook(player()) is None

    def test_earliest_night_wins(self):
        p = player(proj=[proj(2, -120, -5), proj(1, 110, 5), proj(3, 100, 4)])
        o = outlook(p)
        assert o.nights == 1 and o.direction == 1

    def test_string_percent_accepted(self):
        assert outlook(player(proj=[proj(0, "104.5", 5)])).direction == 1

    def test_malformed_ignored(self):
        p = player(proj=[proj(0, "abc", 5), proj(0, None, 5), proj(1, 100, 4)])
        o = outlook(p)
        assert o is not None and o.nights == 1


class TestTimingAdvice:
    def rising(self, nights=0):
        return player(1, [proj(nights, 100, 4)])

    def falling(self, nights=0):
        return player(2, [proj(nights, -100, -4)])

    def test_early_buy_note(self):
        notes = timing_advice([self.rising()], [], 10)
        assert len(notes) == 1
        assert "Buy P1 before prices update tonight" in notes[0]

    def test_early_sell_note(self):
        notes = timing_advice([], [self.falling()], 10)
        assert len(notes) == 1 and "Sell P2" in notes[0]

    def test_no_early_note_close_to_deadline(self):
        notes = timing_advice([self.rising()], [self.falling()], 5)
        assert len(notes) == 1
        assert "Team news is mostly out" in notes[0]

    def test_not_tonight_is_not_early(self):
        notes = timing_advice([self.rising(nights=1)], [], 10)
        assert "before prices update" not in notes[0]

    def test_wait_default_when_far(self):
        notes = timing_advice([], [], 30)
        assert len(notes) == 1 and "press conferences" in notes[0]

    def test_mid_range_default_is_go(self):
        assert "good time" in timing_advice([], [], 20)[0]

    def test_early_note_replaces_default(self):
        notes = timing_advice([self.rising()], [], 30)
        assert all("press conferences" not in n for n in notes)


def test_next_price_update_before_and_after_0130_uk():
    from datetime import UTC, datetime

    from fpl_advisor.prices import next_price_update

    # Winter (GMT): 23:00 UTC on the 30th -> update at 01:30 on the 31st
    assert next_price_update(datetime(2026, 10, 30, 23, 0, tzinfo=UTC)).day == 31
    # 00:30 UTC (00:30 UK) is still before that night's update
    assert next_price_update(datetime(2026, 10, 31, 0, 30, tzinfo=UTC)).day == 31
    # 02:00 UTC is after it -> the following night
    assert next_price_update(datetime(2026, 10, 31, 2, 0, tzinfo=UTC)).day == 1

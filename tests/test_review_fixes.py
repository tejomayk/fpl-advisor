"""Offline regression tests for recent review fixes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import make_world, pick_squad

from fpl_advisor import league as L
from fpl_advisor import rules as R
from fpl_advisor.advise import summary_text
from fpl_advisor.api import FPLError
from fpl_advisor.chips import advise_chips
from fpl_advisor.model import availability, return_offset
from fpl_advisor.optimise import solve
from fpl_advisor.squad import Squad, count_free_transfers
from fpl_advisor.world import Player


def weekly(start: datetime, n: int = 6) -> list[datetime]:
    return [start + timedelta(days=7 * i) for i in range(n)]


NOV = weekly(datetime(2026, 10, 31, 11, 0, tzinfo=UTC))
DEC = weekly(datetime(2026, 12, 5, 11, 0, tzinfo=UTC))


def player(status: str, news: str = "") -> Player:
    return Player(1, "P", "Player", 1, R.MID, 5.0, status=status, news=news)


class TestReturnOffset:
    def test_suspended_until_date(self):
        assert return_offset(player("s", "Suspended until 08 Nov"), NOV) == 1

    def test_suspended_without_date_is_one_match(self):
        assert return_offset(player("s", "Suspended"), NOV) == 1

    def test_expected_back_date(self):
        assert return_offset(player("i", "Expected back 21 Nov"), NOV) == 3

    def test_unknown_return_date(self):
        assert return_offset(player("i", "Unknown return date"), NOV) is None

    def test_available_has_no_offset(self):
        assert return_offset(player("a", "Expected back 21 Nov"), NOV) is None

    def test_rolls_into_next_year(self):
        assert return_offset(player("i", "Expected back 05 Jan"), DEC) == 5

    def test_empty_deadlines(self):
        assert return_offset(player("i", "Expected back 21 Nov"), []) is None


class TestAvailability:
    def test_before_on_after_back_offset(self):
        p = player("i", "Expected back 21 Nov")
        assert availability(p, 0, 2) == 0.0
        assert availability(p, 1, 2) == 0.0
        assert availability(p, 2, 2) == 0.85
        assert availability(p, 3, 2) == 1.0


def hist(*rows: tuple[int, int]) -> list[dict]:
    return [{"event": gw, "event_transfers": used} for gw, used in rows]


class TestFreeTransfers:
    def test_chip_week_keeps_banked_count(self):
        # GW1 start, GW2-4 no transfers -> 4 banked; GW5 Free Hit keeps 4.
        rows = hist((1, 0), (2, 0), (3, 0), (4, 0), (5, 0))
        assert count_free_transfers(rows[:4], []) == 4
        assert count_free_transfers(rows, [("freehit", 5)]) == 4
        assert count_free_transfers(rows, [("wildcard", 5)]) == 4

    def test_following_normal_week_adds_one(self):
        rows = hist((1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0))
        assert count_free_transfers(rows, [("freehit", 5)]) == 5


# ---------------------------------------------------------------- chips


def chip_scenario(stop: int):
    world = make_world()
    world.chips = [
        {"name": "3xc", "start_event": 1, "stop_event": stop},
        {"name": "bboost", "start_event": 1, "stop_event": stop},
    ]
    window = [4, 5, 6]
    xp = {
        p.id: {g: (p.price - 3.0) + (p.id % 7) * 0.05 for g in window}
        for p in world.players.values()
    }
    players = pick_squad(world)
    sell = {p: world.players[p].price for p in players}
    squad = Squad(
        team_id=1,
        manager="m",
        team_name="t",
        players=players,
        starting=players[:11],
        captain=None,
        sell_price=sell,
        purchase_price=dict(sell),
        bank=0.0,
        free_transfers=1,
        chips_available=["3xc", "bboost"],
    )
    base = solve(world, xp, window, players, sell, 0.0, 1)
    return world, xp, window, squad, base


class TestChipExpiry:
    def test_one_week_left_plays_exactly_one(self):
        world, xp, window, squad, base = chip_scenario(stop=4)
        out = advise_chips(world, xp, window, window, squad, base)
        assert len(out) == 2
        playing = [a for a in out if a.play_now]
        assert len(playing) == 1
        assert "expire" in playing[0].reason

    def test_one_week_left_marks_played_chip_forced(self):
        world, xp, window, squad, base = chip_scenario(stop=4)
        out = advise_chips(world, xp, window, window, squad, base)
        playing = [a for a in out if a.play_now]
        assert len(playing) == 1
        assert playing[0].forced is True

    def test_two_weeks_left_plays_at_most_one(self):
        world, xp, window, squad, base = chip_scenario(stop=5)
        out = advise_chips(world, xp, window, window, squad, base)
        assert len(out) == 2
        assert sum(a.play_now for a in out) <= 1


# ---------------------------------------------------------------- league


class FakeClient:
    def __init__(self, n: int, me: int, my_rank: int | None, in_league=True):
        self.me = me
        self.my_rank = my_rank
        self.rows = [
            {
                "entry": 1000 + i,
                "entry_name": f"Team {i}",
                "player_name": f"Mgr {i}",
                "rank": i + 1,
                "total": 1000 - i,
                "event_total": 50,
            }
            for i in range(n)
        ]
        if in_league and my_rank:
            self.rows[my_rank - 1]["entry"] = me
        self.pages: list[int] = []

    def league(self, league_id, page=1):
        self.pages.append(page)
        size = L.PAGE_SIZE
        chunk = self.rows[(page - 1) * size : page * size]
        return {
            "league": {"name": "Test League"},
            "standings": {
                "results": chunk,
                "has_next": page * size < len(self.rows),
            },
        }

    def entry(self, entry_id):
        classic = []
        if self.my_rank:
            classic = [{"id": 7, "entry_rank": self.my_rank}]
        return {"leagues": {"classic": classic}}

    def history(self, entry_id):
        return {"current": [{"event": 1}] * 3, "past": [], "chips": []}

    def picks(self, entry_id, event):
        return {"picks": [{"element": 1, "is_captain": True, "position": 1}]}


class TestLoadLeague:
    def test_me_beyond_first_page(self):
        client = FakeClient(120, me=555, my_rank=95)
        view = L.load_league(client, make_world(), 7, 555)
        assert isinstance(view, L.LeagueView)
        assert view.me.entry == 555
        assert view.me.rank == 95
        assert client.pages == [1, 1, 2] or client.pages == [1, 2]
        assert 2 in client.pages
        assert 3 not in client.pages
        assert all(m.entry != 555 for m in view.rivals)
        assert view.name == "Test League"

    def test_me_not_found_raises(self):
        client = FakeClient(120, me=555, my_rank=None)
        with pytest.raises(L.LeagueError):
            L.load_league(client, make_world(), 7, 555)

    def test_me_rank_given_but_absent_raises(self):
        client = FakeClient(120, me=555, my_rank=95, in_league=False)
        with pytest.raises(L.LeagueError):
            L.load_league(client, make_world(), 7, 555)

    def test_only_me(self):
        client = FakeClient(1, me=555, my_rank=1)
        view = L.load_league(client, make_world(), 7, 555)
        assert view.rivals == []
        assert view.me.entry == 555
        assert view.ownership == {}

    def test_missing_picks_tolerated(self):
        client = FakeClient(3, me=555, my_rank=2)

        def boom(*a):
            raise FPLError("no picks")

        client.picks = boom
        view = L.load_league(client, make_world(), 7, 555)
        assert view.me.picks == []


# ---------------------------------------------------------------- summary


def test_summary_text_without_rivals():
    me = L.Manager(1, "t", "m", 1, 100, 10)
    league = SimpleNamespace(me=me, rivals=[], leader=me)
    plan = SimpleNamespace(
        captain={4: 1}, vice={4: 2}, hits=0, buys=[], sells=[], squad=[]
    )
    advice = SimpleNamespace(
        event=SimpleNamespace(id=4),
        initial=False,
        chip_now=None,
        plan=plan,
        squad=SimpleNamespace(free_transfers=1),
        league=league,
        name=lambda pid: f"P{pid}",
    )
    text = summary_text(advice)
    assert "No transfers" in text
    assert "League" not in text


# ---------------------------------------------------------------- optimise


def test_initial_squad_build():
    world = make_world()
    xp = {
        p.id: {g: (p.price - 3.0) + (p.id % 7) * 0.05 for g in (4, 5)}
        for p in world.players.values()
    }
    plan = solve(world, xp, [4, 5], [], {}, 100.0, 1, unlimited=True)
    pl = world.players
    assert len(set(plan.squad)) == 15
    for pos, n in R.SQUAD_COUNT.items():
        assert sum(pl[p].pos == pos for p in plan.squad) == n
    for t in world.teams:
        assert sum(pl[p].team == t for p in plan.squad) <= R.MAX_PER_CLUB
    assert sum(pl[p].price for p in plan.squad) <= 100.0 + 1e-6
    assert plan.hits == 0

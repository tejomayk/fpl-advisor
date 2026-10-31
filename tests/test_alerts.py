"""Offline tests for cli.collect_alerts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from conftest import make_world

from fpl_advisor import cli
from fpl_advisor.chips import ChipAdvice
from fpl_advisor.config import Config
from fpl_advisor.prices import next_price_update
from fpl_advisor.state import AlertState

GW = 4


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cli, "run_url", lambda: None)
    monkeypatch.setattr(cli, "summary_text", lambda a: "SUMMARY")


@pytest.fixture
def state(tmp_path):
    return AlertState(tmp_path / "alerts.json")


@pytest.fixture
def cfg():
    return Config(team_id=1)


def rising(night=0):
    return [{"offset": night, "projected_percent": 100, "likelihood": 5}]


def falling(night=0):
    return [{"offset": night, "projected_percent": -100, "likelihood": -5}]


def make_advice(
    *, hours=10.0, buys=(), sells=(), squad=(), xi=None, chips=(), mutate=None
):
    world = make_world()
    if mutate:
        mutate(world)
    plan = SimpleNamespace(
        buys=list(buys),
        sells=list(sells),
        xi={GW: list(xi if xi is not None else squad)},
    )
    return SimpleNamespace(
        world=world,
        event=SimpleNamespace(id=GW),
        hours_to_deadline=hours,
        plan=plan,
        squad=SimpleNamespace(players=list(squad)),
        chips=list(chips),
        name=lambda pid: world.players[pid].name,
    )


def chip(name="wildcard", expires=None, reason="use it"):
    return ChipAdvice(name, False, 0.0, None, 0.0, expires, reason)


class TestDeadline:
    def test_tightest_window_only(self, cfg, state):
        out = cli.collect_alerts(make_advice(hours=1.5), cfg, state)
        assert len(out) == 1
        keys, n = out[0]
        assert "2h" in n.title
        assert set(keys) == {f"deadline:{GW}:24", f"deadline:{GW}:2"}
        assert n.priority == 4

    def test_wider_window(self, cfg, state):
        out = cli.collect_alerts(make_advice(hours=12), cfg, state)
        assert len(out) == 1
        keys, n = out[0]
        assert "12h" in n.title
        assert keys == [f"deadline:{GW}:24"]
        assert n.priority == 3

    def test_minutes_when_under_one_hour(self, cfg, state):
        n = cli.collect_alerts(make_advice(hours=0.5), cfg, state)[0][1]
        assert "30 min" in n.title

    def test_none_beyond_largest_window(self, cfg, state):
        assert cli.collect_alerts(make_advice(hours=25), cfg, state) == []

    def test_none_after_deadline(self, cfg, state):
        assert cli.collect_alerts(make_advice(hours=0), cfg, state) == []

    def test_not_resent_once_marked(self, cfg, state):
        keys, _ = cli.collect_alerts(make_advice(hours=1.5), cfg, state)[0]
        for k in keys:
            state.mark(k)
        assert cli.collect_alerts(make_advice(hours=1.5), cfg, state) == []

    def test_two_hour_reminder_after_24h_was_sent(self, cfg, state):
        state.mark(f"deadline:{GW}:24")
        out = cli.collect_alerts(make_advice(hours=1.5), cfg, state)
        assert len(out) == 1 and "2h" in out[0][1].title


class TestPrices:
    def test_bundles_buys_and_sells(self, cfg, state):
        def mutate(w):
            w.players[1].price_projection = rising()
            w.players[2].price_projection = rising()
            w.players[3].price_projection = falling()

        a = make_advice(hours=30, buys=[1, 2], sells=[3], mutate=mutate)
        out = [
            o
            for o in cli.collect_alerts(cfg=cfg, a=a, state=state)
            if o[0][0].startswith("price:")
        ]
        assert len(out) == 1
        keys, n = out[0]
        today = next_price_update(datetime.now(UTC)).strftime("%Y-%m-%d")
        assert set(keys) == {
            f"price:1:up:{today}",
            f"price:2:up:{today}",
            f"price:3:down:{today}",
        }
        assert "Buy P1" in n.body and "Buy P2" in n.body and "Sell P3" in n.body

    def test_suppressed_when_marked(self, cfg, state):
        def mutate(w):
            w.players[1].price_projection = rising()

        a = make_advice(hours=30, buys=[1], mutate=mutate)
        keys, _ = cli.collect_alerts(a, cfg, state)[0]
        for k in keys:
            state.mark(k)
        assert cli.collect_alerts(a, cfg, state) == []

    def test_suppressed_near_deadline(self, cfg, state):
        def mutate(w):
            w.players[1].price_projection = rising()

        a = make_advice(hours=5, buys=[1], mutate=mutate)
        titles = [n.title for _, n in cli.collect_alerts(a, cfg, state)]
        assert not any("prices change" in t for t in titles)

    def test_wrong_direction_or_night_ignored(self, cfg, state):
        def mutate(w):
            w.players[1].price_projection = falling()  # buying a faller
            w.players[2].price_projection = rising(night=1)  # not tonight

        a = make_advice(hours=30, buys=[1, 2], mutate=mutate)
        assert cli.collect_alerts(a, cfg, state) == []


class TestNews:
    def test_skips_available_players(self, cfg, state):
        def mutate(w):
            w.players[1].status, w.players[1].chance_next = "a", None
            w.players[2].status, w.players[2].chance_next = "a", 100

        a = make_advice(hours=30, squad=[1, 2], mutate=mutate)
        assert cli.collect_alerts(a, cfg, state) == []

    def test_bundles_flagged_players(self, cfg, state):
        def mutate(w):
            w.players[1].status, w.players[1].news = "i", "Knee"
            w.players[2].status, w.players[2].chance_next = "a", 75
            w.players[3].status, w.players[3].news = "s", "Banned"

        a = make_advice(
            hours=30, squad=[1, 2, 3, 4], sells=[3], xi=[1, 2], mutate=mutate
        )
        out = cli.collect_alerts(a, cfg, state)
        assert len(out) == 1
        keys, n = out[0]
        assert len(keys) == 3 and all(k.startswith("news:") for k in keys)
        assert "3 of your players" in n.title
        assert "P1: Knee (plan: still start)" in n.body
        assert "P2: doubtful (plan: still start)" in n.body
        assert "P3: Banned (plan: sell)" in n.body

    def test_bench_action(self, cfg, state):
        def mutate(w):
            w.players[1].status = "d"

        a = make_advice(hours=30, squad=[1, 2], xi=[2], mutate=mutate)
        assert "plan: bench" in cli.collect_alerts(a, cfg, state)[0][1].body

    def test_not_resent_after_marking(self, cfg, state):
        def mutate(w):
            w.players[1].status, w.players[1].news = "i", "Knee"

        a = make_advice(hours=30, squad=[1], mutate=mutate)
        keys, _ = cli.collect_alerts(a, cfg, state)[0]
        for k in keys:
            state.mark(k)
        assert cli.collect_alerts(a, cfg, state) == []

    def test_changed_news_resends(self, cfg, state):
        def mutate(w):
            w.players[1].status, w.players[1].news = "i", "Knee"

        a = make_advice(hours=30, squad=[1], mutate=mutate)
        for k in cli.collect_alerts(a, cfg, state)[0][0]:
            state.mark(k)

        def mutate2(w):
            w.players[1].status, w.players[1].news = "i", "Out for season"

        b = make_advice(hours=30, squad=[1], mutate=mutate2)
        assert len(cli.collect_alerts(b, cfg, state)) == 1


class TestChips:
    def test_expiring_within_two_gws(self, cfg, state):
        a = make_advice(
            hours=30, chips=[chip("wildcard", expires=GW + 2, reason="why")]
        )
        out = cli.collect_alerts(a, cfg, state)
        assert len(out) == 1
        keys, n = out[0]
        assert keys == [f"chip:wildcard:{GW + 2}"]
        assert f"expires after GW{GW + 2}" in n.title
        assert n.body == "why"

    def test_not_expiring_soon(self, cfg, state):
        a = make_advice(hours=30, chips=[chip("wildcard", expires=GW + 3)])
        assert cli.collect_alerts(a, cfg, state) == []

    def test_no_expiry(self, cfg, state):
        assert (
            cli.collect_alerts(
                make_advice(hours=30, chips=[chip(expires=None)]), cfg, state
            )
            == []
        )

    def test_not_resent_after_marking(self, cfg, state):
        a = make_advice(hours=30, chips=[chip("wildcard", expires=GW + 1)])
        state.mark(f"chip:wildcard:{GW + 1}")
        assert cli.collect_alerts(a, cfg, state) == []


def test_click_url_attached(monkeypatch, cfg, state):
    monkeypatch.setattr(cli, "run_url", lambda: "https://example.test/run/1")
    out = cli.collect_alerts(make_advice(hours=1.5), cfg, state)
    assert out[0][1].click == "https://example.test/run/1"

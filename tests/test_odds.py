"""Odds API module: maths, matching, cadence and credit budget. No network."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from http import client as http_client
from pathlib import Path

import pytest

from fpl_advisor import odds
from fpl_advisor.world import Event, Fixture, Player, Team, World

DATA = Path(__file__).parent / "data"
DEADLINE = datetime(2026, 11, 7, 11, 0, tzinfo=UTC)
EARLY = DEADLINE - timedelta(hours=30)  # inside the 48h-6h props window
FAR = DEADLINE - timedelta(days=4)  # no props window


def load(name: str):
    return json.loads((DATA / name).read_text())


def odds_world() -> World:
    teams = {
        1: Team(1, "Arsenal", "ARS"),
        2: Team(2, "Spurs", "TOT"),
        3: Team(3, "Man Utd", "MUN"),
        4: Team(4, "Chelsea", "CHE"),
    }
    players = {
        10: Player(10, "Saka", "Bukayo Saka", 1, 3, 10.0),
        11: Player(11, "Gabriel", "Gabriel dos Santos Magalhães", 1, 2, 6.0),
        20: Player(20, "Solanke", "Dominic Solanke", 2, 4, 7.5),
        30: Player(30, "B.Fernandes", "Bruno Borges Fernandes", 3, 3, 9.0),
        40: Player(40, "João Pedro", "João Pedro Junqueira de Jesus", 4, 4, 7.5),
    }
    fixtures = [
        Fixture(90, 9, 2, 1, "2026-10-31T15:00:00Z", True, 1, 1),
        Fixture(101, 10, 1, 2, "2026-11-07T12:30:00Z", False),
        Fixture(102, 10, 3, 4, "2026-11-07T15:00:00Z", False),
    ]
    events = [
        Event(9, DEADLINE - timedelta(days=7), True, True, False),
        Event(10, DEADLINE, False, False, True),
    ]
    return World(players, teams, fixtures, events, {}, {}, [])


class FakeHttp:
    """Answers by path; records every call. Headers mimic the API's credit counters."""

    def __init__(self, remaining: int = 480, status: int = 200):
        self.calls: list[tuple[str, dict]] = []
        self.remaining = remaining
        self.status = status
        self.props = {
            "e912304de2b2ce35b473ce2ecd3d1502": load("odds_event_props.json"),
            "a51bc2e0f4f8a9b0c3d2e1f0a9b8c7d6": {
                "id": "a51bc2e0f4f8a9b0c3d2e1f0a9b8c7d6",
                "home_team": "Manchester United",
                "away_team": "Chelsea",
                "commence_time": "2026-11-07T15:00:00Z",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "markets": [
                            {
                                "key": "player_goal_scorer_anytime",
                                "outcomes": [
                                    {
                                        "name": "Yes",
                                        "description": "Joao Pedro",
                                        "price": 2.8,
                                    },
                                    {
                                        "name": "No",
                                        "description": "Joao Pedro",
                                        "price": 1.4,
                                    },
                                    {
                                        "name": "Yes",
                                        "description": "Bruno Fernandes",
                                        "price": 4.0,
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
        }

    def __call__(self, path: str, params: dict):
        self.calls.append((path, params))
        if self.status != 200:
            return self.status, {}, None
        if path.endswith("/events"):
            return (
                200,
                {"x-requests-last": "0", "x-requests-remaining": str(self.remaining)},
                load("odds_events.json"),
            )
        cost = len(params["markets"].split(","))
        self.remaining -= cost
        hdr = {
            "x-requests-last": str(cost),
            "x-requests-remaining": str(self.remaining),
        }
        if path.endswith("/odds") and "/events/" in path:
            eid = path.split("/events/")[1].split("/")[0]
            return 200, hdr, self.props[eid]
        return 200, hdr, load("odds_team.json")

    def paths(self, kind: str) -> list[str]:
        return [p for p, _ in self.calls if kind in p]


def odds_ledger(tmp_path) -> dict:
    """The odds entry of the shared usage ledger (FPL_DATA_DIR is tmp_path in tests)."""
    data = json.loads((tmp_path / "usage" / "usage.json").read_text())
    return data["providers"]["odds"]


def run(world, tmp_path, now, http, **kw):
    return odds.fetch_signals(
        world,
        tmp_path,
        now=now,
        api_key="test-key",
        http=http,
        sleep=lambda s: None,
        **kw,
    )


# --- no key -----------------------------------------------------------------------------------


def test_no_key_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    http = FakeHttp()
    s = odds.fetch_signals(odds_world(), tmp_path, now=EARLY, http=http)
    assert s.players == {} and s.fixtures == {} and s.updated == {}
    assert http.calls == []
    assert not (tmp_path / "odds").exists()


# --- maths ------------------------------------------------------------------------------------


def test_devig_proportional():
    p = odds.devig([1.8, 3.9, 4.2])
    assert sum(p) == pytest.approx(1.0)
    raw = [1 / 1.8, 1 / 3.9, 1 / 4.2]
    assert p == pytest.approx([x / sum(raw) for x in raw])
    assert odds.devig([2.0, 2.0]) == pytest.approx([0.5, 0.5])
    assert odds.devig([1.0, 2.0]) == []  # a price of 1.0 is not a real price


def test_median_across_bookmakers():
    probs = odds.team_market_probs(load("odds_team.json")[0])
    a = odds.devig([1.8, 3.9, 4.2])
    b = odds.devig([1.83, 3.8, 4.0])
    assert probs["home"] == pytest.approx((a[0] + b[0]) / 2, abs=1e-3)
    assert probs["line"] == 2.5
    over = (odds.devig([1.75, 2.05])[0] + odds.devig([1.72, 2.1])[0]) / 2
    assert probs["over"] == pytest.approx(over)


def test_poisson_fit_recovers_known_lambdas():
    h, d, a, o = odds.poisson_probs(1.6, 1.1)
    lh, la = odds.fit_lambdas(h, d, a, o)
    assert lh == pytest.approx(1.6, abs=0.05)
    assert la == pytest.approx(1.1, abs=0.05)
    # From vigged prices (5% margin) through the de-vig step too.
    price = [1.05 / x for x in (h, d, a)]
    p = odds.devig(price)
    ov = odds.devig([1.05 / o, 1.05 / (1 - o)])[0]
    lh, la = odds.fit_lambdas(*p, ov)
    assert (lh, la) == (pytest.approx(1.6, abs=0.05), pytest.approx(1.1, abs=0.05))


def test_poisson_fit_other_line():
    h, d, a, o = odds.poisson_probs(2.2, 0.7, line=3.5)
    lh, la = odds.fit_lambdas(h, d, a, o, line=3.5)
    assert (lh, la) == (pytest.approx(2.2, abs=0.05), pytest.approx(0.7, abs=0.05))


def test_player_props_devig_and_yes_only():
    probs = odds.player_prop_probs(load("odds_event_props.json"))
    saka_dk = odds.devig([2.5, 1.5])[0]
    saka_fd = odds.devig([2.3, 1.6])[0]
    assert probs["Bukayo Saka"]["goal"] == pytest.approx((saka_dk + saka_fd) / 2)
    assert probs["Bukayo Saka"]["assist"] == pytest.approx(odds.devig([3.2, 1.33])[0])
    # Yes price only: margin removed with the fixed overround.
    assert probs["Gabriel Magalhães"]["goal"] == pytest.approx(
        (1 / 6.0) / odds.YES_ONLY_OVERROUND
    )


# --- matching ---------------------------------------------------------------------------------


def test_fixture_matching_with_alias_and_kickoff_window():
    w = odds_world()
    ev = load("odds_team.json")
    assert odds.match_fixture(w, ev[0]).id == 101  # "Tottenham Hotspur" -> Spurs alias
    assert (
        odds.match_fixture(w, ev[1]).id == 102
    )  # "Manchester United" -> Man Utd alias
    assert odds.match_fixture(w, ev[2]) is None  # unknown club
    moved = dict(ev[0], commence_time="2026-11-10T12:30:00Z")  # 3 days off
    assert odds.match_fixture(w, moved) is None
    w.teams[4] = Team(4, "Ipswich Town", "IPS")  # FPL's own name is also an alias key
    ipswich = dict(ev[1], away_team="Ipswich Town")
    assert odds.match_fixture(w, ipswich).id == 102
    swapped = dict(ev[0], home_team="Tottenham Hotspur", away_team="Arsenal")
    assert odds.match_fixture(w, swapped) is None


def test_player_mapping_accents_and_unmappable(tmp_path, caplog):
    w = odds_world()
    http = FakeHttp()
    with caplog.at_level(logging.INFO, logger="fpl_advisor.odds"):
        s = run(w, tmp_path, EARLY, http)
    assert s.players[10].p_goal is not None and s.players[10].p_assist is not None
    assert (
        s.players[11].p_goal is not None
    )  # "Gabriel Magalhães" -> Gabriel dos Santos M.
    assert s.players[20].p_goal is not None
    assert s.players[40].p_goal == pytest.approx(odds.devig([2.8, 1.4])[0], abs=1e-4)
    assert (
        s.players[30].p_goal is not None
    )  # "Bruno Fernandes" -> Bruno Borges Fernandes
    assert set(s.players) == {10, 11, 20, 30, 40}
    assert "1 bookmaker player names not matched" in caplog.text
    assert "Nobody McUnknown" in caplog.text
    assert "test-key" not in caplog.text


# --- end to end, cadence and budget -----------------------------------------------------------


def test_fixtures_signal_and_updated(tmp_path):
    w = odds_world()
    http = FakeHttp()
    s = run(w, tmp_path, FAR, http)
    assert set(s.fixtures) == {101, 102}
    f = s.fixtures[101]
    assert f.source == "odds" and f.lam_home > f.lam_away  # Arsenal favourites
    assert s.updated["odds"] == FAR.isoformat()
    assert s.updated["odds_gw"] == "10"  # tagged with the gameweek it is for
    assert http.paths("/events/") == []  # no props outside the windows
    assert all("test-key" not in json.dumps(p) for _, p in http.calls)


def test_team_odds_cadence_uses_cache_within_12h(tmp_path):
    w = odds_world()
    http = FakeHttp()
    first = run(w, tmp_path, FAR, http)
    second = run(w, tmp_path, FAR + timedelta(hours=11), FakeHttp())
    assert len(http.calls) == 1
    assert second.fixtures == first.fixtures
    # A restart with a fresh fake still reads the cache, spending nothing.
    http3 = FakeHttp()
    run(w, tmp_path, FAR + timedelta(hours=11, minutes=59), http3)
    assert http3.calls == []
    http4 = FakeHttp()
    run(w, tmp_path, FAR + timedelta(hours=12), http4)
    assert len(http4.paths("/sports/soccer_epl/odds")) == 1


def test_props_at_most_once_per_window(tmp_path):
    w = odds_world()
    http = FakeHttp()
    run(w, tmp_path, EARLY, http)
    assert len(http.paths("/events/")) == 2  # one per next-gameweek match
    run(w, tmp_path, EARLY + timedelta(hours=13), http)
    assert len(http.paths("/events/")) == 2  # same window: cached
    late = DEADLINE - timedelta(hours=3)
    s = run(w, tmp_path, late, http)
    assert len(http.paths("/events/")) == 4  # second window
    run(w, tmp_path, late + timedelta(hours=1), http)
    assert len(http.paths("/events/")) == 4
    assert s.players[10].p_goal is not None
    for _, params in http.calls:
        if "markets" in params and "player" in params["markets"]:
            assert params["regions"] == "us"
        elif "markets" in params:
            assert params["regions"] == "uk"


def test_budget_floor_stops_calls(tmp_path):
    w = odds_world()
    # 53 left: team odds (2) is fine -> 51; the first props call (2) would leave 49 < 50.
    http = FakeHttp(remaining=53)
    run(w, tmp_path, EARLY, http)
    assert len(http.paths("/soccer_epl/odds")) == 1
    assert len(http.paths("/events/")) == 0
    ledger = odds_ledger(tmp_path)
    assert ledger["remaining"] == 51 and ledger["spent_month"] == 2


def test_monthly_budget_stops_calls(tmp_path):
    w = odds_world()
    http = FakeHttp()
    budget = odds.Budget(monthly=3, floor=0)
    run(w, tmp_path, EARLY, http, budget=budget)
    # Team odds (2) fit; the first props call would make 4 > 3.
    assert len(http.calls) == 1
    # The ledger restarts each calendar month.
    http2 = FakeHttp()
    run(w, tmp_path, EARLY + timedelta(days=40), http2, budget=budget)
    assert len(http2.calls) == 1


def test_monthly_budget_env(monkeypatch):
    monkeypatch.setenv("ODDS_MONTHLY_BUDGET", "123")
    assert odds.Budget().monthly == 123


def test_bad_key_stops_after_first_call(tmp_path):
    http = FakeHttp(status=401)
    s = run(odds_world(), tmp_path, EARLY, http)
    assert len(http.calls) == 1 and s.fixtures == {} and s.players == {}


def test_rate_limit_retries_once_with_spacing(tmp_path):
    w = odds_world()
    inner = FakeHttp()
    state = {"n": 0}

    def flaky(path, params):
        state["n"] += 1
        if state["n"] == 1:
            return 429, {}, None
        return inner(path, params)

    slept: list[float] = []
    s = odds.fetch_signals(
        w, tmp_path, now=FAR, api_key="k", http=flaky, sleep=slept.append
    )
    assert state["n"] == 2 and slept and set(s.fixtures) == {101, 102}


# --- review fixes -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        http_client.RemoteDisconnected("closed"),
        http_client.IncompleteRead(b"partial"),
        ConnectionResetError("reset"),
        TimeoutError(),
    ],
)
def test_transport_errors_are_failed_calls(tmp_path, monkeypatch, exc):
    w = odds_world()
    first = run(w, tmp_path, FAR, FakeHttp())
    ledger = odds_ledger(tmp_path)

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(odds.urllib.request, "urlopen", boom)
    assert odds._urllib_http("test-key")("/x", {}) == (0, {}, None)
    # Through the real transport: no crash, the estimate is counted, cached odds still used.
    s = odds.fetch_signals(
        w,
        tmp_path,
        now=FAR + timedelta(hours=13),
        api_key="test-key",
        sleep=lambda s: None,
    )
    assert s.fixtures == first.fixtures
    after = odds_ledger(tmp_path)
    # A transport error may still have been billed: the estimate (2 credits) is counted.
    assert after["spent_month"] == ledger["spent_month"] + 2
    assert after["pending"] == {}  # the reservation was settled, not left hanging


def test_empty_late_props_fall_back_to_early(tmp_path):
    w = odds_world()
    early = run(w, tmp_path, EARLY, FakeHttp())
    http = FakeHttp()
    eid = "e912304de2b2ce35b473ce2ecd3d1502"
    http.props[eid] = {"id": eid, "bookmakers": []}  # late file with no prices
    s = run(w, tmp_path, DEADLINE - timedelta(hours=3), http)
    assert len(http.paths("/events/")) == 2  # late files were fetched
    assert s.players[10].p_goal == early.players[10].p_goal is not None
    assert s.players[20].p_goal == early.players[20].p_goal is not None


def test_old_gameweek_prop_files_are_pruned(tmp_path):
    pdir = tmp_path / "odds" / "props"
    pdir.mkdir(parents=True)
    for name in ("gw8_late_a.json", "gw9_early_b.json", "gw10_early_c.json"):
        (pdir / name).write_text("{}")
    (pdir / "notes.txt").write_text("keep")
    run(odds_world(), tmp_path, FAR, FakeHttp())  # next gameweek is 10
    assert sorted(p.name for p in pdir.iterdir()) == ["gw10_early_c.json", "notes.txt"]


def test_double_gameweek_keeps_first_fixture():
    w = odds_world()
    later = Fixture(103, 10, 4, 1, "2026-11-09T16:00:00Z", False)  # Chelsea v Arsenal
    w.fixtures.append(later)
    f101 = next(f for f in w.fixtures if f.id == 101)
    first = {1: 101, 2: 101, 3: 102, 4: 102}
    out = odds.Signals()
    # Processed in either order, Saka keeps the price from Arsenal's first match (101).
    odds.map_players(w, later, {"Bukayo Saka": {"goal": 0.9}}, out, first)
    odds.map_players(w, f101, {"Bukayo Saka": {"goal": 0.4}}, out, first)
    odds.map_players(w, later, {"Bukayo Saka": {"goal": 0.9}}, out, first)
    assert out.players[10].p_goal == 0.4


def test_double_gameweek_end_to_end(tmp_path):
    w = odds_world()
    # Arsenal also play Chelsea before Arsenal v Spurs: Arsenal players come only from that
    # (earlier) match, which has no props, so the Arsenal v Spurs prices are not used for them.
    w.fixtures.append(Fixture(103, 10, 1, 4, "2026-11-06T19:45:00Z", False))
    s = run(w, tmp_path, EARLY, FakeHttp())
    assert 10 not in s.players and 11 not in s.players
    assert s.players[20].p_goal is not None  # Spurs play once
    assert s.updated["odds_gw"] == "10"


def test_each_attempt_reserves_its_own_credits(tmp_path):
    calls = []

    def limited(path, params):
        calls.append(path)
        return 429, {"x-requests-last": "2"}, None

    budget = odds.Budget(monthly=100, floor=0, daily=3)
    odds.fetch_signals(
        odds_world(),
        tmp_path,
        now=FAR,
        api_key="k" * 20,
        http=limited,
        sleep=lambda s: None,
        budget=budget,
    )
    # The 429 cost 2; the retry would need 2 more, past the daily 3, so it is not made.
    assert len(calls) == 1
    assert odds_ledger(tmp_path)["spent_day"] == 2

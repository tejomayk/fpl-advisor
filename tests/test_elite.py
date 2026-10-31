"""Elite managers: discovery (ranking, resume) and summary (shares, transfers), with a fake client."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from fpl_advisor import elite
from fpl_advisor.api import FPLError
from fpl_advisor.elite import EliteManager
from fpl_advisor.world import Event, World

SEASONS = ["2023/24", "2024/25", "2025/26"]


def NOSLEEP(s):
    return None


def h(a, b, c):
    return {
        "past": [
            {"season_name": "2022/23", "rank": 1},  # older season: ignored
            {"season_name": "2023/24", "rank": a},
            {"season_name": "2024/25", "rank": b},
            {"season_name": "2025/26", "rank": c},
        ]
    }


class FakeClient:
    def __init__(
        self, histories, page_size=50, fail_once=(), picks=None, transfers=None
    ):
        self.histories = histories  # entry id -> history dict (missing -> 404)
        self.ids = list(histories)
        self.page_size = page_size
        self.fail_once = set(fail_once)
        self.calls: list[str] = []
        self._picks = picks or {}
        self._transfers = transfers or {}

    def bootstrap(self):
        return {"events": [{"deadline_time": "2026-08-15T10:00:00Z"}]}

    def get_uncached(self, path, retries=3):
        assert retries == 1  # the scan is the only retry layer
        self.calls.append(path)
        if path in self.fail_once:
            self.fail_once.discard(path)
            raise FPLError("FPL is updating (503). Try again in a few minutes.")
        if path.startswith("leagues-classic/314/standings/"):
            page = int(path.rsplit("=", 1)[1])
            chunk = self.ids[(page - 1) * 50 : page * 50]
            return {
                "standings": {
                    "has_next": page * 50 < len(self.ids),
                    "results": [
                        {"entry": e, "entry_name": f"T{e}", "player_name": f"P{e}"}
                        for e in chunk
                    ],
                }
            }
        eid = int(path.split("/")[1])
        if eid not in self.histories:
            raise FPLError(f"Not found: {path} (check your team/league ID)")
        return self.histories[eid]

    def picks(self, eid, gw):
        if (eid, gw) not in self._picks:
            raise FPLError("Not found: picks")
        return self._picks[(eid, gw)]

    def transfers(self, eid):
        return self._transfers.get(eid, [])


def test_last_completed_seasons():
    assert elite.last_completed_seasons(FakeClient({}), 3) == SEASONS


def test_ranking_by_median_with_worst_season_tiebreak():
    hist = {
        1: h(100, 5000, 200),  # median 200, worst 5000
        2: h(150, 200, 250),  # median 200, worst 250 -> beats 1 on tiebreak
        3: h(10, 20, 900_000),  # median 20: one bad season does not sink a great median
        4: h(50_000, 60_000, 70_000),
    }
    got = elite.discover(FakeClient(hist), pool_size=100, top=3, sleep=lambda s: None)
    assert [m.id for m in got] == [3, 2, 1]
    assert got[0].median_rank == 20 and got[0].worst_rank == 900_000
    assert got[1].ranks == {"2023/24": 150, "2024/25": 200, "2025/26": 250}
    assert got[1].name == "T2" and got[1].player_name == "P2"


def test_missing_season_excluded():
    hist = {
        1: {
            "past": [
                {"season_name": "2024/25", "rank": 1},
                {"season_name": "2025/26", "rank": 1},
            ]
        },
        2: h(1000, 1000, 1000),
        3: {"past": []},  # new manager
    }
    got = elite.discover(FakeClient(hist), pool_size=50, top=10, sleep=lambda s: None)
    assert [m.id for m in got] == [2]


def test_404_history_skipped_and_transient_error_retried():
    hist = {1: h(5, 5, 5), 2: h(9, 9, 9)}
    client = FakeClient(hist, fail_once={"entry/2/history/"})
    client.ids.append(99)  # in standings, but its history 404s (deleted team)
    got = elite.discover(client, pool_size=50, top=10, sleep=lambda s: None)
    assert [m.id for m in got] == [1, 2]
    assert client.calls.count("entry/2/history/") == 2
    assert client.calls.count("entry/99/history/") == 1


def test_pool_size_limits_candidates():
    hist = {i: h(1000 - i, 1000 - i, 1000 - i) for i in range(1, 121)}
    client = FakeClient(hist)
    got = elite.discover(client, pool_size=60, top=5, sleep=lambda s: None)
    # Only the first 60 standings rows are candidates: 2 pages, 60 histories.
    assert sum(c.startswith("leagues") for c in client.calls) == 2
    assert sum(c.startswith("entry") for c in client.calls) == 60
    assert [m.id for m in got] == [60, 59, 58, 57, 56]


def test_checkpoint_resume(tmp_path):
    hist = {i: h(i, i, i) for i in range(1, 101)}
    cp = tmp_path / "cp.json"

    class Crash(FakeClient):
        def get_uncached(self, path, retries=3):
            if path == "entry/70/history/":
                raise RuntimeError("power cut")
            return super().get_uncached(path, retries)

    with pytest.raises(RuntimeError):
        elite.discover(
            Crash(hist),
            pool_size=100,
            top=5,
            checkpoint_path=cp,
            workers=1,
            sleep=NOSLEEP,
        )
    saved = json.loads(cp.read_text())
    assert set(saved["pages"]) == {"1", "2"}
    assert 0 < len(saved["ranks"]) < 100

    client = FakeClient(hist)
    got = elite.discover(
        client, pool_size=100, top=5, checkpoint_path=cp, workers=1, sleep=NOSLEEP
    )
    assert [m.id for m in got] == [1, 2, 3, 4, 5]
    # No standings page re-read, and only histories not already done are fetched.
    assert not any(c.startswith("leagues") for c in client.calls)
    assert len(client.calls) == 100 - len(saved["ranks"])
    assert len(json.loads(cp.read_text())["ranks"]) == 100


def test_load_or_discover_reuses_fresh_file(tmp_path):
    path = tmp_path / "elite.json"
    client = FakeClient({1: h(3, 3, 3), 2: h(1, 1, 1)})
    got = elite.load_or_discover(
        client, path, pool_size=50, top=10, sleep=lambda s: None
    )
    assert [m.id for m in got] == [2, 1]
    assert json.loads(path.read_text())["seasons"] == SEASONS
    n = len(client.calls)
    again = elite.load_or_discover(client, path, pool_size=50, top=10)
    assert [m.id for m in again] == [2, 1] and len(client.calls) == n
    later = datetime.now(UTC) + timedelta(days=31)
    elite.load_or_discover(client, path, pool_size=50, top=10, now=later, sleep=NOSLEEP)
    assert len(client.calls) > n


def _stale_saved(tmp_path, ids):
    path = tmp_path / "elite.json"
    elite.save_managers(path, [mgr(i) for i in ids], SEASONS, 50)
    later = datetime.now(UTC) + timedelta(days=31)
    return path, path.read_text(), later


def test_load_or_discover_keeps_saved_list_when_rediscovery_is_empty(tmp_path):
    path, before, later = _stale_saved(tmp_path, [1, 2, 3])
    assert not elite.discovery_backoff_active(path, later)
    client = FakeClient({})  # nobody in the standings
    client.ids = [7, 8]  # in the standings, but every history 404s
    got = elite.load_or_discover(
        client, path, pool_size=50, top=10, now=later, sleep=NOSLEEP
    )
    assert [m.id for m in got] == [1, 2, 3]
    assert path.read_text() == before  # untouched
    assert elite.attempt_path(path).exists()
    assert elite.discovery_backoff_active(path, later)
    assert elite.discovery_backoff_active(path, later + timedelta(hours=23))
    assert not elite.discovery_backoff_active(path, later + timedelta(hours=25))


def test_load_or_discover_keeps_saved_list_when_result_shrinks_below_half(tmp_path):
    path, before, later = _stale_saved(tmp_path, [1, 2, 3, 4, 5])
    client = FakeClient({9: h(1, 1, 1), 10: h(2, 2, 2)})  # 2 < 5 / 2
    got = elite.load_or_discover(
        client, path, pool_size=50, top=10, now=later, sleep=NOSLEEP
    )
    assert [m.id for m in got] == [1, 2, 3, 4, 5]
    assert path.read_text() == before
    assert elite.discovery_backoff_active(path, later)


def test_load_or_discover_records_failed_attempt_and_clears_it_on_success(tmp_path):
    path, before, later = _stale_saved(tmp_path, [1, 2])

    class Down(FakeClient):
        def get_uncached(self, path, retries=3):
            raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        elite.load_or_discover(Down({}), path, pool_size=50, now=later, sleep=NOSLEEP)
    assert path.read_text() == before
    assert elite.discovery_backoff_active(path, later)

    client = FakeClient({9: h(1, 1, 1), 10: h(2, 2, 2)})  # 2 >= 2 / 2: accepted
    got = elite.load_or_discover(
        client, path, pool_size=50, top=10, now=later, sleep=NOSLEEP
    )
    assert [m.id for m in got] == [9, 10]
    assert [m.id for m in elite.load_managers(path)[1]] == [9, 10]
    assert not elite.attempt_path(path).exists()
    assert not elite.discovery_backoff_active(path, later)


# ---------------------------------------------------------------- politeness


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_throttle_spaces_requests_across_workers():
    clock = FakeClock()
    th = elite.Throttle(max_rps=4, clock=clock, sleep=clock.sleep)
    times = []
    for _ in range(5):  # any thread's call takes the next shared slot
        th.acquire()
        times.append(clock.t)
    assert times == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_throttle_is_shared_between_real_threads():
    th = elite.Throttle(max_rps=200)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: th.acquire(), range(41)))
    # 41 requests at 200/s need at least 40 gaps of 5ms, however many threads ask.
    assert time.monotonic() - start >= 0.19


def test_throttle_shared_cool_down_backs_off_and_honours_retry_after():
    clock = FakeClock()
    th = elite.Throttle(max_rps=8, clock=clock, sleep=clock.sleep)
    assert th.cool_down() == 30
    # Refusals of requests already in flight do not extend or escalate the pause.
    clock.t = 10
    assert th.cool_down() == 20
    th.acquire()  # every thread waits out the pause
    assert clock.t == 30
    assert th.cool_down() == 60  # refused again right after: back off
    th.acquire()
    assert clock.t == 90
    th.ok()
    assert th.cool_down() == 30  # a success resets the back-off
    th.acquire()
    assert th.cool_down(retry_after=7) == 7  # the server's Retry-After wins
    th.acquire()
    assert clock.t == 127


def test_fetch_429_pauses_everyone_with_a_single_retry_layer():
    clock = FakeClock()
    th = elite.Throttle(max_rps=8, clock=clock, sleep=clock.sleep)
    backoff_sleeps = []

    class Busy(FakeClient):
        refusals = 2

        def get_uncached(self, path, retries=3):
            assert retries == 1
            self.calls.append(path)
            if self.refusals:
                self.refusals -= 1
                raise FPLError("Failed: 429", status=429, retry_after=None)
            return {"ok": True}

    client = Busy({})
    got = elite._fetch(
        client, "entry/1/history/", sleep=backoff_sleeps.append, throttle=th
    )
    assert got == {"ok": True}
    assert len(client.calls) == 3
    assert backoff_sleeps == []  # 429s wait in the shared cool-down, not privately
    assert clock.t == 30 + 60

    class Gone(FakeClient):
        def get_uncached(self, path, retries=3):
            raise FPLError("Failed to fetch", status=404)

    with pytest.raises(elite.NotFound):
        elite._fetch(Gone({}), "entry/2/history/", sleep=NOSLEEP, throttle=th)


def test_fetch_retry_after_from_server_is_used():
    clock = FakeClock()
    th = elite.Throttle(max_rps=8, clock=clock, sleep=clock.sleep)

    class Limited(FakeClient):
        first = True

        def get_uncached(self, path, retries=3):
            if self.first:
                self.first = False
                raise FPLError("Failed: 429", status=429, retry_after=12)
            return {"ok": True}

    assert elite._fetch(Limited({}), "x", sleep=NOSLEEP, throttle=th) == {"ok": True}
    assert clock.t == 12


# ---------------------------------------------------------------- summarise


def mgr(eid):
    return EliteManager(
        eid, "", "", dict.fromkeys(SEASONS, eid * 100), eid * 100.0, eid * 100
    )


def picks(players, captain, chip=None):
    return {
        "active_chip": chip,
        "picks": [{"element": p, "is_captain": p == captain} for p in players],
    }


def world_with_events(now):
    events = [
        Event(1, now - timedelta(days=14), True, False, False),
        Event(2, now - timedelta(days=7), True, False, False),
        Event(3, now - timedelta(hours=1), False, True, False),
        Event(4, now + timedelta(days=6), False, False, True),
    ]
    return World(
        players={},
        teams={},
        fixtures=[],
        events=events,
        history={},
        past={},
        chips=[],
    )


def test_summarise_shares_transfers_free_hit_and_404():
    now = datetime.now(UTC)
    world = world_with_events(now)
    gw = 3
    client = FakeClient(
        {},
        picks={
            (1, gw): picks([10, 11, 12], captain=10),
            (2, gw): picks([10, 11, 13], captain=11),
            (3, gw): picks([10, 14, 15], captain=10, chip="freehit"),
            (4, gw): picks([10, 11, 16], captain=10),
            # manager 5: picks 404 -> left out of every share
        },
        transfers={
            1: [
                {"event": gw, "element_in": 12, "element_out": 20},
                {"event": gw - 1, "element_in": 11, "element_out": 21},  # older week
            ],
            2: [{"event": gw, "element_in": 13, "element_out": 20}],
            # Free Hit: these swaps revert next week, so they are not buys/sells.
            3: [
                {"event": gw, "element_in": 14, "element_out": 11},
                {"event": gw, "element_in": 15, "element_out": 12},
            ],
            # Wildcard churn: bought 30 then sold it again the same week -> neither.
            4: [
                {"event": gw, "element_in": 30, "element_out": 22},
                {"event": gw, "element_in": 16, "element_out": 30},
            ],
        },
    )
    sig = elite.summarise(client, [mgr(i) for i in range(1, 6)], world, now=now)
    assert sig.elite.gameweek == 3
    assert sig.elite.managers == 4
    assert sig.elite.seasons == SEASONS
    assert sig.elite.median_rank == 250.0
    assert "elite" in sig.updated
    p = sig.players
    assert p[10].elite_own == 1.0 and p[10].elite_captain == 0.75
    assert p[11].elite_own == 0.75 and p[11].elite_captain == 0.25
    assert p[14].elite_own == 0.25 and p[14].elite_in == 0
    assert p[12].elite_in == 1 and p[13].elite_in == 1 and p[16].elite_in == 1
    assert p[20].elite_out == 2 and p[22].elite_out == 1
    assert p[11].elite_out == 0 and p[12].elite_out == 0
    # Churned and older-week players are not counted (absent: this world has no player list).
    assert 30 not in p and 21 not in p


def test_summarise_too_few_picks_is_empty_and_uncacheable():
    # FPL answers 503 for a while after a deadline: 3 of 5 (60%) is not a trustworthy week.
    now = datetime.now(UTC)
    client = FakeClient(
        {}, picks={(i, 3): picks([10, 11], captain=10) for i in (1, 2, 3)}
    )
    sig = elite.summarise(
        client, [mgr(i) for i in range(1, 6)], world_with_events(now), now=now
    )
    assert sig.elite is None
    assert "elite" not in sig.updated
    assert not sig.players


def test_summarise_no_passed_deadline_is_empty():
    now = datetime.now(UTC)
    world = world_with_events(now - timedelta(days=30))
    world.events = [Event(1, now + timedelta(days=1), False, False, True)]
    sig = elite.summarise(FakeClient({}), [mgr(1)], world, now=now)
    assert sig.elite is None and not sig.players


def test_signals_roundtrip():
    now = datetime.now(UTC)
    client = FakeClient({}, picks={(1, 3): picks([10], captain=10)})
    sig = elite.summarise(client, [mgr(1)], world_with_events(now), now=now)
    back = type(sig).from_json(sig.to_json())
    assert back.elite == sig.elite and back.players[10].elite_captain == 1.0

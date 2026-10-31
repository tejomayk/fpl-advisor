import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fpl_advisor import alerts_service as svc
from fpl_advisor import web
from fpl_advisor.api import FPLError
from fpl_advisor.notify import Notification, NotifyError, NtfyNotifier


class FakeClient:
    def entry(self, team_id):
        if team_id == 404:
            raise FPLError("Not found: entry")
        return {"name": "Test FC"}


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(
        NtfyNotifier, "send", lambda self, n: out.append((self.topic, n))
    )
    monkeypatch.setenv("NTFY_TOKEN", "")
    return out


@pytest.fixture
def app(tmp_path, monkeypatch, sent):
    monkeypatch.setenv("FPL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISABLE_ALERT_SCHEDULER", "1")
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    return web.create_app(client=FakeClient(), start_background=False, alerts=True)


@pytest.fixture
def http(app):
    return TestClient(app)


def _create(http, team=1, league=None):
    return http.post("/api/alerts", json={"team": team, "league": league})


def _status(http, topic, token):
    return http.post("/api/alerts/status", json={"topic": topic, "manage_token": token})


def test_create_status_delete(http, sent):
    r = _create(http, 5, 9)
    assert r.status_code == 201
    d = r.json()
    assert d["topic"].startswith("fpl-") and d["topic"] == d["topic"].lower()
    assert d["subscribe_url"] == f"https://ntfy.sh/{d['topic']}"
    assert d["reminder_hours"] == [24, 2]
    assert sent and sent[0][0] == d["topic"]
    assert "Test FC" in sent[0][1].title
    s = _status(http, d["topic"], d["manage_token"])
    assert s.status_code == 200
    body = s.json()
    assert (
        body["team"] == 5 and body["league"] == 9 and body["reminder_hours"] == [24, 2]
    )
    assert body["created"]
    assert body["active"] is True and body["inactive_reason"] is None
    bad = http.request(
        "DELETE", "/api/alerts", json={"topic": d["topic"], "manage_token": "x"}
    )
    assert bad.status_code == 404 and "error" in bad.json()
    ok = http.request(
        "DELETE",
        "/api/alerts",
        json={"topic": d["topic"], "manage_token": d["manage_token"]},
    )
    assert ok.status_code == 204
    assert _status(http, d["topic"], d["manage_token"]).status_code == 404


def test_status_wrong_token(http):
    d = _create(http).json()
    assert _status(http, d["topic"], "bad").status_code == 404
    assert _status(http, "fpl-nope", "bad").status_code == 404


def test_status_get_removed(http):
    d = _create(http).json()
    r = http.get(
        "/api/alerts/status", params={"topic": d["topic"], "token": d["manage_token"]}
    )
    assert r.status_code in (404, 405)
    assert "manage_token" not in r.text and d["manage_token"] not in r.text


def test_status_shows_inactive(http, app):
    d = _create(http).json()
    svc.get_store(app).deactivate_all("season_ended")
    body = _status(http, d["topic"], d["manage_token"]).json()
    assert body["active"] is False and body["inactive_reason"] == "season_ended"


def test_token_not_stored_plaintext(http, tmp_path):
    d = _create(http).json()
    for f in tmp_path.glob("alerts.db*"):
        assert d["manage_token"].encode() not in f.read_bytes()


def test_welcome_failure_still_201(http, monkeypatch):
    def boom(self, n):
        raise RuntimeError("down")

    monkeypatch.setattr(NtfyNotifier, "send", boom)
    assert _create(http).status_code == 201


def test_rate_limit(http):
    for i in range(5):
        assert _create(http, i + 1).status_code == 201
    r = _create(http, 50)
    assert r.status_code == 429 and "error" in r.json()


def test_invalid_team(http):
    r = _create(http, 404)
    assert r.status_code == 404 and "error" in r.json()


def test_capacity(http, monkeypatch):
    monkeypatch.setenv("MAX_SUBSCRIPTIONS", "1")
    assert _create(http).status_code == 201
    assert _create(http).status_code == 503


def test_capacity_default(monkeypatch):
    monkeypatch.delenv("MAX_SUBSCRIPTIONS", raising=False)
    assert svc.max_subscriptions() == 300


def test_capacity_refusal_does_not_use_rate_limit(http, monkeypatch):
    monkeypatch.setenv("MAX_SUBSCRIPTIONS", "1")
    assert _create(http, 1).status_code == 201
    for _ in range(10):
        assert _create(http, 2).status_code == 503
    monkeypatch.setenv("MAX_SUBSCRIPTIONS", "100")
    assert _create(http, 2).status_code == 201  # quota was not spent on refusals


def test_per_team_cap(http, app):
    for _ in range(svc.MAX_PER_TEAM):
        assert _create(http, 7).status_code == 201
    r = _create(http, 7)
    assert r.status_code == 429 and "error" in r.json()
    assert _create(http, 8).status_code == 201


def test_spoofed_forwarded_for_does_not_bypass_limit(http):
    for i in range(5):
        r = http.post(
            "/api/alerts",
            json={"team": 10 + i},
            headers={"X-Forwarded-For": f"203.0.113.{i}"},
        )
        assert r.status_code == 201
    r = http.post(
        "/api/alerts", json={"team": 99}, headers={"X-Forwarded-For": "198.51.100.9"}
    )
    assert r.status_code == 429


def test_limiter_memory_bounded():
    lim = svc._Limiter(2, 100)
    for i in range(1000):
        assert lim.allow(f"ip{i}", now=1000.0 + i * 0.01)
    assert len(lim._hits) == 1000
    # Much later, a single request sweeps out every expired entry.
    assert lim.allow("fresh", now=5000.0)
    assert list(lim._hits) == ["fresh"]
    # Hard cap on tracked addresses while all entries are live.
    small = svc._Limiter(1, 100, max_ips=3)
    assert [small.allow(f"x{i}", now=1.0) for i in range(5)] == [True] * 3 + [False] * 2
    assert len(small._hits) == 3


def test_limiter_window_expires():
    lim = svc._Limiter(1, 10)
    assert lim.allow("a", now=0) and not lim.allow("a", now=5)
    assert lim.allow("a", now=11)


def test_welcome_uses_ntfy_token(http, monkeypatch):
    monkeypatch.setenv("NTFY_TOKEN", "tk_test")
    tokens = []
    monkeypatch.setattr(NtfyNotifier, "send", lambda self, n: tokens.append(self.token))
    assert _create(http).status_code == 201
    assert tokens == ["tk_test"]


# ------------------------------------------------------------ scheduler


def _world(hours=10.0, ev_id=1):
    ev = SimpleNamespace(id=ev_id, deadline=datetime.now(UTC) + timedelta(hours=hours))
    return SimpleNamespace(next_event=ev)


@pytest.fixture
def cycle(app, monkeypatch):
    holder = SimpleNamespace(world=_world(), builds=[])

    class Worlds:
        def get(self):
            return holder.world

    app.state.worlds = Worlds()

    def fake_build(cfg, client, world=None):
        holder.builds.append((cfg.team_id, cfg.league_id))
        return cfg

    monkeypatch.setattr(svc, "build_advice", fake_build)
    store = svc.get_store(app)

    def fake_collect(cfg, _cfg, state, click=None):
        key = f"deadline:1:{cfg.reminder_hours[0]}"
        if state.seen(key):
            return []
        if cfg.team_id == 2:
            raise RuntimeError("boom")
        return [([key], Notification(title="GW1 deadline", body="b", click=click))]

    # build_advice returns cfg, so collect_alerts(advice, cfg, ...) gets cfg twice
    monkeypatch.setattr(svc, "collect_alerts", fake_collect)
    store.holder = holder
    return store


def test_cycle_sends_once(app, cycle, sent, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://x.example")
    cycle.create("tA", "tok", 1, 7, [24, 2])
    stats = svc.run_alert_cycle(app, sleep=0)
    assert stats["sent"] == 1
    assert sent[0][0] == "tA"
    assert sent[0][1].click == "https://x.example/?team=1&league=7"
    svc.run_alert_cycle(app, sleep=0)
    assert len(sent) == 1


def test_cycle_isolates_failures(app, cycle, sent):
    cycle.create("tBad", "t", 2, None, [24])
    cycle.create("tGood", "t", 3, None, [24])
    stats = svc.run_alert_cycle(app, sleep=0)
    assert stats["failed"] == 1 and stats["sent"] == 1
    assert [t for t, _ in sent] == ["tGood"]
    assert cycle.get("tBad").failures == 1
    assert cycle.get("tGood").failures == 0


def test_season_end_deactivates_without_failures(app, cycle, sent):
    cycle.create("tA", "t", 1, None, [24])
    cycle.create("tB", "t", 3, None, [24])
    cycle.holder.world = SimpleNamespace(next_event=None)
    stats = svc.run_alert_cycle(app, sleep=0)
    assert stats["failed"] == 0 and not cycle.holder.builds
    assert cycle.list_active() == []
    sub = cycle.get("tA")
    assert sub.active is False and sub.inactive_reason == "season_ended"
    assert sub.failures == 0
    assert cycle.count() == 0  # capacity freed


def test_inactive_rows_pruned_after_30_days(app, cycle):
    cycle.create("tA", "t", 1, None, [24])
    cycle.deactivate_all("season_ended")
    with sqlite3.connect(cycle.path) as c:
        old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        c.execute("UPDATE subscriptions SET deactivated_at=?", (old,))
    svc.run_alert_cycle(app, sleep=0)
    assert cycle.get("tA") is None


def test_notifier_429_stops_cycle_and_keeps_keys_unmarked(app, cycle, monkeypatch):
    calls = []

    def limited(self, n):
        calls.append(self.topic)
        raise NotifyError("rate limited", status=429)

    monkeypatch.setattr(NtfyNotifier, "send", limited)
    cycle.create("tA", "t", 1, None, [24])
    cycle.create("tB", "t", 3, None, [24])
    stats = svc.run_alert_cycle(app, sleep=0)
    assert calls == ["tA"]  # stopped after the first 429
    assert stats["failed"] == 0 and stats["rate_limited"] == 1
    assert cycle.get("tA").failures == 0 and cycle.get("tB").failures == 0
    assert not cycle.state_seen("tA", "deadline:1:24")
    # Next cycle retries.
    monkeypatch.setattr(NtfyNotifier, "send", lambda self, n: calls.append("ok"))
    # tA was built moments ago, but its reminder is still due and unsent.
    stats = svc.run_alert_cycle(app, sleep=0)
    assert stats["sent"] == 2 and cycle.state_seen("tA", "deadline:1:24")


@pytest.mark.parametrize("status", [500, 503, None])
def test_notifier_errors_do_not_count_as_failures(app, cycle, monkeypatch, status):
    def down(self, n):
        raise NotifyError("down", status=status)

    monkeypatch.setattr(NtfyNotifier, "send", down)
    cycle.create("tA", "t", 1, None, [24])
    cycle.create("tB", "t", 3, None, [24])
    stats = svc.run_alert_cycle(app, sleep=0)
    assert stats["failed"] == 0 and stats["transient"] == 2
    assert cycle.get("tA").failures == 0 and cycle.get("tB").failures == 0
    assert not cycle.state_seen("tA", "deadline:1:24")


def test_fpl_not_found_counts_but_fpl_outage_does_not(app, cycle, monkeypatch):
    def fake_build(cfg, client, world=None):
        raise FPLError("Not found: entry" if cfg.team_id == 1 else "HTTP 503")

    monkeypatch.setattr(svc, "build_advice", fake_build)
    cycle.create("tA", "t", 1, None, [24])
    cycle.create("tB", "t", 3, None, [24])
    stats = svc.run_alert_cycle(app, sleep=0)
    assert stats["failed"] == 1 and stats["transient"] == 1
    assert cycle.get("tA").failures == 1 and cycle.get("tB").failures == 0


def test_scheduled_sends_use_ntfy_token(app, cycle, monkeypatch):
    monkeypatch.setenv("NTFY_TOKEN", "tk_test")
    tokens = []
    monkeypatch.setattr(NtfyNotifier, "send", lambda self, n: tokens.append(self.token))
    cycle.create("tA", "t", 1, None, [24])
    svc.run_alert_cycle(app, sleep=0)
    assert tokens == ["tk_test"]


def test_full_build_skipped_when_not_due(app, cycle, sent):
    cycle.create("tA", "t", 1, None, [24])
    svc.run_alert_cycle(app, sleep=0)  # first cycle: builds and sends
    assert len(cycle.holder.builds) == 1 and len(sent) == 1
    stats = svc.run_alert_cycle(app, sleep=0)  # sent already, <6h since build
    assert len(cycle.holder.builds) == 1
    assert stats["skipped"] == 1 and stats["built"] == 0


def test_full_build_when_window_due(app, cycle, sent, monkeypatch):
    monkeypatch.setattr(svc, "collect_alerts", lambda *a, **k: [])  # never marks keys
    cycle.create("tA", "t", 1, None, [24, 2])
    cycle.holder.world = _world(hours=30)  # no window open yet
    svc.run_alert_cycle(app, sleep=0)  # first cycle builds anyway
    assert len(cycle.holder.builds) == 1
    svc.run_alert_cycle(app, sleep=0)
    assert len(cycle.holder.builds) == 1  # not due, not stale
    cycle.holder.world = _world(hours=20)  # 24h window opens
    svc.run_alert_cycle(app, sleep=0)
    assert len(cycle.holder.builds) == 2


def test_full_build_when_stale(app, cycle):
    cycle.create("tA", "t", 1, None, [24])
    cycle.holder.world = _world(hours=100)
    svc.run_alert_cycle(app, sleep=0)
    svc.run_alert_cycle(app, sleep=0)
    assert len(cycle.holder.builds) == 1
    cycle.set_last_full_build("tA", datetime.now(UTC) - timedelta(hours=7))
    svc.run_alert_cycle(app, sleep=0)
    assert len(cycle.holder.builds) == 2


def test_shared_team_league_built_once(app, cycle, sent):
    cycle.create("tA", "t", 1, 5, [24])
    cycle.create("tB", "t", 1, 5, [24])
    cycle.create("tC", "t", 1, 6, [24])
    stats = svc.run_alert_cycle(app, sleep=0)
    assert sorted(cycle.holder.builds) == [(1, 5), (1, 6)]
    assert stats["built"] == 2 and stats["reused"] == 1 and stats["sent"] == 3


def test_sleep_only_after_full_builds(app, cycle, monkeypatch):
    naps = []
    monkeypatch.setattr(svc.time, "sleep", lambda s: naps.append(s))
    cycle.create("tA", "t", 1, None, [24])
    svc.run_alert_cycle(app, sleep=0.2)
    assert naps == [0.2]
    naps.clear()
    svc.run_alert_cycle(app, sleep=0.2)  # nothing built
    assert naps == []


def test_seconds_until_run():
    t = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert svc.seconds_until_run(t, 7) == 7 * 60
    assert svc.seconds_until_run(t.replace(minute=7), 7) == 3600
    assert svc.seconds_until_run(t.replace(minute=30), 7) == 37 * 60

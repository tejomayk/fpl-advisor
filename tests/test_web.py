"""Web API tests with a fake FPL client (no network)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fpl_advisor import web
from fpl_advisor.api import FPLError


class FailingClient:
    def __init__(self, error: str):
        self.error = error

    def __getattr__(self, name):
        def fail(*_a, **_k):
            raise FPLError(self.error)

        return fail


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("FPL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISABLE_ALERT_SCHEDULER", "1")

    def _make(fpl_client):
        return TestClient(web.create_app(client=fpl_client, start_background=False))

    return _make


def test_health(make_client):
    c = make_client(FailingClient("x"))
    assert c.get("/api/health").json()["ok"] is True


def test_unknown_team_is_a_friendly_404(make_client):
    c = make_client(FailingClient("Not found: https://... (check your team/league ID)"))
    r = c.get("/api/advice", params={"team": 123})
    assert r.status_code == 404
    assert "couldn't find" in r.json()["error"]


def test_fpl_updating_is_a_503(make_client):
    c = make_client(FailingClient("FPL is updating (503). Try again in a few minutes."))
    r = c.get("/api/advice", params={"team": 123})
    assert r.status_code == 503
    assert "updating" in r.json()["error"]


def test_bad_params_rejected(make_client):
    c = make_client(FailingClient("x"))
    r = c.get("/api/advice", params={"team": "abc"})
    assert r.status_code == 422
    assert "valid FPL ID" in r.json()["error"]
    assert c.get("/api/advice", params={"team": 1, "risk": "yolo"}).status_code == 422


def test_rate_limit(make_client, monkeypatch):
    monkeypatch.setattr(web, "RATE_LIMIT", (2, 60))
    c = make_client(FailingClient("Not found: x"))
    codes = [c.get("/api/advice", params={"team": t}).status_code for t in (1, 2, 3)]
    assert codes == [404, 404, 429]


def test_advice_cache_returns_same_object():
    cache = web.AdviceCache()
    cache.put((1, None, "auto"), {"a": 1})
    assert cache.get((1, None, "auto")) == {"a": 1}
    assert cache.get((2, None, "auto")) is None


def test_spoofed_forwarded_for_does_not_bypass_rate_limit(make_client, monkeypatch):
    monkeypatch.setattr(web, "RATE_LIMIT", (2, 60))
    c = make_client(FailingClient("Not found: x"))
    codes = [
        c.get(
            "/api/advice",
            params={"team": 1},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        ).status_code
        for i in range(3)
    ]
    assert codes[-1] == 429


def test_security_headers(make_client):
    r = make_client(FailingClient("x")).get("/api/health")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_validation_message_is_route_specific(make_client):
    c = make_client(FailingClient("x"))
    assert "valid FPL ID" in c.get("/api/advice", params={"team": 0}).json()["error"]


class _World:
    def __init__(self, tag):
        self.tag = tag
        self.next_event = None


def test_world_cache_serves_stale_and_keeps_old_on_failed_refresh(monkeypatch):
    calls = {"n": 0}

    def fake_load(_client):
        calls["n"] += 1
        if calls["n"] == 2:
            raise web.FPLError("FPL is updating")
        return _World(calls["n"])

    monkeypatch.setattr(web, "load_world", fake_load)
    cache = web.WorldCache(client=None)
    assert cache.get().tag == 1
    cache._loaded_at = 0  # force stale
    assert cache.get().tag == 1  # served immediately while the refresh runs
    for _ in range(100):
        if not cache._refreshing.is_set():
            break
        import time

        time.sleep(0.01)
    assert cache.get().tag == 1  # failed refresh keeps the old snapshot
    assert calls["n"] == 2


def test_world_cache_first_load_failure_is_not_retried_immediately(monkeypatch):
    calls = {"n": 0}

    def fail(_client):
        calls["n"] += 1
        raise web.FPLError("down")

    monkeypatch.setattr(web, "load_world", fail)
    cache = web.WorldCache(client=None)
    for _ in range(3):
        with pytest.raises(web.FPLError):
            cache.get()
    assert calls["n"] == 1


def test_prune_cache(tmp_path):
    import os
    import time

    from fpl_advisor.api import prune_cache

    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (time.time() - 10_000, time.time() - 10_000))
    assert prune_cache(tmp_path, max_age_seconds=3600) == 1
    assert not old.exists() and new.exists()


def test_alert_routes_are_off_unless_enabled(make_client, monkeypatch):
    c = make_client(FailingClient("x"))
    assert c.post("/api/alerts/status", json={}).status_code == 404
    monkeypatch.setenv("FPL_ALERTS", "1")
    c = make_client(FailingClient("x"))
    assert c.post("/api/alerts/status", json={}).status_code != 404

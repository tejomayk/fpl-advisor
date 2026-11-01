"""The Vercel (serverless) web app and its config: reads the stored snapshot, holds no paid keys,
makes no paid calls, routes /api/* and static paths. Fake Blob, fake FPL; no network."""

from __future__ import annotations

import importlib.util
import json
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import PAID_HOSTS, make_world
from fakeblob import TOKEN, FakeBlob
from fastapi.testclient import TestClient
from test_no_leaks import FakeFPL

from fpl_advisor import jobs, store, web
from fpl_advisor.signals import PlayerSignal, Signals
from fpl_advisor.world import World

ROOT = Path(__file__).resolve().parents[1]


def future_world() -> World:
    """The synthetic world, moved so its next deadline is two days away."""
    w = make_world()
    shift = datetime.now(UTC) + timedelta(days=2) - w.next_event.deadline
    return replace(
        w, events=[replace(e, deadline=e.deadline + shift) for e in w.events]
    )


class CountingFPL(FakeFPL):
    def __init__(self):
        self.bootstraps = 0

    def bootstrap(self):  # load_world's first call: counts live loads
        self.bootstraps += 1
        raise web.FPLError("live load attempted")


@pytest.fixture
def paid_wire(monkeypatch):
    seen = []
    real = urllib.request.urlopen

    def guarded(req, *a, **k):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        if urlsplit(url).hostname in PAID_HOSTS:
            seen.append(url)
            raise RuntimeError("paid call from the web app")
        return real(req, *a, **k)

    monkeypatch.setattr(urllib.request, "urlopen", guarded)
    return seen


def _site(st, fpl=None):
    app = web.create_app(client=fpl or CountingFPL(), serverless=True, store=st)
    return TestClient(app, raise_server_exceptions=False), app


def _store_with_snapshot(world, signals=None, saved=None):
    fake = FakeBlob()
    st = store.BlobStore(TOKEN, opener=fake, sleep=lambda s: None)
    snap = jobs.make_snapshot(world, signals or Signals(), saved or datetime.now(UTC))
    st.put_json(store.WEB_SNAPSHOT_KEY, snap)
    return fake, st


def test_advice_comes_from_the_stored_snapshot_with_no_paid_calls(paid_wire):
    w = future_world()
    sig = Signals(players={5: PlayerSignal(news="Fit again")}, updated={"news": "t"})
    fake, st = _store_with_snapshot(w, sig)
    http, app = _site(st)
    reads = fake.reads
    for team in (1, 2, 3):
        r = http.get("/api/advice", params={"team": team})
        assert r.status_code == 200, r.text
    assert fake.reads == reads + 1  # one store read, then the in-memory copy
    assert app.state.worlds.client.bootstraps == 0  # no live FPL load needed
    assert paid_wire == []
    assert http.get("/api/health").json() == {
        "ok": True,
        "world_loaded": True,
        "mode": "serverless",
    }


def test_missing_or_stale_snapshot_falls_back_to_live_fpl(paid_wire):
    w = future_world()
    for _fake, st in (
        (None, store.BlobStore(TOKEN, opener=FakeBlob())),  # no snapshot
        _store_with_snapshot(
            w, saved=datetime.now(UTC) - timedelta(hours=7)
        ),  # too old
        _store_with_snapshot(make_world()),  # its deadline has passed
    ):
        http, app = _site(st)
        r = http.get("/api/advice", params={"team": 1})
        assert app.state.worlds.client.bootstraps == 1  # tried FPL live
        assert r.status_code in (200, 503)
        assert paid_wire == []


def test_serverless_refuses_paid_keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "ab" * 20)  # fake
    with pytest.raises(RuntimeError) as e:
        web.create_app(
            client=CountingFPL(), serverless=True, store=store.LocalStore("x")
        )
    assert "OPENROUTER_API_KEY" in str(e.value) and "ab" * 20 not in str(e.value)


def test_serverless_starts_no_background_work(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(web, "_start_signals", lambda *a: started.append("signals"))
    monkeypatch.setattr(web, "_start_cache_pruner", lambda *a: started.append("prune"))
    monkeypatch.setattr(web, "_start_alerts", lambda *a: started.append("alerts"))
    _fake, st = _store_with_snapshot(future_world())
    with TestClient(
        web.create_app(client=CountingFPL(), serverless=True, store=st)
    ) as c:
        c.get("/api/health")
        app = c.app
    assert started == []
    assert not isinstance(app.state.worlds, web.WorldCache)
    assert not isinstance(_site(st)[1].state.signals, web.SignalsHub)


def test_vercel_mode_is_detected_and_uses_the_edge_ip(monkeypatch):
    assert web.serverless_mode({"VERCEL": "1"}) and not web.serverless_mode({})
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(web, "RATE_LIMIT", (1, 60))
    _fake, st = _store_with_snapshot(future_world())
    http, _app = _site(st)
    a = http.get("/api/advice", params={"team": 1}, headers={"x-real-ip": "1.1.1.1"})
    b = http.get("/api/advice", params={"team": 1}, headers={"x-real-ip": "2.2.2.2"})
    c = http.get("/api/advice", params={"team": 1}, headers={"x-real-ip": "1.1.1.1"})
    assert (a.status_code, b.status_code, c.status_code) == (200, 200, 429)


def test_entrypoint_routes_api_and_static(monkeypatch, tmp_path):
    """index.py (what Vercel runs) serves the API at its real paths, plus the page and static
    files (on Vercel those come from the CDN instead)."""
    monkeypatch.setenv("FPL_DATA_DIR", str(tmp_path))
    st = store.LocalStore(tmp_path)
    st.put_json(
        store.WEB_SNAPSHOT_KEY,
        jobs.make_snapshot(future_world(), Signals(), datetime.now(UTC)),
    )
    monkeypatch.setattr(web, "FPLClient", lambda *a, **k: CountingFPL())
    spec = importlib.util.spec_from_file_location("vercel_index", ROOT / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    http = TestClient(mod.app)
    assert http.get("/api/health").json()["mode"] == "serverless"
    assert http.get("/api/advice", params={"team": 1}).status_code == 200
    assert http.get("/api/advice", params={"team": "x"}).status_code == 422
    assert "<html" in http.get("/").text.lower()
    assert http.get("/static/app.js").status_code == 200
    assert http.get("/no-such-page").status_code == 404


# ------------------------------------------------------------------ vercel.json & friends


def _vercel():
    return json.loads((ROOT / "vercel.json").read_text())


def test_vercel_json_headers_match_the_app():
    cfg = _vercel()
    every = next(h for h in cfg["headers"] if h["source"] == "/(.*)")
    got = {h["key"]: h["value"] for h in every["headers"]}
    assert got == web.SECURITY_HEADERS  # static files get the same CSP as the API


def test_vercel_json_function_settings():
    cfg = _vercel()
    fn = cfg["functions"]["index.py"]
    assert fn["maxDuration"] <= 60
    assert cfg["regions"] == ["iad1"]
    assert (
        "public/" in cfg["buildCommand"]
        and "src/fpl_advisor/static" in cfg["buildCommand"]
    )
    assert "rewrites" not in cfg  # the FastAPI preset keeps the original path


def test_vercel_files_hold_no_secrets():
    text = (ROOT / "vercel.json").read_text() + (ROOT / "index.py").read_text()
    for word in ("API_KEY", "TOKEN", "sk-or-", "AIza", "vercel_blob_rw"):
        assert word not in text


def test_vercelignore_keeps_keys_and_data_out():
    lines = {
        ln.strip()
        for ln in (ROOT / ".vercelignore").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    }
    for must in (
        ".env",
        ".env.*",
        ".cache",
        "data",
        "state",
        "tests",
        ".venv",
        "scripts",
    ):
        assert must in lines, must
    for keep in ("src", "pyproject.toml", "uv.lock", "README.md", "index.py"):
        assert keep not in lines, keep


def test_workflows_are_single_writer_and_pinned():
    wf = ROOT / ".github" / "workflows"
    for name in ("refresh.yml", "elite.yml"):
        text = (wf / name).read_text()
        assert "group: refresh" in text and "cancel-in-progress: false" in text
        assert "FPL_NEWS_FEEDS" not in text.split("\non:")[1]  # BBC news stays off
        for line in text.splitlines():
            if "uses:" in line:
                ref = line.split("@", 1)[1].split()[0]
                assert len(ref) == 40, line  # pinned to a commit
        assert "echo $" not in text and "secrets." in text
    assert '"*/30 * * * *"' in (wf / "refresh.yml").read_text()
    assert "timeout-minutes: 330" in (wf / "elite.yml").read_text()
    secrets_wf = (wf / "secrets.yml").read_text()
    assert "push:" in secrets_wf and "pull_request:" in secrets_wf

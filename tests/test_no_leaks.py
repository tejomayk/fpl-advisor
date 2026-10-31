"""End to end: fake keys never reach a response, a log line or a file, and visitors' requests
never make a paid call. Every key here is fake and every paid API is faked."""

from __future__ import annotations

import functools
import io
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import timedelta
from typing import ClassVar
from urllib.parse import quote, urlsplit

import pytest
from conftest import make_world
from fastapi.testclient import TestClient

from fpl_advisor import cli, creators, keys, news, odds, web
from fpl_advisor import signals_service as svc
from fpl_advisor.api import FPLError

# Built at runtime so this file never contains a key-shaped string.
FAKE = {
    "ODDS_API_KEY": "0f" * 16,
    "OPENROUTER_API_KEY": "sk-or-v1-" + "cd34" * 16,
    "YOUTUBE_API_KEY": "AIza" + "Fake" * 8 + "Key",
    "NTFY_TOKEN": "tk_" + "n0t4r34lt0k3n" * 2,
}
PAID = ("api.the-odds-api.com", "openrouter.ai", "www.googleapis.com")

W = make_world()
DEADLINE = W.next_event.deadline
NOW = DEADLINE - timedelta(hours=48)

FEED = f"""<?xml version="1.0"?><rss><channel>
<item><title>Team 1 injury news: P5 is a doubt</title>
<link>https://news.example.com/a1</link>
<description>P5 has a hamstring knock and is a doubt for Team 1.</description>
<pubDate>{(NOW - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate>
</item></channel></rss>"""


def leaks(text: str | bytes) -> list[str]:
    """Names of fake keys found in `text` (raw or URL-encoded)."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return [k for k, v in FAKE.items() if v in text or quote(v, safe="") in text]


def files_with_keys(root) -> list[str]:
    return [str(p) for p in root.rglob("*") if p.is_file() and leaks(p.read_bytes())]


class PaidWire:
    """Stands in for urlopen. Feeds work; every paid API fails with an error whose message
    holds the full URL and headers (the worst case for a leak)."""

    def __init__(self, mode: str = "error"):
        self.mode = mode
        self.paid: list[str] = []

    def __call__(self, req, *a, **k):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        host = urlsplit(url).hostname
        headers = (
            dict(req.header_items()) if isinstance(req, urllib.request.Request) else {}
        )
        if host in PAID:
            self.paid.append(host)
            detail = f"{url} {headers}"
            if self.mode == "http":
                raise urllib.error.HTTPError(url, 500, detail, {}, io.BytesIO(b""))
            if self.mode == "weird":
                raise RuntimeError(detail)
            raise urllib.error.URLError(detail)
        if url.endswith("/robots.txt"):
            return io.BytesIO(b"")
        return _Resp(FEED.encode())


class _Resp(io.BytesIO):
    status = 200
    headers: ClassVar[dict] = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_keys(monkeypatch):
    for k, v in FAKE.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FPL_NEWS_FEEDS", "https://news.example.com/rss")
    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        svc.odds,
        "fetch_signals",
        functools.partial(odds.fetch_signals, sleep=lambda s: None),
    )
    monkeypatch.setattr(creators.config, "read_table", lambda name, path=None: {})
    keys.install_redaction()
    return FAKE


@pytest.mark.parametrize("mode", ["error", "http", "weird"])
def test_refresh_failures_leak_no_key(
    fake_keys, monkeypatch, tmp_path, caplog, capsys, mode
):
    wire = PaidWire(mode)
    monkeypatch.setattr(urllib.request, "urlopen", wire)
    hub = svc.SignalsHub(None, tmp_path)
    with caplog.at_level(logging.DEBUG):
        hub.refresh(W, now=NOW, discover=False)

        # A source that crashes with the key in its message: logged with a traceback.
        def boom(*a, **k):
            raise RuntimeError(
                "crash https://api.the-odds-api.com/v4/x?apiKey=" + FAKE["ODDS_API_KEY"]
            )

        monkeypatch.setattr(svc.creators, "fetch_signals", boom)
        hub.refresh(W, now=NOW + timedelta(hours=13), discover=False)
    # Every paid API was tried (so the error paths really ran) ...
    assert set(wire.paid) == set(PAID)
    # ... and no key is in any log line, any output or any file the app wrote.
    assert not leaks(caplog.text), caplog.text
    assert not leaks("".join(r.getMessage() for r in caplog.records))
    out = capsys.readouterr()
    assert not leaks(out.out + out.err)
    assert files_with_keys(tmp_path) == []
    assert "RuntimeError" in caplog.text  # errors are still reported, by type


def test_youtube_key_is_never_in_a_url(fake_keys, monkeypatch, tmp_path):
    seen = []

    def get(url, headers=None):
        seen.append((url, headers))
        raise urllib.error.URLError(url)

    creators.refresh_videos(
        tmp_path,
        [creators.Creator("A", "AlphaFPL")],
        FAKE["YOUTUBE_API_KEY"],
        W.next_event.id,
        NOW - timedelta(days=3),
        DEADLINE,
        NOW,
        get,
    )
    assert seen
    for url, headers in seen:
        assert not leaks(url)
        assert headers == {"X-Goog-Api-Key": FAKE["YOUTUBE_API_KEY"]}


def test_odds_cache_files_hold_only_response_bodies(fake_keys, tmp_path):
    def http(path, params):
        return 200, {"x-requests-last": "2", "x-requests-remaining": "400"}, []

    odds.fetch_signals(W, tmp_path, now=NOW, http=http, sleep=lambda s: None)
    assert files_with_keys(tmp_path) == []


# ---------------------------------------------------------------------------- web


class FakeFPL:
    """Public FPL endpoints for brand-new managers (so advice picks a squad from scratch)."""

    def entry(self, team_id):
        if team_id == 404:
            raise FPLError("Not found: entry?apiKey=" + FAKE["ODDS_API_KEY"])
        return {
            "player_first_name": "Test",
            "player_last_name": f"Manager{team_id}",
            "name": f"Team {team_id}",
            "started_event": 1,
            "leagues": {"classic": []},
        }

    def history(self, team_id):
        return {"current": [], "past": [], "chips": []}

    def element_summaries(self, ids):
        return {}

    def __getattr__(self, name):
        def fail(*a, **k):
            raise FPLError(f"{name} unavailable in this test")

        return fail


@pytest.fixture
def site(fake_keys, monkeypatch, tmp_path):
    monkeypatch.setenv("DISABLE_ALERT_SCHEDULER", "1")
    wire = PaidWire()
    monkeypatch.setattr(urllib.request, "urlopen", wire)
    app = web.create_app(client=FakeFPL(), start_background=False)
    hub = app.state.signals
    hub.refresh(W, now=NOW, discover=False)  # what the scheduled loop would do
    worlds = app.state.worlds
    worlds._world, worlds._loaded_at = W, time.time()
    monkeypatch.setattr(worlds, "refresh_in_background", lambda: None)

    calls = []
    for mod in (odds, news, creators):
        monkeypatch.setattr(
            mod, "fetch_signals", lambda *a, _m=mod.__name__, **k: calls.append(_m)
        )
    monkeypatch.setattr(
        svc.SignalsHub, "refresh", lambda *a, **k: calls.append("refresh")
    )
    wire.paid.clear()
    return TestClient(app, raise_server_exceptions=False), wire, calls, hub


def test_web_responses_never_contain_keys_and_make_no_paid_calls(site, caplog):
    http, wire, calls, hub = site
    hub._signals.gameweek = (
        999  # looks like a deadline passed: the request may only ask
    )
    bodies = []
    with caplog.at_level(logging.DEBUG):
        for team in (1, 2, 77, 1234567, 99_999_999):  # arbitrary visitors
            r = http.get("/api/advice", params={"team": team})
            assert r.status_code == 200, r.text
            bodies.append(r.text)
        assert set(json.loads(bodies[0])["sources"]) <= {
            "odds",
            "odds_gw",
            "news",
            "news_gw",
            "news_partial",
            "creators",
            "elite",
        }
        for path in (
            "/api/health",
            "/",
            "/static/app.js",
            "/api/advice?team=404",  # FPL error whose message holds a key
            "/api/advice?team=abc",
            "/api/advice?team=1&risk=yolo",
            "/no-such-page",
            "/api/alerts/status",
        ):
            r = http.get(path)
            bodies.append(r.text + json.dumps(dict(r.headers)))
    assert not any(leaks(b) for b in bodies)
    assert not leaks(caplog.text)
    assert wire.paid == [] and calls == []  # no paid call, no refresh from a request
    assert hub.wake.is_set()  # the scheduled loop was asked to refresh instead


def test_web_error_pages_hide_unexpected_errors(site, monkeypatch, caplog):
    http = site[0]

    def crash(*a, **k):
        raise RuntimeError(
            "https://openrouter.ai/x Bearer " + FAKE["OPENROUTER_API_KEY"]
        )

    monkeypatch.setattr(web, "build_advice", crash)
    with caplog.at_level(logging.ERROR):
        r = http.get("/api/advice", params={"team": 5})
    assert r.status_code == 500 and "Something went wrong" in r.json()["error"]
    assert not leaks(r.text) and not leaks(caplog.text)

    def bad_value(*a, **k):
        raise ValueError("bad input near key=" + FAKE["YOUTUBE_API_KEY"])

    monkeypatch.setattr(web, "build_advice", bad_value)
    r = http.get("/api/advice", params={"team": 6})
    assert r.status_code == 400 and not leaks(r.text)


# ---------------------------------------------------------------------------- CLI


def test_cli_errors_and_usage_never_print_keys(fake_keys, monkeypatch, capsys):
    class Broken:
        def __init__(self, *a, **k):
            raise FPLError("failed https://x/?apiKey=" + FAKE["ODDS_API_KEY"])

    monkeypatch.setattr(cli, "FPLClient", Broken)
    assert cli.main(["--team", "1", "advise"]) == 2
    assert cli.main(["usage"]) == 0
    out = capsys.readouterr()
    assert "***" in out.err
    assert not leaks(out.out + out.err)

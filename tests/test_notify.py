import io
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from fpl_advisor.config import Config
from fpl_advisor.notify import (
    Notification,
    NotifyError,
    NtfyNotifier,
    StdoutNotifier,
    get_notifier,
    run_url,
)


def ok_response(status=200):
    resp = MagicMock()
    resp.status = status
    resp.__enter__.return_value = resp
    return resp


def test_ntfy_request_headers_and_body():
    n = Notification(
        "Deadline soon",
        "Body ✓",
        priority=4,
        tags=["alarm_clock", "fpl"],
        click="https://x.test/r",
    )
    with patch("urllib.request.urlopen", return_value=ok_response()) as uo:
        NtfyNotifier("https://ntfy.example/", "topic", token="tok").send(n)
    req = uo.call_args.args[0]
    assert req.full_url == "https://ntfy.example/topic"
    assert req.data == "Body ✓".encode()
    h = {k.lower(): v for k, v in req.header_items()}
    assert h["title"] == "Deadline soon"
    assert h["priority"] == "4"
    assert h["tags"] == "alarm_clock,fpl"
    assert h["click"] == "https://x.test/r"
    assert h["authorization"] == "Bearer tok"


def test_non_ascii_title_is_header_safe():
    n = Notification("⚽ Captain: Müller", "b")
    req = NtfyNotifier("https://s", "t").build_request(n)
    headers = {k.lower(): v for k, v in req.header_items()}
    headers["title"].encode("latin-1")  # must not raise
    assert headers["title"].startswith("=?UTF-8?B?")
    assert "authorization" not in headers


def test_http_error_raises_notify_error():
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO())
    with (
        patch("urllib.request.urlopen", side_effect=err),
        pytest.raises(NotifyError, match="403"),
    ):
        NtfyNotifier("https://s", "t").send(Notification("a", "b"))


def test_network_error_raises_notify_error():
    with (
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")),
        pytest.raises(NotifyError, match="reach"),
    ):
        NtfyNotifier("https://s", "t").send(Notification("a", "b"))


def test_stdout(capsys):
    StdoutNotifier().send(Notification("T", "hello"))
    out = capsys.readouterr().out
    assert "T" in out and "hello" in out


def test_get_notifier():
    assert isinstance(get_notifier(Config(team_id=1)), StdoutNotifier)
    n = get_notifier(Config(team_id=1, notify="ntfy", ntfy_topic="x"))
    assert isinstance(n, NtfyNotifier) and n.topic == "x"


def test_run_url(monkeypatch):
    for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(k, raising=False)
    assert run_url() is None
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    assert run_url() == "https://github.com/o/r/actions/runs/123"

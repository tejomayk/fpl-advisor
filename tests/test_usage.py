"""The shared spend ledger: caps, reservations, alerts, corruption and concurrency."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fpl_advisor import cli, usage

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)
REAL_OWNER_ALERT = usage.owner_alert  # conftest swaps it for a no-op during each test


@pytest.fixture
def alerts():
    return []


@pytest.fixture
def ledger(tmp_path, alerts):
    return usage.Usage(
        tmp_path / "usage" / "usage.json", alert=lambda t, b: alerts.append((t, b))
    )


def raw(ledger) -> dict:
    return json.loads(ledger.path.read_text())


# ------------------------------------------------------------------------- caps


def test_daily_cap_refuses_before_the_call(ledger):
    caps = usage.Caps(daily=5)
    for _ in range(2):
        r = ledger.reserve("odds", 2, caps, now=NOW)
        ledger.settle(r, 2, caps=caps, now=NOW)
    assert ledger.reserve("odds", 2, caps, now=NOW) is None  # 4 + 2 > 5
    r = ledger.reserve("odds", 1, caps, now=NOW)  # 4 + 1 fits
    assert r is not None
    ledger.settle(r, 1, caps=caps, now=NOW)
    # A new day starts from zero, the month keeps counting.
    tomorrow = NOW + timedelta(days=1)
    assert ledger.reserve("odds", 2, caps, now=tomorrow) is not None
    assert ledger.snapshot(tomorrow)["odds"]["spent_month"] == 5


def test_monthly_cap_and_rollover(ledger):
    caps = usage.Caps(monthly=3)
    r = ledger.reserve("odds", 3, caps, now=NOW)
    ledger.settle(r, 3, caps=caps, now=NOW)
    assert ledger.reserve("odds", 1, caps, now=NOW + timedelta(days=3)) is None
    assert ledger.reserve("odds", 1, caps, now=NOW + timedelta(days=20)) is not None


def test_reservations_hold_room_until_settled(ledger):
    caps = usage.Caps(daily=1.0)
    a = ledger.reserve("openrouter", 0.6, caps, now=NOW)
    assert ledger.reserve("openrouter", 0.6, caps, now=NOW) is None  # 0.6 held
    ledger.settle(a, 0.1, caps=caps, now=NOW)  # real cost was lower: rest released
    assert ledger.reserve("openrouter", 0.6, caps, now=NOW) is not None
    assert ledger.snapshot(NOW)["openrouter"]["spent_day"] == pytest.approx(0.1)


def test_release_frees_everything(ledger):
    caps = usage.Caps(daily=1)
    r = ledger.reserve("youtube", 1, caps, now=NOW)
    ledger.release(r, now=NOW)
    s = ledger.snapshot(NOW)["youtube"]
    assert s["spent_day"] == 0 and s["calls_day"] == 0 and s["pending_calls"] == 0


def test_unsettled_reservation_counts_as_spent_after_ttl(ledger):
    caps = usage.Caps(daily=10)
    ledger.reserve("odds", 4, caps, now=NOW)  # process "dies" mid-call
    later = NOW + usage.PENDING_TTL + timedelta(seconds=1)
    assert ledger.reserve("odds", 7, caps, now=later) is None  # 4 counted + 7 > 10
    assert raw(ledger)["providers"]["odds"]["spent_day"] == 4


def test_calls_per_day_limit(ledger):
    caps = usage.Caps(daily=100, calls_per_day=2)
    for _ in range(2):
        ledger.settle(ledger.reserve("openrouter", 0.01, caps, now=NOW), 0.01, now=NOW)
    assert ledger.reserve("openrouter", 0.01, caps, now=NOW) is None


def test_credits_floor(ledger):
    caps = usage.Caps(monthly=400, floor=50)
    r = ledger.reserve("odds", 2, caps, now=NOW)
    ledger.settle(r, 2, remaining=51, caps=caps, now=NOW)
    assert ledger.reserve("odds", 2, caps, now=NOW) is None  # would leave 49
    assert ledger.reserve("odds", 0, caps, now=NOW) is not None  # free calls still fine


def test_zero_cap_turns_paid_calls_off(ledger):
    assert ledger.reserve("youtube", 1, usage.Caps(daily=0), now=NOW) is None


def test_refusal_is_logged_once(ledger, caplog):
    caps = usage.Caps(daily=1)
    with caplog.at_level(logging.WARNING, logger="fpl_advisor.usage"):
        for _ in range(3):
            assert ledger.reserve("youtube", 2, caps, now=NOW) is None
    assert caplog.text.count("daily cap reached") == 1


def test_caps_from_env(monkeypatch):
    monkeypatch.setenv("ODDS_DAILY_BUDGET", "12")
    monkeypatch.setenv("ODDS_MONTHLY_BUDGET", "123")
    monkeypatch.setenv("OPENROUTER_DAILY_BUDGET_USD", "0.05")
    monkeypatch.setenv("OPENROUTER_MAX_CALLS_PER_DAY", "7")
    monkeypatch.setenv("YOUTUBE_DAILY_UNITS", "nonsense")
    assert usage.caps_for("odds") == usage.Caps(daily=12, monthly=123, floor=50)
    o = usage.caps_for("openrouter")
    assert o.daily == 0.05 and o.monthly == 1.0 and o.calls_per_day == 7
    assert usage.caps_for("youtube").daily == usage.DEFAULT_YOUTUBE_DAILY_UNITS


def test_conservative_defaults():
    assert usage.caps_for("odds", env={}) == usage.Caps(daily=40, monthly=400, floor=50)
    o = usage.caps_for("openrouter", env={})
    assert (o.daily, o.monthly, o.calls_per_day) == (0.20, 1.00, 100)
    assert usage.caps_for("youtube", env={}).daily == 2000


# -------------------------------------------------------------------- periods


def test_youtube_day_ends_at_midnight_pacific():
    # 15 July: Pacific daylight time is UTC-7, so the YouTube day turns at 07:00 UTC.
    before = datetime(2026, 7, 15, 6, 59, tzinfo=UTC)
    after = datetime(2026, 7, 15, 7, 0, tzinfo=UTC)
    assert usage.periods("youtube", before)[0] == "2026-07-14"
    assert usage.periods("youtube", after)[0] == "2026-07-15"
    assert usage.periods("odds", before)[0] == "2026-07-15"  # UTC for the others
    day_end, _ = usage.resets("youtube", after)
    assert day_end.astimezone(UTC) == datetime(2026, 7, 16, 7, tzinfo=UTC)


# -------------------------------------------------------------------- OpenRouter cost


def test_openrouter_cost_prefers_reported_cost():
    m = "google/gemini-2.5-flash-lite"
    assert usage.openrouter_cost(m, {"usage": {"cost": 0.0012}}, 1.0) == 0.0012
    tokens = {"usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
    assert usage.openrouter_cost(m, tokens, 1.0) == pytest.approx(
        (1000 * 0.10 + 500 * 0.40) / 1e6
    )
    assert usage.openrouter_cost(m, {"choices": []}, 0.5) == 0.5  # no usage: worst case


@pytest.mark.parametrize(
    "model",
    [
        "someone/new-model",
        "openrouter/auto",
        "google/gemini-2.5-flash-lite:online",
        "openai/gpt-4o-mini:nitro",
        "",
    ],
)
def test_models_off_the_priced_list_are_refused(model):
    assert usage.model_price(model) is None
    assert usage.openrouter_worst_case(model, "hi", 100) is None


def test_worst_case_counts_every_byte_as_a_token():
    prompt = "é" * 3000  # 6000 UTF-8 bytes
    cheap = usage.openrouter_worst_case("google/gemini-2.5-flash-lite", prompt, 2500)
    assert cheap == pytest.approx(((6000 + 50) * 0.10 + 2500 * 0.40) / 1e6)


def test_declared_price_only_for_the_configured_model():
    env = {"OPENROUTER_MODEL": "a/b", "OPENROUTER_MODEL_PRICE": "2,8"}
    assert usage.model_price("a/b", env) == (2.0, 8.0)
    assert usage.model_price("c/d", env) is None
    bad = {"OPENROUTER_MODEL": "a/b", "OPENROUTER_MODEL_PRICE": "cheap"}
    assert usage.model_price("a/b", bad) is None
    routed = {"OPENROUTER_MODEL": "openrouter/auto", "OPENROUTER_MODEL_PRICE": "1,1"}
    assert usage.model_price("openrouter/auto", routed) is None


# ------------------------------------------------------------------------ alerts


def test_alerts_once_per_threshold_per_period(ledger, alerts):
    caps = usage.Caps(daily=10)
    for amount in (5, 3, 1):  # 50%, 80%, 90%
        ledger.settle(
            ledger.reserve("odds", amount, caps, now=NOW), amount, caps=caps, now=NOW
        )
    assert len(alerts) == 1 and "80%" in alerts[0][0]
    ledger.settle(ledger.reserve("odds", 1, caps, now=NOW), 1, caps=caps, now=NOW)
    assert len(alerts) == 2 and "cap reached" in alerts[1][0]
    assert ledger.reserve("odds", 1, caps, now=NOW) is None
    assert len(alerts) == 2  # the refusal does not repeat the 100% alert
    tomorrow = NOW + timedelta(days=1)
    ledger.settle(
        ledger.reserve("odds", 9, caps, now=tomorrow), 9, caps=caps, now=tomorrow
    )
    assert len(alerts) == 3  # a new day can alert again


def test_refusal_sends_the_100_percent_alert(ledger, alerts):
    caps = usage.Caps(daily=1.0)
    ledger.settle(
        ledger.reserve("openrouter", 0.5, caps, now=NOW), 0.5, caps=caps, now=NOW
    )
    assert ledger.reserve("openrouter", 0.6, caps, now=NOW) is None
    assert len(alerts) == 1 and "cap reached" in alerts[0][0]


def test_alert_text_has_no_key(ledger, alerts, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "7" * 40)
    caps = usage.Caps(daily=0.01)
    ledger.settle(
        ledger.reserve("openrouter", 0.01, caps, now=NOW), 0.01, caps=caps, now=NOW
    )
    assert alerts and all("7" * 40 not in t + b for t, b in alerts)


def test_owner_alert_goes_to_ntfy_only_with_a_topic(monkeypatch):
    from fpl_advisor import config
    from fpl_advisor.notify import NtfyNotifier

    sent = []
    monkeypatch.setattr(
        NtfyNotifier, "send", lambda self, n: sent.append((self.topic, n))
    )
    monkeypatch.setattr(config, "_read_toml", lambda path: {})
    REAL_OWNER_ALERT("t", "b")
    assert sent == []
    monkeypatch.setenv("NTFY_TOPIC", "my-test-topic")
    REAL_OWNER_ALERT("t", "b")
    assert sent and sent[0][0] == "my-test-topic" and sent[0][1].body == "b"


# ----------------------------------------------------------------- corrupt ledger


@pytest.mark.parametrize("content", ["{not json", '{"providers": {"odds": "x"}}', "[]"])
def test_corrupt_ledger_fails_closed_and_is_kept(ledger, content, caplog, alerts):
    ledger.path.parent.mkdir(parents=True)
    ledger.path.write_text(content)
    usage._logged.clear()
    with caplog.at_level(logging.ERROR, logger="fpl_advisor.usage"):
        assert ledger.reserve("odds", 1, usage.Caps(daily=100), now=NOW) is None
        assert (
            ledger.reserve("odds", 0, usage.Caps(), now=NOW) is None
        )  # even free calls
    assert "UNREADABLE" in caplog.text
    assert ledger.path.read_text() == content  # never silently reset
    backups = list(ledger.path.parent.glob("usage.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == content
    assert alerts and "stopped" in alerts[0][0]
    assert "cannot be read" in usage.report(ledger, NOW)


# ------------------------------------------------------------------ concurrency


def test_threads_never_overspend(ledger):
    caps = usage.Caps(daily=50)
    got = []

    def work():
        mine = usage.Usage(ledger.path, alert=lambda t, b: None)  # own file handle
        for _ in range(20):
            r = mine.reserve("youtube", 1, caps, now=NOW)
            if r:
                mine.settle(r, 1, caps=caps, now=NOW)
                got.append(1)

    threads = [threading.Thread(target=work) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 50
    assert ledger.snapshot(NOW)["youtube"]["spent_day"] == 50


def test_processes_never_overspend(tmp_path):
    path = tmp_path / "usage" / "usage.json"
    script = (
        "import sys\n"
        "from datetime import datetime, UTC\n"
        "from fpl_advisor import usage\n"
        "u = usage.Usage(sys.argv[1], alert=lambda t, b: None)\n"
        "caps = usage.Caps(daily=30)\n"
        "now = datetime(2026, 7, 15, 12, tzinfo=UTC)\n"
        "n = 0\n"
        "for _ in range(15):\n"
        "    r = u.reserve('youtube', 1, caps, now=now)\n"
        "    if r:\n"
        "        u.settle(r, 1, caps=caps, now=now); n += 1\n"
        "print(n)\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path)],
            stdout=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        for _ in range(4)
    ]
    made = sum(int(p.communicate(timeout=60)[0]) for p in procs)
    assert made == 30
    data = json.loads(path.read_text())
    assert data["providers"]["youtube"]["spent_day"] == 30


# ---------------------------------------------------------------------- report


def test_report_is_plain_english_and_has_no_keys(ledger, monkeypatch, capsys):
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIza" + "R" * 35)
    caps = usage.caps_for("youtube")
    ledger.settle(ledger.reserve("youtube", 1, caps, now=NOW), 1, caps=caps, now=NOW)
    text = usage.report(ledger, NOW)
    assert "YouTube Data API (key set)" in text
    assert "1 units of 2000 units" in text and "midnight Pacific time" in text
    assert "The Odds API (no key set: off)" in text
    assert "R" * 35 not in text
    # The CLI command uses the ledger in FPL_DATA_DIR and needs no team ID.
    assert cli.main(["usage"]) == 0
    out = capsys.readouterr().out
    assert "OpenRouter" in out and "R" * 35 not in out


# ------------------------------------------------------- wired into each provider


def test_openrouter_call_is_skipped_when_capped(monkeypatch):
    from fpl_advisor import news

    monkeypatch.setenv("OPENROUTER_DAILY_BUDGET_USD", "0")
    posts = []
    out = news._chat("k" * 20, news.DEFAULT_MODEL, "hi", lambda *a: posts.append(a))
    assert out is None and posts == []


def test_openrouter_records_the_reported_cost(monkeypatch):
    from fpl_advisor import news

    payloads = []

    def post(url, payload, headers):
        payloads.append(payload)
        return {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"cost": 0.0031, "prompt_tokens": 10, "completion_tokens": 5},
        }

    assert news._chat("k" * 20, news.DEFAULT_MODEL, "hi", post) == "{}"
    assert payloads[0]["usage"] == {"include": True}
    s = usage.default().snapshot()["openrouter"]
    assert s["spent_day"] == pytest.approx(0.0031) and s["calls_day"] == 1
    assert s["pending_calls"] == 0


def test_openrouter_failure_counts_worst_case_unless_refused(monkeypatch):
    import urllib.error

    from fpl_advisor import news

    monkeypatch.setattr(news.time, "sleep", lambda s: None)

    def timeout(*a):
        raise TimeoutError

    news._chat("k" * 20, news.DEFAULT_MODEL, "hi", timeout)
    worst = usage.openrouter_worst_case(
        news.DEFAULT_MODEL, news.SYSTEM_PROMPT + "hi", 2500
    )
    s = usage.default().snapshot()["openrouter"]
    assert s["spent_day"] == pytest.approx(2 * worst) and s["calls_day"] == 2

    def refused(*a):
        raise urllib.error.HTTPError("u", 402, "no credit", {}, None)

    news._chat("k" * 20, news.DEFAULT_MODEL, "hi", refused)
    assert usage.default().snapshot()["openrouter"]["spent_day"] == pytest.approx(
        2 * worst
    )


def test_odds_daily_cap_stops_the_call(monkeypatch, tmp_path):
    from conftest import make_world

    from fpl_advisor import odds

    monkeypatch.setenv("ODDS_DAILY_BUDGET", "1")
    calls = []
    odds.fetch_signals(
        make_world(),
        tmp_path,
        now=NOW,
        api_key="k" * 20,
        http=lambda p, q: calls.append(p) or (200, {}, []),
        sleep=lambda s: None,
    )
    assert calls == []  # team odds cost 2 > daily cap 1


def test_youtube_unit_cap_stops_listing(monkeypatch, tmp_path):
    from fpl_advisor import creators

    monkeypatch.setenv("YOUTUBE_DAILY_UNITS", "1")
    calls = []

    def get(url, headers=None):
        calls.append(url)
        return b'{"items": []}'

    roster = [creators.Creator(h, h) for h in ("AlphaFPL", "BravoFPL", "CharlieFPL")]
    creators.refresh_videos(
        tmp_path,
        roster,
        "k" * 20,
        4,
        NOW - timedelta(days=5),
        NOW + timedelta(days=2),
        NOW,
        get,
    )
    assert len(calls) == 1
    assert usage.default().snapshot(NOW)["youtube"]["spent_day"] == 1


# ------------------------------------------------------- audit fixes: one ledger


def test_ledger_path_ignores_cwd_and_data_dir(monkeypatch, tmp_path):
    from fpl_advisor import keys
    from fpl_advisor import signals_service as svc

    monkeypatch.delenv("FPL_USAGE_PATH")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    a = usage.ledger_path()
    monkeypatch.setenv("FPL_DATA_DIR", str(tmp_path / "elsewhere"))
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")
    assert usage.ledger_path() == a == tmp_path / "home/.fpl-advisor/usage/usage.json"
    assert a.is_absolute()
    # A hub on any data dir uses that same one ledger.
    assert svc.SignalsHub(None, tmp_path / "d1").usage.path == a
    monkeypatch.setenv("FPL_USAGE_PATH", "rel/u.json")
    assert usage.ledger_path() == (tmp_path / "sub" / "rel/u.json")
    assert {"FPL_USAGE_PATH", "FPL_DATA_DIR", "FPL_USAGE_MIRROR"} <= keys.DOTENV_VARS


def test_usage_command_prints_the_resolved_ledger(capsys):
    assert cli.main(["usage"]) == 0
    out = capsys.readouterr().out
    assert str(usage.ledger_path()) in out and "FPL_USAGE_PATH" in out


# ------------------------------------------------------- audit fixes: lost ledger


def _spend(ledger, amount=3, now=NOW):
    caps = usage.Caps(daily=100, monthly=100)
    ledger.settle(
        ledger.reserve("odds", amount, caps, now=now), amount, caps=caps, now=now
    )


def test_mirror_is_in_another_folder_and_restores(ledger):
    _spend(ledger)
    assert ledger.mirror.exists() and ledger.mirror.parent != ledger.path.parent
    ledger.path.unlink()
    assert ledger.snapshot(NOW)["odds"]["spent_month"] == 3  # from the mirror
    _spend(ledger)
    assert json.loads(ledger.path.read_text())["providers"]["odds"]["spent_month"] == 6
    ledger.mirror.unlink()
    assert ledger.snapshot(NOW)["odds"]["spent_month"] == 6  # from the main copy
    ledger.mirror.parent.mkdir(exist_ok=True)
    ledger.mirror.write_text("{garbage")
    assert ledger.snapshot(NOW)["odds"]["spent_month"] == 6  # damaged copy ignored


def test_both_copies_lost_this_month_fails_closed_until_reset(ledger, alerts, capsys):
    _spend(ledger)
    ledger.path.unlink()
    ledger.mirror.unlink()
    usage._logged.clear()
    assert ledger.reserve("odds", 1, usage.Caps(daily=100), now=NOW) is None
    assert alerts and "stopped" in alerts[-1][0]
    assert "reset-confirm" in usage.report(ledger, NOW)
    ledger.reset(NOW)
    assert ledger.reserve("odds", 1, usage.Caps(daily=100), now=NOW) is not None


def test_cli_reset_confirm(capsys):
    led = usage.default()
    _spend(led, now=datetime.now(UTC))
    led.path.unlink()
    led.mirror.unlink()
    assert cli.main(["usage"]) == 0
    assert "cannot be read" in capsys.readouterr().out
    assert cli.main(["usage", "--reset-confirm"]) == 0
    out = capsys.readouterr().out
    assert "reset to zero" in out and "cannot be read" not in out


def test_lost_last_month_starts_fresh(ledger):
    _spend(ledger)
    ledger.path.unlink()
    ledger.mirror.unlink()
    later = NOW + timedelta(days=40)
    assert ledger.reserve("odds", 1, usage.Caps(daily=100), now=later) is not None


def test_missing_volume_warns_loudly(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv(
        "FPL_EXPECT_VOLUME", str(tmp_path)
    )  # a plain folder, not a mount
    with caplog.at_level(logging.ERROR, logger="fpl_advisor.usage"):
        assert usage.warn_if_ephemeral() is False
    assert "NOT A MOUNTED VOLUME" in caplog.text
    monkeypatch.setenv("FPL_EXPECT_VOLUME", "/")
    assert usage.warn_if_ephemeral() is True


# ------------------------------------------------------- audit fixes: clock and caps


def test_clock_stepping_back_across_a_month_keeps_totals(ledger):
    nov = datetime(2026, 11, 1, 0, 30, tzinfo=UTC)
    caps = usage.Caps(daily=10, monthly=10)
    ledger.settle(ledger.reserve("odds", 8, caps, now=nov), 8, caps=caps, now=nov)
    back = nov - timedelta(hours=2)  # 31 October: an older day and an older month
    assert ledger.reserve("odds", 3, caps, now=back) is None  # still 8 of 10 spent
    r = ledger.reserve("odds", 2, caps, now=back)
    ledger.settle(r, 2, caps=caps, now=back)
    rec = raw(ledger)["providers"]["odds"]
    assert (rec["day"], rec["month"], rec["spent_month"]) == (
        "2026-11-01",
        "2026-11",
        10,
    )


def test_strictest_cap_seen_this_period_wins(ledger):
    strict, loose = usage.Caps(daily=5), usage.Caps(daily=100)
    ledger.settle(ledger.reserve("odds", 4, strict, now=NOW), 4, caps=strict, now=NOW)
    other = usage.Usage(
        ledger.path, alert=lambda t, b: None
    )  # another process, looser env
    assert other.reserve("odds", 2, loose, now=NOW) is None
    tomorrow = NOW + timedelta(days=1)
    assert (
        other.reserve("odds", 50, loose, now=tomorrow) is not None
    )  # new day, new pin

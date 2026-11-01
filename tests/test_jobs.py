"""The scheduled refresh job and the world snapshot (local job folder, fake paid APIs; no
network)."""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import timedelta

import pytest
from conftest import make_world

from fpl_advisor import cli, jobs, odds, store, usage
from fpl_advisor.signals import PlayerSignal, Signals
from fpl_advisor.world import World

W = make_world()
NOW = W.next_event.deadline - timedelta(days=5)  # outside the player-props windows


def test_world_snapshot_round_trips_exactly():
    w = make_world()
    w.players[1].price_projection = [{"day": 1, "change": 0.1}]
    w.chips = [{"name": "wildcard", "start_event": 2, "stop_event": 19}]
    w.past = {1: {"season_name": "2024/25", "total_points": 99}}
    back = World.from_json(w.to_json())
    assert back == w
    assert back.next_event.deadline == w.next_event.deadline
    assert back.next_event.deadline.tzinfo is not None
    assert all(isinstance(k, int) for k in back.players | back.history | back.past)
    assert back.as_of(3).history == w.as_of(3).history


def test_world_snapshot_ignores_unknown_fields_and_rejects_garbage():
    d = make_world().to_dict()
    d["players"][0]["added_in_a_later_version"] = 1
    assert World.from_dict(d).players[d["players"][0]["id"]]
    for bad in ("{}", '{"version": 99}', "[]", "not json"):
        with pytest.raises(ValueError):
            World.from_json(bad)


def test_snapshot_digest_ignores_transfer_drift_but_not_news():
    s = Signals()
    base = jobs.make_snapshot(W, s, NOW)
    w2 = World.from_dict(W.to_dict())
    w2.players[1].transfers_in_event += 5000
    assert jobs.make_snapshot(w2, s, NOW)["digest"] == base["digest"]
    w2.players[1].news = "Knock"
    assert jobs.make_snapshot(w2, s, NOW)["digest"] != base["digest"]
    assert not jobs.snapshot_due(base, base, NOW + timedelta(hours=1))
    assert jobs.snapshot_due(base, base, NOW + jobs.SNAPSHOT_MAX_AGE)
    assert jobs.snapshot_due(None, base, NOW)


class OddsWire:
    """Stands in for The Odds API: every call costs what it was reserved for."""

    def __init__(self):
        self.calls = []

    def make(self, key):
        def get(path, params):
            self.calls.append(path)
            return 200, {"x-requests-last": str(len(odds.TEAM_MARKETS))}, []

        return get


@pytest.fixture
def fake_world(monkeypatch):
    monkeypatch.setattr(jobs, "load_world", lambda client: World.from_dict(W.to_dict()))


def _folder(monkeypatch, root):
    """A job folder as the workflows set it up: the spend ledger lives inside it."""
    monkeypatch.setenv("FPL_DATA_DIR", str(root))
    monkeypatch.setenv("FPL_USAGE_PATH", str(root / "usage" / "usage.json"))
    monkeypatch.setenv("FPL_USAGE_MIRROR", str(root / "usage.mirror" / "usage.json"))
    return store.LocalStore(root)


def _restore(monkeypatch, prev, root):
    """What the next workflow run sees: a fresh machine with the cached folder restored."""
    shutil.copytree(prev, root)
    return _folder(monkeypatch, root)


def test_refresh_keeps_caps_across_runs_through_the_cached_folder(
    fake_world, monkeypatch, tmp_path
):
    wire = OddsWire()
    monkeypatch.setattr(odds, "_urllib_http", wire.make)
    monkeypatch.setenv("ODDS_API_KEY", "0f" * 16)  # fake
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    cost = len(odds.TEAM_MARKETS)
    monkeypatch.setenv("ODDS_DAILY_BUDGET", str(cost + 1))  # room for one call a day

    st1 = _folder(monkeypatch, tmp_path / "run1")
    r1 = jobs.refresh(None, st=st1, now=NOW)
    assert wire.calls == [f"/sports/{odds.SPORT}/odds"]
    assert r1.snapshot_written

    # The next run is a fresh machine: it only knows what the cached folder holds.
    st2 = _restore(monkeypatch, tmp_path / "run1", tmp_path / "run2")
    r2 = jobs.refresh(None, st=st2, now=NOW + timedelta(hours=13))  # odds due again
    assert len(wire.calls) == 1, "the daily cap must hold across separate runs"
    ledger = usage.Usage(tmp_path / "run2" / "usage" / "usage.json")
    assert ledger.snapshot(now=NOW + timedelta(hours=13))["odds"]["spent_day"] == cost
    assert r2.gameweek == W.next_event.id

    snap = jobs.read_snapshot(st2)
    assert snap.world == W and snap.gameweek == W.next_event.id
    assert any(k.startswith("odds") for k in snap.signals.updated)


def test_refresh_rewrites_the_snapshot_only_when_due(fake_world, monkeypatch, tmp_path):
    st = _folder(monkeypatch, tmp_path / "d")
    r1 = jobs.refresh(None, st=st, now=NOW)
    assert r1.snapshot_written
    first = st.get(store.WEB_SNAPSHOT_KEY)
    r2 = jobs.refresh(None, st=st, now=NOW + timedelta(minutes=30))
    assert not r2.snapshot_written
    assert st.get(store.WEB_SNAPSHOT_KEY) == first  # same bytes: nothing to publish
    r3 = jobs.refresh(None, st=st, now=NOW + timedelta(minutes=31), force_snapshot=True)
    assert r3.snapshot_written
    r4 = jobs.refresh(None, st=st, now=NOW + jobs.SNAPSHOT_MAX_AGE * 2)
    assert r4.snapshot_written


def test_in_github_actions_the_ledger_must_be_inside_the_cached_folder(
    fake_world, monkeypatch, tmp_path
):
    """Only FPL_DATA_DIR is cached between runs: a ledger anywhere else would restart from zero
    every run, so the job refuses to start (and so spends nothing)."""
    wire = OddsWire()
    monkeypatch.setattr(odds, "_urllib_http", wire.make)
    monkeypatch.setenv("ODDS_API_KEY", "0f" * 16)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    st = _folder(monkeypatch, tmp_path / "data")
    monkeypatch.setenv("FPL_USAGE_PATH", str(tmp_path / "home" / "usage.json"))
    with pytest.raises(jobs.JobError):
        jobs.refresh(None, st=st, now=NOW)
    with pytest.raises(jobs.JobError):
        jobs.discover_elite(None, st=st)
    assert wire.calls == []  # no ledger, no spending


def test_jobs_refuse_a_read_only_store(fake_world):
    st = store.HttpSnapshotStore("https://example.invalid/s.json.gz")
    with pytest.raises(jobs.JobError):
        jobs.refresh(None, st=st, now=NOW)


def test_refresh_with_local_files_works_in_place(fake_world, tmp_path):
    st = store.LocalStore(tmp_path)
    r = jobs.refresh(None, st=st, now=NOW)
    assert r.snapshot_written
    assert (tmp_path / "signals" / "signals.json").exists()
    assert (tmp_path / store.WEB_SNAPSHOT_KEY).exists()
    assert jobs.read_snapshot(st).world == W


def test_refresh_cli_prints_a_summary(fake_world, capsys):
    assert cli.main(["refresh"]) == 0
    out = capsys.readouterr().out
    assert f"GW{W.next_event.id}" in out and "Web snapshot written" in out


def test_published_snapshot_holds_only_what_the_site_shows(
    fake_world, monkeypatch, tmp_path
):
    """The snapshot becomes a public release asset: no keys, no spend ledger, no raw paid-API
    responses or cache paths. Only the world and the merged signals."""
    wire = OddsWire()
    monkeypatch.setattr(odds, "_urllib_http", wire.make)
    key = "0f" * 16  # fake
    monkeypatch.setenv("ODDS_API_KEY", key)
    st = _folder(monkeypatch, tmp_path / "pub")
    jobs.refresh(None, st=st, now=NOW)
    assert wire.calls  # a paid source did run
    text = gzip.decompress((tmp_path / "pub" / store.WEB_SNAPSHOT_KEY).read_bytes())
    text = text.decode()
    d = json.loads(text)
    assert set(d) == {"version", "saved_at", "gameweek", "digest", "world", "signals"}
    assert set(d["signals"]) == {
        "players",
        "fixtures",
        "elite",
        "updated",
        "gameweek",
        "creators",
    }
    ledger_text = (tmp_path / "pub" / "usage" / "usage.json").read_text()
    assert "providers" in ledger_text  # the ledger exists ...
    for leak in (key, "providers", "spent_day", "usage.json", str(tmp_path), "apiKey"):
        assert leak not in text, leak  # ... but none of it is published


def test_discover_elite_saves_the_list_in_the_job_folder(monkeypatch, tmp_path):
    seen = {}

    def fake_discover(client, path, max_age_days, pool_size, top):
        seen["max_age"] = max_age_days
        path.write_text(json.dumps({"created": NOW.isoformat(), "managers": []}))
        return [1, 2, 3]

    monkeypatch.setattr(jobs.elite, "load_or_discover", fake_discover)
    st = _folder(monkeypatch, tmp_path / "e")
    assert jobs.discover_elite(None, st=st, force=True) == 3
    assert seen["max_age"] == 0
    assert (tmp_path / "e" / "signals" / "elite_managers.json").exists()


def test_signals_survive_the_snapshot():
    s = Signals(
        players={7: PlayerSignal(p_goal=0.3, news="Fit")}, updated={"odds": "t"}
    )
    snap = jobs.parse_snapshot(jobs.make_snapshot(W, s, NOW))
    assert snap.signals.players[7].p_goal == 0.3 and snap.signals.updated == {
        "odds": "t"
    }

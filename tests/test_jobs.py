"""The scheduled refresh job and the world snapshot (fake Blob, fake paid APIs; no network)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from conftest import make_world
from fakeblob import TOKEN, FakeBlob

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
def blob(monkeypatch):
    fake = FakeBlob()
    st = store.BlobStore(TOKEN, opener=fake, sleep=lambda s: None)
    monkeypatch.setattr(jobs, "load_world", lambda client: World.from_dict(W.to_dict()))
    return fake, st


def _run(st, tmp_path, name, now, **kw):
    return jobs.refresh(None, st=st, now=now, workdir=tmp_path / name, **kw)


def test_refresh_keeps_caps_across_runs_through_the_store(blob, monkeypatch, tmp_path):
    _fake, st = blob
    wire = OddsWire()
    monkeypatch.setattr(odds, "_urllib_http", wire.make)
    monkeypatch.setenv("ODDS_API_KEY", "0f" * 16)  # fake
    cost = len(odds.TEAM_MARKETS)
    monkeypatch.setenv("ODDS_DAILY_BUDGET", str(cost + 1))  # room for one call a day

    r1 = _run(st, tmp_path, "run1", NOW)
    assert wire.calls == [f"/sports/{odds.SPORT}/odds"]
    assert r1.state_written and r1.snapshot_written

    # The next run is a fresh machine: it only knows what the store holds.
    r2 = _run(st, tmp_path, "run2", NOW + timedelta(hours=13))  # team odds due again
    assert len(wire.calls) == 1, "the daily cap must hold across separate runs"
    ledger = usage.Usage(tmp_path / "run2" / "usage" / "usage.json")
    assert ledger.snapshot(now=NOW + timedelta(hours=13))["odds"]["spent_day"] == cost
    assert r2.gameweek == W.next_event.id

    snap = jobs.read_snapshot(st)
    assert snap.world == W and snap.gameweek == W.next_event.id
    assert any(k.startswith("odds") for k in snap.signals.updated)


def test_refresh_without_keys_makes_no_paid_call_and_uploads_little(blob, tmp_path):
    fake, st = blob
    r1 = _run(st, tmp_path, "a", NOW)
    assert r1.snapshot_written
    first = fake.uploads
    assert first <= 2
    r2 = _run(st, tmp_path, "b", NOW + timedelta(minutes=30))
    assert not r2.snapshot_written and not r2.state_written
    assert fake.uploads == first  # nothing changed: no upload spent
    assert all(kind != "list" for kind, _ in fake.calls)
    r3 = _run(st, tmp_path, "c", NOW + jobs.SNAPSHOT_MAX_AGE)
    assert r3.snapshot_written


def test_refresh_refuses_to_start_if_the_store_cannot_be_read(
    blob, monkeypatch, tmp_path
):
    fake, st = blob
    wire = OddsWire()
    monkeypatch.setattr(odds, "_urllib_http", wire.make)
    monkeypatch.setenv("ODDS_API_KEY", "0f" * 16)
    fake.fail_next = [500, 500, 500]
    with pytest.raises(jobs.JobError):
        _run(st, tmp_path, "x", NOW)
    assert wire.calls == []  # no ledger, no spending


def test_ledger_upload_failure_is_loud(blob, monkeypatch, tmp_path):
    _fake, st = blob
    alerts = []
    monkeypatch.setattr(usage, "owner_alert", lambda t, b: alerts.append(t))
    monkeypatch.setattr(
        store.JobState,
        "push",
        lambda self: (_ for _ in ()).throw(store.StoreError("down")),
    )
    with pytest.raises(jobs.JobError):
        _run(st, tmp_path, "x", NOW)
    assert alerts


def test_refresh_with_local_files_works_in_place(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "load_world", lambda client: World.from_dict(W.to_dict()))
    st = store.LocalStore(tmp_path)
    r = jobs.refresh(None, st=st, now=NOW)
    assert r.snapshot_written and not r.state_written
    assert (tmp_path / "signals" / "signals.json").exists()
    assert (tmp_path / store.WEB_SNAPSHOT_KEY).exists()
    assert jobs.read_snapshot(st).world == W


def test_refresh_cli_prints_a_summary(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(jobs, "load_world", lambda client: World.from_dict(W.to_dict()))
    assert cli.main(["refresh"]) == 0
    out = capsys.readouterr().out
    assert f"GW{W.next_event.id}" in out and "Web snapshot written" in out


def test_discover_elite_saves_the_list_to_the_store(blob, monkeypatch, tmp_path):
    _fake, st = blob
    seen = {}

    def fake_discover(client, path, max_age_days, pool_size, top):
        seen["max_age"] = max_age_days
        path.write_text(json.dumps({"created": NOW.isoformat(), "managers": []}))
        return [1, 2, 3]

    monkeypatch.setattr(jobs.elite, "load_or_discover", fake_discover)
    assert jobs.discover_elite(None, st=st, workdir=tmp_path / "e", force=True) == 3
    assert seen["max_age"] == 0
    with store.job_state(st, tmp_path / "check") as js:
        js.pull()
        assert (js.root / "signals" / "elite_managers.json").exists()


def test_signals_survive_the_snapshot():
    s = Signals(
        players={7: PlayerSignal(p_goal=0.3, news="Fit")}, updated={"odds": "t"}
    )
    snap = jobs.parse_snapshot(jobs.make_snapshot(W, s, NOW))
    assert snap.signals.players[7].p_goal == 0.3 and snap.signals.updated == {
        "odds": "t"
    }

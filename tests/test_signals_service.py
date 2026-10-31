from datetime import UTC, datetime

from conftest import make_world

from fpl_advisor import signals_service as svc
from fpl_advisor.signals import EliteSummary, FixtureSignal, PlayerSignal, Signals

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def fake(monkeypatch, odds=None, news=None, elite_list=None, summary=None):
    monkeypatch.setattr(svc.odds, "fetch_signals", odds or (lambda *a, **k: Signals()))
    monkeypatch.setattr(svc.news, "fetch_signals", news or (lambda *a, **k: Signals()))
    monkeypatch.setattr(svc.creators, "fetch_signals", lambda *a, **k: Signals())
    monkeypatch.setattr(svc.elite, "load_managers", lambda path: elite_list)
    monkeypatch.setattr(svc.elite, "latest_started_gameweek", lambda world, now: 3)
    calls = {"summarise": 0, "discover": 0}

    def summarise(*a, **k):
        calls["summarise"] += 1
        return summary or Signals()

    monkeypatch.setattr(svc.elite, "summarise", summarise)
    monkeypatch.setattr(
        svc.elite,
        "load_or_discover",
        lambda *a, **k: calls.__setitem__("discover", calls["discover"] + 1),
    )
    return calls


def test_merges_sources_and_saves(monkeypatch, tmp_path):
    def odds(*a, **k):
        return Signals(
            players={1: PlayerSignal(p_goal=0.4)},
            fixtures={9: FixtureSignal(1.5, 1.0)},
            updated={"odds": "t1"},
        )

    def news(*a, **k):
        return Signals(
            players={1: PlayerSignal(p_available=0.5)}, updated={"news": "t2"}
        )

    fake(monkeypatch, odds=odds, news=news)
    hub = svc.SignalsHub(None, tmp_path)
    s = hub.refresh(make_world(), now=NOW, discover=False)
    assert s.players[1].p_goal == 0.4 and s.players[1].p_available == 0.5
    assert "odds=t1" in hub.version and "news=t2" in hub.version
    assert (
        svc.SignalsHub(None, tmp_path).current().players[1].p_goal == 0.4
    )  # persisted


def test_failing_source_does_not_break_others(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("down")

    def news(*a, **k):
        return Signals(players={2: PlayerSignal(news="Fit")})

    fake(monkeypatch, odds=boom, news=news)
    s = svc.SignalsHub(None, tmp_path).refresh(make_world(), now=NOW, discover=False)
    assert s.players[2].news == "Fit"


def test_elite_summary_cached_per_gameweek(monkeypatch, tmp_path):
    summary = Signals(
        players={3: PlayerSignal(elite_own=0.6)},
        elite=EliteSummary(managers=2, gameweek=3, seasons=[]),
    )
    calls = fake(monkeypatch, elite_list=(NOW, ["m1", "m2"]), summary=summary)
    hub = svc.SignalsHub(None, tmp_path)
    for _ in range(2):
        s = hub.refresh(make_world(), now=NOW, discover=False)
    assert s.players[3].elite_own == 0.6
    assert calls["summarise"] == 1


def test_no_elite_list_starts_discovery_only_when_allowed(monkeypatch, tmp_path):
    calls = fake(monkeypatch, elite_list=None)
    hub = svc.SignalsHub(None, tmp_path)
    hub.refresh(make_world(), now=NOW, discover=False)
    assert calls["discover"] == 0
    hub.refresh(make_world(), now=NOW, discover=True)
    for _ in range(100):
        if not hub._discovering.is_set():
            break
        import time

        time.sleep(0.01)
    assert calls["discover"] == 1


def test_failed_source_reuses_its_last_good_result(monkeypatch, tmp_path):
    state = {"fail": False}

    def odds(*a, **k):
        if state["fail"]:
            raise ConnectionResetError("dropped")
        return Signals(players={5: PlayerSignal(p_goal=0.3)}, updated={"odds": "t"})

    fake(monkeypatch, odds=odds)
    hub = svc.SignalsHub(None, tmp_path)
    hub.refresh(make_world(), now=NOW, discover=False)
    version = hub.version
    state["fail"] = True
    s = hub.refresh(make_world(), now=NOW, discover=False)
    assert s.players[5].p_goal == 0.3
    assert hub.version == version  # advice cache not flushed by a blip


def test_refresh_tags_the_gameweek(monkeypatch, tmp_path):
    fake(monkeypatch)
    w = make_world()
    s = svc.SignalsHub(None, tmp_path).refresh(w, now=NOW, discover=False)
    assert s.gameweek == (w.next_event.id if w.next_event else None)


def test_partial_elite_summary_is_not_cached(monkeypatch, tmp_path):
    calls = fake(monkeypatch, elite_list=(NOW, ["m1"]), summary=Signals())
    hub = svc.SignalsHub(None, tmp_path)
    hub.refresh(make_world(), now=NOW, discover=False)
    hub.refresh(make_world(), now=NOW, discover=False)
    assert calls["summarise"] == 2

from types import SimpleNamespace

from fpl_advisor import serialize

XI = list(range(1, 12))
BENCH = [12, 13, 14, 15]


def fake(monkeypatch, captain=3, vice=4, initial=False, starting=XI):
    monkeypatch.setattr(serialize, "_player", lambda a, pid: {"id": pid})
    xp = {p: {9: float(p)} for p in XI + BENCH}
    squad = SimpleNamespace(
        players=XI + BENCH, starting=starting, captain=captain, vice=vice
    )
    return SimpleNamespace(
        squad=squad, initial=initial, event=SimpleNamespace(id=9), xp=xp
    )


def test_current_lineup_is_the_saved_team_scored_with_its_captain(monkeypatch):
    cur = serialize._current_lineup(fake(monkeypatch))
    assert [p["id"] for p in cur["xi"]] == XI
    assert [p["id"] for p in cur["bench"]] == BENCH  # bench keeps FPL's pick order
    assert cur["captain_id"] == 3 and cur["vice_id"] == 4
    assert cur["expected_points"] == sum(XI) + 3  # captain's score counts twice


def test_current_lineup_without_a_valid_captain_adds_no_bonus(monkeypatch):
    cur = serialize._current_lineup(fake(monkeypatch, captain=12, vice=None))
    assert cur["captain_id"] is None
    assert cur["expected_points"] == sum(XI)


def test_no_current_lineup_for_a_new_squad(monkeypatch):
    assert serialize._current_lineup(fake(monkeypatch, initial=True)) is None
    assert serialize._current_lineup(fake(monkeypatch, starting=XI[:5])) is None

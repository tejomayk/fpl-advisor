"""How outside signals (odds, team news) change the projection."""

import pytest
from conftest import fixture_for, make_world

from fpl_advisor import rules as R
from fpl_advisor.model import project
from fpl_advisor.signals import FixtureSignal, PlayerSignal, Signals

GWS = [4, 5, 6]


def mid(world, team=1):
    return next(p for p in world.players.values() if p.team == team and p.pos == R.MID)


def test_no_signals_matches_plain_projection():
    w = make_world()
    assert project(w, GWS, signals=Signals()) == project(w, GWS)


def test_news_can_at_most_halve_a_player_fpl_lists_as_fit():
    w = make_world()
    p = mid(w)
    base = project(w, GWS)[p.id]
    s = Signals(players={p.id: PlayerSignal(p_available=0.0)})
    got = project(w, GWS, signals=s)[p.id]
    assert got[4] == pytest.approx(base[4] * 0.5, rel=0.05)
    assert got[5] == pytest.approx(base[5])


def test_news_decides_for_doubtful_but_never_revives_suspended():
    w = make_world()
    p = mid(w)
    p.status, p.chance_next = "d", 50
    half = project(w, GWS)[p.id][4]
    out = project(
        w, GWS, signals=Signals(players={p.id: PlayerSignal(p_available=0.0)})
    )
    assert out[p.id][4] == 0.0 and half > 0
    p.status, p.chance_next = "s", 0
    back = project(
        w, GWS, signals=Signals(players={p.id: PlayerSignal(p_available=1.0)})
    )
    assert back[p.id][4] == 0.0


def test_stale_gameweek_signals_are_ignored():
    w = make_world()
    p = mid(w)
    base = project(w, GWS)[p.id][4]
    s = Signals(players={p.id: PlayerSignal(p_available=0.0, p_goal=0.9)}, gameweek=3)
    assert project(w, GWS, signals=s)[p.id][4] == pytest.approx(base)


def test_scorer_odds_are_conditional_on_playing():
    from fpl_advisor.model import Rates, _odds_adjustment

    r = Rates(0.5, 85, 0.8, 0.6, 0.3, 0.1, 0, 0.2, 0.1, 0)
    # Bookmakers' 30% (given he plays) with an 80% chance he appears is below what a naive
    # unconditional reading of the same odds would give.
    adj = _odds_adjustment(3, r, 1.0, 0.30, None)
    naive = (
        0.5
        * (-__import__("math").log(0.7) - 0.3 * (0.5 * 85 / 90 + 0.5 * 0.6 * 20 / 90))
        * 5
    )
    assert adj < naive


def test_news_rotation_risk_lowers_next_gameweek():
    w = make_world()
    p = mid(w)
    base = project(w, GWS)[p.id][4]
    s = Signals(players={p.id: PlayerSignal(p_start=0.2)})
    assert project(w, GWS, signals=s)[p.id][4] < base


def test_scorer_odds_move_points_toward_bookmakers():
    w = make_world()
    p = mid(w)
    base = project(w, GWS)[p.id][4]
    hot = Signals(players={p.id: PlayerSignal(p_goal=0.6)})
    cold = Signals(players={p.id: PlayerSignal(p_goal=0.02)})
    assert project(w, GWS, signals=hot)[p.id][4] > base
    assert project(w, GWS, signals=cold)[p.id][4] < base
    assert project(w, GWS, signals=cold)[p.id][4] >= 0


def test_fixture_odds_shift_both_teams_next_gameweek_only():
    w = make_world()
    f = fixture_for(w, 1, 4)
    attacker = mid(w, f.home)
    base = project(w, GWS)
    s = Signals(fixtures={f.id: FixtureSignal(lam_home=3.5, lam_away=0.3)})
    got = project(w, GWS, signals=s)
    assert got[attacker.id][4] > base[attacker.id][4]
    assert got[attacker.id][5] == pytest.approx(base[attacker.id][5])


def test_signals_round_trip(tmp_path):
    s = Signals(
        players={7: PlayerSignal(p_goal=0.3, news="Back in training", elite_own=0.4)},
        fixtures={11: FixtureSignal(1.6, 1.1)},
    )
    s.save(tmp_path / "s.json")
    back = Signals.load(tmp_path / "s.json")
    assert back.players[7].p_goal == 0.3 and back.players[7].news == "Back in training"
    assert back.fixtures[11].lam_home == 1.6
    assert Signals.load(tmp_path / "missing.json").players == {}


def test_evidence_lines_in_plain_words():
    from types import SimpleNamespace

    from fpl_advisor.advise import evidence
    from fpl_advisor.signals import EliteSummary

    s = Signals(
        players={
            1: PlayerSignal(
                news="Back in full training.", p_goal=0.42, elite_own=0.37, elite_in=8
            ),
            2: PlayerSignal(elite_own=0.05, elite_out=11),
        },
        elite=EliteSummary(managers=100, gameweek=9, seasons=["2025/26"]),
        gameweek=9,
    )
    a = SimpleNamespace(
        signals=s, event=SimpleNamespace(id=9), fixture_count=lambda pid: 1
    )
    buy = evidence(a, 1)
    assert buy == [
        "team news: Back in full training",
        "bookmakers give a 42% chance to score next match",
        "37 of the 100 most consistent managers own this player (8 bought last week)",
    ]
    assert evidence(a, 2, selling=True) == [
        "5 of the 100 most consistent managers own this player (11 sold last week)"
    ]
    assert evidence(a, 3) == []


def test_elite_ownership_is_a_small_nudge_next_gameweek_only():
    from fpl_advisor.signals import EliteSummary

    w = make_world()
    p = mid(w)
    base = project(w, GWS)[p.id]
    s = Signals(
        players={p.id: PlayerSignal(elite_own=0.9)},
        elite=EliteSummary(managers=100, gameweek=3, seasons=[]),
    )
    got = project(w, GWS, signals=s)[p.id]
    assert 0 < got[4] - base[4] <= 1.0
    assert got[5] == pytest.approx(base[5])

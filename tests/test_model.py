import math

import pytest
from conftest import fixture_for, make_world

from fpl_advisor import rules as R
from fpl_advisor.model import _poisson_floor_div, _poisson_sf, availability, project
from fpl_advisor.world import Fixture, Player


def pmf(mu, i):
    return math.exp(-mu + i * math.log(mu) - math.lgamma(i + 1))


@pytest.mark.parametrize("mu", [0.1, 0.8, 1.5, 3.0, 7.5])
@pytest.mark.parametrize("k", [0, 1, 2, 5, 10])
def test_poisson_sf_matches_brute_force(mu, k):
    expected = sum(pmf(mu, i) for i in range(k, 200))
    assert _poisson_sf(mu, k) == pytest.approx(expected, abs=1e-9)


def test_poisson_sf_edges():
    assert _poisson_sf(0, 0) == 1.0
    assert _poisson_sf(0, 3) == 0.0
    assert _poisson_sf(2.0, 0) == pytest.approx(1.0)


@pytest.mark.parametrize("mu", [0.2, 1.0, 2.5, 6.0])
@pytest.mark.parametrize("d", [1, 2, 3])
def test_poisson_floor_div_matches_brute_force(mu, d):
    expected = sum(pmf(mu, i) * (i // d) for i in range(200))
    assert _poisson_floor_div(mu, d) == pytest.approx(expected, abs=1e-9)


def test_poisson_floor_div_zero_mu():
    assert _poisson_floor_div(0, 2) == 0.0


def mk(status="a", chance=None):
    return Player(1, "x", "x x", 1, R.MID, 5.0, status=status, chance_next=chance)


def test_availability_fully_fit():
    assert availability(mk("a"), 0) == 1.0
    assert availability(mk("a"), 3) == 1.0


@pytest.mark.parametrize("status", ["u", "n"])
def test_availability_unavailable(status):
    assert availability(mk(status, 0), 0) == 0.0
    assert availability(mk(status), 4) == 0.0


def test_availability_doubtful_with_chance():
    p = mk("d", 75)
    assert availability(p, 0) == pytest.approx(0.75)
    assert availability(p, 1) == pytest.approx(1 - 0.25 * 0.5)
    assert availability(p, 5) > availability(p, 1)


def test_availability_a_with_chance():
    assert availability(mk("a", 75), 0) == pytest.approx(0.75)


@pytest.mark.parametrize("status", ["i", "s"])
def test_availability_out_gradual_return(status):
    p = mk(status, 0)
    assert availability(p, 0) == 0.0
    assert availability(p, 1) == pytest.approx(0.2)
    assert availability(p, 2) == pytest.approx(0.4)
    assert availability(p, 10) == pytest.approx(0.8)


def test_availability_injured_with_chance():
    assert availability(mk("i", 50), 0) == pytest.approx(0.5)


def test_availability_doubtful_no_chance_given():
    # No chance supplied for a non-'a' status is treated as 0.
    assert availability(mk("d", None), 0) == 0.0


GWS = [4, 5, 6]


def first_outfield(world, team):
    return next(p for p in world.players.values() if p.team == team and p.pos == R.MID)


def test_project_shape_and_nonnegative(world):
    xp = project(world, GWS)
    assert set(xp) == set(world.players)
    assert all(set(v) == set(GWS) for v in xp.values())
    assert all(x >= 0 for v in xp.values() for x in v.values())


def test_project_blank_gameweek_is_zero():
    w = make_world()
    team = 1
    f = fixture_for(w, team, 5)
    w = make_world(drop_fixtures={f.id})
    xp = project(w, GWS)
    for p in w.players.values():
        if p.team == team:
            assert xp[p.id][5] == 0.0
            assert xp[p.id][4] > 0
            assert xp[p.id][6] > 0


def test_project_double_gameweek_roughly_double():
    base = make_world()
    team = 1
    p = first_outfield(base, team)
    single = project(base, GWS)[p.id][5]
    f = fixture_for(base, team, 5)
    # a second GW5 fixture for the same team, with home/away reversed
    extra = Fixture(9999, 5, f.away, f.home, None, False)
    dbl = make_world(extra_fixtures=[extra])
    two = project(dbl, GWS)[p.id][5]
    assert two == pytest.approx(2 * single, rel=0.35)
    assert two > 1.5 * single


def test_project_home_vs_weak_defence_beats_away_vs_strong():
    w = make_world()
    # Team 11 concedes lots, team 12 concedes few; rebuild fixtures so team 1 plays both.
    for f in w.fixtures:
        if f.event in (1, 2, 3):
            f.home_goals = f.away_goals = 1
    for f in w.fixtures:
        if f.finished:
            if 11 in (f.home, f.away):
                f.home_goals, f.away_goals = (0, 5) if f.home == 11 else (5, 0)
            if 12 in (f.home, f.away):
                f.home_goals, f.away_goals = (0, 0)
    w.history = {}  # ratings fall back to goals
    w.fixtures = [f for f in w.fixtures if f.event not in (4, 5)]
    w.fixtures.append(Fixture(901, 4, 1, 11, None, False))  # home vs leaky defence
    w.fixtures.append(Fixture(902, 5, 12, 1, None, False))  # away at tight defence
    p = first_outfield(w, 1)
    xp = project(w, [4, 5])
    assert xp[p.id][4] > xp[p.id][5]


def test_project_unavailable_player_zero_but_use_availability_false_ignores():
    w = make_world()
    p = first_outfield(w, 2)
    p.status = "u"
    assert project(w, GWS)[p.id][4] == 0.0
    assert project(w, GWS, use_availability=False)[p.id][4] > 0

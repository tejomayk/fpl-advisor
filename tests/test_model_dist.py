"""project_dist: a distribution per player and gameweek alongside project()'s mean."""

import pytest
from conftest import fixture_for, make_world

from fpl_advisor import rules as R
from fpl_advisor.model import PointsDist, project, project_dist
from fpl_advisor.world import Fixture

GWS = [4, 5, 6]


def test_mean_is_exactly_project():
    w = make_world()
    xp = project(w, GWS)
    dist = project_dist(w, GWS)
    assert set(dist) == set(xp)
    for pid, by_gw in dist.items():
        for gw, d in by_gw.items():
            assert isinstance(d, PointsDist)
            assert d.mean == pytest.approx(xp[pid][gw])
            assert d.sd > 0 and d.mean_sd > 0
            assert 0 < d.p_play <= 1


def test_unavailable_player_has_no_spread():
    w = make_world()
    p = next(p for p in w.players.values() if p.pos == R.MID)
    p.status = "u"
    d = project_dist(w, GWS)[p.id][4]
    assert d.mean == d.sd == d.mean_sd == d.p_play == 0.0


def test_more_matches_mean_more_certainty():
    w = make_world()
    p = next(p for p in w.players.values() if p.pos == R.FWD)
    few = project_dist(w, GWS)[p.id][4].mean_sd
    w.history[p.id] = w.history[p.id] * 4  # same matches seen four times over
    for i, r in enumerate(w.history[p.id]):
        w.history[p.id][i] = {**r, "round": 1 + i // 3}
    assert project_dist(w, GWS)[p.id][4].mean_sd < few


def test_double_gameweek_widens_the_spread():
    base = make_world()
    p = next(p for p in base.players.values() if p.team == 1 and p.pos == R.MID)
    f = fixture_for(base, 1, 5)
    dbl = make_world(extra_fixtures=[Fixture(9999, 5, f.away, f.home, None, False)])
    one, two = project_dist(base, GWS)[p.id][5], project_dist(dbl, GWS)[p.id][5]
    assert two.sd > one.sd
    assert two.mean > one.mean

"""Stage 4: choosing the plan and captain by P(finish 1st) when a league is set."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from conftest import make_world, pick_squad

from fpl_advisor import advise
from fpl_advisor import leaguechoice as LC
from fpl_advisor import rules as R
from fpl_advisor.config import Config
from fpl_advisor.model import PointsDist
from fpl_advisor.optimise import Plan
from fpl_advisor.serialize import advice_to_dict
from fpl_advisor.squad import Squad

GW = 38
LAYOUT = [R.GKP] * 2 + [R.DEF] * 5 + [R.MID] * 5 + [R.FWD] * 3
STAR, DIFF = 13, 12  # a forward everyone captains, and a midfielder nobody does


def tiny_world():
    players = {
        i + 1: SimpleNamespace(pos=pos, team=1 + i % 2, name=f"P{i + 1}")
        for i, pos in enumerate(LAYOUT)
    }
    fx = [SimpleNamespace(home=1, away=2)]
    return SimpleNamespace(players=players, fixtures_in=lambda gw: fx, chips=[])


def tiny_dists(world):
    out = {}
    for p in world.players:
        mean, sd = 3.0, 2.0
        if p == STAR:
            mean, sd = 8.0, 6.0
        elif p == DIFF:
            mean, sd = 6.0, 6.0
        out[p] = {GW: PointsDist(mean, sd, 0.3, 0.95)}
    return out


def my_plan(dists) -> Plan:
    squad = list(range(1, 16))
    xi = [1, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14]
    bench = [2, 6, 7, 15]
    return Plan(
        squad=squad,
        buys=[],
        sells=[],
        hits=0,
        xi={GW: xi},
        captain={GW: STAR},
        vice={GW: DIFF},
        bench={GW: bench},
        objective=0.0,
        bank_after=0.0,
        gw_points={GW: sum(dists[p][GW].mean for p in xi) + dists[STAR][GW].mean},
    )


def view(my_total: int, rival_total: int, n_rivals: int = 5):
    squad = list(range(1, 16))

    def manager(entry, total, rank):
        return SimpleNamespace(
            entry=entry,
            team_name=f"Team {entry}",
            manager=f"Manager {entry}",
            total=total,
            picks=list(squad),
            captain=STAR,
            gws_played=37,
            chips_used=[],
            rank=rank,
        )

    me = manager(1, my_total, 1 if my_total > rival_total else n_rivals + 1)
    rivals = [manager(10 + k, rival_total, k + 1) for k in range(n_rivals)]
    return SimpleNamespace(me=me, rivals=rivals, remaining_gws=1)


def choose(my_total, rival_total):
    world = tiny_world()
    dists = tiny_dists(world)
    xp = {p: {GW: d[GW].mean} for p, d in dists.items()}
    plan = my_plan(dists)
    return LC.choose(
        view(my_total, rival_total), world, dists, xp, [GW], plan, {}, n_sims=4000
    )


def test_far_behind_switches_to_a_differential_captain():
    # Everyone owns the same 15 and captains the star: with one week left and 10 points behind,
    # copying them can't win; a different captain can.
    c = choose(1500, 1510)
    assert c.switched
    assert c.plan.captain[GW] != STAR
    assert c.p_first[c.name] > c.p_first["plan"] + 2 * c.gain_se
    assert c.gain > 0
    assert c.reason_captain and "instead of P13" in c.reason_captain
    assert "behind" in c.reason_captain


def test_ahead_keeps_the_captain_everyone_has():
    c = choose(1510, 1500)
    assert not c.switched
    assert c.plan.captain[GW] == STAR
    assert c.reason_captain is None
    assert (
        c.odds.p_first[1] > 0.9
    )  # identical teams and a 10-point lead with a week left


def test_pick_needs_more_than_two_standard_errors():
    res = SimpleNamespace(vs_baseline={"plan": (0.0, 0.0), "a": (0.01, 0.006)})
    assert LC.pick(res)[0] == "plan"
    res = SimpleNamespace(vs_baseline={"plan": (0.0, 0.0), "a": (0.02, 0.006)})
    assert LC.pick(res)[0] == "a"


# ------------------------------------------------------------------ no league: nothing changes


def test_no_league_path_unchanged(monkeypatch):
    world = make_world()
    players = pick_squad(world)
    sell = {p: world.players[p].price for p in players}
    sq = Squad(1, "me", "me", players, [], None, sell, dict(sell), 1.0, 1, [])

    def no_league(*a, **k):  # the league choice must not run without a league
        raise AssertionError("league choice called without a league")

    monkeypatch.setattr(advise, "load_squad", lambda *a, **k: sq)
    monkeypatch.setattr(advise, "choose_for_league", no_league)
    client = SimpleNamespace(element_summaries=lambda ids: {})
    now = datetime(2020, 1, 1, tzinfo=UTC)
    a = advise.build_advice(Config(team_id=1), client, now=now, world=world)
    dec = advise.decide(world, sq, initial=False, max_hits=Config(1).max_hits)
    assert a.league_choice is None
    assert a.plan.buys == dec.plan.buys and a.plan.captain == dec.plan.captain
    d = advice_to_dict(a)
    assert d["league"] is None
    assert d["captain"]["reason_league"] is None
    conf = d["transfers"]["confidence"]
    if a.plan.buys or a.p_beats_hold is not None:
        assert conf["level"] in ("strong", "likely", "close call")
        assert 0 <= conf["p_beats_hold"] <= 1
    for m in d["transfers"]["moves"]:
        assert m["confidence"]["level"] in ("strong", "likely", "close call")


@pytest.mark.parametrize(
    "p,level", [(0.9, "strong"), (0.7, "likely"), (0.5, "close call")]
)
def test_serialized_confidence_shape(p, level):
    a = SimpleNamespace(initial=False, confidence=p, p_beats_hold=p)
    assert advise.transfer_confidence(a) == {"level": level, "p_beats_hold": p}

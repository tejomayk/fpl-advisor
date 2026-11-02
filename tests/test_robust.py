"""Stage 3: stress-testing this week's moves against forecast error (robust.py, advise.decide)."""

import pytest
from conftest import make_world, pick_squad

from fpl_advisor import advise, robust
from fpl_advisor import rules as R
from fpl_advisor.model import PointsDist
from fpl_advisor.optimise import Move, solve
from fpl_advisor.squad import Squad

GWS = [4, 5, 6, 7, 8]


def outsiders(world, squad, pos, n):
    held: dict[int, int] = {}
    for p in squad:
        held[world.players[p].team] = held.get(world.players[p].team, 0) + 1
    out = []
    for p in sorted(world.players.values(), key=lambda p: (p.price, p.id)):
        if p.pos != pos or p.id in squad or held.get(p.team, 0) >= R.MAX_PER_CLUB - 1:
            continue
        out.append(p.id)
        held[p.team] = held.get(p.team, 0) + 1
        if len(out) == n:
            break
    return out


def squad_obj(world, players, ft=1):
    sell = {p: world.players[p].price for p in players}
    return Squad(0, "me", "me", list(players), [], None, sell, dict(sell), 10.0, ft, [])


def run_decide(monkeypatch, world, squad, dists, ft=1):
    monkeypatch.setattr(advise, "project_dist", lambda w, gws, **kw: dists)
    return advise.decide(world, squad_obj(world, squad, ft), initial=False, max_hits=2)


def dists_with(world, squad, special, owned=3.0, msd=0.2):
    """Owned players score `owned` a week, others 0, plus `special` {pid: {gw: (mean, msd)}}."""
    out = {}
    for p in world.players:
        m = owned if p in squad else 0.0
        out[p] = {g: PointsDist(m, 3.0, msd if m else 0.0, 0.9) for g in GWS}
    for p, by_gw in special.items():
        for g, (m, s) in by_gw.items():
            out[p][g] = PointsDist(m, 3.0, s, 0.9)
    return out


@pytest.fixture
def base():
    world = make_world()
    return world, pick_squad(world)


def test_labels():
    assert robust.level(0.95) == "strong"
    assert robust.level(0.85) == "strong"
    assert robust.level(0.7) == "likely"
    assert robust.level(0.65) == "likely"
    assert robust.level(0.6) == "close call"
    assert robust.level(0.2) == "close call"


def test_compare_is_symmetric_and_grows_with_the_margin(base):
    world, squad = base
    sell = {p: world.players[p].price for p in squad}
    xp = {p: {g: (3.0 if p in squad else 0.0) for g in GWS} for p in world.players}
    a = outsiders(world, squad, R.MID, 1)[0]
    xp[a] = {g: 4.0 for g in GWS}
    plan = solve(world, xp, GWS, squad, sell, 10.0, 1)
    hold = solve(world, xp, GWS, squad, sell, 10.0, 1, max_transfers=0)
    assert a in plan.buys
    msd = {p: {g: 0.5 for g in GWS} for p in world.players}
    after = {p: 0.5 for p in world.players}
    lp = robust.loadings(plan, GWS, msd, after, world=world)
    lh = robust.loadings(hold, GWS, msd, after, world=world)
    ab, ba = robust.compare(plan, hold, lp, lh), robust.compare(hold, plan, lh, lp)
    assert ab.p == pytest.approx(1 - ba.p)
    assert 0.5 < ab.p < 1
    # Players held in the same role by both plans cancel: only the swap adds spread.
    assert ab.sd < 0.5 * sum(abs(v) for v in lp.values())
    # Less uncertainty about the forecasts makes the same gain surer.
    tight = {p: {g: 0.1 for g in GWS} for p in world.players}
    lp2 = robust.loadings(plan, GWS, tight, after, world=world)
    lh2 = robust.loadings(hold, GWS, tight, after, world=world)
    assert robust.compare(plan, hold, lp2, lh2).p > ab.p


def test_move_confidence_counts_the_hit_share():
    m = Move(out=1, inn=2, sell_price=5.0, buy_price=5.0, gain=3.0)
    from fpl_advisor.optimise import Plan

    plan = Plan(
        squad=[2],
        buys=[2],
        sells=[1],
        hits=0,
        xi={4: [2]},
        captain={4: 2},
        vice={4: 2},
        bench={4: []},
        objective=0.0,
        bank_after=0.0,
        squads={4: [2]},
    )
    msd = {1: {4: 1.0}, 2: {4: 1.0}}
    free = robust.move_confidence(m, plan, [4], msd)
    hit = robust.move_confidence(m, plan, [4], msd, cost=4.0)
    assert free.p > 0.5 > hit.p


def test_hold_beats_a_marginal_hit(monkeypatch, base):
    world, squad = base
    a, b = outsiders(world, squad, R.MID, 2)
    # a: a clear upgrade (worth the free transfer). b: worth a little more than the -4 hit this
    # week only, and we know little about him (large forecast error).
    special = {
        a: {g: (8.0, 0.2) for g in GWS},
        b: {g: ((7.6 if g == GWS[0] else 3.0), 1.5) for g in GWS},
    }
    monkeypatch.setattr(robust, "HIT_CONFIDENCE", 0.7)
    dec = run_decide(monkeypatch, world, squad, dists_with(world, squad, special))
    first = dec.alternatives.get("planner")
    assert (
        first is not None and first.hits == 1 and b in first.buys
    )  # the planner took it ...
    assert (
        dec.plan.hits == 0 and a in dec.plan.buys and b not in dec.plan.buys
    )  # ... not now
    assert dec.p_hit is not None and dec.p_hit < robust.HIT_CONFIDENCE
    assert "hit" in dec.robust_note
    assert (
        dec.confidence == dec.p_beats_hold > 0.85
    )  # the free move itself is a strong call
    assert len(dec.move_confidence) == 1 and dec.move_confidence[0].p > 0.85


def test_a_sure_hit_is_kept(monkeypatch, base):
    world, squad = base
    a, b = outsiders(world, squad, R.MID, 2)
    special = {a: {g: (12.0, 0.1) for g in GWS}, b: {g: (12.0, 0.1) for g in GWS}}
    monkeypatch.setattr(robust, "HIT_CONFIDENCE", 0.7)
    dec = run_decide(monkeypatch, world, squad, dists_with(world, squad, special))
    assert dec.plan.hits == 1 and {a, b} <= set(dec.plan.buys)
    assert dec.robust_note is None
    assert dec.p_hit is not None and dec.p_hit > 0.85


def test_marginal_free_move_is_held(monkeypatch, base):
    world, squad = base
    (a,) = outsiders(world, squad, R.MID, 1)
    # A slightly better player we know little about.
    special = {a: {g: (3.4, 1.5) for g in GWS}}
    dists = dists_with(world, squad, special)
    monkeypatch.setattr(robust, "MOVE_CONFIDENCE", 0.65)
    dec = run_decide(monkeypatch, world, squad, dists)
    first = dec.alternatives.get("planner")
    assert first is not None and a in first.buys  # the planner would make the move ...
    assert not dec.plan.buys
    assert dec.p_beats_hold < 0.65
    assert dec.confidence == pytest.approx(1 - dec.p_beats_hold, abs=1e-3)

import pytest
from conftest import pick_squad

from fpl_advisor import rules as R
from fpl_advisor.optimise import solve

GWS = [4, 5, 6]


def make_xp(world, boost=None):
    """Deterministic xp: dearer players are better; GKP/DEF a bit lower."""
    scale = {R.GKP: 0.8, R.DEF: 0.9, R.MID: 1.0, R.FWD: 1.0}
    xp = {}
    for p in world.players.values():
        base = (p.price - 3.0) * scale[p.pos] + (p.id % 7) * 0.05
        xp[p.id] = {g: base for g in GWS}
    for pid, v in (boost or {}).items():
        xp[pid] = {g: v for g in GWS}
    return xp


@pytest.fixture
def setup(world):
    squad = pick_squad(world)
    sell = {p: world.players[p].price for p in squad}
    return world, squad, sell, make_xp(world)


def run(setup, bank=2.0, ft=1, **kw):
    world, squad, sell, xp = setup
    return solve(world, xp, GWS, squad, sell, bank, ft, **kw), world, squad, sell, xp


def check_valid(plan, world, sell, bank, squad):
    pl = world.players
    assert len(plan.squad) == len(set(plan.squad)) == 15
    for pos, n in R.SQUAD_COUNT.items():
        assert sum(pl[p].pos == pos for p in plan.squad) == n
    for t in world.teams:
        assert sum(pl[p].team == t for p in plan.squad) <= R.MAX_PER_CLUB
    cost = sum(sell[p] if p in squad else pl[p].price for p in plan.squad)
    assert cost <= bank + sum(sell[p] for p in squad) + 1e-6
    for g in GWS:
        xi = plan.xi[g]
        assert len(xi) == 11 and set(xi) <= set(plan.squad)
        for pos in R.POS_NAME:
            n = sum(pl[p].pos == pos for p in xi)
            assert R.XI_MIN[pos] <= n <= R.XI_MAX[pos]
        assert plan.captain[g] in xi and plan.vice[g] in xi
        assert plan.captain[g] != plan.vice[g]
        assert len(plan.bench[g]) == 4 and plan.bench[g][0] in plan.squad
        assert pl[plan.bench[g][0]].pos == R.GKP


def test_solution_valid(setup):
    plan, world, squad, sell, _ = run(setup, ft=2)
    check_valid(plan, world, sell, 2.0, squad)
    assert plan.bank_after >= -1e-6


def test_one_ft_no_hits_at_most_one_transfer(setup):
    plan, *_ = run(setup, ft=1, max_hits=0)
    assert len(plan.buys) <= 1
    assert len(plan.sells) == len(plan.buys)
    assert plan.hits == 0


def test_max_hits_zero_never_hits(setup):
    plan, *_ = run(setup, ft=0, max_hits=0)
    assert plan.hits == 0
    assert plan.buys == []


def test_hits_counted_when_beneficial(setup):
    plan, *_ = run(setup, ft=1, max_hits=3, bank=5.0)
    assert plan.hits == max(0, len(plan.buys) - 1)


def test_unlimited_reports_zero_hits(setup):
    plan, world, squad, sell, _ = run(setup, ft=1, unlimited=True, bank=2.0)
    assert plan.hits == 0
    assert len(plan.buys) > 1  # a wildcard on a cheap squad makes many changes
    check_valid(plan, world, sell, 2.0, squad)


def test_injured_player_sold_for_better_same_price(setup):
    world, squad, sell, xp = setup
    victim = max(
        (p for p in squad if world.players[p].pos == R.MID),
        key=lambda p: world.players[p].price,
    )
    xp[victim] = {g: 0.0 for g in GWS}
    world.players[victim].status = "u"
    plan = solve(world, xp, GWS, squad, sell, 0.0, 1, max_hits=0)
    assert victim in plan.sells
    assert victim not in plan.squad
    assert len(plan.buys) == 1
    assert world.players[plan.buys[0]].price <= sell[victim] + 0.0 + 1e-6


def test_forced_out_respected(setup):
    world, squad, sell, xp = setup
    # Make the forced player the best in the squad so only forced_out can remove them.
    target = max(squad, key=lambda p: xp[p][4])
    xp[target] = {g: 50.0 for g in GWS}
    plan = solve(world, xp, GWS, squad, sell, 5.0, 1, forced_out={target}, max_hits=2)
    assert target not in plan.squad
    assert target in plan.sells


def test_banned_not_bought(setup):
    world, squad, sell, xp = setup
    best = max((p for p in world.players if p not in squad), key=lambda p: xp[p][4])
    plan = solve(world, xp, GWS, squad, sell, 10.0, 5, banned={best})
    assert best not in plan.squad


def test_max_transfers(setup):
    world, squad, sell, xp = setup
    plan = solve(world, xp, GWS, squad, sell, 5.0, 5, max_transfers=2)
    assert len(plan.buys) <= 2


def test_triple_captain_and_bench_boost_change_points(setup):
    world, squad, sell, xp = setup
    base = solve(world, xp, GWS, squad, sell, 2.0, 1, max_hits=0)
    tc = solve(world, xp, GWS, squad, sell, 2.0, 1, max_hits=0, triple_captain_gw=5)
    bb = solve(world, xp, GWS, squad, sell, 2.0, 1, max_hits=0, bench_boost_gw=5)
    cap_xp = xp[base.captain[5]][5]
    assert cap_xp > 0
    assert tc.gw_points[5] == pytest.approx(base.gw_points[5] + cap_xp, rel=1e-6)
    bench_xp = sum(xp[p][5] for p in base.bench[5])
    assert bb.gw_points[5] == pytest.approx(base.gw_points[5] + bench_xp, rel=1e-6)
    # other gameweeks are unaffected
    assert tc.gw_points[4] == pytest.approx(base.gw_points[4])
    assert bb.gw_points[6] == pytest.approx(base.gw_points[6])


def test_gw_points_formula(setup):
    plan, _world, _squad, _sell, xp = run(setup, ft=1, max_hits=0)
    for g in GWS:
        expect = sum(xp[p][g] for p in plan.xi[g]) + xp[plan.captain[g]][g]
        assert plan.gw_points[g] == pytest.approx(expect)

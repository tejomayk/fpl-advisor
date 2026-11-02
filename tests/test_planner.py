"""The multi-period planner: free transfers carried between weeks, hits, clubs, chips, pairing."""

import pytest
from conftest import make_world, pick_squad

from fpl_advisor import league as L
from fpl_advisor import rules as R
from fpl_advisor.chips import advise_chips
from fpl_advisor.optimise import pair_moves, solve
from fpl_advisor.squad import Squad

GWS = [4, 5, 6, 7, 8]


def flat_xp(world, squad, owned_xp=3.0):
    """Owned players score `owned_xp` a week; everyone else 0 (so no move is worth anything)."""
    return {
        p: {g: (owned_xp if p in squad else 0.0) for g in GWS} for p in world.players
    }


def outsiders(world, squad, pos, n):
    """Players not owned, at a position, from clubs where the squad has room."""
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


@pytest.fixture
def base():
    world = make_world()
    squad = pick_squad(world)
    sell = {p: world.players[p].price for p in squad}
    return world, squad, sell


def test_rolls_a_transfer_when_next_week_has_two_better_moves(base):
    world, squad, sell = base
    xp = flat_xp(world, squad)
    # A small upgrade now (+0.5 a week) ...
    small = outsiders(world, squad, R.DEF, 1)[0]
    xp[small] = {g: 3.5 for g in GWS}
    # ... and two players who only become worth buying next week.
    x, y = outsiders(world, squad, R.MID, 2)
    for p in (x, y):
        xp[p] = {g: (0.0 if g == 4 else 12.0) for g in GWS}
    plan = solve(world, xp, GWS, squad, sell, 10.0, 1, max_hits=2)
    assert plan.buys == []  # the free transfer is saved ...
    assert plan.weeks[1].free_transfers == 2
    assert set(plan.weeks[1].buys) >= {x, y}  # ... to bring both in next week, free
    assert plan.weeks[1].hits == 0


def test_no_hit_for_a_small_gain(base):
    world, squad, sell = base
    xp = flat_xp(world, squad)
    a, b = outsiders(world, squad, R.MID, 2)
    xp[a] = {g: 4.0 for g in GWS}
    xp[b] = {g: 4.0 for g in GWS}
    plan = solve(world, xp, GWS, squad, sell, 10.0, 1, max_hits=2)
    # One move now with the free transfer; the other waits for next week's instead of a -4.
    assert plan.hits == 0
    assert len(plan.buys) == 1
    later = {p for w in plan.weeks[1:] for p in w.buys}
    assert ({a, b} - set(plan.buys)) <= later


def test_hit_taken_when_it_pays(base):
    world, squad, sell = base
    xp = flat_xp(world, squad)
    a, b = outsiders(world, squad, R.MID, 2)
    xp[a] = {g: 12.0 for g in GWS}
    xp[b] = {g: (12.0 if g == 4 else 0.0) for g in GWS}  # only worth having this week
    plan = solve(world, xp, GWS, squad, sell, 10.0, 1, max_hits=2)
    assert {a, b} <= set(plan.buys)
    assert plan.hits == len(plan.buys) - 1


def test_no_pointless_moves_with_full_free_transfers(base):
    world, squad, sell = base
    xp = flat_xp(world, squad)
    plan = solve(world, xp, GWS, squad, sell, 10.0, 5)
    assert plan.buys == [] and plan.sells == []


def test_free_transfers_respect_the_season_cap(base):
    world, squad, sell = base
    xp = flat_xp(world, squad)
    plan = solve(world, xp, GWS, squad, sell, 10.0, 2, ft_cap=2)
    assert all(w.free_transfers <= 2 for w in plan.weeks)
    plan5 = solve(world, xp, GWS, squad, sell, 10.0, 2, ft_cap=5)
    assert [w.free_transfers for w in plan5.weeks] == [2, 3, 4, 5, 5]


def test_wildcard_keeps_or_resets_free_transfers(base):
    world, squad, sell = base
    xp = flat_xp(world, squad)
    keep = solve(world, xp, GWS, squad, sell, 10.0, 3, wildcard_gw=4)
    reset = solve(
        world, xp, GWS, squad, sell, 10.0, 3, wildcard_gw=4, keep_fts_on_chip=False
    )
    assert keep.weeks[1].free_transfers == 3
    assert reset.weeks[1].free_transfers == 1
    assert keep.hits == 0


def test_club_over_the_limit_is_not_infeasible():
    world = make_world()
    squad = pick_squad(world)
    # A player in the squad moves to a club the squad already has three from (the old one-shot
    # optimiser found that infeasible), and a strong player from elsewhere joins that club too.
    clubs: dict[int, list[int]] = {}
    for p in squad:
        clubs.setdefault(world.players[p].team, []).append(p)
    full = next(t for t, ps in clubs.items() if len(ps) == R.MAX_PER_CLUB)
    mover = next(p for p in squad if world.players[p].team != full)
    world.players[mover].team = full
    newcomer = next(
        p.id
        for p in world.players.values()
        if p.id not in squad and p.pos == world.players[mover].pos
    )
    world.players[newcomer].team = full
    sell = {p: world.players[p].price for p in squad}
    xp = flat_xp(world, squad)
    xp[newcomer] = {g: 20.0 for g in GWS}
    plan = solve(world, xp, GWS, squad, sell, 10.0, 1)
    # The illegal state may stay until a sale, but nobody new joins from that club.
    for g in GWS:
        assert newcomer not in plan.squads[g]
        assert sum(world.players[p].team == full for p in plan.squads[g]) <= 4
    # With no transfer possible this week the four must simply stay.
    stuck = solve(world, xp, GWS, squad, sell, 10.0, 0, max_hits=0)
    assert stuck.buys == [] and set(stuck.squad) == set(squad)


def test_bench_order_follows_expected_points(base):
    world, squad, sell = base
    xp = flat_xp(world, squad)
    outfield = [p for p in squad if world.players[p].pos != R.GKP]
    for k, p in enumerate(outfield):
        xp[p] = {g: 1.0 + 0.1 * k for g in GWS}
    plan = solve(world, xp, GWS, squad, sell, 0.0, 1, max_hits=0)
    bench = plan.bench[4]
    assert world.players[bench[0]].pos == R.GKP
    pts = [xp[p][4] for p in bench[1:]]
    assert pts == sorted(pts, reverse=True)


def test_pairing_matches_position_and_price(world):
    pl = world.players
    mids = sorted((p for p in pl.values() if p.pos == R.MID), key=lambda p: p.price)
    defs = sorted((p for p in pl.values() if p.pos == R.DEF), key=lambda p: p.price)
    sells = [mids[0].id, mids[1].id, defs[0].id]
    buys = [mids[-1].id, defs[-1].id, mids[-2].id]
    sp = {p: pl[p].price for p in sells}
    moves = pair_moves(world, buys, sells, sp)
    assert len(moves) == 3
    for m in moves:
        assert pl[m.out].pos == pl[m.inn].pos
    mid_moves = [m for m in moves if pl[m.out].pos == R.MID]
    # Dearest sale pays for the dearest signing.
    dearest_out = max(mid_moves, key=lambda m: m.sell_price)
    assert dearest_out.inn == max((m.inn for m in mid_moves), key=lambda p: pl[p].price)
    assert all(m.money == round(m.sell_price - m.buy_price, 1) for m in moves)


def test_move_gain_counts_weeks_the_plan_keeps_him(base):
    world, squad, sell = base
    xp = flat_xp(world, squad)
    a = outsiders(world, squad, R.MID, 1)[0]
    xp[a] = {g: 8.0 for g in GWS}
    plan = solve(world, xp, GWS, squad, sell, 10.0, 1, max_hits=0)
    (m,) = plan.moves
    assert m.inn == a
    # He starts every week (and captains), replacing a 3-point player: a clearly positive gain,
    # bigger than one week's difference.
    assert m.gain > 8.0 - 3.0


# ---------------------------------------------------------------- chips


def chip_squad(world, players, chips):
    sell = {p: world.players[p].price for p in players}
    return Squad(
        team_id=1,
        manager="m",
        team_name="t",
        players=players,
        starting=players[:11],
        captain=None,
        sell_price=sell,
        purchase_price=dict(sell),
        bank=0.0,
        free_transfers=1,
        chips_available=chips,
    )


def test_expiring_chip_not_forced_when_worth_nothing():
    world = make_world()
    world.chips = [{"name": "bboost", "start_event": 1, "stop_event": 4}]
    players = pick_squad(world)
    xp = flat_xp(world, players)
    base_plan = solve(
        world, xp, GWS, players, {p: world.players[p].price for p in players}, 0.0, 1
    )
    for p in base_plan.bench[4]:
        xp[p][4] = 0.0  # the bench won't score this week
    squad = chip_squad(world, players, ["bboost"])
    plan = solve(world, xp, GWS, players, squad.sell_price, 0.0, 1)
    (bb,) = advise_chips(world, xp, GWS, GWS, squad, plan)
    assert bb.expires_gw == 4
    assert bb.play_now is False


def test_expiring_chip_used_when_it_gains():
    world = make_world()
    world.chips = [{"name": "3xc", "start_event": 1, "stop_event": 4}]
    players = pick_squad(world)
    xp = flat_xp(world, players)
    squad = chip_squad(world, players, ["3xc"])
    plan = solve(world, xp, GWS, players, squad.sell_price, 0.0, 1)
    (tc,) = advise_chips(world, xp, GWS, GWS, squad, plan)
    assert tc.play_now and tc.forced
    assert tc.plan is not None and tc.plan.chips == {4: "3xc"}


def test_chip_with_time_left_kept_for_an_ordinary_week():
    world = make_world()
    world.chips = [{"name": "3xc", "start_event": 1, "stop_event": 38}]
    players = pick_squad(world)
    xp = flat_xp(world, players)
    squad = chip_squad(world, players, ["3xc"])
    plan = solve(world, xp, GWS, players, squad.sell_price, 0.0, 1)
    (tc,) = advise_chips(world, xp, GWS, GWS, squad, plan)
    assert (
        tc.play_now is False
    )  # a 3-point captain is far below what the chip is worth later


# ---------------------------------------------------------------- rival multiplier


def test_multiplier_off_without_rival_data():
    assert L.ownership_multiplier([1, 2], {}, 0.2) == {}


def test_multiplier_centred_on_own_squad():
    own = {1: 0.8, 2: 0.4, 3: 0.0}
    centre = L.ownership_centre([1, 2], own)
    m = L.ownership_multiplier([1, 2, 3], own, 0.2, centre)
    assert (m[1] + m[2]) / 2 == pytest.approx(1.0)
    assert m[3] > 1 > m[1]

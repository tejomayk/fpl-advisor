"""Game-rule mechanics used by the season simulator (scripts/season_sim.py)."""

import pytest

from fpl_advisor import rules as R
from fpl_advisor.sim import (
    SEASONS,
    Manager,
    apply_transfers,
    auto_subs,
    hits_for,
    next_free_transfers,
    score_gameweek,
)
from fpl_advisor.squad import selling_price

# ------------------------------------------------------------------ selling price and bank


@pytest.mark.parametrize(
    "bought,now,sell",
    [
        (5.0, 5.0, 5.0),
        (5.0, 5.1, 5.0),  # half of 0.1 rounds down to nothing
        (5.0, 5.2, 5.1),
        (5.0, 5.3, 5.1),
        (7.5, 8.4, 7.9),
        (5.0, 4.8, 4.8),  # falls are passed on in full
    ],
)
def test_selling_price(bought, now, sell):
    assert selling_price(bought, now) == sell


def test_apply_transfers_uses_selling_price_and_todays_price():
    m = Manager([1, 2], {1: 5.0, 2: 6.0}, bank=0.5)
    price = {1: 5.3, 2: 6.0, 3: 5.6}
    after = apply_transfers(m, buys=[3], sells=[1], price_now=price)
    assert after.bank == pytest.approx(0.5 + 5.1 - 5.6)
    assert sorted(after.players) == [2, 3]
    assert after.purchase_price == {2: 6.0, 3: 5.6}
    assert m.players == [1, 2]  # the original is untouched


def test_apply_transfers_rejects_overspend_and_unowned():
    m = Manager([1], {1: 5.0}, bank=0.0)
    with pytest.raises(ValueError):
        apply_transfers(m, [2], [1], {1: 5.0, 2: 5.5})
    with pytest.raises(ValueError):
        apply_transfers(m, [2], [9], {1: 5.0, 2: 4.0, 9: 4.0})


# ------------------------------------------------------------------ free transfers


def test_ft_accrual_and_cap_of_five():
    r = SEASONS["2024-25"]
    ft = 1
    for g in range(2, 10):
        ft = next_free_transfers(ft, 0, None, r, g)
    assert ft == 5
    assert next_free_transfers(5, 0, None, r, 11) == 5  # capped
    assert next_free_transfers(5, 2, None, r, 11) == 4
    assert next_free_transfers(1, 3, None, r, 11) == 1  # hits never make it negative


def test_ft_cap_of_two_in_2023_24_and_chip_resets():
    r = SEASONS["2023-24"]
    assert next_free_transfers(2, 0, None, r, 5) == 2
    assert next_free_transfers(2, 0, "wildcard", r, 5) == 1
    assert next_free_transfers(2, 0, "freehit", r, 5) == 1


def test_fts_kept_through_wildcard_and_free_hit_from_2024_25():
    r = SEASONS["2024-25"]
    assert next_free_transfers(4, 9, "freehit", r, 30) == 4
    assert next_free_transfers(3, 15, "wildcard", r, 10) == 3


def test_afcon_top_up_2025_26():
    r = SEASONS["2025-26"]
    assert next_free_transfers(1, 1, None, r, 16) == 5
    assert next_free_transfers(1, 1, None, r, 17) == 1


def test_hits():
    assert hits_for(3, 1, None) == 2
    assert hits_for(1, 2, None) == 0
    assert hits_for(12, 1, "wildcard") == 0
    assert hits_for(12, 1, "freehit") == 0


# ------------------------------------------------------------------ auto-subs and captaincy

# 1 GK, 4 DEF, 4 MID, 2 FWD starting; bench GK 12, then 13 (DEF), 14 (MID), 15 (FWD).
POS = {
    1: R.GKP,
    2: R.DEF,
    3: R.DEF,
    4: R.DEF,
    5: R.DEF,
    6: R.MID,
    7: R.MID,
    8: R.MID,
    9: R.MID,
    10: R.FWD,
    11: R.FWD,
    12: R.GKP,
    13: R.DEF,
    14: R.MID,
    15: R.FWD,
}
XI = list(range(1, 12))
BENCH = [12, 13, 14, 15]


def mins(zero=(), **_):
    return {p: (0 if p in zero else 90) for p in POS}


def test_no_subs_when_everyone_plays():
    final, subs = auto_subs(XI, BENCH, mins(), POS)
    assert final == XI and subs == []


def test_first_bench_outfielder_in_order_comes_on():
    final, subs = auto_subs(XI, BENCH, mins(zero={7}), POS)
    assert subs == [(7, 13)]  # bench order, not position, decides
    assert 13 in final and 7 not in final


def test_goalkeeper_only_replaced_by_goalkeeper():
    _, subs = auto_subs(XI, BENCH, mins(zero={1}), POS)
    assert subs == [(1, 12)]
    # Outfielders never use the bench GK, even if he's first on the bench.
    _, subs = auto_subs(XI, BENCH, mins(zero={10}), POS)
    assert subs == [(10, 13)]


def test_bench_player_who_did_not_play_is_skipped():
    _, subs = auto_subs(XI, BENCH, mins(zero={6, 13}), POS)
    assert subs == [(6, 14)]


def test_formation_must_stay_valid():
    # 3-5-2: if a defender misses out, only the bench defender can come on (3 DEF minimum).
    pos = dict(POS)
    pos[5] = R.MID  # starting XI now 1 GK, 3 DEF, 5 MID, 2 FWD
    final, subs = auto_subs(XI, [12, 14, 15, 13], mins(zero={2}), pos)
    assert subs == [(2, 13)]
    # With no playing bench defender there is no sub.
    final, subs = auto_subs(XI, [12, 14, 15, 13], mins(zero={2, 13}), pos)
    assert subs == [] and final == XI


def test_one_forward_minimum():
    pos = dict(POS)
    pos[11] = R.MID  # 1 FWD only
    _, subs = auto_subs(XI, BENCH, mins(zero={10}), pos)
    assert subs == [(10, 15)]  # DEF 13 and MID 14 would leave no forward


def test_double_gameweek_and_blank_counted_by_minutes():
    m = mins(zero={11})  # e.g. his team has no match this week
    _, subs = auto_subs(XI, BENCH, m, POS)
    assert subs == [(11, 13)]


def test_captain_doubled():
    pts = {p: 2 for p in POS} | {10: 10}
    s = score_gameweek(XI, BENCH, 10, 9, pts, mins(), POS)
    assert s.points == 2 * 10 + 10 * 2 and s.multiplier_on == 10


def test_vice_captain_takes_over_when_captain_does_not_play():
    pts = {p: 2 for p in POS} | {10: 0, 9: 6}
    s = score_gameweek(XI, BENCH, 10, 9, pts, mins(zero={10}), POS)
    assert s.multiplier_on == 9
    # XI of 10 others at 2 except vice 6, plus sub 13 (2), plus vice doubled.
    assert s.points == 2 * 9 + 6 + 2 + 6


def test_no_multiplier_when_captain_and_vice_both_miss():
    pts = {p: 2 for p in POS}
    s = score_gameweek(XI, BENCH, 10, 9, pts, mins(zero={9, 10}), POS)
    assert s.multiplier_on is None
    assert s.points == 2 * 11  # two subs came on


def test_triple_captain_and_bench_boost():
    pts = {p: 2 for p in POS} | {10: 10}
    s = score_gameweek(XI, BENCH, 10, 9, pts, mins(), POS, chip="3xc")
    assert s.points == 2 * 10 + 30
    s = score_gameweek(XI, BENCH, 10, 9, pts, mins(zero={7}), POS, chip="bboost")
    # All 15 count (no auto-subs needed), captain doubled.
    assert s.points == 2 * 14 + 20 and s.subs == []


# ------------------------------------------------------------------ vaastav CSVs -> World


def _csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        ",".join(header) + "\n" + "".join(",".join(map(str, r)) + "\n" for r in rows)
    )


def test_load_season_prices_doubles_and_no_peeking(tmp_path):
    from fpl_advisor.sim import load_season

    d = tmp_path / "s"
    _csv(
        d / "teams.csv",
        ["id", "name", "short_name"],
        [[1, "A", "AAA"], [2, "B", "BBB"]],
    )
    fx = ["id", "event", "team_h", "team_a", "kickoff_time", "finished"]
    fx += ["team_h_score", "team_a_score"]
    _csv(
        d / "fixtures.csv",
        fx,
        [
            [1, 1, 1, 2, "2025-08-15T19:00:00Z", True, 2, 0],
            [2, 2, 2, 1, "2025-08-22T19:00:00Z", True, 1, 1],
            [3, 2, 1, 2, "2025-08-24T14:00:00Z", True, 0, 0],
        ],
    )
    raw = ["id", "code", "web_name", "first_name", "second_name", "element_type"]
    _csv(d / "players_raw.csv", raw, [[7, 77, "Striker", "S", "T", 4]])
    gw = ["element", "name", "position", "round", "fixture", "was_home", "minutes"]
    gw += ["total_points", "value", "expected_goals"]
    _csv(
        d / "gws" / "merged_gw.csv",
        gw,
        [
            [7, "S T", "FWD", 1, 1, True, 90, 8, 75, 0.9],
            [7, "S T", "FWD", 2, 2, False, 0, 0, 76, 0.0],  # double gameweek
            [7, "S T", "FWD", 2, 3, True, 30, 2, 76, 0.2],
        ],
    )
    s = load_season("test", d)
    assert s.points[7] == {1: 8, 2: 2} and s.minutes[7] == {1: 90, 2: 30}
    w1, w2 = s.world_at(1), s.world_at(2)
    assert w1.players[7].price == 7.5 and w2.players[7].price == 7.6
    assert w1.history[7] == [] and len(w2.history[7]) == 1
    assert not any(f.finished for f in w1.fixtures)
    assert w2.next_event.id == 2

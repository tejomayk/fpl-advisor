import itertools

import pytest

from fpl_advisor import rules as R
from fpl_advisor.squad import (
    chip_deadline,
    chips_available,
    count_free_transfers,
    selling_price,
)


@pytest.mark.parametrize(
    "purchase,now,expected",
    [
        (5.0, 5.0, 5.0),
        (5.0, 5.1, 5.0),  # a 0.1 rise: half rounds down, keep purchase price
        (5.0, 5.2, 5.1),
        (5.0, 5.3, 5.1),
        (5.0, 5.4, 5.2),
        (5.0, 5.5, 5.2),
        (5.0, 6.0, 5.5),
        (5.0, 4.9, 4.9),  # falls are passed on in full
        (5.0, 4.0, 4.0),
        (4.5, 4.8, 4.6),  # float edge: (4.8 - 4.5) * 10 is 2.9999...
        (4.5, 4.7, 4.6),
        (5.7, 6.0, 5.8),
        (7.1, 7.4, 7.2),
        (4.0, 4.1, 4.0),
    ],
)
def test_selling_price(purchase, now, expected):
    assert selling_price(purchase, now) == pytest.approx(expected)


def test_selling_price_never_below_purchase_on_rise_or_above_now():
    for a, b in itertools.product(range(40, 140), range(40, 140)):
        purchase, now = a / 10, b / 10
        sp = selling_price(purchase, now)
        if now > purchase:
            assert purchase <= sp <= now
        else:
            assert sp == now
        assert sp == round(sp, 1)


def rows(*used, start=1):
    return [{"event": start + i, "event_transfers": u} for i, u in enumerate(used)]


def test_ft_no_rows_defaults_to_one():
    assert count_free_transfers([], []) == 1


def test_ft_first_gameweek_only():
    assert count_free_transfers(rows(15), []) == 1


def test_ft_unused_roll_up():
    assert count_free_transfers(rows(15, 0), []) == 2
    assert count_free_transfers(rows(15, 0, 0, 0), []) == 4


def test_ft_capped_at_five():
    assert count_free_transfers(rows(15, *[0] * 10), []) == R.MAX_FREE_TRANSFERS == 5


def test_ft_using_one_resets_to_one():
    assert count_free_transfers(rows(15, 0, 0, 3), []) == 1  # had 3, used all 3


def test_ft_partial_use():
    assert count_free_transfers(rows(15, 0, 0, 0, 2), []) == 3  # had 4, used 2 -> 2, +1


def test_ft_hits_reset_to_one():
    assert count_free_transfers(rows(15, 0, 0, 5), []) == 1
    assert count_free_transfers(rows(15, 3), []) == 1


def test_ft_wildcard_keeps_fts_without_adding_one():
    # GW1 -> 1 FT, GW2 nothing -> 2, GW3 wildcard (many transfers) -> still 2
    assert count_free_transfers(rows(15, 0, 12), [("wildcard", 3)]) == 2


def test_ft_freehit_keeps_fts_without_adding_one():
    # 3 banked going into the Free Hit week -> still 3 afterwards
    assert count_free_transfers(rows(15, 0, 0, 14), [("freehit", 4)]) == 3


def test_ft_chip_week_capped():
    assert count_free_transfers(rows(15, *[0] * 5, 11), [("wildcard", 7)]) == 5


def test_ft_other_chip_does_not_bypass_transfer_use():
    assert count_free_transfers(rows(15, 0, 2), [("bboost", 3)]) == 1


def test_ft_started_mid_season():
    # Manager's first row is GW10; that week is the free "unlimited" one.
    assert count_free_transfers(rows(15, start=10), []) == 1
    assert count_free_transfers(rows(15, 0, 0, start=10), []) == 3
    assert count_free_transfers(rows(15, 0, 2, start=10), []) == 1


WINDOWS = [
    {"name": "wildcard", "start_event": 1, "stop_event": 19},
    {"name": "wildcard", "start_event": 20, "stop_event": 38},
    {"name": "freehit", "start_event": 1, "stop_event": 38},
    {"name": "bboost", "start_event": 1, "stop_event": 19},
    {"name": "bboost", "start_event": 20, "stop_event": 38},
    {"name": "3xc", "start_event": 1, "stop_event": 19},
    {"name": "3xc", "start_event": 20, "stop_event": 38},
]


def test_chips_all_available_first_half():
    assert sorted(chips_available(WINDOWS, [], 5)) == [
        "3xc",
        "bboost",
        "freehit",
        "wildcard",
    ]


def test_chips_used_first_half_not_available_but_returns_in_second():
    used = [("wildcard", 5), ("bboost", 10)]
    assert sorted(chips_available(WINDOWS, used, 12)) == ["3xc", "freehit"]
    assert sorted(chips_available(WINDOWS, used, 20)) == [
        "3xc",
        "bboost",
        "freehit",
        "wildcard",
    ]


def test_chips_used_second_half_only_blocks_second_window():
    used = [("3xc", 25)]
    assert "3xc" not in chips_available(WINDOWS, used, 30)
    assert "3xc" in chips_available(WINDOWS, used, 19)


def test_chips_boundaries():
    assert "wildcard" in chips_available(WINDOWS, [], 19)
    assert "wildcard" in chips_available(WINDOWS, [], 20)
    assert chips_available(WINDOWS, [], 39) == []


def test_chip_deadline():
    assert chip_deadline(WINDOWS, "wildcard", 5) == 19
    assert chip_deadline(WINDOWS, "wildcard", 19) == 19
    assert chip_deadline(WINDOWS, "wildcard", 20) == 38
    assert chip_deadline(WINDOWS, "freehit", 3) == 38
    assert chip_deadline(WINDOWS, "nonexistent", 3) is None
    assert chip_deadline(WINDOWS, "wildcard", 40) is None

"""The start/minutes model: who gets picked, separate from who is fit."""

import pytest
from conftest import history_row

from fpl_advisor import rules as R
from fpl_advisor.model import Rates, absent_rows, start_model, start_rate_at
from fpl_advisor.world import Player


def player(pos=R.DEF, status="a", chance=None):
    return Player(1, "x", "x x", 1, pos, 5.0, status=status, chance_next=chance)


def rows(minutes):
    return [history_row(i + 1, i + 1, True, minutes=m) for i, m in enumerate(minutes)]


def test_regular_who_was_benched_once_is_still_a_likely_starter():
    # 8 starts in 9 with no usable last season: the old model said ~0.75.
    start, long, _ = start_model(player(), rows([90, 90, 20] + [90] * 6), None)
    assert start > 0.85
    assert long > 0.75


def test_recent_benching_counts_more_than_old_ones():
    early = start_model(player(), rows([0, 0, 90, 90, 90, 90, 90, 90]), None)[0]
    late = start_model(player(), rows([90, 90, 90, 90, 90, 90, 0, 0]), None)[0]
    assert late < early


def test_flagged_players_current_absence_is_not_counted_as_dropped():
    history = rows([90] * 6 + [0, 0])
    fit = start_model(player(status="a"), history, None)[0]
    injured = start_model(player(status="i", chance=0), history, None)[0]
    # Unflagged, two blanks may mean he lost his place; flagged, availability covers it.
    assert fit < 0.6
    assert injured > 0.8
    assert absent_rows(player(status="i", chance=0), history) == {6, 7}
    assert absent_rows(player(status="a"), history) == set()


def test_only_the_current_run_is_treated_as_absence():
    history = rows([90, 0, 0, 90, 90, 0])
    assert absent_rows(player(status="d", chance=50), history) == {5}


def test_first_choice_goalkeeper_and_backup():
    first = start_model(player(R.GKP), rows([90] * 9), None)
    backup = start_model(player(R.GKP), rows([0] * 9), None)
    assert first[0] > 0.9
    assert backup[0] < 0.15
    assert backup[2] < 0.05  # a backup keeper almost never comes off the bench


def test_injured_regular_last_season_keeps_a_high_prior():
    # 22 starts but few sub minutes: injuries, not rotation. starts/38 alone would say 0.58.
    past = {"minutes": 1900, "starts": 22}
    rotation = {"minutes": 1200, "starts": 10}
    regular = start_model(player(), [], past)[0]
    rotated = start_model(player(), [], rotation)[0]
    assert regular > 0.65
    assert rotated < 0.4


def test_later_gameweeks_revert_toward_the_long_run_rate():
    r = Rates(0.4, 85, 0.8, 0.3, 0.1, 0.1, 0, 0.2, 0.1, 0, start_rate_long=0.8)
    assert start_rate_at(r, 0) == pytest.approx(0.4)
    assert 0.4 < start_rate_at(r, 1) < start_rate_at(r, 3) < 0.8
    no_long = Rates(0.4, 85, 0.8, 0.3, 0.1, 0.1, 0, 0.2, 0.1, 0)
    assert start_rate_at(no_long, 3) == pytest.approx(0.4)

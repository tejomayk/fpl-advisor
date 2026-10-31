"""Offline tests for the mini-league helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from fpl_advisor import league as L
from fpl_advisor.world import Event


def _rows(n: int) -> list[dict]:
    return [{"entry": 1000 + i, "rank": i + 1} for i in range(n)]


class TestRiskLambda:
    @pytest.mark.parametrize("name,val", list(L.RISK_PRESETS.items()))
    def test_presets(self, name, val):
        lam, why = L.risk_lambda(100, [200], 10, name)
        assert lam == val
        assert name in why

    def test_preset_ignores_standings(self):
        assert L.risk_lambda(0, [], 0, "high")[0] == 0.2

    def test_small_gap_small_lambda(self):
        lam, _ = L.risk_lambda(1000, [1005], 10)
        assert 0.02 < lam < 0.08

    def test_lambda_grows_with_gap_and_is_capped(self):
        small = L.risk_lambda(1000, [1010], 10)[0]
        big = L.risk_lambda(1000, [1060], 10)[0]
        huge = L.risk_lambda(1000, [5000], 10)[0]
        assert 0 < small < big < 0.2
        assert huge == 0.2

    def test_one_typical_swing_is_about_014(self):
        spread = L.GW_DIFF_SD * 4  # remaining = 16
        lam, _ = L.risk_lambda(1000, [1000 + int(spread)], 16)
        assert lam == pytest.approx(0.14, abs=0.005)

    def test_leading_goes_negative_and_floors(self):
        lam, why = L.risk_lambda(1100, [1000], 10)
        assert lam < 0
        assert "lead" in why
        assert L.risk_lambda(9000, [1000], 10)[0] == -0.12

    def test_tiny_lead_is_near_neutral(self):
        lam, why = L.risk_lambda(1001, [1000], 10)
        assert 0 <= lam <= 0.02
        assert "neutral" in why

    def test_uses_best_other(self):
        assert L.risk_lambda(1000, [900, 1010], 10) == L.risk_lambda(1000, [1010], 10)

    def test_no_rivals(self):
        assert L.risk_lambda(1000, [], 10)[0] == 0.0

    def test_nothing_remaining(self):
        assert L.risk_lambda(1000, [1200], 0)[0] == 0.0


class TestOwnershipMultiplier:
    def test_unowned_boosted_owned_discounted(self):
        m = L.ownership_multiplier([1, 2, 3], {2: 1.0, 3: 0.5}, 0.1)
        assert m[1] > 1
        assert m[2] < 1
        assert m[3] == pytest.approx(1.0)
        assert m[1] == pytest.approx(1.1)
        assert m[2] == pytest.approx(0.9)

    def test_negative_lambda_flips(self):
        m = L.ownership_multiplier([1, 2], {2: 1.0}, -0.1)
        assert m[1] < 1 < m[2]

    def test_zero_lambda_empty(self):
        assert L.ownership_multiplier([1, 2], {1: 0.3}, 0) == {}


class TestSelectRivals:
    def test_small_league_kept_whole(self):
        rows = _rows(30)
        assert L._select_rivals(rows, 1010) == rows

    def test_large_league_top10_and_around_me(self):
        rows = _rows(100)
        me = 1050  # rank 51, index 50
        out = L._select_rivals(rows, me)
        entries = [r["entry"] for r in out]
        assert len(entries) == len(set(entries))
        assert set(range(1000, 1010)) <= set(entries)
        assert set(range(1040, 1055)) <= set(entries)
        assert me in entries
        assert len(out) == 25

    def test_me_in_top_has_no_duplicates(self):
        out = L._select_rivals(_rows(100), 1003)
        entries = [r["entry"] for r in out]
        assert len(entries) == len(set(entries))
        assert 1003 in entries

    def test_unsorted_input_handled(self):
        rows = list(reversed(_rows(100)))
        out = L._select_rivals(rows, 1050)
        assert 1050 in {r["entry"] for r in out}
        assert 1000 in {r["entry"] for r in out}


class TestPreviousSeasonName:
    @staticmethod
    def _world(*deadlines):
        return SimpleNamespace(
            events=[
                Event(i + 1, d, False, False, False) for i, d in enumerate(deadlines)
            ]
        )

    def test_august_deadline(self):
        w = self._world(
            datetime(2025, 8, 15, tzinfo=UTC), datetime(2025, 8, 22, tzinfo=UTC)
        )
        assert L._previous_season_name(w) == "2024/25"

    def test_january_deadline(self):
        w = self._world(datetime(2026, 1, 10, tzinfo=UTC))
        assert L._previous_season_name(w) == "2024/25"

    def test_uses_earliest_deadline(self):
        w = self._world(
            datetime(2026, 1, 10, tzinfo=UTC), datetime(2025, 8, 15, tzinfo=UTC)
        )
        assert L._previous_season_name(w) == "2024/25"

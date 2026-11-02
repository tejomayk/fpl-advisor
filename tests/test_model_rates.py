"""Attacking rates: shrinkage toward players of the same position and price."""

from dataclasses import replace

from conftest import history_row, make_world

from fpl_advisor import model as M
from fpl_advisor import rules as R


def test_price_bands():
    assert M.price_band(4.5) == 0
    assert M.price_band(5.0) == 1
    assert M.price_band(12.0) == len(M.PRICE_BANDS)


def _rich_world():
    """Pricey midfielders who produce lots of xG, cheap ones who don't, enough minutes per band."""
    w = make_world()
    mids = [p for p in w.players.values() if p.pos == R.MID]
    for i, p in enumerate(mids):
        rich = i % 2 == 0
        p.price = 11.0 if rich else 4.5
        w.past[p.id] = {
            "minutes": 3000,
            "starts": 34,
            "expected_goals": "15.0" if rich else "1.0",
            "expected_assists": "8.0" if rich else "1.0",
            "saves": 0,
            "bonus": 20 if rich else 2,
            "yellow_cards": 3,
            "defensive_contribution": 0,
        }
    return w, mids


def test_new_signing_gets_a_price_based_prior():
    w, mids = _rich_world()
    priors = M._position_priors(w)
    new = mids[0]
    rows = [history_row(1, 1, True, minutes=90, expected_goals="0.0")]
    budget = replace(new, price=4.5)
    cheap = M.player_rates(budget, rows, None, M.population_prior(priors, budget))
    pricey = M.player_rates(new, rows, None, M.population_prior(priors, new))
    assert pricey.xg90 > 2 * cheap.xg90


def test_one_hot_season_is_regressed_toward_the_population():
    w, mids = _rich_world()
    priors = M._position_priors(w)
    p = mids[1]  # a cheap midfielder
    hot = {**w.past[p.id], "minutes": 900, "expected_goals": "9.0"}  # 0.9 xG/90
    r = M.player_rates(p, [], hot, M.population_prior(priors, p))
    assert r.xg90 < 0.6
    assert r.xg90 > M.population_prior(priors, p)["expected_goals"]

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


def _preseason_world():
    """Before GW1: club 1's players had big xG last season, club 2's have no PL past.

    The test world has ~3 players a club, so each carries a whole squad's minutes."""
    w = make_world()
    w.history = {}
    for f in w.fixtures:
        f.finished, f.home_goals, f.away_goals = False, None, None
    for p in w.players.values():
        if p.team == 2:
            continue
        strong = p.team == 1
        w.past[p.id] = {
            "minutes": 12000,
            "starts": 28,
            "expected_goals": "60.0" if strong else "15.0",
            "expected_assists": "8.0",
            "expected_goals_conceded": "100.0" if strong else "200.0",
            "saves": 0,
            "bonus": 5,
            "yellow_cards": 3,
            "defensive_contribution": 0,
        }
    return w


def test_preseason_ratings_come_from_last_season_and_promoted_sides_start_low():
    w = _preseason_world()
    pri = M.team_priors(w)
    assert pri[1].attack > 1.2 and pri[1].defence < 0.8  # strong attack, tight defence
    assert pri[2].attack == M.PROMOTED_ATTACK
    assert pri[2].defence == M.PROMOTED_DEFENCE
    ratings, _ = M.team_ratings(w)
    assert ratings[1].attack > ratings[3].attack > ratings[2].attack
    assert ratings[2].defence > ratings[3].defence > ratings[1].defence


def test_only_the_previous_season_counts_as_last_season():
    w = make_world()  # first deadline in August 2025, so last season is 2024/25
    p = next(iter(w.players.values()))
    w.past[p.id] = {"season_name": "2024/25", "minutes": 2000}
    assert M._usable_past(w, p.id) is w.past[p.id]
    w.past[p.id] = {"season_name": "2019/20", "minutes": 3000}
    assert M._usable_past(w, p.id) is None
    w.past[p.id] = {"minutes": 2000}  # no season name (season replay data): trusted
    assert M._usable_past(w, p.id) is w.past[p.id]


def test_preseason_prior_fades_as_results_come_in():
    pre = _preseason_world()
    later = make_world()  # three gameweeks played
    later.past = pre.past
    r0, avg0 = M.team_ratings(pre)
    r3, avg3 = M.team_ratings(later)
    assert abs(r3[2].attack - avg3) < abs(r0[2].attack - avg0)

"""Tests for the mini-league win-probability simulator, on tiny synthetic leagues."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from fpl_advisor import rules as R
from fpl_advisor import winprob as W
from fpl_advisor.model import PointsDist

GWS = [10, 11, 12]
LAYOUT = [R.GKP] * 2 + [R.DEF] * 5 + [R.MID] * 5 + [R.FWD] * 3


def make_world(n_squads: int = 4):
    """Squads of 15 numbered 1..15, 101..115, ...; squad k plays for team k (teams 1v2, 3v4)."""
    players = {}
    for k in range(n_squads):
        for i, pos in enumerate(LAYOUT):
            players[100 * k + i + 1] = SimpleNamespace(pos=pos, team=k + 1)
    fx = [SimpleNamespace(home=2 * i + 1, away=2 * i + 2) for i in range(n_squads // 2)]
    return SimpleNamespace(players=players, fixtures_in=lambda gw: fx)


def squad(k: int) -> list[int]:
    return [100 * k + i + 1 for i in range(15)]


def dists_for(world, mean=4.0, sd=3.0, mean_sd=0.5, p_play=0.9, overrides=None):
    out = {
        p: {g: PointsDist(mean, sd, mean_sd, p_play) for g in GWS}
        for p in world.players
    }
    for p, d in (overrides or {}).items():
        out[p] = {g: d for g in GWS}
    return out


def pos_of(world):
    return {p: v.pos for p, v in world.players.items()}


def entrant(key, total, sq, world, dists, gws=GWS, **kw):
    return W.Entrant(
        key, f"Team {key}", total, W.best_picks(sq, dists, gws, pos_of(world)), **kw
    )


def totals(ents, dists, world, gws_left=3, n=4000, seed=1):
    gws = sorted({g for e in ents for g in e.picks})
    t, _, _ = W._run(ents, dists, W.structure_from_world(world, gws), gws_left, n, seed)
    return t


class TestKnownAnswers:
    def test_identical_squads_share_first_equally(self):
        world = make_world()
        d = dists_for(world)
        ents = [entrant(k, 300, squad(0), world, d) for k in range(4)]
        odds = W.win_odds(ents, d, world, len(GWS), n_sims=2000)
        assert all(p == pytest.approx(0.25) for p in odds.p_first.values())
        assert all(r == pytest.approx(2.5) for r in odds.expected_rank.values())

    def test_identical_squads_with_later_weeks_are_roughly_equal(self):
        world = make_world()
        d = dists_for(world)
        ents = [entrant(k, 300, squad(0), world, d, gws_played=9) for k in range(4)]
        odds = W.win_odds(ents, d, world, 20, n_sims=8000)
        for k, p in odds.p_first.items():
            assert p == pytest.approx(0.25, abs=4 * odds.p_first_se[k] + 0.005)

    def test_big_lead_with_few_weeks_left_is_nearly_certain(self):
        world = make_world()
        d = dists_for(world)
        ents = [entrant(0, 900, squad(0), world, d, gws_played=35)] + [
            entrant(k, 700, squad(k), world, d, gws_played=35) for k in (1, 2, 3)
        ]
        odds = W.win_odds(ents, d, world, len(GWS), n_sims=4000)
        assert odds.p_first[0] > 0.999
        assert odds.expected_rank[0] == pytest.approx(1.0, abs=0.01)
        assert odds.gap_to_winner[0][90] == 0

    def test_small_lead_with_many_weeks_left_is_open(self):
        world = make_world()
        d = dists_for(world)
        ents = [entrant(0, 305, squad(0), world, d, gws_played=8)] + [
            entrant(k, 300, squad(k), world, d, gws_played=8) for k in (1, 2, 3)
        ]
        odds = W.win_odds(ents, d, world, 30, n_sims=4000)
        assert 0.25 < odds.p_first[0] < 0.5
        assert sum(odds.p_first.values()) == pytest.approx(1.0)

    def test_better_form_wins_more_often_in_later_weeks(self):
        world = make_world()
        d = dists_for(world)
        ents = [
            W.Entrant(0, "A", 600, {}, gws_played=10),
            W.Entrant(1, "B", 500, {}, gws_played=10),
        ]
        # Same points from here, but A's 10 points a week more so far is a (shrunk) edge.
        a = W.win_odds(ents, d, world, 20, n_sims=4000)
        ents[0].total, ents[1].total = 550, 550
        b = W.win_odds(ents, d, world, 20, n_sims=4000)
        assert a.p_first[0] > 0.95
        assert 0.5 < b.p_first[0] < 0.95  # level now, but A's form still counts


class TestSharedPlayers:
    def test_identical_teams_never_drift_apart(self):
        world = make_world()
        d = dists_for(world)
        ents = [entrant(k, 300, squad(0), world, d) for k in range(2)]
        t = totals(ents, d, world)
        assert np.all(t[:, 0] == t[:, 1])
        assert t[:, 0].std() > 5  # yet each total does vary

    def test_only_the_differential_creates_a_gap(self):
        world = make_world()
        d = dists_for(world, mean_sd=0.0, p_play=1.0)
        base = W.best_picks(squad(0), d, GWS, pos_of(world))
        swap_out = base[GWS[0]].xi[-1]  # a forward, not the captain
        other = 315  # forward from squad 3, a different team
        diff = {
            g: W.GWPick(
                [other if p == swap_out else p for p in pk.xi],
                [],
                pk.captain,
                pk.vice,
            )
            for g, pk in base.items()
        }
        same = {
            g: W.GWPick(list(pk.xi), [], pk.captain, pk.vice) for g, pk in base.items()
        }
        ents = [W.Entrant(0, "A", 0, same), W.Entrant(1, "B", 0, diff)]
        t = totals(ents, d, world, n=6000)
        gap_sd = (t[:, 0] - t[:, 1]).std()
        # Two players' sums over 3 weeks: sd about sqrt(2 * 3) * 3 = 7.3, far below a whole XI.
        assert gap_sd < 9
        assert t[:, 0].std() > 2 * gap_sd
        ents_disjoint = [
            W.Entrant(0, "A", 0, same),
            W.Entrant(1, "B", 0, W.best_picks(squad(2), d, GWS, pos_of(world))),
        ]
        t2 = totals(ents_disjoint, d, world, n=6000)
        assert (t2[:, 0] - t2[:, 1]).std() > 2 * gap_sd

    def test_team_factor_links_a_teams_attackers(self):
        world = make_world()
        d = dists_for(world, mean_sd=0.0, p_play=1.0)
        gws = [GWS[0]]
        att = [13, 14, 15]  # squad 0's forwards, all team 1
        rival_gk = 101  # team 2 keeper, faces team 1
        ents = [
            W.Entrant(i, str(i), 0, {gws[0]: W.GWPick([p])})
            for i, p in enumerate([*att, rival_gk])
        ]
        t = totals(ents, d, world, gws_left=1, n=20000)
        c = np.corrcoef(t.T)
        assert c[0, 1] > 0.1  # teammates rise together
        assert c[0, 3] < -0.1  # their goals sink the opposing keeper


class TestScoring:
    def _one(self, pick, d, world, n=20000):
        e = [W.Entrant(0, "A", 0, {GWS[0]: pick})]
        return totals(e, d, world, gws_left=1, n=n)[:, 0]

    def test_means_match_the_forecast(self):
        world = make_world()
        d = dists_for(world, mean=4.0, sd=3.0, mean_sd=0.0, p_play=1.0)
        xi = squad(0)[:1] + squad(0)[2:12]
        t = self._one(W.GWPick(xi, [], xi[5], xi[6]), d, world)
        assert t.mean() == pytest.approx(12 * 4.0, rel=0.03)

    def test_bench_player_comes_on_for_a_non_starter(self):
        world = make_world()
        absent = PointsDist(0.0, 0.0, 0.0, 0.0)
        d = dists_for(world, mean_sd=0.0, p_play=1.0, overrides={3: absent})
        xi = [1, *range(3, 13)]
        t = self._one(W.GWPick(xi, [13, 2], 5, 6), d, world)
        assert t.mean() == pytest.approx(12 * 4.0, rel=0.03)  # 13 replaced 3
        t = self._one(W.GWPick(xi, [2], 5, 6), d, world)  # only a keeper on the bench
        assert t.mean() == pytest.approx(11 * 4.0, rel=0.03)

    def test_vice_takes_over_when_captain_misses_out(self):
        world = make_world()
        absent = PointsDist(0.0, 0.0, 0.0, 0.0)
        star = PointsDist(10.0, 3.0, 0.0, 1.0)
        d = dists_for(world, mean_sd=0.0, p_play=1.0, overrides={5: absent, 6: star})
        xi = [1, *range(3, 13)]
        t = self._one(W.GWPick(xi, [], 5, 6), d, world)
        assert t.mean() == pytest.approx(9 * 4.0 + 2 * 10.0, rel=0.03)

    def test_triple_captain_bench_boost_and_hits(self):
        world = make_world()
        d = dists_for(world, mean_sd=0.0, p_play=1.0)
        xi = [1, *range(3, 13)]
        bench = [2, 13, 14, 15]
        tc = self._one(W.GWPick(xi, bench, 5, 6, chip="3xc"), d, world)
        bb = self._one(W.GWPick(xi, bench, 5, 6, chip="bboost", hits=4), d, world)
        assert tc.mean() == pytest.approx(13 * 4.0, rel=0.03)
        assert bb.mean() == pytest.approx(16 * 4.0 - 4, rel=0.03)

    def test_forecast_error_widens_the_spread(self):
        world = make_world()
        xi = [1, *range(3, 13)]
        pick = W.GWPick(xi, [], 5, 6)
        sure = self._one(pick, dists_for(world, mean_sd=0.0), world)
        unsure = self._one(pick, dists_for(world, mean_sd=3.0), world)
        assert unsure.std() > sure.std() * 1.1


class TestRiskTaking:
    """A differential with the same mean but more variance helps when chasing, hurts when leading."""

    def setup(self, my_total):
        world = make_world()
        safe = PointsDist(5.0, 1.0, 0.0, 1.0)
        wild = PointsDist(5.0, 8.0, 0.0, 1.0)
        d = dists_for(
            world, mean_sd=0.0, p_play=1.0, overrides={13: safe, 14: wild, 315: safe}
        )
        gw = GWS[0]
        core = [1, *range(3, 12)]  # 10 shared players, captain shared too
        leader = W.Entrant(1, "Leader", 100, {gw: W.GWPick(core + [315], [], 5, 6)})
        me = W.Entrant(0, "Me", my_total, {gw: W.GWPick(core + [13], [], 5, 6)})
        cands = {
            "safe": {gw: W.GWPick(core + [13], [], 5, 6)},
            "wild": {gw: W.GWPick(core + [14], [], 5, 6)},
        }
        return W.choose_plan([me, leader], 0, cands, d, world, 1, n_sims=20000)

    def test_far_behind_takes_variance(self):
        ch = self.setup(my_total=88)  # 12 behind with one week left
        assert ch.p_first["safe"] < 0.001
        assert ch.best == "wild"
        diff, se = ch.vs_baseline["wild"]
        assert diff > 4 * se > 0

    def test_ahead_avoids_variance(self):
        ch = self.setup(my_total=103)
        assert ch.best == "safe"
        diff, se = ch.vs_baseline["wild"]
        assert diff < -4 * se < 0


class TestChoosePlan:
    def test_identical_candidates_have_zero_difference(self):
        world = make_world()
        d = dists_for(world)
        ents = [entrant(k, 300, squad(k), world, d, gws_played=9) for k in range(4)]
        same = ents[0].picks
        ch = W.choose_plan(ents, 0, {"a": same, "b": dict(same)}, d, world, 10, 2000)
        assert ch.vs_baseline["b"] == (0.0, 0.0)
        assert ch.margin == 0.0
        # The baseline candidate's P(1st) is the league view's.
        assert ch.p_first["a"] == pytest.approx(ch.odds.p_first[0])

    def test_captain_variants(self):
        world = make_world()
        star = PointsDist(9.0, 4.0, 0.3, 0.95)
        d = dists_for(world, overrides={7: star})
        ents = [entrant(k, 300, squad(k), world, d, gws_played=9) for k in range(4)]
        picks = ents[0].picks
        assert picks[GWS[0]].captain == 7
        cands = {
            str(p): W.with_captain(picks, GWS[0], p) for p in [7, *picks[GWS[0]].xi[:3]]
        }
        ch = W.choose_plan(ents, 0, cands, d, world, 10, 6000)
        assert ch.best == "7"
        assert ch.margin >= 0
        with pytest.raises(ValueError):
            W.with_captain(picks, GWS[0], 999)

    def test_mismatched_weeks_are_rejected(self):
        world = make_world()
        d = dists_for(world)
        a = entrant(0, 0, squad(0), world, d)
        b = entrant(1, 0, squad(1), world, d, gws=GWS[:1])
        with pytest.raises(ValueError):
            W.win_odds([a, b], d, world, 3)
        with pytest.raises(ValueError):
            W.win_odds([a], d, world, 2)  # more detailed weeks than are left


class TestBestPicks:
    def test_valid_xi_and_sticky_captain(self):
        world = make_world()
        pos = pos_of(world)
        d = dists_for(
            world,
            overrides={
                7: PointsDist(8.0, 3, 0, 1),
                8: PointsDist(7.5, 3, 0, 1),
                9: PointsDist(5.0, 3, 0, 1),
            },
        )
        picks = W.best_picks(squad(0), d, GWS, pos, last_captain=8)
        pk = picks[GWS[0]]
        assert len(pk.xi) == 11 and len(pk.bench) == 4
        counts = {k: sum(pos[p] == k for p in pk.xi) for k in R.POS_NAME}
        assert counts[R.GKP] == 1
        assert all(R.XI_MIN[k] <= counts[k] <= R.XI_MAX[k] for k in R.POS_NAME)
        assert pk.captain == 8 and pk.vice == 7  # 7.5 is within 85% of 8
        assert W.best_picks(squad(0), d, GWS, pos, last_captain=9)[GWS[0]].captain == 7


class TestDescribe:
    def odds(self, p):
        keys = list(range(len(p)))
        return W.LeagueOdds(
            keys=keys,
            labels={k: f"Rival {k}" for k in keys},
            p_first=dict(zip(keys, p, strict=True)),
            p_first_se={k: 0.0 for k in keys},
            expected_rank={0: 2.6, 1: 1.8, 2: 3.0},
            gap_to_winner={},
            n_sims=1000,
            detailed_gws=[],
            later_gws=0,
        )

    def test_threat(self):
        s = W.describe(self.odds([0.23, 0.31, 0.46]), 0)
        assert s.startswith("You win this league in about 23% of simulated seasons")
        assert "around 3rd" in s
        assert "Rival 2 is the biggest threat at 46%." in s

    def test_favourite_and_extremes(self):
        s = W.describe(self.odds([0.995, 0.004, 0.001]), 0)
        assert "over 99%" in s and "You are the favourite" in s
        assert "Rival 1 is the closest challenger at under 1%." in s

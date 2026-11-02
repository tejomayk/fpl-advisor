"""Time the league simulator on a synthetic 15-manager league (no network, no data needed).

Usage: uv run python scripts/winprob_speed.py
"""

from __future__ import annotations

import random
import time
from types import SimpleNamespace

from fpl_advisor import rules as R
from fpl_advisor import winprob as W
from fpl_advisor.model import PointsDist


def synthetic(n_managers: int = 15, first_gw: int = 9, seed: int = 1):
    rnd = random.Random(seed)
    players, pid = {}, 0
    for t in range(1, 21):
        for pos, n in ((R.GKP, 3), (R.DEF, 8), (R.MID, 8), (R.FWD, 5)):
            for _ in range(n):
                pid += 1
                players[pid] = SimpleNamespace(pos=pos, team=t)
    gws = list(range(first_gw, 39))
    fx = {
        g: [SimpleNamespace(home=2 * i + 1, away=2 * i + 2) for i in range(10)]
        for g in gws
    }
    world = SimpleNamespace(players=players, fixtures_in=lambda g: fx[g])
    dists = {
        p: {g: PointsDist(rnd.uniform(1, 7), 3.0, 0.6, 0.85) for g in gws}
        for p in players
    }
    pos = {p: v.pos for p, v in players.items()}

    def squad():
        out = []
        for k, n in R.SQUAD_COUNT.items():
            out += rnd.sample([p for p in players if pos[p] == k], n)
        return out

    ents = [
        W.Entrant(
            i,
            f"Team {i + 1}",
            rnd.randint(400, 500),
            W.best_picks(squad(), dists, gws, pos),
            gws_played=first_gw - 1,
        )
        for i in range(n_managers)
    ]
    return world, dists, gws, ents


def main():
    world, dists, gws, ents = synthetic()
    left = len(gws)
    for detailed in (left, 6):
        es = [
            W.Entrant(
                e.key, e.label, e.total, {g: e.picks[g] for g in gws[:detailed]}, 8
            )
            for e in ents
        ]
        t = time.time()
        odds = W.win_odds(es, dists, world, left, n_sims=10_000)
        print(
            f"15 managers, {detailed} detailed + {left - detailed} later GWs, 10k sims: "
            f"{time.time() - t:.2f}s"
        )
    print(W.describe(odds, 0))
    me = ents[0]
    gw = gws[0]
    caps = {
        f"captain {p}": W.with_captain(me.picks, gw, p) for p in me.picks[gw].xi[:5]
    }
    t = time.time()
    ch = W.choose_plan(ents, 0, caps, dists, world, left, n_sims=10_000)
    print(f"choose_plan, 5 captains, 30 detailed GWs, 10k sims: {time.time() - t:.2f}s")
    print(ch.best, f"margin {ch.margin:+.4f} +/- {ch.margin_se:.4f}")


if __name__ == "__main__":
    main()

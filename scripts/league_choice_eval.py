"""Does choosing the captain by P(finish 1st) win more mini-leagues than maximising expected points?

Usage:
    uv run python scripts/league_choice_eval.py                         # all seasons
    uv run python scripts/league_choice_eval.py --season 2024-25 --leagues 100 --sims 2000

Synthetic 12-manager leagues from scripts/winprob_calibration.py (a pool of simple simulated
managers plays each past season on real points). In each league one manager is "you". Up to
`--start` you are that manager; from then on your squad still follows theirs (same transfers),
but your XI and captain come from our forecasts, under two policies played side by side:

- expected points: the XI and captain with the most expected points (what advise does without
  a league);
- P(1st): the same XI, but the captain chosen by `leaguechoice.pick` among the top captains on
  P(finish 1st) (winprob.choose_plan, common random numbers, switch only when the gain is
  more than leaguechoice.SWITCH_SE standard errors) - what advise does with a league.

Each week is scored on real points (auto-subs, vice), and at GW38 we check who won. The two
policies face the same rivals and the same real points, so the difference in win rate is paired
per league.

Only the captain is tested: transfer plans need the full planner, too slow for thousands of
league-weeks. Caveats: leagues share managers from one pool, and each season is one set of
real results, so leagues are not independent and the standard error is optimistic.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from winprob_calibration import PREVIOUS, Season, SimManager

from fpl_advisor import leaguechoice as LC
from fpl_advisor import winprob as W
from fpl_advisor.model import project_dist
from fpl_advisor.sim import load_season, score_gameweek

ROOT = Path(__file__).resolve().parent.parent


def _win(final_me: float, rivals: np.ndarray) -> float:
    best = rivals.max()
    if final_me > best:
        return 1.0
    if final_me < best:
        return 0.0
    return 1.0 / (1 + (rivals == best).sum())


def run_season(name, data, season_dir, args, rng, out):
    t0 = time.time()
    season = Season(data, season_dir)
    pool = [
        SimManager(season, np.random.default_rng(rng.integers(1 << 32)))
        for _ in range(args.pool)
    ]
    for g in season.gws:
        for m in pool:
            m.play(g)
    last = max(season.gws)
    leagues = [
        rng.choice(args.pool, size=args.size, replace=False)
        for _ in range(args.leagues)
    ]
    print(f"  {name}: pool built in {time.time() - t0:.0f}s")
    weeks = [g for g in season.gws if g >= args.start]
    # Per league: running totals for the two policies, switch count.
    tot = {li: [0.0, 0.0] for li in range(len(leagues))}
    for li, members in enumerate(leagues):
        me = pool[members[0]]
        before = sum(me.points[g] for g in season.gws if g < args.start)
        tot[li] = [float(before), float(before)]
    switches = 0
    decisions = 0
    for g in weeks:
        t1 = time.time()
        gws = [x for x in range(g, g + LC.DETAIL_GWS) if x <= last]
        world = data.world_at(g)
        dists = project_dist(world, gws)
        pts, mins = data.actual(g)
        for li, members in enumerate(leagues):
            ms = [pool[i] for i in members]
            me = ms[0]
            squad = me.squads[g]
            mine = W.best_picks(
                squad, dists, gws, season.pos_of
            )  # EP captain each week
            pk = mine[g]
            xp = {p: dists[p][g].mean for p in pk.xi}
            ranked = sorted(pk.xi, key=lambda p: -xp[p])
            cands = {"plan": mine}
            for pid in [p for p in ranked if p != pk.captain][: LC.CAPTAIN_OPTIONS]:
                cands[f"captain:{pid}"] = W.with_captain(mine, g, pid)
            ents = []
            for j, m in enumerate(ms):
                past = [m.points[x] for x in season.gws if x < g]
                total = sum(past) if j else tot[li][1]
                prev = max(x for x in season.gws if x < g) if g > season.gws[0] else g
                ents.append(
                    W.Entrant(
                        j,
                        f"Manager {j + 1}",
                        float(total),
                        W.best_picks(
                            m.squads[prev], dists, gws, season.pos_of, m.captains[prev]
                        )
                        if j
                        else mine,
                        gws_played=len(past),
                        past_scores=past,
                    )
                )
            res = W.choose_plan(
                ents,
                0,
                cands,
                dists,
                world,
                last - g + 1,
                args.sims,
                seed=li * 100 + g,
            )
            best, _, _ = LC.pick(res)
            decisions += 1
            switches += best != "plan"
            cap_b = cands[best][g].captain
            vice_b = cands[best][g].vice
            sa = score_gameweek(
                pk.xi, pk.bench, pk.captain, pk.vice, pts, mins, season.pos_of
            )
            sb = score_gameweek(
                pk.xi, pk.bench, cap_b, vice_b, pts, mins, season.pos_of
            )
            tot[li][0] += sa.points
            tot[li][1] += sb.points
        print(f"    GW{g}: {len(leagues)} leagues in {time.time() - t1:.0f}s")
    for li, members in enumerate(leagues):
        rivals = np.array(
            [sum(pool[i].points.values()) for i in members[1:]], dtype=float
        )
        out.append(
            {
                "season": name,
                "ep": _win(tot[li][0], rivals),
                "p1": _win(tot[li][1], rivals),
                "pts_diff": tot[li][1] - tot[li][0],
            }
        )
    print(f"  {name}: captain switched in {switches} of {decisions} league-weeks")


def report(rows):
    print(
        f"\n{'subset':<10}{'leagues':>8}{'win EP':>9}{'win P1':>9}{'diff':>9}{'se':>8}"
    )
    for name in ["all", *sorted({r["season"] for r in rows})]:
        rs = [r for r in rows if name in ("all", r["season"])]
        a = np.array([r["ep"] for r in rs])
        b = np.array([r["p1"] for r in rs])
        d = b - a
        se = d.std(ddof=1) / math.sqrt(len(d)) if len(d) > 1 else 0.0
        print(
            f"{name:<10}{len(rs):>8}{a.mean():>9.3f}{b.mean():>9.3f}{d.mean():>+9.3f}{se:>8.3f}"
        )
    pts = np.array([r["pts_diff"] for r in rows])
    print(
        f"\nPoints, P(1st) policy minus expected-points policy: {pts.mean():+.1f} per league"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache" / "history")
    ap.add_argument("--season", action="append", choices=sorted(PREVIOUS))
    ap.add_argument("--pool", type=int, default=120)
    ap.add_argument("--leagues", type=int, default=150)
    ap.add_argument("--size", type=int, default=12)
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument(
        "--start", type=int, default=10, help="first gameweek the policies play"
    )
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--switch-se",
        type=float,
        default=LC.SWITCH_SE,
        help="standard errors of P(1st) gain needed to switch captain",
    )
    args = ap.parse_args()
    LC.SWITCH_SE = args.switch_se
    rng = np.random.default_rng(args.seed)
    rows: list[dict] = []
    for name in args.season or sorted(PREVIOUS):
        d = args.cache / name
        if not (d / "gws" / "merged_gw.csv").exists():
            print(f"  {name}: no data in {d} (run scripts/season_sim.py first)")
            continue
        prev = args.cache / PREVIOUS[name]
        data = load_season(name, d, prev if prev.exists() else None)
        print(f"== {name}")
        run_season(name, data, d, args, rng, rows)
    if rows:
        report(rows)


if __name__ == "__main__":
    main()

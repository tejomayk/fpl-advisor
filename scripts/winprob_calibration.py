"""Is P(finish 1st) calibrated? Synthetic mini-leagues replayed on past seasons.

Usage:
    uv run python scripts/winprob_calibration.py                      # all seasons
    uv run python scripts/winprob_calibration.py --leagues 100 --sims 2000   # quicker
    uv run python scripts/winprob_calibration.py --cache /path/to/.cache/history

Data: the past-season CSVs that scripts/season_sim.py downloads into .cache/history/.

Method, per season:
1. A pool of simulated managers plays the whole season on real points. Each follows a simple,
   plausible strategy with its own random settings: how much it follows ownership (the template),
   recent form and our own expected points, a persistent personal taste for some players, how often it transfers,
   all within the budget, position and club rules. XI and captain by its own scores, real
   auto-subs and vice-captain. No chips and no hits.
2. Leagues of 12 managers are drawn from the pool. At GW10, GW20 and GW30 the simulator predicts
   each manager's P(1st) from what was known then (standings, past gameweek scores, current
   squads and last captain, and `project_dist` from the world as it was before the next deadline).
3. The prediction is compared with who actually won at GW38.

Reported: a reliability table (predicted vs observed win rate, binned), Brier score and log loss
for the full model, a form-only model (no player-level weeks) and a 1/12 baseline.

What this can show: whether probabilities are roughly right in level and spread for leagues like
these. What it can't: leagues share managers and each season is one set of real results, so the
outcomes are far from independent (three seasons is the real sample size for anything that hits
all managers at once); synthetic managers are more alike and simpler than people (no chips, no
hits, no wildcards, no mood swings), so real leagues have more variety than tested here; chip
effects and the rival model are not tested at all.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
import time
from pathlib import Path

import numpy as np

from fpl_advisor import rules as R
from fpl_advisor import winprob as W
from fpl_advisor.model import project_dist
from fpl_advisor.sim import load_season, score_gameweek
from fpl_advisor.squad import STARTING_BUDGET

ROOT = Path(__file__).resolve().parent.parent
PREVIOUS = {"2023-24": "2022-23", "2024-25": "2023-24", "2025-26": "2024-25"}
CHECKPOINTS = (10, 20, 30)
BINS = (0, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9, 1.0001)
FORM_GWS = 6
FORM_PRIOR_GWS = 3


def selected_by_gw(season_dir: Path) -> dict[int, dict[int, float]]:
    out: dict[int, dict[int, float]] = {}
    with open(season_dir / "gws" / "merged_gw.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                pid, gw, sel = int(r["element"]), int(r["round"]), float(r["selected"])
            except (KeyError, ValueError):
                continue
            row = out.setdefault(gw, {})
            row[pid] = max(row.get(pid, 0.0), sel)
    return out


def _z(x: np.ndarray, mask: np.ndarray) -> np.ndarray:
    m, s = x[mask].mean(), x[mask].std() or 1.0
    return (x - m) / s


class Season:
    """Per-gameweek features every simulated manager scores players on."""

    def __init__(self, data, season_dir: Path):
        self.data = data
        self.gws = data.gameweeks
        self.pids = sorted(data.world.players)
        self.ix = {p: i for i, p in enumerate(self.pids)}
        n = len(self.pids)
        self.pos = np.array([data.world.players[p].pos for p in self.pids])
        self.pos_of = {p: data.world.players[p].pos for p in self.pids}
        sel = selected_by_gw(season_dir)
        past_ppg = np.array(
            [
                (data.world.past.get(p, {}).get("total_points", 0) or 0) / 38
                for p in self.pids
            ]
        )
        self.price, self.team, self.active, self.score = {}, {}, {}, {}
        last_sel = np.zeros(n)
        pts_hist = []
        for g in self.gws:
            prices = data.prices_at(g)
            self.price[g] = np.array([prices.get(p, 99.0) for p in self.pids])
            self.team[g] = np.array(
                [
                    data._at(data.team_at[p], g) if p in data.team_at else 0
                    for p in self.pids
                ]
            )
            self.active[g] = np.array(
                [
                    data.active.get(p, (99, -1))[0]
                    <= g
                    <= data.active.get(p, (99, -1))[1]
                    for p in self.pids
                ]
            )
            s = sel.get(g, {})
            last_sel = np.array(
                [s.get(p, last_sel[i]) for i, p in enumerate(self.pids)]
            )
            recent = pts_hist[-FORM_GWS:]
            rsum = np.sum(recent, axis=0) if recent else np.zeros(n)
            form = (rsum + FORM_PRIOR_GWS * past_ppg) / (len(recent) + FORM_PRIOR_GWS)
            act = self.active[g]
            xp = project_dist(data.world_at(g), [g])
            xpv = np.array([xp[p][g].mean if p in xp else 0.0 for p in self.pids])
            self.score[g] = (_z(np.log1p(last_sel), act), _z(form, act), _z(xpv, act))
            pts, _ = data.actual(g)
            pts_hist.append(np.array([pts.get(p, 0) for p in self.pids], dtype=float))


class SimManager:
    def __init__(self, season: Season, rng: np.random.Generator):
        self.s = season
        self.w_own = rng.uniform(0.3, 1.5)
        self.w_form = rng.uniform(0.3, 1.5)
        self.w_xp = rng.uniform(0.0, 1.5)
        self.taste = rng.normal(0, rng.uniform(0.15, 0.5), len(season.pids))
        self.p_transfer = rng.uniform(0.4, 0.9)
        self.rng = rng
        self.squad: list[int] = []
        self.bank = STARTING_BUDGET
        self.squads, self.captains, self.points = {}, {}, {}

    def scores(self, g: int) -> np.ndarray:
        own, form, xp = self.s.score[g]
        x = self.w_own * own + self.w_form * form + self.w_xp * xp + self.taste
        x = x + self.rng.normal(0, 0.3, len(x))
        return np.where(self.s.active[g], x, -1e9)

    def _min_fill(self, g, need: dict[int, int]) -> float:
        out = 0.0
        for k, n in need.items():
            if n:
                cheap = np.sort(self.s.price[g][(self.s.pos == k) & self.s.active[g]])
                out += cheap[:n].sum()
        return out

    def build(self, g: int, sc: np.ndarray):
        need = dict(R.SQUAD_COUNT)
        clubs: dict[int, int] = {}
        bank = STARTING_BUDGET
        for i in np.argsort(-sc):
            k = self.s.pos[i]
            t = self.s.team[g][i]
            price = self.s.price[g][i]
            if need[k] == 0 or clubs.get(t, 0) >= R.MAX_PER_CLUB or sc[i] < -1e8:
                continue
            need[k] -= 1
            if price + self._min_fill(g, need) > bank + 1e-9:
                need[k] += 1
                continue
            self.squad.append(self.s.pids[i])
            clubs[t] = clubs.get(t, 0) + 1
            bank -= price
            if not any(need.values()):
                break
        self.bank = bank

    def transfer(self, g: int, sc: np.ndarray):
        ix, price = self.s.ix, self.s.price[g]
        gone = [p for p in self.squad if not self.s.active[g][ix[p]]]
        n = len(gone) + (1 if self.rng.random() < self.p_transfer else 0)
        for _ in range(n):
            sell = gone.pop() if gone else min(self.squad, key=lambda p: sc[ix[p]])
            k = self.s.pos_of[sell]
            clubs: dict[int, int] = {}
            for p in self.squad:
                if p != sell:
                    t = self.s.team[g][ix[p]]
                    clubs[t] = clubs.get(t, 0) + 1
            budget = self.bank + price[ix[sell]]
            ok = (
                (self.s.pos == k)
                & self.s.active[g]
                & (price <= budget + 1e-9)
                & ~np.isin(np.array(self.s.pids), self.squad)
                & np.array([clubs.get(t, 0) < R.MAX_PER_CLUB for t in self.s.team[g]])
            )
            if not ok.any():
                continue
            cand = np.where(ok, sc, -np.inf)
            buy = int(np.argmax(cand))
            forced = not self.s.active[g][ix[sell]]
            if not forced and cand[buy] <= sc[ix[sell]] + 0.3:
                continue
            self.squad = [p for p in self.squad if p != sell] + [self.s.pids[buy]]
            self.bank = budget - price[buy]

    def play(self, g: int):
        sc = self.scores(g)
        if not self.squad:
            self.build(g, sc)
        else:
            self.transfer(g, sc)
        xp = {p: sc[self.s.ix[p]] for p in self.squad}
        xi, bench = W._best_xi(self.squad, xp, self.s.pos_of)
        cap, vice = sorted(xi, key=lambda p: -xp[p])[:2]
        pts, mins = self.s.data.actual(g)
        res = score_gameweek(xi, bench, cap, vice, pts, mins, self.s.pos_of)
        self.squads[g], self.captains[g], self.points[g] = (
            list(self.squad),
            cap,
            res.points,
        )


def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def logloss(p, y):
    p = np.clip(np.asarray(p), 1e-4, 1 - 1e-4)
    y = np.asarray(y)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def run_season(name, data, season_dir, args, rng, rows):
    t0 = time.time()
    season = Season(data, season_dir)
    pool = [
        SimManager(season, np.random.default_rng(rng.integers(1 << 32)))
        for _ in range(args.pool)
    ]
    for g in season.gws:
        for m in pool:
            m.play(g)
    finals = np.array([sum(m.points.values()) for m in pool])
    print(
        f"  {name}: pool of {args.pool} managers, final totals {finals.min()}-{finals.max()} "
        f"(median {int(np.median(finals))}), built in {time.time() - t0:.0f}s"
    )
    last = max(season.gws)
    leagues = [
        rng.choice(args.pool, size=args.size, replace=False)
        for _ in range(args.leagues)
    ]
    for c in CHECKPOINTS:
        t1 = time.time()
        gws = [g for g in range(c + 1, c + 1 + args.horizon) if g <= last]
        world = data.world_at(c + 1)
        dists = project_dist(world, gws)
        for li, members in enumerate(leagues):
            ms = [pool[i] for i in members]
            final = np.array([sum(m.points.values()) for m in ms], dtype=float)
            won = (final == final.max()) / (final == final.max()).sum()
            base = []
            for j, m in enumerate(ms):
                past = [m.points[g] for g in season.gws if g <= c]
                base.append(
                    W.Entrant(
                        j,
                        f"Manager {j + 1}",
                        float(sum(past)),
                        W.best_picks(
                            m.squads[c], dists, gws, season.pos_of, m.captains[c]
                        ),
                        gws_played=len(past),
                        past_scores=past,
                    )
                )
            left = last - c
            full = W.win_odds(base, dists, world, left, args.sims, seed=li)
            form_only = [
                W.Entrant(e.key, e.label, e.total, {}, e.gws_played, e.past_scores)
                for e in base
            ]
            fo = W.win_odds(form_only, dists, world, left, args.sims, seed=li)
            for j in range(len(ms)):
                rows.append(
                    {
                        "season": name,
                        "gw": c,
                        "league": li,
                        "full": full.p_first[j],
                        "form": fo.p_first[j],
                        "uniform": 1 / len(ms),
                        "won": float(won[j]),
                    }
                )
        print(f"    GW{c}: {len(leagues)} leagues predicted in {time.time() - t1:.0f}s")


def report(rows):
    y = np.array([r["won"] for r in rows])
    print("\nReliability (full model; every manager in every league at GW10/20/30):")
    print(f"{'predicted':>12}{'n':>7}{'mean pred':>11}{'observed':>10}{'+/- 2se':>9}")
    p = np.array([r["full"] for r in rows])
    for lo, hi in itertools.pairwise(BINS):
        m = (p >= lo) & (p < hi)
        if not m.any():
            continue
        obs = y[m].mean()
        se = math.sqrt(max(obs * (1 - obs), 1e-9) / m.sum())
        print(
            f"{lo:>5.0%}-{min(hi, 1):>5.0%}{m.sum():>7}{p[m].mean():>11.3f}"
            f"{obs:>10.3f}{2 * se:>9.3f}"
        )
    print(
        f"\n{'subset':<16}{'n':>6}"
        + "".join(
            f"{k + ' brier':>14}{k + ' logloss':>16}"
            for k in ("full", "form", "uniform")
        )
    )
    subsets = [("all", rows)]
    subsets += [(f"GW{c}", [r for r in rows if r["gw"] == c]) for c in CHECKPOINTS]
    subsets += [
        (s, [r for r in rows if r["season"] == s])
        for s in sorted({r["season"] for r in rows})
    ]
    for name, rs in subsets:
        yy = [r["won"] for r in rs]
        line = f"{name:<16}{len(rs):>6}"
        for k in ("full", "form", "uniform"):
            pp = [r[k] for r in rs]
            line += f"{brier(pp, yy):>14.4f}{logloss(pp, yy):>16.4f}"
        print(line)
    # How often the predicted favourite actually won.
    fav_p, fav_won = [], []
    keyed: dict[tuple, list] = {}
    for r in rows:
        keyed.setdefault((r["season"], r["gw"], r["league"]), []).append(r)
    for rs in keyed.values():
        f = max(rs, key=lambda r: r["full"])
        fav_p.append(f["full"])
        fav_won.append(f["won"])
    print(
        f"\nPredicted favourite: mean P(1st) {np.mean(fav_p):.3f}, actually won "
        f"{np.mean(fav_won):.3f} of {len(fav_p)} league-checkpoints"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache" / "history")
    ap.add_argument("--season", action="append", choices=sorted(PREVIOUS))
    ap.add_argument("--pool", type=int, default=120)
    ap.add_argument("--leagues", type=int, default=200)
    ap.add_argument("--size", type=int, default=12)
    ap.add_argument("--sims", type=int, default=3000)
    ap.add_argument("--horizon", type=int, default=6)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
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

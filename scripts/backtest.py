"""Out-of-sample backtest: for each finished gameweek, project using only earlier data and compare.

Usage: uv run python scripts/backtest.py [--from 3]
Availability news is not historical, so it is ignored here (it only helps the live model).
"""

from __future__ import annotations

import argparse
import math
import statistics

from fpl_advisor.api import FPLClient
from fpl_advisor.model import project
from fpl_advisor.world import load_world


def pearson(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sxy / (sx * sy) if sx and sy else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=3)
    args = ap.parse_args()

    world = load_world(FPLClient())
    finished = [e.id for e in world.events if e.finished]
    rows = {"model": [], "ppg_so_far": [], "last_season_ppg": []}
    bins: dict[int, list] = {}
    actual_all = []
    topk = {k: [] for k in rows}
    for gw in [g for g in finished if g >= args.start]:
        w = world.as_of(gw)
        proj = project(w, [gw], use_availability=False)
        cands = []
        for pid, hist in world.history.items():
            before = [r for r in hist if r["round"] < gw]
            now = [r for r in hist if r["round"] == gw]
            if not now or not any(r["minutes"] > 0 for r in before):
                continue  # only players who had played before: the ones you'd consider
            actual = sum(r["total_points"] for r in now)
            ppg = sum(r["total_points"] for r in before) / max(
                1, sum(r["minutes"] > 0 for r in before)
            )
            past = world.past.get(pid)
            lppg = past["total_points"] / 38 if past else 0.0
            cands.append((pid, proj[pid][gw], ppg, lppg, actual))
        for i, k in enumerate(rows, start=1):
            rows[k].extend(c[i] for c in cands)
            best = sorted(cands, key=lambda c: -c[i])[:30]
            topk[k].append(statistics.fmean(c[4] for c in best))
        actual_all.extend(c[4] for c in cands)
        for c in cands:
            bins.setdefault(min(int(c[1]), 7), []).append((c[1], c[4]))
        print(f"GW{gw}: {len(cands)} players")

    print(f"\n{'predictor':<18}{'corr':>7}{'MAE':>7}{'top30 avg pts':>15}")
    for k, preds in rows.items():
        # The model's MAE is raw. Baselines aren't on a points-per-match scale for every player,
        # so they are rescaled to the actual mean to compare shape only.
        scale = (
            1.0
            if k == "model"
            else statistics.fmean(actual_all) / max(1e-9, statistics.fmean(preds))
        )
        mae = statistics.fmean(abs(p * scale - a) for p, a in zip(preds, actual_all))
        print(
            f"{k:<18}{pearson(preds, actual_all):>7.3f}{mae:>7.2f}{statistics.fmean(topk[k]):>15.2f}"
        )

    print(f"\nCalibration (model)\n{'bin':<8}{'n':>6}{'predicted':>11}{'actual':>9}")
    for b in sorted(bins):
        pairs = bins[b]
        label = f"{b}-{b + 1}" if b < 7 else "7+"
        pred = statistics.fmean(p for p, _ in pairs)
        act = statistics.fmean(a for _, a in pairs)
        print(f"{label:<8}{len(pairs):>6}{pred:>11.2f}{act:>9.2f}")


if __name__ == "__main__":
    main()

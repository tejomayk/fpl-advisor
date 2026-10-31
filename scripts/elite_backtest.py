"""Does knowing what the elite own help predict next gameweek's points? Out-of-sample backtest.

For each finished gameweek k >= 3: the elite's picks from gameweek k-1 (public before k's deadline)
and the model's projection for k made with `world.as_of(k)`. Compared against plain popularity:
FPL-wide ownership at gameweek k-1 (the `selected` count in each player's match history).

Usage: uv run python scripts/elite_backtest.py [--pool 10000] [--top 100] [--elite PATH]

Leakage: the elite are picked from this season's top `pool` managers, and that rank partly reflects
good picks in the very gameweeks tested. They are ranked on past seasons only, which limits it,
but the pool filter itself still leaks. Treat a small edge as no edge.
"""

from __future__ import annotations

import argparse
import logging
import statistics
import time

# Run as a script, so scripts/ is on sys.path.
from backtest import pearson

from fpl_advisor import elite
from fpl_advisor.api import FPLClient
from fpl_advisor.model import project
from fpl_advisor.world import load_world

BETAS = (1.0, 2.0, 4.0, 8.0)


def selected_before(rows: list[dict], gw: int) -> float:
    """Managers who owned the player at the latest round before `gw` (0 if never)."""
    before = [r for r in rows if r["round"] < gw]
    return float(max(before, key=lambda r: r["round"])["selected"]) if before else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=3)
    ap.add_argument("--pool", type=int, default=10_000)
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--elite", default=".cache/elite_managers.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    client = FPLClient()
    t0 = time.monotonic()
    managers = elite.load_or_discover(
        client, args.elite, pool_size=args.pool, top=args.top
    )
    print(
        f"Elite: {len(managers)} managers from pool {args.pool} "
        f"({time.monotonic() - t0:.0f}s); median ranks "
        f"{managers[0].median_rank:.0f}..{managers[-1].median_rank:.0f}"
    )

    world = load_world(client)
    total = max(1, world.total_managers)
    finished = [e.id for e in world.events if e.finished]
    preds: dict[str, list[float]] = {}
    topk: dict[str, list[float]] = {}
    actual_all: list[float] = []

    for gw in [g for g in finished if g >= args.start]:
        picks = elite.read_picks(client, managers, gw - 1)
        own, _ = elite.ownership(picks)
        proj = project(world.as_of(gw), [gw], use_availability=False)
        cands = []
        for pid, hist in world.history.items():
            before = [r for r in hist if r["round"] < gw]
            now = [r for r in hist if r["round"] == gw]
            if not now or not any(r["minutes"] > 0 for r in before):
                continue
            cands.append(
                (
                    proj[pid][gw],
                    own.get(pid, 0.0),
                    selected_before(hist, gw) / total,
                    sum(r["total_points"] for r in now),
                )
            )
        # Centre ownership within the week so a blend shifts the ranking, not the average level.
        m_e = statistics.fmean(c[1] for c in cands)
        m_g = statistics.fmean(c[2] for c in cands)
        week = {
            "model": [c[0] for c in cands],
            "elite_own": [c[1] for c in cands],
            "global_own": [c[2] for c in cands],
        }
        for b in BETAS:
            week[f"model+{b:g}*elite"] = [c[0] + b * (c[1] - m_e) for c in cands]
            week[f"model+{b:g}*global"] = [c[0] + b * (c[2] - m_g) for c in cands]
        acts = [c[3] for c in cands]
        for k, v in week.items():
            preds.setdefault(k, []).extend(v)
            best = sorted(zip(v, acts), key=lambda x: -x[0])[:30]
            topk.setdefault(k, []).append(statistics.fmean(a for _, a in best))
        actual_all.extend(acts)
        print(
            f"GW{gw}: {len(cands)} players, elite picks from GW{gw - 1}: {len(picks)}"
        )

    mean_act = statistics.fmean(actual_all)
    print(f"\n{'predictor':<20}{'corr':>7}{'MAE':>7}{'top30 avg pts':>15}")
    for k, p in preds.items():
        # Ownership alone is not on a points scale: rescale to the actual mean (shape only).
        raw = k.startswith("model")
        scale = 1.0 if raw else mean_act / max(1e-9, statistics.fmean(p))
        mae = statistics.fmean(abs(x * scale - a) for x, a in zip(p, actual_all))
        print(
            f"{k:<20}{pearson(p, actual_all):>7.3f}{mae:>7.2f}"
            f"{statistics.fmean(topk[k]):>15.2f}"
        )
    print(
        "\nCaveat: the elite come from this season's top "
        f"{args.pool} overall, which partly reflects the gameweeks tested (ranked on past "
        "seasons only to limit it). Global ownership has no such advantage."
    )


if __name__ == "__main__":
    main()

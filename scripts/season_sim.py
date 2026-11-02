"""Season simulator: replay whole past seasons following the advisor's decisions, scored on real points.

Usage:
    uv run python scripts/season_sim.py                       # every available season
    uv run python scripts/season_sim.py --season 2024-25      # one season
    uv run python scripts/season_sim.py --time-limit 5 --until 10   # quicker, partial season

Data: the public vaastav/Fantasy-Premier-League dataset on GitHub (merged_gw.csv, players_raw.csv,
fixtures.csv, teams.csv per season), downloaded once into .cache/history/ (gitignored). The FPL
API only serves the current season, so it can't be used for this.

Each gameweek the advisor sees only what was known before that deadline (World.as_of) at that
week's prices, and makes its decision through `advise.decide`, the same code `advise` runs. The
moves are applied under the season's real rules (free-transfer accrual and cap, -4 hits, selling
price = purchase price + half the profit rounded down, bank, chip windows) and scored with each
player's actual points, including auto-substitutions and the vice-captain fallback.

Baselines:
- set-and-forget: the same GW1 squad, never transferred; best XI and captain each week, no chips.
- advisor: the current decision path.

Results print as a table and are written to .cache/history/season_sim_results.json.

Caveats (see also the notes printed per season):
- No historical injury/suspension news exists, so every player in the game counts as available
  (the live advisor would see FPL's flags). Players who joined or left the game mid-season are
  only buyable while they are in it.
- Fixture dates come from the final schedule, so postponed matches and doubles are "known" earlier
  than they really were.
- Projections use the current rules.py; actual outcomes use the data's total_points, so scoring
  is exact for every season.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import replace
from pathlib import Path

from fpl_advisor import optimise
from fpl_advisor import rules as R
from fpl_advisor.advise import decide
from fpl_advisor.chips import best_xi
from fpl_advisor.config import Config
from fpl_advisor.league import ownership_multiplier, risk_lambda
from fpl_advisor.sim import (
    SEASONS,
    Manager,
    SeasonData,
    SeasonRules,
    apply_transfers,
    hits_for,
    load_season,
    next_free_transfers,
    score_gameweek,
)
from fpl_advisor.squad import STARTING_BUDGET, Squad, chips_available

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "history"
BASE_URL = (
    "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
)
FILES = ("players_raw.csv", "fixtures.csv", "teams.csv", "gws/merged_gw.csv")
PREVIOUS = {"2023-24": "2022-23", "2024-25": "2023-24", "2025-26": "2024-25"}


# ------------------------------------------------------------------ data


def download(season: str, need_gws: bool = True) -> Path | None:
    """Fetch a season's CSVs into the cache (once). None if the dataset lacks the season."""
    d = CACHE / season
    for f in FILES if need_gws else ("players_raw.csv",):
        path = d / f
        if path.exists() and path.stat().st_size > 0:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(f"{BASE_URL}/{season}/{f}", timeout=60) as r:
                data = r.read()
        except Exception as e:  # noqa: BLE001 - a missing season is reported, not fatal
            print(f"  {season}/{f}: not available ({e})", file=sys.stderr)
            return None
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    return d


def load(season: str, rules: SeasonRules) -> SeasonData | None:
    d = download(season)
    if d is None:
        return None
    prev = PREVIOUS.get(season)
    prev_dir = download(prev, need_gws=False) if prev else None
    data = load_season(season, d, prev_dir)
    data.world = replace(data.world, chips=rules.chip_windows)
    return data


# ------------------------------------------------------------------ simulation


def make_squad(m: Manager, sell: dict[int, float], chips: list[str]) -> Squad:
    return Squad(
        team_id=0,
        manager="simulated",
        team_name="simulated",
        players=list(m.players),
        starting=[],
        captain=None,
        sell_price=sell,
        purchase_price=dict(m.purchase_price),
        bank=m.bank,
        free_transfers=m.free_transfers,
        chips_available=chips,
        chips_used=list(m.chips_used),
    )


def simulate(
    data: SeasonData, rules: SeasonRules, until: int | None, log=print
) -> dict:
    defaults = Config.__dataclass_fields__
    max_hits = defaults["max_hits"].default
    lam, _ = risk_lambda(
        0, [], 0, defaults["risk"].default
    )  # no mini-league in the replay
    gws = [g for g in data.gameweeks if until is None or g <= until]
    pos = {pid: p.pos for pid, p in data.world.players.items()}

    adv: Manager | None = None
    saf: Manager | None = None
    out = {"advisor": [], "set_and_forget": []}
    plan_mismatch = []
    for g in gws:
        t0 = time.time()
        world = data.world_at(g)
        price = data.prices_at(g)
        pts, mins = data.actual(g)
        mult = ownership_multiplier(world.players, {}, lam) or None

        # ---------------- advisor
        if adv is None:
            empty = Manager([], {}, bank=STARTING_BUDGET, free_transfers=0)
            dec = decide(
                world,
                make_squad(empty, {}, []),
                initial=True,
                max_hits=max_hits,
                multiplier=mult,
                ft_cap=rules.max_free_transfers,
                keep_fts_on_chip=rules.keep_fts_on_chip,
                compare_hold=True,
            )
            plan, chip = dec.plan, None
            adv = Manager(
                list(plan.squad),
                {p: price[p] for p in plan.squad},
                bank=round(STARTING_BUDGET - sum(price[p] for p in plan.squad), 1),
                free_transfers=1,
            )
            saf = replace(adv, purchase_price=dict(adv.purchase_price))
            n_tr, hits, ft_before = 0, 0, None
            playing = adv
        else:
            ft_before = adv.free_transfers
            sell = adv.sell_prices(price)
            avail = chips_available(rules.chip_windows, adv.chips_used, g)
            dec = decide(
                world,
                make_squad(adv, sell, avail),
                initial=False,
                max_hits=max_hits,
                multiplier=mult,
                ft_cap=rules.max_free_transfers,
                keep_fts_on_chip=rules.keep_fts_on_chip,
                compare_hold=True,
            )
            plan = dec.plan
            chip = dec.chip_now.chip if dec.chip_now else None
            n_tr = len(plan.buys)
            hits = hits_for(n_tr, adv.free_transfers, chip)
            if hits != plan.hits:
                plan_mismatch.append((g, "hits", plan.hits, hits))
            playing = apply_transfers(adv, plan.buys, plan.sells, price)
            if abs(playing.bank - plan.bank_after) > 0.05:
                plan_mismatch.append((g, "bank", plan.bank_after, playing.bank))
            if chip:
                playing.chips_used = adv.chips_used + [(chip, g)]
            nxt = next_free_transfers(adv.free_transfers, n_tr, chip, rules, g + 1)
            # A Free Hit squad reverts after the gameweek (bank too); the chip stays used.
            base = adv if chip == "freehit" else playing
            adv = replace(base, free_transfers=nxt, chips_used=playing.chips_used)

        sc = score_gameweek(
            plan.xi[g],
            plan.bench[g],
            plan.captain[g],
            plan.vice[g],
            pts,
            mins,
            pos,
            chip,
        )
        out["advisor"].append(
            {
                "gw": g,
                "points": sc.points - 4 * hits,
                "raw_points": sc.points,
                "expected": round(plan.gw_points[g], 1),
                "hits": hits,
                "transfers": n_tr,
                "free_transfers": ft_before,
                "chip": chip,
                "captain": data.world.players[plan.captain[g]].name,
                "captain_points": pts.get(plan.captain[g], 0),
                "multiplied": data.world.players[sc.multiplier_on].name
                if sc.multiplier_on
                else None,
                "autosubs": len(sc.subs),
                # Robustness (advise.decide): P(the moves beat holding), P(the hit beats the
                # best plan without one), and whether the rule changed the planner's choice.
                "p_beats_hold": dec.p_beats_hold,
                "p_hit": dec.p_hit,
                "robust_changed": dec.robust_note is not None,
                "in": [data.world.players[p].name for p in plan.buys]
                if g > gws[0]
                else [],
                "out": [data.world.players[p].name for p in plan.sells],
                "bank": playing.bank,
                "value": round(
                    playing.bank + sum(playing.sell_prices(price).values()), 1
                ),
            }
        )

        # ---------------- set and forget: same GW1 squad, best XI/captain, no moves, no chips
        # A greedy best XI is optimal for a fixed 15, and unlike the optimiser it can't be made
        # infeasible by a club move putting four of one club in the squad.
        xp_g = {p: dec.xp[p].get(g, 0.0) for p in saf.players}
        s_xi, s_bench = best_xi(saf.players, world, xp_g)
        s_bench.sort(key=lambda p: (pos[p] != R.GKP, -xp_g[p]))
        s_cap, s_vice = sorted(s_xi, key=lambda p: -xp_g[p])[:2]
        ss = score_gameweek(s_xi, s_bench, s_cap, s_vice, pts, mins, pos)
        out["set_and_forget"].append({"gw": g, "points": ss.points})

        a = out["advisor"][-1]
        log(
            f"  GW{g:>2}: advisor {a['points']:>3} (exp {a['expected']:>5.1f}, "
            f"{a['transfers']} tr, {a['hits']} hits{', ' + chip if chip else ''}, "
            f"C {a['captain']} {a['captain_points']})  set&forget {ss.points:>3}  "
            f"[{time.time() - t0:.0f}s]"
        )

    def summary(rows):
        return {
            "total": sum(r["points"] for r in rows),
            "per_gw": [r["points"] for r in rows],
        }

    adv_rows = out["advisor"]
    return {
        "season": data.name,
        "gameweeks": [gws[0], gws[-1]],
        "advisor": {
            **summary(adv_rows),
            "hits": sum(r["hits"] for r in adv_rows),
            "hit_points": 4 * sum(r["hits"] for r in adv_rows),
            "transfers": sum(r["transfers"] for r in adv_rows),
            # Weeks a free transfer was saved (no move, no chip), and weeks one was lost because
            # the bank of free transfers was already full.
            "fts_banked": sum(
                1 for r in adv_rows[1:] if r["transfers"] == 0 and not r["chip"]
            ),
            "fts_wasted": sum(
                1
                for r in adv_rows[1:]
                if r["transfers"] == 0
                and not r["chip"]
                and r["free_transfers"] == rules.max_free_transfers
            ),
            "chips": [(r["chip"], r["gw"]) for r in adv_rows if r["chip"]],
            "final_value": adv_rows[-1]["value"],
            "detail": adv_rows,
        },
        "set_and_forget": summary(out["set_and_forget"]),
        "plan_mismatches": plan_mismatch,
        "data": {
            "defensive_contribution": data.has_defensive_contribution,
            "xg": data.has_xg,
            "players": len(data.world.players),
            "with_last_season": len(data.world.past),
        },
        "rule_notes": list(rules.notes),
    }


# ------------------------------------------------------------------ noise


def add_jitter(rel: float, seed: int) -> None:
    """Multiply every projection by (1 + N(0, rel)), seeded by (seed, player, gameweek).

    Single season runs move by tens of points on tiny changes, so a change should be judged on
    the mean over several seeds. The same seed gives the same noise for the same projection.
    """
    import random

    from fpl_advisor import advise

    project_dist = advise.project_dist

    def noisy(world, gws, **kw):
        dists = project_dist(world, gws, **kw)
        base = world.next_event.id if world.next_event else 0
        for pid, by_gw in dists.items():
            for g, d in by_gw.items():
                rng = random.Random(f"{seed}:{base}:{pid}:{g}")
                by_gw[g] = replace(d, mean=d.mean * max(0.0, 1 + rng.gauss(0, rel)))
        return dists

    advise.project_dist = noisy


def apply_settings(settings: list[str]) -> None:
    """--set module.NAME=value (value as JSON), e.g. --set robust.MOVE_CONFIDENCE=0.6."""
    import importlib

    for s in settings:
        key, _, raw = s.partition("=")
        mod_name, _, name = key.rpartition(".")
        mod = importlib.import_module(f"fpl_advisor.{mod_name}")
        if not hasattr(mod, name):
            raise SystemExit(f"--set: fpl_advisor.{mod_name} has no {name}")
        val = json.loads(raw)
        if isinstance(getattr(mod, name), tuple):
            val = tuple(val)
        setattr(mod, name, val)


# ------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", action="append", choices=sorted(SEASONS))
    ap.add_argument(
        "--time-limit",
        type=float,
        default=10,
        help="CBC seconds per solve (live advice uses 60)",
    )
    ap.add_argument("--until", type=int, help="stop after this gameweek")
    ap.add_argument("--out", type=Path, default=CACHE / "season_sim_results.json")
    ap.add_argument(
        "--jitter",
        type=float,
        default=0.0,
        help="relative noise on every projection (e.g. 0.01), to see how noisy a total is",
    )
    ap.add_argument("--seed", type=int, default=0, help="seed for --jitter")
    ap.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="MODULE.NAME=JSON",
        help="override a constant for this run, e.g. robust.MOVE_CONFIDENCE=0.6",
    )
    args = ap.parse_args()
    apply_settings(args.set)
    optimise.SOLVER_TIME_LIMIT = args.time_limit
    if args.jitter:
        add_jitter(args.jitter, args.seed)

    results = []
    for season in args.season or sorted(SEASONS):
        rules = SEASONS[season]
        print(f"== {season}")
        data = load(season, rules)
        if data is None:
            print("  skipped: data not available")
            continue
        t0 = time.time()
        res = simulate(data, rules, args.until)
        res["runtime_s"] = round(time.time() - t0)
        results.append(res)
        print(
            f"  {season}: advisor {res['advisor']['total']}, "
            f"set&forget {res['set_and_forget']['total']}  ({res['runtime_s']}s)"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    previous = {}
    if args.out.exists():
        try:
            previous = {r["season"]: r for r in json.loads(args.out.read_text())}
        except (ValueError, KeyError, TypeError):
            previous = {}
    previous.update({r["season"]: r for r in results})
    args.out.write_text(json.dumps(list(previous.values()), indent=1))

    print(
        f"\n{'season':<9}{'GWs':>7}{'advisor':>9}{'set&forget':>12}{'diff':>7}"
        f"{'transfers':>11}{'hits':>6}{'banked':>8}{'wasted':>8}{'time':>7}  chips"
    )
    for r in results:
        a, s = r["advisor"], r["set_and_forget"]
        chips = ", ".join(f"{c}@{g}" for c, g in a["chips"]) or "-"
        gw = f"{r['gameweeks'][0]}-{r['gameweeks'][1]}"
        print(
            f"{r['season']:<9}{gw:>7}{a['total']:>9}{s['total']:>12}{a['total'] - s['total']:>+7}"
            f"{a['transfers']:>11}{a['hits']:>6}{a.get('fts_banked', 0):>8}"
            f"{a.get('fts_wasted', 0):>8}{r['runtime_s']:>6}s  {chips}"
        )
        if r["plan_mismatches"]:
            print(f"  plan vs rules mismatches: {r['plan_mismatches']}")
        for n in r["rule_notes"]:
            print(f"  note: {n}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()

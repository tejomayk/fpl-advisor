"""Chance of winning the mini-league: simulate the rest of the season for you and your rivals.

A mini-league pays only for 1st place, so the number that matters is P(finish 1st), not expected
points. This module estimates it by Monte Carlo:

- **Detailed weeks** (the next few gameweeks, where we know or can guess everyone's team): each
  player's score is drawn once per simulated season and shared by every manager who owns him, so
  shared players cancel and only differentials create luck between you. Scores are correlated
  within a fixture: a shared team factor lifts a side's attackers and sinks the other side's
  defenders and keeper. Each player also gets one forecast-error draw per simulated season
  (`PointsDist.mean_sd`), shared across his gameweeks. Auto-subs, captain/vice, Triple Captain,
  Bench Boost and hits are applied.
- **Later weeks** (the rest of the season, where nobody's team is known): each manager scores the
  league average plus their own edge, estimated from points per gameweek so far and shrunk
  toward the league (hot starts fade), with week-to-week noise. If past gameweek scores are
  given, managers whose scores move together (similar teams) stay correlated.

`win_odds` gives P(1st), expected rank and the gap to the winner for every manager.
`choose_plan` compares candidate plans for one manager on the same random draws (common random
numbers) and returns the plan with the best P(1st) plus the difference and its standard error.
`describe` turns the result into a sentence for users.

Simplifications (see also scripts/winprob_calibration.py): auto-subs ignore formation rules
(any bench outfielder who played replaces any outfield starter who didn't); a player's chance
of playing is drawn independently each week (injuries don't persist); rivals hold their current
squad through the detailed weeks; in a double gameweek a player shares one team factor for both
matches; unused chips add a flat expected value in the later weeks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import rules as R
from .model import PointsDist

# How strongly a player's score follows his team's attacking output (own) and the opponent's
# attacking output (opp, negative: their goals cost him a clean sheet). Teammate attackers then
# correlate about 0.4 * 0.45 = 0.18, a keeper and his defenders about 0.5 * 0.45 = 0.23.
TEAM_LOADING = {
    R.GKP: (0.0, 0.5),
    R.DEF: (0.1, 0.45),
    R.MID: (0.4, 0.1),
    R.FWD: (0.45, 0.0),
}
# Later weeks: one manager's gameweek score around their expected score (two managers with
# unrelated teams then differ by about 14 a week, matching league.GW_DIFF_SD).
TAIL_GW_SD = 10.0
# How much managers' true per-gameweek averages differ (prior). With TAIL_GW_SD this gives the
# same shrinkage as league.py: an edge seen over g gameweeks is trusted g / (g + ~15).
SKILL_PRIOR_SD = 2.6
# Rough average gain from an unused chip over playing without it (points). Only used for later
# weeks, where nobody's chip timing is known.
CHIP_VALUE = {"3xc": 6.0, "bboost": 8.0, "freehit": 6.0, "wildcard": 6.0}
MIN_COV_GWS = 5  # past gameweeks needed before their covariance is used
COV_SHRINK_GWS = 10  # empirical covariance weight: n / (n + this)
GAP_PERCENTILES = (10, 25, 50, 75, 90)


@dataclass
class GWPick:
    """One manager's team for one gameweek."""

    xi: list[int]
    bench: list[int] = field(default_factory=list)  # in auto-sub order
    captain: int | None = None
    vice: int | None = None
    chip: str | None = (
        None  # "3xc" and "bboost" change scoring; others need no special case
    )
    hits: int = 0  # points deducted this gameweek


Picks = dict[int, GWPick]  # gameweek -> that week's team


@dataclass
class Entrant:
    """A manager in the simulation.

    key: any id (e.g. the FPL entry id); label: the name shown to users (from the league data).
    total: points so far. picks: their team for each detailed gameweek (the same weeks for all).
    gws_played and past_scores (points per gameweek so far, oldest first) estimate their edge
    for the later weeks; chips_left adds the rough value of unused chips there.
    """

    key: int | str
    label: str
    total: float
    picks: Picks
    gws_played: int = 0
    past_scores: list[float] | None = None
    chips_left: list[str] = field(default_factory=list)


@dataclass
class LeagueOdds:
    keys: list[int | str]
    labels: dict[int | str, str]
    p_first: dict[int | str, float]
    p_first_se: dict[int | str, float]
    expected_rank: dict[int | str, float]
    # percentile -> points behind the eventual winner (0 when this manager wins)
    gap_to_winner: dict[int | str, dict[int, float]]
    n_sims: int
    detailed_gws: list[int]
    later_gws: int


@dataclass
class PlanChoice:
    """Candidate plans for one manager compared on P(1st), on the same simulated seasons."""

    best: str
    p_first: dict[str, float]
    # each candidate minus the first one (the baseline): (difference, standard error)
    vs_baseline: dict[str, tuple[float, float]]
    margin: float  # best minus runner-up
    margin_se: float
    odds: LeagueOdds  # the league with the manager on the baseline plan


# ------------------------------------------------------------------ building teams


def best_picks(
    squad: list[int],
    dists: dict[int, dict[int, PointsDist]],
    gws: list[int],
    pos: dict[int, int],
    last_captain: int | None = None,
    keep_captain_within: float = 0.85,
) -> Picks:
    """A held squad's best XI, bench order, captain and vice for each gameweek, by expected points.

    Used for rivals (and as a plain default for you). The last captain is kept while he is in the
    XI and within `keep_captain_within` of the best expected score: managers stick with captains.
    """
    out: Picks = {}
    for gw in gws:
        xp = {p: _dist(dists, p, gw).mean for p in squad}
        xi, bench = _best_xi(squad, xp, pos)
        ranked = sorted(xi, key=lambda p: -xp[p])
        cap = ranked[0] if ranked else None
        if (
            last_captain in xi
            and cap is not None
            and xp[last_captain] >= keep_captain_within * xp[cap]
        ):
            cap = last_captain
        vice = next((p for p in ranked if p != cap), None)
        out[gw] = GWPick(xi, bench, cap, vice)
    return out


def with_captain(picks: Picks, gw: int, captain: int) -> Picks:
    """The same picks with a different captain in one gameweek (the old captain becomes vice)."""
    old = picks[gw]
    if captain not in old.xi:
        raise ValueError(f"captain {captain} is not in the XI for gameweek {gw}")
    vice = old.captain if old.captain != captain else old.vice
    new = dict(picks)
    new[gw] = GWPick(list(old.xi), list(old.bench), captain, vice, old.chip, old.hits)
    return new


def _best_xi(
    squad: list[int], xp: dict[int, float], pos: dict[int, int]
) -> tuple[list[int], list[int]]:
    by_pos = {
        k: sorted((p for p in squad if pos.get(p) == k), key=lambda p: -xp[p])
        for k in R.POS_NAME
    }
    xi = by_pos[R.GKP][:1]
    for k in (R.DEF, R.MID, R.FWD):
        xi += by_pos[k][: R.XI_MIN[k]]
    rest = sorted(
        (p for k in (R.DEF, R.MID, R.FWD) for p in by_pos[k][R.XI_MIN[k] :]),
        key=lambda p: -xp[p],
    )
    for p in rest:
        if len(xi) == 11:
            break
        if sum(pos[q] == pos[p] for q in xi) < R.XI_MAX[pos[p]]:
            xi.append(p)
    bench = sorted(
        (p for p in squad if p not in xi), key=lambda p: (pos.get(p) == R.GKP, -xp[p])
    )
    return xi, bench


_ZERO = PointsDist(0.0, 0.0, 0.0, 0.0)


def _dist(dists, pid: int, gw: int) -> PointsDist:
    return dists.get(pid, {}).get(gw, _ZERO)


# ------------------------------------------------------------------ the simulation


@dataclass
class _Structure:
    """Who plays for whom and against whom, for the team factors."""

    pos: dict[int, int]
    team: dict[int, int]
    opponents: dict[int, dict[int, list[int]]]  # gw -> team -> opposing teams


def structure_from_world(world, gws: list[int]) -> _Structure:
    opp: dict[int, dict[int, list[int]]] = {}
    for gw in gws:
        o: dict[int, list[int]] = {}
        for f in world.fixtures_in(gw):
            o.setdefault(f.home, []).append(f.away)
            o.setdefault(f.away, []).append(f.home)
        opp[gw] = o
    return _Structure(
        pos={pid: p.pos for pid, p in world.players.items()},
        team={pid: p.team for pid, p in world.players.items()},
        opponents=opp,
    )


def _pick_players(p: GWPick) -> list[int]:
    return list(p.xi) + list(p.bench)


def _run(
    entrants: list[Entrant],
    dists: dict[int, dict[int, PointsDist]],
    st: _Structure,
    gws_left: int,
    n_sims: int,
    seed: int,
    candidates: list[Picks] | None = None,
    me_index: int | None = None,
) -> tuple[np.ndarray, np.ndarray | None, list[int]]:
    """Final totals (n_sims x managers) and, if given, the candidates' totals for `me_index`."""
    if not entrants:
        raise ValueError("no managers to simulate")
    gws = sorted({g for e in entrants for g in e.picks})
    for e in entrants:
        if sorted(e.picks) != gws:
            raise ValueError(
                f"{e.label}: picks must cover the same gameweeks as everyone else ({gws})"
            )
    candidates = candidates or []
    for c in candidates:
        if sorted(c) != gws:
            raise ValueError(f"candidate plans must cover gameweeks {gws}")
    later = gws_left - len(gws)
    if later < 0:
        raise ValueError(f"{len(gws)} detailed gameweeks but only {gws_left} left")

    rng = np.random.default_rng(seed)
    S = n_sims
    totals = np.tile(np.array([e.total for e in entrants], dtype=float), (S, 1))
    cand = np.zeros((S, len(candidates))) if candidates else None
    if cand is not None:
        cand += entrants[me_index].total

    universe = sorted(
        {
            p
            for plans in [e.picks for e in entrants] + candidates
            for pk in plans.values()
            for p in _pick_players(pk)
        }
    )
    idx = {p: i for i, p in enumerate(universe)}
    U = len(universe)
    if not U:
        gws_to_draw: list[int] = []
    else:
        gws_to_draw = gws
        teams = sorted({st.team.get(p, 0) for p in universe} | _all_teams(st, gws))
        tix = {t: i for i, t in enumerate(teams)}
        pteam = np.array([tix[st.team.get(p, 0)] for p in universe])
        ppos = [st.pos.get(p, R.MID) for p in universe]
        own_w = np.array([TEAM_LOADING[k][0] for k in ppos])
        opp_w = np.array([TEAM_LOADING[k][1] for k in ppos])
        idio_w = np.sqrt(1 - own_w**2 - opp_w**2)
        is_gk = np.array([k == R.GKP for k in ppos])
        zf = rng.standard_normal((S, U))  # forecast error, shared across gameweeks

    for gw in gws_to_draw:
        d = [_dist(dists, p, gw) for p in universe]
        mean = np.array([x.mean for x in d])
        sd = np.array([x.sd for x in d])
        msd = np.array([x.mean_sd for x in d])
        pp = np.clip(np.array([x.p_play for x in d]), 0.0, 1.0)
        # Team factors: own attack, and the average attack of this week's opponents.
        g = rng.standard_normal((S, len(teams)))
        A = np.zeros((len(teams), len(teams)))
        for t, opps in st.opponents.get(gw, {}).items():
            if t in tix:
                for o in opps:
                    A[tix[t], tix[o]] = 1 / len(opps)
        g_opp = g @ A.T
        z = (
            own_w * g[:, pteam]
            - opp_w * g_opp[:, pteam]
            + idio_w * rng.standard_normal((S, U))
        )
        played = rng.random((S, U)) < pp
        safe_p = np.maximum(pp, 1e-9)
        # Points given that he plays: mean/p, and the variance that matches sd overall.
        var_c = np.maximum((sd**2 + mean**2) / safe_p - (mean / safe_p) ** 2, 0.25)
        m_c = np.maximum(mean + zf * msd, 0.0) / safe_p
        m_c = np.maximum(m_c, 1e-3)
        s2 = np.log1p(var_c / m_c**2)
        pts = np.exp(
            np.log(m_c) - s2 / 2 + np.sqrt(s2) * z
        )  # lognormal: hauls are rare
        pts = np.where(played & (pp > 0), pts, 0.0)
        for j, e in enumerate(entrants):
            totals[:, j] += _gw_score(pts, played, e.picks[gw], idx, is_gk)
        for c, plan in enumerate(candidates):
            cand[:, c] += _gw_score(pts, played, plan[gw], idx, is_gk)

    if later > 0:
        add = _later_weeks(entrants, later, rng, S)
        totals += add
        if cand is not None:
            cand += add[:, [me_index]]
    return totals, cand, gws


def _all_teams(st: _Structure, gws: list[int]) -> set[int]:
    out: set[int] = set()
    for gw in gws:
        for t, opps in st.opponents.get(gw, {}).items():
            out.add(t)
            out.update(opps)
    return out


def _gw_score(
    pts: np.ndarray, played: np.ndarray, pick: GWPick, idx: dict[int, int], is_gk
) -> np.ndarray:
    xi = [idx[p] for p in pick.xi]
    bench = [idx[p] for p in pick.bench]
    if pick.chip == "bboost":
        score = pts[:, xi + bench].sum(axis=1)
    else:
        score = pts[:, xi].sum(axis=1)
        xi_gk = [i for i in xi if is_gk[i]]
        b_gk = [i for i in bench if is_gk[i]]
        if xi_gk and b_gk:
            swap = ~played[:, xi_gk[0]] & played[:, b_gk[0]]
            score = score + np.where(swap, pts[:, b_gk[0]], 0.0)
        xi_out = [i for i in xi if not is_gk[i]]
        b_out = [i for i in bench if not is_gk[i]]
        if xi_out and b_out:
            missing = (~played[:, xi_out]).sum(axis=1)
            came_on = played[:, b_out]
            use = came_on & (np.cumsum(came_on, axis=1) <= missing[:, None])
            score = score + (pts[:, b_out] * use).sum(axis=1)
    if pick.captain is not None:
        extra = 2 if pick.chip == "3xc" else 1
        c = idx[pick.captain]
        star = np.where(played[:, c], pts[:, c], 0.0)
        if pick.vice is not None:
            v = idx[pick.vice]
            star = np.where(played[:, c], star, np.where(played[:, v], pts[:, v], 0.0))
        score = score + extra * star
    return score - pick.hits


def _later_weeks(
    entrants: list[Entrant], n: int, rng: np.random.Generator, S: int
) -> np.ndarray:
    """Points over the remaining `n` weeks relative to the league average (which cancels)."""
    M = len(entrants)
    rates = [e.total / e.gws_played for e in entrants if e.gws_played > 0]
    avg = sum(rates) / len(rates) if rates else 0.0
    k = TAIL_GW_SD**2 / SKILL_PRIOR_SD**2
    edge = np.zeros(M)
    edge_sd = np.full(M, SKILL_PRIOR_SD)
    chips = np.zeros(M)
    for j, e in enumerate(entrants):
        g = e.gws_played
        if g > 0:
            edge[j] = (e.total / g - avg) * g / (g + k)
            edge_sd[j] = math.sqrt(1 / (1 / SKILL_PRIOR_SD**2 + g / TAIL_GW_SD**2))
        chips[j] = sum(CHIP_VALUE.get(c, 0.0) for c in e.chips_left)
    cov = TAIL_GW_SD**2 * np.eye(M)
    hist = [e.past_scores for e in entrants]
    n_hist = min((len(h) for h in hist if h), default=0) if all(hist) else 0
    if n_hist >= MIN_COV_GWS and M > 1:
        X = np.array([h[-n_hist:] for h in hist], dtype=float)
        emp = np.cov(X)
        # Only differences between managers matter: remove the league-wide swing that moves
        # everyone together, then shrink toward independent noise.
        J = np.eye(M) - np.ones((M, M)) / M
        emp = J @ emp @ J
        w = n_hist / (n_hist + COV_SHRINK_GWS)
        cov = w * emp + (1 - w) * cov
    L = np.linalg.cholesky(cov + 1e-6 * np.eye(M))
    skill = edge + edge_sd * rng.standard_normal((S, M))
    noise = rng.standard_normal((S, M)) @ L.T
    return n * skill + math.sqrt(n) * noise + chips


# ------------------------------------------------------------------ results


def _win_share(final: np.ndarray) -> np.ndarray:
    """Per simulation, each manager's share of 1st place (ties split)."""
    best = final.max(axis=1, keepdims=True)
    top = final >= best - 1e-9
    return top / top.sum(axis=1, keepdims=True)


def _odds(
    entrants: list[Entrant], totals: np.ndarray, gws: list[int], later: int
) -> LeagueOdds:
    S = totals.shape[0]
    share = _win_share(totals)
    p = share.mean(axis=0)
    se = share.std(axis=0) / math.sqrt(S)
    above = (totals[:, None, :] > totals[:, :, None] + 1e-9).sum(axis=2)
    tied = (np.abs(totals[:, None, :] - totals[:, :, None]) <= 1e-9).sum(axis=2) - 1
    rank = (1 + above + 0.5 * tied).mean(axis=0)
    gap = totals.max(axis=1, keepdims=True) - totals
    keys = [e.key for e in entrants]
    return LeagueOdds(
        keys=keys,
        labels={e.key: e.label for e in entrants},
        p_first={k: float(p[j]) for j, k in enumerate(keys)},
        p_first_se={k: float(se[j]) for j, k in enumerate(keys)},
        expected_rank={k: float(rank[j]) for j, k in enumerate(keys)},
        gap_to_winner={
            k: {q: float(np.percentile(gap[:, j], q)) for q in GAP_PERCENTILES}
            for j, k in enumerate(keys)
        },
        n_sims=S,
        detailed_gws=gws,
        later_gws=later,
    )


def win_odds(
    entrants: list[Entrant],
    dists: dict[int, dict[int, PointsDist]],
    world,
    gws_left: int,
    n_sims: int = 10_000,
    seed: int = 0,
) -> LeagueOdds:
    """P(finish 1st), expected rank and gap to the winner for every manager.

    `dists` must cover every detailed gameweek (the weeks in the entrants' picks);
    `gws_left` counts every gameweek still to play, detailed or not.
    """
    gws = sorted({g for e in entrants for g in e.picks})
    totals, _, gws = _run(
        entrants, dists, structure_from_world(world, gws), gws_left, n_sims, seed
    )
    return _odds(entrants, totals, gws, gws_left - len(gws))


def choose_plan(
    entrants: list[Entrant],
    me: int | str,
    candidates: dict[str, Picks],
    dists: dict[int, dict[int, PointsDist]],
    world,
    gws_left: int,
    n_sims: int = 10_000,
    seed: int = 0,
) -> PlanChoice:
    """Pick the candidate plan with the best P(1st) for manager `me`.

    Every candidate is scored on the same simulated seasons (same player scores, same rivals),
    so differences between them are not swamped by luck. The first candidate is the baseline;
    `me`'s own picks in `entrants` are replaced by each candidate in turn.
    """
    if not candidates:
        raise ValueError("no candidate plans")
    names = list(candidates)
    me_index = next(i for i, e in enumerate(entrants) if e.key == me)
    base = list(entrants)
    base[me_index] = _replace_picks(entrants[me_index], candidates[names[0]])
    gws = sorted({g for e in base for g in e.picks})
    totals, cand, gws = _run(
        base,
        dists,
        structure_from_world(world, gws),
        gws_left,
        n_sims,
        seed,
        [candidates[n] for n in names],
        me_index,
    )
    rivals = np.delete(totals, me_index, axis=1)
    best_rival = rivals.max(axis=1) if rivals.shape[1] else np.full(n_sims, -np.inf)
    n_tied_rivals = (rivals >= best_rival[:, None] - 1e-9).sum(axis=1)
    wins = np.empty_like(cand)
    for c in range(cand.shape[1]):
        mine = cand[:, c]
        tie = np.abs(mine - best_rival) <= 1e-9
        wins[:, c] = np.where(tie, 1 / (1 + n_tied_rivals), (mine > best_rival) * 1.0)
    p = wins.mean(axis=0)
    p_first = {n: float(p[i]) for i, n in enumerate(names)}

    def diff(a: int, b: int) -> tuple[float, float]:
        d = wins[:, a] - wins[:, b]
        return float(d.mean()), float(d.std() / math.sqrt(n_sims))

    vs = {n: diff(i, 0) for i, n in enumerate(names)}
    order = sorted(range(len(names)), key=lambda i: -p[i])
    margin, margin_se = diff(order[0], order[1]) if len(names) > 1 else (0.0, 0.0)
    return PlanChoice(
        best=names[order[0]],
        p_first=p_first,
        vs_baseline=vs,
        margin=margin,
        margin_se=margin_se,
        odds=_odds(base, totals, gws, gws_left - len(gws)),
    )


def _replace_picks(e: Entrant, picks: Picks) -> Entrant:
    return Entrant(
        e.key,
        e.label,
        e.total,
        picks,
        e.gws_played,
        e.past_scores,
        list(e.chips_left),
    )


# ------------------------------------------------------------------ words


def _pct(p: float) -> str:
    if p < 0.01:
        return "under 1%"
    if p > 0.99:
        return "over 99%"
    return f"about {round(p * 100)}%"


def _ordinal(n: int) -> str:
    suffix = (
        "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix}"


def describe(odds: LeagueOdds, me: int | str) -> str:
    """One or two plain sentences on your chance of winning the league and the biggest threat."""
    p = odds.p_first[me]
    others = [k for k in odds.keys if k != me]
    head = f"You win this league in {_pct(p)} of simulated seasons"
    if not others:
        return head + "."
    rank = max(1, round(odds.expected_rank[me]))
    head += f" and typically finish around {_ordinal(rank)}"
    top = max(others, key=lambda k: odds.p_first[k])
    label, q = odds.labels[top], odds.p_first[top]
    if p >= q:
        return f"{head}. You are the favourite; {label} is the closest challenger at {_pct(q).removeprefix('about ')}."
    return f"{head}. {label} is the biggest threat at {_pct(q).removeprefix('about ')}."


# ------------------------------------------------------------------ live league


def entrants_from_league(
    view,
    world,
    dists: dict[int, dict[int, PointsDist]],
    gws: list[int],
    my_picks: Picks | None = None,
) -> list[Entrant]:
    """Managers from a `league.LeagueView`, each holding last gameweek's squad (best XI by our
    expected points, captain kept if still sensible). `my_picks` overrides yours."""
    from .squad import chips_available

    pos = {pid: p.pos for pid, p in world.players.items()}
    next_gw = gws[0] if gws else 39
    out = []
    for m in [view.me, *view.rivals]:
        squad = [p for p in m.picks if p in world.players]
        picks = best_picks(squad, dists, gws, pos, m.captain)
        if m is view.me and my_picks is not None:
            picks = my_picks
        out.append(
            Entrant(
                key=m.entry,
                label=m.team_name or m.manager,
                total=m.total,
                picks=picks,
                gws_played=m.gws_played,
                chips_left=chips_available(world.chips, m.chips_used, next_gw)
                if world.chips
                else [],
            )
        )
    return out


def league_odds(
    view, world, horizon: int = 6, n_sims: int = 10_000, seed: int = 0
) -> LeagueOdds | None:
    """P(1st) for a loaded league: detailed weeks for the next `horizon` gameweeks, then the
    rest of the season from each manager's form. None when the season is over."""
    from .model import project_dist

    remaining = view.remaining_gws
    if remaining <= 0:
        return None
    first = 39 - remaining
    gws = [g for g in range(first, first + min(horizon, remaining))]
    dists = project_dist(world, gws)
    return win_odds(
        entrants_from_league(view, world, dists, gws),
        dists,
        world,
        remaining,
        n_sims,
        seed,
    )

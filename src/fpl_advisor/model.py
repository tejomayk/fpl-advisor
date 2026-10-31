"""Expected-points projection.

For each player we estimate per-90 rates (xG, xA, saves, bonus, cards), how often they start, how long
they play and how often they reach the defensive-contribution threshold. This season's matches are
weighted toward the most recent ones and blended with last season as a prior. For each fixture we
estimate both teams' expected goals from attack and defence ratings and home advantage. A player's
expected points then follow from FPL's scoring rules (see rules.py).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from . import rules as R
from .signals import Signals
from .world import Fixture, Player, World

# Tunables (checked by scripts/backtest.py; change only with before/after numbers).
HALF_LIFE_MATCHES = 8.0  # recency weighting of this season's matches
PRIOR_MINUTES_CAP = 900.0  # most a past season can count for, in minutes
PRIOR_WEIGHT = 0.6  # multiplier on past-season minutes when used as prior
FALLBACK_PRIOR_MINUTES = (
    360.0  # prior strength when only a position/price prior is available
)
TEAM_PRIOR_GAMES = 4.0  # shrink team ratings toward the league average
XG_SHARE = 0.7  # team ratings: weight on xG vs actual goals
HOME_ADV = 1.12
LEAGUE_GOALS_PER_TEAM = 1.35  # fallback before any matches are played

# Outside signals (signals.py). Odds history costs 10x credits, so these weights are judgment
# calls, not backtested: betting markets are usually sharper than stats models, but we keep
# our own estimate in the mix. Revisit once a season of live results exists.
ODDS_FIXTURE_WEIGHT = 0.6  # weight on bookmaker goal expectancy vs our team ratings
ODDS_PLAYER_WEIGHT = 0.5  # weight on bookmaker scorer/assist odds vs our player rates
# Points per unit of elite ownership above average, next gameweek only. scripts/elite_backtest.py
# (GW3-8, 2026/27): beta 2 lifted top-30 picks from 4.11 to 4.46 pts with flat correlation, but
# the elite are drawn from this season's top 50k (some leakage), so we use half of that.
ELITE_WEIGHT = 1.0
# Team news is LLM-read from articles that may be days old, so it is trusted only so far:
NEWS_MIN_WHEN_FPL_CLEAR = (
    0.5  # news alone can at most halve the chance of a player FPL lists as fit
)
NEWS_START_WEIGHT = 0.7  # blend of news "chance to start" with our own start rate


@dataclass
class Rates:
    start_rate: float
    mins_per_start: float
    p60: float  # P(60+ minutes | start)
    sub_rate: float  # P(comes on | not started)
    xg90: float
    xa90: float
    saves90: float
    bonus90: float
    yc90: float
    dc_hit: float  # P(reaches defensive-contribution threshold | start)


@dataclass
class TeamRating:
    attack: float  # expected goals scored per match vs an average team, neutral venue
    defence: (
        float  # expected goals conceded per match vs an average team, neutral venue
    )


def _poisson_sf(mu: float, k: int) -> float:
    """P(X >= k) for X ~ Poisson(mu)."""
    if mu <= 0:
        return 0.0 if k > 0 else 1.0
    term, cdf = math.exp(-mu), 0.0
    for i in range(k):
        cdf += term
        term *= mu / (i + 1)
    return max(0.0, 1.0 - cdf)


def _poisson_floor_div(mu: float, d: int, kmax: int = 30) -> float:
    """E[floor(X/d)] for X ~ Poisson(mu)."""
    if mu <= 0:
        return 0.0
    term, total = math.exp(-mu), 0.0
    for i in range(kmax):
        total += term * (i // d)
        term *= mu / (i + 1)
    return total


def _fl(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- team ratings


def team_ratings(world: World) -> tuple[dict[int, TeamRating], float]:
    """Attack/defence ratings from finished fixtures (xG blended with goals), plus league average."""
    xg_by_side: dict[tuple[int, bool], float] = {}
    for rows in world.history.values():
        for r in rows:
            key = (r["fixture"], r["was_home"])
            xg_by_side[key] = xg_by_side.get(key, 0.0) + _fl(r["expected_goals"])

    played = [f for f in world.finished_fixtures() if f.home_goals is not None]
    current = max((f.event or 0 for f in played), default=0)
    sums = {t: [0.0, 0.0, 0.0] for t in world.teams}  # weighted for, against, games
    total_goals, n = 0.0, 0
    for f in played:
        w = 0.5 ** ((current - (f.event or current)) / (HALF_LIFE_MATCHES * 1.5))
        h_xg = xg_by_side.get((f.id, True), f.home_goals)
        a_xg = xg_by_side.get((f.id, False), f.away_goals)
        h = XG_SHARE * h_xg + (1 - XG_SHARE) * f.home_goals
        a = XG_SHARE * a_xg + (1 - XG_SHARE) * f.away_goals
        # Remove home advantage so ratings are venue-neutral.
        sums[f.home][0] += w * h / HOME_ADV
        sums[f.home][1] += w * a * HOME_ADV
        sums[f.away][0] += w * a * HOME_ADV
        sums[f.away][1] += w * h / HOME_ADV
        sums[f.home][2] += w
        sums[f.away][2] += w
        total_goals += h + a
        n += 2
    avg = total_goals / n if n else LEAGUE_GOALS_PER_TEAM
    ratings = {}
    for t, (gf, ga, g) in sums.items():
        k = TEAM_PRIOR_GAMES
        ratings[t] = TeamRating(
            attack=(gf + avg * k) / (g + k), defence=(ga + avg * k) / (g + k)
        )
    return ratings, avg


def fixture_lambdas(
    f: Fixture, ratings: dict[int, TeamRating], avg: float
) -> tuple[float, float]:
    """Expected goals for (home, away) in a fixture."""
    h, a = ratings[f.home], ratings[f.away]
    lam_h = h.attack * a.defence / avg * HOME_ADV
    lam_a = a.attack * h.defence / avg / HOME_ADV
    return lam_h, lam_a


# ---------------------------------------------------------------- player rates


def _position_priors(world: World) -> dict[int, dict[str, float]]:
    """Per-90 rates for an average regular at each position (used when a player has no past)."""
    acc: dict[int, dict[str, float]] = {p: {} for p in R.POS_NAME}
    mins: dict[int, float] = {p: 0.0 for p in R.POS_NAME}
    for pid, rows in world.history.items():
        p = world.players.get(pid)
        if not p:
            continue
        for r in rows:
            if r["minutes"] <= 0:
                continue
            mins[p.pos] += r["minutes"]
            for k in (
                "expected_goals",
                "expected_assists",
                "saves",
                "bonus",
                "yellow_cards",
            ):
                acc[p.pos][k] = acc[p.pos].get(k, 0.0) + _fl(r[k])
    defaults = {
        "expected_goals": 0.1,
        "expected_assists": 0.08,
        "saves": 0,
        "bonus": 0.2,
        "yellow_cards": 0.1,
    }
    out = {}
    for p in R.POS_NAME:
        m = mins[p]
        out[p] = {
            k: (acc[p].get(k, 0.0) / m * 90 if m > 450 else v)
            for k, v in defaults.items()
        }
        if p == R.GKP and m <= 450:
            out[p]["saves"] = 2.7
    return out


def player_rates(
    player: Player, rows: list[dict], past: dict | None, pos_prior: dict[str, float]
) -> Rates:
    current = max((r["round"] for r in rows), default=0)
    w_starts = w_games = w_mins = w_sub_games = w_subs = w_s60 = w_dc = 0.0
    sums = dict.fromkeys(
        ("expected_goals", "expected_assists", "saves", "bonus", "yellow_cards"), 0.0
    )
    for r in rows:
        w = 0.5 ** ((current - r["round"]) / HALF_LIFE_MATCHES)
        w_games += w
        started = r.get("starts", 0) > 0 or r["minutes"] >= 60
        if started:
            w_starts += w
            w_mins += w * r["minutes"]
            w_s60 += w * (r["minutes"] >= 60)
            if (
                player.pos in R.DC_THRESHOLD
                and r.get("defensive_contribution", 0) >= R.DC_THRESHOLD[player.pos]
            ):
                w_dc += w
        else:
            w_sub_games += w
            w_subs += w * (r["minutes"] > 0)
        for k in sums:
            sums[k] += w * _fl(r[k])
    all_mins = sum(
        0.5 ** ((current - r["round"]) / HALF_LIFE_MATCHES) * r["minutes"] for r in rows
    )

    # Prior: last season if they played enough, else position average.
    prior_rates = dict(pos_prior)
    prior_mins = FALLBACK_PRIOR_MINUTES
    prior_start = 0.5
    prior_dc90 = None
    if past and past.get("minutes", 0) >= 450:
        pm = past["minutes"]
        prior_rates = {k: _fl(past.get(k, 0)) / pm * 90 for k in sums}
        prior_mins = min(pm, PRIOR_MINUTES_CAP) * PRIOR_WEIGHT
        prior_start = min(1.0, past.get("starts", 0) / 38)
        prior_dc90 = _fl(past.get("defensive_contribution", 0)) / pm * 90

    rate = {
        k: (sums[k] + prior_rates[k] * prior_mins / 90) / (all_mins + prior_mins) * 90
        for k in sums
    }

    k_games = 3.0
    if w_games or past:
        start_rate = (w_starts + prior_start * k_games) / (w_games + k_games)
    else:
        start_rate = 0.05  # no matches this season and no past: a fringe player
    mins_per_start = (w_mins + 80 * 2) / (w_starts + 2)
    p60 = (w_s60 + 0.85 * 2) / (w_starts + 2)
    sub_rate = (w_subs + 0.3 * 2) / (w_sub_games + 2)

    # Defensive contribution: empirical hit rate per start, shrunk to a Poisson estimate.
    dc_hit = 0.0
    if player.pos in R.DC_THRESHOLD:
        dc_count = sum(
            0.5 ** ((current - r["round"]) / HALF_LIFE_MATCHES)
            * r.get("defensive_contribution", 0)
            for r in rows
        )
        dc90 = dc_count / all_mins * 90 if all_mins > 0 else 0.0
        if prior_dc90 is not None:
            dc90 = (
                (dc_count + prior_dc90 * prior_mins / 90) / (all_mins + prior_mins) * 90
            )
        pois = _poisson_sf(dc90 * mins_per_start / 90, R.DC_THRESHOLD[player.pos])
        dc_hit = (w_dc + pois * 4) / (w_starts + 4)

    return Rates(
        start_rate=start_rate,
        mins_per_start=min(90.0, mins_per_start),
        p60=p60,
        sub_rate=sub_rate,
        xg90=rate["expected_goals"],
        xa90=rate["expected_assists"],
        saves90=rate["saves"],
        bonus90=rate["bonus"],
        yc90=rate["yellow_cards"],
        dc_hit=dc_hit,
    )


_RETURN_DATE = re.compile(r"(?:until|back)\s+(\d{1,2})\s+([A-Z][a-z]{2})")
_MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ),
        start=1,
    )
}


def return_offset(player: Player, deadlines: list[datetime]) -> int | None:
    """Index into `deadlines` of the first gameweek the player is expected back, if FPL says.

    Reads news like "Suspended until 08 Nov" or "Expected back 21 Nov". A suspension with no
    date counts as one match. Returns None when there is no usable date.
    """
    if player.status == "a" or not deadlines:
        return None
    m = _RETURN_DATE.search(player.news or "")
    if m and m.group(2) in _MONTHS:
        first = deadlines[0]
        month, day = _MONTHS[m.group(2)], int(m.group(1))
        year = first.year + (1 if month < first.month - 6 else 0)
        try:
            back = datetime(year, month, day, tzinfo=first.tzinfo)
        except ValueError:
            return None
        # Matches follow a deadline by up to ~3 days, so being back 2 days after it is in time.
        return next(
            (i for i, d in enumerate(deadlines) if d + timedelta(days=2) >= back),
            len(deadlines),
        )
    if player.status == "s":
        return 1
    return None


def availability(
    player: Player, gw_offset: int, back_offset: int | None = None
) -> float:
    """Chance the player is available, `gw_offset` gameweeks after the next one (0 = next).

    `back_offset` (from `return_offset`) is the gameweek they are expected back, when known.
    """
    if player.status == "a" and player.chance_next is None:
        return 1.0
    if player.status in ("u", "n"):
        return 0.0
    if back_offset is not None:
        if gw_offset < back_offset:
            return 0.0
        return 0.85 if gw_offset == back_offset else 1.0  # may be eased back in
    p0 = (
        player.chance_next
        if player.chance_next is not None
        else (100 if player.status == "a" else 0)
    ) / 100
    if gw_offset == 0:
        return p0
    if p0 == 0:  # out with no return date: assume a gradual return
        return min(0.8, 0.2 * gw_offset)
    return 1 - (1 - p0) * 0.5**gw_offset


# ---------------------------------------------------------------- expected points


def fixture_points(
    pos: int,
    r: Rates,
    lam_for: float,
    lam_against: float,
    team_attack: float,
    avg: float,
) -> float:
    """Expected points from one fixture, assuming the player is available."""
    att_mult = lam_for / team_attack if team_attack > 0 else 1.0
    m = r.mins_per_start
    attack = (r.xg90 * R.GOAL_PTS[pos] + r.xa90 * R.ASSIST_PTS) * att_mult
    p_cs = math.exp(-lam_against)

    start = R.APPEARANCE_PTS_60 * r.p60 + R.APPEARANCE_PTS_SUB60 * (1 - r.p60)
    start += attack * m / 90
    start += R.CLEAN_SHEET_PTS[pos] * r.p60 * p_cs
    if pos in (R.GKP, R.DEF):
        start -= _poisson_floor_div(lam_against * m / 90, R.GOALS_CONCEDED_PER_MINUS)
    if pos == R.GKP:
        start += _poisson_floor_div(
            r.saves90 * m / 90 * lam_against / avg, R.SAVES_PER_PT
        )
    start += R.DC_PTS * r.dc_hit
    bonus_mult = (
        math.sqrt(att_mult)
        if pos in (R.MID, R.FWD)
        else math.sqrt(p_cs / math.exp(-avg) + 1e-9)
    )
    start += r.bonus90 * m / 90 * bonus_mult
    start += R.YELLOW_PTS * r.yc90 * m / 90

    sub_mins = 20
    sub = R.APPEARANCE_PTS_SUB60 + attack * sub_mins / 90

    return r.start_rate * start + (1 - r.start_rate) * r.sub_rate * sub


def _involvement(r: Rates, att_mult: float) -> tuple[float, float]:
    """Expected goals and assists in one match, counting the chance of starting or coming on."""
    start_m, sub_m = r.mins_per_start / 90, 20 / 90
    share = r.start_rate * start_m + (1 - r.start_rate) * r.sub_rate * sub_m
    return r.xg90 * att_mult * share, r.xa90 * att_mult * share


def _odds_adjustment(
    pos: int, r: Rates, att_mult: float, p_goal: float | None, p_assist: float | None
) -> float:
    """Points to add so our goal/assist expectation moves part-way to the bookmakers'.

    Bookmaker "anytime" odds give P(at least one) and are void if the player doesn't play, so
    they describe the match *given he appears*. Under a Poisson model that mean is -ln(1 - p);
    we scale it by our chance he appears before comparing with our own (unconditional) figure.
    """
    eg, ea = _involvement(r, att_mult)
    p_appear = r.start_rate + (1 - r.start_rate) * r.sub_rate
    adj = 0.0
    if p_goal is not None and 0 < p_goal < 0.99:
        target = -math.log(1 - p_goal) * p_appear
        adj += ODDS_PLAYER_WEIGHT * (target - eg) * R.GOAL_PTS[pos]
    if p_assist is not None and 0 < p_assist < 0.99:
        target = -math.log(1 - p_assist) * p_appear
        adj += ODDS_PLAYER_WEIGHT * (target - ea) * R.ASSIST_PTS
    return adj


def _news_availability(player: Player, fpl: float, news: float) -> float:
    """Combine FPL's availability with LLM-read team news for the next gameweek.

    News never brings back a player FPL has suspended, ruled out or listed at 0%, and it can at
    most halve the chance of one FPL lists as fit (the article may be older than FPL's flag).
    For doubtful players, where news is most useful, it replaces FPL's figure.
    """
    news = min(1.0, max(0.0, news))
    if player.status in ("s", "u", "n") or fpl <= 0:
        return min(fpl, news)
    if fpl >= 1:
        return max(news, NEWS_MIN_WHEN_FPL_CLEAR)
    return news


def project(
    world: World,
    gameweeks: list[int],
    use_availability: bool = True,
    signals: Signals | None = None,
) -> dict[int, dict[int, float]]:
    """Expected points for every player for each gameweek in `gameweeks`: {player_id: {gw: xp}}.

    `signals` (odds, team news) only change the first gameweek: they describe the next match.
    """
    ratings, avg = team_ratings(world)
    priors = _position_priors(world)
    by_team: dict[int, list[Fixture]] = {}
    for gw in gameweeks:
        for f in world.fixtures_in(gw):
            by_team.setdefault(f.home, []).append(f)
            by_team.setdefault(f.away, []).append(f)

    deadline_of = {e.id: e.deadline for e in world.events}
    deadlines = [deadline_of[g] for g in gameweeks if g in deadline_of]
    back = {pid: return_offset(p, deadlines) for pid, p in world.players.items()}

    sig = signals or Signals()
    first = gameweeks[0] if gameweeks else None
    # Odds and news describe one gameweek; ignore them once it has passed (elite ownership is
    # about squads, not a match, so it still applies).
    live = sig.gameweek in (None, first)
    lambdas: dict[int, tuple[float, float]] = {}
    for fs in by_team.values():
        for f in fs:
            if f.id in lambdas:
                continue
            lam_h, lam_a = fixture_lambdas(f, ratings, avg)
            o = sig.fixtures.get(f.id)
            if o and f.event == first and o.lam_home > 0 and o.lam_away > 0:
                w = ODDS_FIXTURE_WEIGHT
                lam_h = w * o.lam_home + (1 - w) * lam_h
                lam_a = w * o.lam_away + (1 - w) * lam_a
            lambdas[f.id] = (lam_h, lam_a)

    out: dict[int, dict[int, float]] = {}
    avail_first: dict[int, float] = {}
    for pid, p in world.players.items():
        rows = world.history.get(pid, [])
        rates = player_rates(p, rows, world.past.get(pid), priors[p.pos])
        team_attack = ratings[p.team].attack
        ps = sig.players.get(pid) if live else None
        first_fixtures = [f for f in by_team.get(p.team, []) if f.event == first]
        xp = {gw: 0.0 for gw in gameweeks}
        for f in by_team.get(p.team, []):
            lam_h, lam_a = lambdas[f.id]
            home = f.home == p.team
            lam_for, lam_against = (lam_h, lam_a) if home else (lam_a, lam_h)
            offset = gameweeks.index(f.event)
            r = rates
            if offset == 0 and ps and ps.p_start is not None:
                news_start = min(1.0, max(0.0, ps.p_start))
                r = replace(
                    rates,
                    start_rate=NEWS_START_WEIGHT * news_start
                    + (1 - NEWS_START_WEIGHT) * rates.start_rate,
                )
            # Compare to how this team does at a neutral venue so home/away still matters.
            pts = fixture_points(p.pos, r, lam_for, lam_against, team_attack, avg)
            # Scorer odds are per match; skip them in a double gameweek rather than guess.
            if offset == 0 and ps and len(first_fixtures) == 1:
                att_mult = lam_for / team_attack if team_attack > 0 else 1.0
                pts = max(
                    0.0,
                    pts + _odds_adjustment(p.pos, r, att_mult, ps.p_goal, ps.p_assist),
                )
            if not use_availability:
                avail = 1.0
            else:
                avail = availability(p, offset, back.get(pid))
                if offset == 0 and ps and ps.p_available is not None:
                    avail = _news_availability(p, avail, ps.p_available)
            if offset == 0:
                avail_first[pid] = avail
            xp[f.event] += avail * pts
        out[pid] = xp
    if first is not None and sig.elite:
        owns = [s.elite_own or 0.0 for s in sig.players.values()]
        mean = sum(owns) / max(1, len(world.players))
        for pid, ps in sig.players.items():
            if ps.elite_own is not None and out.get(pid, {}).get(first, 0) > 0:
                nudge = ELITE_WEIGHT * (ps.elite_own - mean) * avail_first.get(pid, 0.0)
                out[pid][first] = max(0.0, out[pid][first] + nudge)
    return out

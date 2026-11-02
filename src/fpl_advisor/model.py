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
# Start model (start_model). Backtest (2026/27 GW3-8 and three past seasons): half-life 3 with a
# 1-match prior beat 8 and 3 on start Brier, correlation and MAE in all four; later gameweeks
# revert toward a slower rate so the plan doesn't churn on one benching.
START_HALF_LIFE = 3.0  # recency weighting of starts, in matches
START_PRIOR_GAMES = 1.0  # weight of last season's start rate, in matches
START_LONG_HALF_LIFE = 8.0  # the long-run start rate later gameweeks revert to
START_LONG_PRIOR_GAMES = 3.0
START_REVERSION = 0.5  # share of the gap to the long-run rate left after each gameweek
START_PRIOR_APP_WEIGHT = 0.5  # blend of starts per appearance with starts per 38
NO_PAST_START_PRIOR = 0.5  # prior for a player with no usable last season
NO_HISTORY_START = 0.05  # no matches this season and no past: a fringe player
SUB_PRIOR = 0.3  # P(comes on | not started), outfield
GK_SUB_PRIOR = 0.02  # goalkeepers almost never come off the bench
# Population priors (_position_priors): per-90 rates shrink toward players of the same position
# and price band, so a hot start or one good season is regressed (xG over-performance repeats
# little year to year).
PRICE_BANDS = (5.0, 6.0, 7.0, 8.5, 10.5)  # £m boundaries
BAND_MIN_MINUTES = 5000.0  # a thinner band borrows the position-wide prior
POP_PRIOR_MINUTES = 900.0  # weight of the population prior on last season's rates
# FPL gives ~1.38x as many assists as Opta xA in every season 2023/24-2026/27 (forwards ~2.1x),
# but scaling xA up made projections worse (bias +0.1 for MID/FWD, no gain in ranking): the
# extra assists are already offset elsewhere in the formula. Not applied.
TEAM_PRIOR_GAMES = 4.0
TEAM_COVERAGE_FULL = (
    0.6  # share of a season's outfield minutes that counts as a full PL squad
)
# A promoted side starts at the level of a typical relegated one, relative to the league average
# goals per team: relegated sides 2022/23-2024/25 scored 0.98 and conceded 2.26 a match (league
# ~1.5); promoted sides then scored 0.98 and conceded 1.87.
PROMOTED_ATTACK = 0.70
PROMOTED_DEFENCE = 1.35  # shrink team ratings toward the league average
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
    start_rate_long: float | None = None  # long-run start rate later gameweeks drift to


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


def team_priors(world: World) -> dict[int, TeamRating]:
    """Pre-season attack/defence for each club, relative to the league (1.0 = average).

    Built from last season's per-90 xG and xG-conceded of the players now at the club, so
    transfers count. A club whose squad has little Premier League history (a promoted side)
    leans toward the level of a typical relegated side instead (PROMOTED_ATTACK/DEFENCE).
    """
    acc = {t: [0.0, 0.0, 0.0, 0.0] for t in world.teams}  # xG, outfield mins, xGC, mins
    for pid, p in world.players.items():
        past = _usable_past(world, pid)
        if p.team not in acc or not past or past.get("minutes", 0) <= 0:
            continue
        a = acc[p.team]
        m = past["minutes"]
        if p.pos != R.GKP:
            a[0] += _fl(past.get("expected_goals", 0))
            a[1] += m
        if "expected_goals_conceded" in past:
            a[2] += _fl(past["expected_goals_conceded"])
            a[3] += m
    att = {t: a[0] / a[1] * 990 for t, a in acc.items() if a[1] > 0}
    dfc = {t: a[2] / a[3] * 90 for t, a in acc.items() if a[3] > 0}
    # Normalise by clubs with a full Premier League squad behind them.
    full = [t for t, a in acc.items() if a[1] >= 38 * 990 * TEAM_COVERAGE_FULL]
    att_mean = sum(att[t] for t in full) / len(full) if full else 0.0
    dfc_full = [t for t in full if t in dfc]
    dfc_mean = sum(dfc[t] for t in dfc_full) / len(dfc_full) if dfc_full else 0.0
    out = {}
    for t, a in acc.items():
        c = min(1.0, a[1] / (38 * 990 * TEAM_COVERAGE_FULL))
        ra = att[t] / att_mean if att_mean and t in att else PROMOTED_ATTACK
        rd = dfc[t] / dfc_mean if dfc_mean and t in dfc else PROMOTED_DEFENCE
        out[t] = TeamRating(
            attack=c * ra + (1 - c) * PROMOTED_ATTACK,
            defence=c * rd + (1 - c) * PROMOTED_DEFENCE,
        )
    return out


def team_ratings(world: World) -> tuple[dict[int, TeamRating], float]:
    """Attack/defence ratings from finished fixtures (xG blended with goals), plus league average.

    Each club starts from its pre-season prior (`team_priors`) worth `TEAM_PRIOR_GAMES` matches,
    so the prior fades as the season's own results build up.
    """
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
    priors = team_priors(world)
    ratings = {}
    for t, (gf, ga, g) in sums.items():
        k = TEAM_PRIOR_GAMES
        pr = priors.get(t, TeamRating(1.0, 1.0))
        ratings[t] = TeamRating(
            attack=(gf + avg * pr.attack * k) / (g + k),
            defence=(ga + avg * pr.defence * k) / (g + k),
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


def _usable_past(world: World, pid: int) -> dict | None:
    return world.past.get(pid)


_RATE_KEYS = ("expected_goals", "expected_assists", "saves", "bonus", "yellow_cards")
_RATE_DEFAULTS = {
    "expected_goals": 0.1,
    "expected_assists": 0.08,
    "saves": 0.0,
    "bonus": 0.2,
    "yellow_cards": 0.1,
}


def price_band(price: float) -> int:
    """Index of the price band a player falls in (see PRICE_BANDS)."""
    return sum(price >= b for b in PRICE_BANDS)


def _position_priors(world: World) -> dict[tuple[int, int], dict[str, float]]:
    """Per-90 rates for a typical player of each position and price band, as {(pos, band): rates}.

    Pools this season's matches and last season's totals of every player now in the game. A
    band with too few minutes borrows from its whole position. The position-wide figure is under
    band -1. These are the population priors every player's own numbers are shrunk toward.
    """
    acc: dict[tuple[int, int], dict[str, float]] = {}
    mins: dict[tuple[int, int], float] = {}

    def add(key, minutes, stats):
        mins[key] = mins.get(key, 0.0) + minutes
        a = acc.setdefault(key, {})
        for k in _RATE_KEYS:
            a[k] = a.get(k, 0.0) + _fl(stats.get(k, 0))

    for pid, p in world.players.items():
        keys = ((p.pos, -1), (p.pos, price_band(p.price)))
        for r in world.history.get(pid, []):
            if r["minutes"] > 0:
                for key in keys:
                    add(key, r["minutes"], r)
        past = _usable_past(world, pid)
        if past and past.get("minutes", 0) > 0:
            for key in keys:
                add(key, past["minutes"], past)
    out = {}
    for pos in R.POS_NAME:
        m = mins.get((pos, -1), 0.0)
        base = {
            k: (acc[(pos, -1)][k] / m * 90 if m > 450 else v)
            for k, v in _RATE_DEFAULTS.items()
        }
        if pos == R.GKP and m <= 450:
            base["saves"] = 2.7
        out[(pos, -1)] = base
        for band in range(len(PRICE_BANDS) + 1):
            mb = mins.get((pos, band), 0.0)
            if mb < BAND_MIN_MINUTES:
                out[(pos, band)] = base
                continue
            out[(pos, band)] = {k: acc[(pos, band)][k] / mb * 90 for k in _RATE_KEYS}
    return out


def population_prior(
    priors: dict[tuple[int, int], dict[str, float]], player: Player
) -> dict[str, float]:
    return priors[(player.pos, price_band(player.price))]


def _started(r: dict) -> bool:
    return r.get("starts", 0) > 0 or r["minutes"] >= 60


def _flagged(player: Player) -> bool:
    """FPL lists the player as less than fully fit (injured, doubtful, suspended, away...)."""
    return player.status != "a" or (
        player.chance_next is not None and player.chance_next < 100
    )


def absent_rows(player: Player, rows: list[dict]) -> set[int]:
    """Indexes of `rows` (in match order) to leave out of his start rate as absences.

    FPL's match history has no injury flags, so a 0-minute row is either "unused or dropped" or
    "not available". The one case we can tell apart is the run of 0-minute matches he is in now
    while FPL flags him: that is injury or suspension, and `availability` already counts it, so
    also counting it as "not picked" would double-count. Leaving out past runs that ended in a
    return (likely injuries) was tried and lost in the season replay: players coming back are
    often eased in, so their old start rate overstates the next few weeks.
    """
    if not _flagged(player):
        return set()
    out: set[int] = set()
    for i in range(len(rows) - 1, -1, -1):
        if rows[i]["minutes"] > 0:
            break
        out.add(i)
    return out


def start_prior(player: Player, past: dict | None) -> float:
    """Prior chance of starting, from last season: starts per league match.

    Starts per match he was fit for (minutes / ~85) sounds better for injured regulars but
    backtested worse on all four seasons: it rates every squad player who started when fit as
    nailed. In-season absences are handled by `absent_rows` instead.
    """
    if past and past.get("minutes", 0) >= 450:
        starts = past.get("starts", 0)
        per38 = min(1.0, starts / 38)
        # Appearances = starts + sub appearances, the latter estimated from spare minutes.
        subs = max(0.0, past["minutes"] - 80 * starts) / 25
        per_app = starts / max(1.0, starts + subs)
        w = START_PRIOR_APP_WEIGHT
        return w * per_app + (1 - w) * per38
    return NO_PAST_START_PRIOR


def start_model(
    player: Player, rows: list[dict], past: dict | None
) -> tuple[float, float, float]:
    """(next-match start rate, long-run start rate, P(comes on | not started)).

    Start rates are P(start | available): absences (`absent_rows`) are left out, because
    whether he is fit is `availability`'s job. The next-match rate follows the last few matches
    closely (half-life `START_HALF_LIFE`); the long-run rate (half-life `START_LONG_HALF_LIFE`)
    is what later gameweeks drift back to, since a lost place is often won back and a stand-in
    often loses it again. Both shrink toward last season (see `start_prior`).
    """
    sub_prior = GK_SUB_PRIOR if player.pos == R.GKP else SUB_PRIOR
    if not rows and not past:
        return NO_HISTORY_START, NO_HISTORY_START, sub_prior
    skip = absent_rows(player, rows)
    current = max((r["round"] for r in rows), default=0)
    prior = start_prior(player, past)

    def rate(half_life: float, prior_games: float) -> tuple[float, float, float]:
        games = starts = bench = subs = 0.0
        for i, r in enumerate(rows):
            if i in skip:
                continue
            w = 0.5 ** ((current - r["round"]) / half_life)
            games += w
            if _started(r):
                starts += w
            else:
                bench += w
                subs += w * (r["minutes"] > 0)
        return (starts + prior * prior_games) / (games + prior_games), bench, subs

    short, bench, subs = rate(START_HALF_LIFE, START_PRIOR_GAMES)
    long, _, _ = rate(START_LONG_HALF_LIFE, START_LONG_PRIOR_GAMES)
    return short, long, (subs + sub_prior * 2) / (bench + 2)


def start_rate_at(r: Rates, gw_offset: int) -> float:
    """Start rate `gw_offset` gameweeks after the next one: from the next-match rate toward
    the long-run rate."""
    if r.start_rate_long is None or gw_offset <= 0:
        return r.start_rate
    k = START_REVERSION**gw_offset
    return r.start_rate_long + (r.start_rate - r.start_rate_long) * k


def player_rates(
    player: Player, rows: list[dict], past: dict | None, pos_prior: dict[str, float]
) -> Rates:
    current = max((r["round"] for r in rows), default=0)
    w_starts = w_mins = w_s60 = w_dc = 0.0
    sums = dict.fromkeys(
        ("expected_goals", "expected_assists", "saves", "bonus", "yellow_cards"), 0.0
    )
    for r in rows:
        w = 0.5 ** ((current - r["round"]) / HALF_LIFE_MATCHES)
        if _started(r):
            w_starts += w
            w_mins += w * r["minutes"]
            w_s60 += w * (r["minutes"] >= 60)
            if (
                player.pos in R.DC_THRESHOLD
                and r.get("defensive_contribution", 0) >= R.DC_THRESHOLD[player.pos]
            ):
                w_dc += w
        for k in sums:
            sums[k] += w * _fl(r[k])
    all_mins = sum(
        0.5 ** ((current - r["round"]) / HALF_LIFE_MATCHES) * r["minutes"] for r in rows
    )

    # Prior: last season if they played enough, else position average.
    prior_rates = dict(pos_prior)
    prior_mins = FALLBACK_PRIOR_MINUTES
    prior_dc90 = None
    if past and past.get("minutes", 0) >= 450:
        pm = past["minutes"]
        # Last season is itself a sample: shrink it toward players of his position and price.
        prior_rates = {
            k: (_fl(past.get(k, 0)) + pos_prior[k] * POP_PRIOR_MINUTES / 90)
            / (pm + POP_PRIOR_MINUTES)
            * 90
            for k in sums
        }
        prior_mins = min(pm, PRIOR_MINUTES_CAP) * PRIOR_WEIGHT
        prior_dc90 = _fl(past.get("defensive_contribution", 0)) / pm * 90

    rate = {
        k: (sums[k] + prior_rates[k] * prior_mins / 90) / (all_mins + prior_mins) * 90
        for k in sums
    }

    start_rate, start_long, sub_rate = start_model(player, rows, past)
    mins_per_start = (w_mins + 80 * 2) / (w_starts + 2)
    p60 = (w_s60 + 0.85 * 2) / (w_starts + 2)

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
        start_rate_long=start_long,
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
        rates = player_rates(
            p, rows, _usable_past(world, pid), population_prior(priors, p)
        )
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
            if offset > 0:
                r = replace(rates, start_rate=start_rate_at(rates, offset))
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

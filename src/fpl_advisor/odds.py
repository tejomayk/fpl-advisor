"""Bookmaker odds from The Odds API (v4): goal expectancy per fixture and scorer/assist chances.

Everything here is optional. Without `ODDS_API_KEY` in the environment every function is a
no-op that returns an empty `Signals`, and the model runs on FPL data alone.

What it produces (see signals.py):
- `fixtures[fpl_fixture_id]`: expected goals for each side, found by fitting two independent
  Poisson goal counts to the bookmakers' win/draw/win and over/under prices.
- `players[pid].p_goal / p_assist`: the chance a player scores / assists in their next
  fixture, from US bookmakers' "anytime goalscorer" and "assists over 0.5" prices.

The free plan has 500 credits a month, so calls are rationed:
- Team odds (h2h + totals, region `uk`, one call for every match, ~2 credits): at most once
  every 12 hours.
- Player props (anytime scorer + assists, region `us`, one call per match, ~2 credits each):
  only for the next gameweek, at most twice per match: once 48h-6h before the deadline and once
  in the last 6h.
- The shared usage ledger (usage.py, fed from the `x-requests-*` response headers) stops all
  paid calls when the credits left fall under a safety floor or today's / this month's spend
  would pass its cap (ODDS_DAILY_BUDGET, ODDS_MONTHLY_BUDGET).
- Raw responses are cached on disk, so a restart never pays twice for the same data.
- The key has to travel as the `apiKey` query parameter, so request URLs are never logged,
  errors are reduced to their type or HTTP status, and only response bodies are cached.

Rough monthly spend: team odds 2 credits x 2/day x 30 = 120; props 10 matches x 2 credits x
2 windows = 40 per gameweek, ~4.3 gameweeks a month = ~172. Total ~290 of the 400 default.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.client import HTTPException
from pathlib import Path

from . import keys, names, usage
from .signals import FixtureSignal, Signals
from .world import Fixture, World

log = logging.getLogger(__name__)

API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "soccer_epl"
TEAM_MARKETS = ("h2h", "totals")
TEAM_REGION = "uk"
PROP_MARKETS = ("player_goal_scorer_anytime", "player_assists")
PROP_REGION = "us"  # The Odds API only carries soccer props from US bookmakers.

TEAM_EVERY = timedelta(hours=12)
PROPS_EARLY = timedelta(hours=48)  # first props window opens 48h before the deadline
PROPS_LATE = timedelta(hours=6)  # second window opens 6h before the deadline
MATCH_WINDOW = timedelta(hours=36)  # bookmaker kickoff vs FPL kickoff
STALE_TEAM_ODDS = timedelta(days=4)  # never use team odds older than this

# Yes-only scorer/assist prices still carry the bookmaker's margin. US books price two-way
# player props at roughly 108-115% in total; taking 1.12 and spreading it proportionally means
# dividing the Yes-side implied chance by 1.12. Used only when the No/Under price is missing.
YES_ONLY_OVERROUND = 1.12

MAX_GOALS = 10  # Poisson tail beyond 10 goals is negligible for realistic expectancies
DEFAULT_LINE = 2.5

Http = Callable[[str, dict], tuple[int, dict, object]]


@dataclass
class Budget:
    """Credit limits; defaults from env ODDS_MONTHLY_BUDGET (400) and ODDS_DAILY_BUDGET (40)."""

    monthly: float = field(default_factory=lambda: usage.caps_for("odds").monthly)
    floor: float = usage.ODDS_FLOOR  # stop when the API says fewer credits are left
    spacing: float = 1.5  # seconds between calls (the API returns 429 on bursts)
    daily: float = field(default_factory=lambda: usage.caps_for("odds").daily)

    def caps(self) -> usage.Caps:
        return usage.Caps(daily=self.daily, monthly=self.monthly, floor=self.floor)


# --------------------------------------------------------------------------------------------
# Maths
# --------------------------------------------------------------------------------------------


def devig(prices: list[float]) -> list[float]:
    """Decimal prices for one complete market -> probabilities summing to 1 (proportional)."""
    implied = [1 / p for p in prices if p and p > 1]
    if len(implied) != len(prices):
        return []
    total = sum(implied)
    return [x / total for x in implied]


def _pmf(lam: float) -> list[float]:
    p = [math.exp(-lam)]
    for k in range(1, MAX_GOALS + 1):
        p.append(p[-1] * lam / k)
    return p


def poisson_probs(
    lam_home: float, lam_away: float, line: float = DEFAULT_LINE
) -> tuple[float, float, float, float]:
    """P(home win), P(draw), P(away win), P(total over `line`) for independent Poisson goals.

    For a whole-number line a push refunds the bet, so "over" means over given no push.
    """
    ph, pa = _pmf(lam_home), _pmf(lam_away)
    hw = dr = aw = over = under = 0.0
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            q = x * y
            if i > j:
                hw += q
            elif i == j:
                dr += q
            else:
                aw += q
            if i + j > line:
                over += q
            elif i + j < line:
                under += q
    s = hw + dr + aw
    return hw / s, dr / s, aw / s, over / (over + under)


def fit_lambdas(
    p_home: float,
    p_draw: float,
    p_away: float,
    p_over: float | None = None,
    line: float = DEFAULT_LINE,
) -> tuple[float, float]:
    """Least-squares (lam_home, lam_away) matching the 1X2 (and, if given, over/under) chances.

    Coarse-to-fine grid search: 0.1 steps over 0.1-5.0, then 0.01, then 0.001 around the best.
    """

    def cost(lh: float, la: float) -> float:
        h, d, a, o = poisson_probs(lh, la, line)
        c = (h - p_home) ** 2 + (d - p_draw) ** 2 + (a - p_away) ** 2
        if p_over is not None:
            c += (o - p_over) ** 2
        return c

    best = (1.0, 1.0)
    lo_h, lo_a, hi_h, hi_a, step = 0.1, 0.1, 5.0, 5.0, 0.1
    for _ in range(3):
        cands = (
            (lo_h + i * step, lo_a + j * step)
            for i in range(round((hi_h - lo_h) / step) + 1)
            for j in range(round((hi_a - lo_a) / step) + 1)
        )
        best = min(cands, key=lambda c: cost(*c))
        lo_h, hi_h = max(0.05, best[0] - step), best[0] + step
        lo_a, hi_a = max(0.05, best[1] - step), best[1] + step
        step /= 10
    return round(best[0], 3), round(best[1], 3)


def team_market_probs(event: dict) -> dict | None:
    """De-vigged median 1X2 and over/under chances for one Odds API event.

    Returns {"home", "draw", "away", "over", "line"} ("over"/"line" may be None), or None if
    no bookmaker offers a complete 1X2 market.
    """
    home, away = event.get("home_team"), event.get("away_team")
    h2h: list[list[float]] = []
    totals: dict[float, list[float]] = {}
    for book in event.get("bookmakers") or []:
        for m in book.get("markets") or []:
            outs = m.get("outcomes") or []
            if m.get("key") == "h2h":
                price = {o.get("name"): o.get("price") for o in outs}
                if all(price.get(k) for k in (home, "Draw", away)):
                    p = devig([price[home], price["Draw"], price[away]])
                    if p:
                        h2h.append(p)
            elif m.get("key") == "totals":
                by_line: dict[float, dict[str, float]] = {}
                for o in outs:
                    if o.get("point") is None or not o.get("price"):
                        continue
                    by_line.setdefault(float(o["point"]), {})[o.get("name")] = o[
                        "price"
                    ]
                for pt, sides in by_line.items():
                    # Quarter lines (2.25, 2.75) split the stake; skip them.
                    if (pt * 2) % 1 or "Over" not in sides or "Under" not in sides:
                        continue
                    p = devig([sides["Over"], sides["Under"]])
                    if p:
                        totals.setdefault(pt, []).append(p[0])
    if not h2h:
        return None
    med = [statistics.median(col) for col in zip(*h2h)]
    s = sum(med)
    out = {"home": med[0] / s, "draw": med[1] / s, "away": med[2] / s}
    out["line"], out["over"] = None, None
    if totals:
        if DEFAULT_LINE in totals:
            line = DEFAULT_LINE
        else:  # most widely offered line, ties broken by closeness to 2.5
            counts = Counter({pt: len(v) for pt, v in totals.items()})
            line = min(counts, key=lambda pt: (-counts[pt], abs(pt - DEFAULT_LINE)))
        out["line"], out["over"] = line, statistics.median(totals[line])
    return out


def _yes_prob(yes: float | None, no: float | None) -> float | None:
    if not yes or yes <= 1:
        return None
    if no and no > 1:
        return devig([yes, no])[0]
    return min(1.0, (1 / yes) / YES_ONLY_OVERROUND)


def player_prop_probs(event: dict) -> dict[str, dict[str, float]]:
    """{player name: {"goal": p, "assist": p}} from one event's props (median across books)."""
    acc: dict[str, dict[str, list[float]]] = {}
    for book in event.get("bookmakers") or []:
        for m in book.get("markets") or []:
            key = m.get("key")
            sides: dict[str, dict[str, float]] = {}
            for o in m.get("outcomes") or []:
                who = o.get("description")
                if not who or not o.get("price"):
                    continue
                if key == "player_goal_scorer_anytime":
                    side = {"Yes": "y", "No": "n"}.get(o.get("name"))
                elif key == "player_assists" and o.get("point") == 0.5:
                    side = {"Over": "y", "Under": "n"}.get(o.get("name"))
                else:
                    continue
                if side:
                    sides.setdefault(who, {})[side] = o["price"]
            what = {"player_goal_scorer_anytime": "goal", "player_assists": "assist"}
            for who, s in sides.items():
                p = _yes_prob(s.get("y"), s.get("n"))
                if p is not None and key in what:
                    acc.setdefault(who, {}).setdefault(what[key], []).append(p)
    return {
        who: {k: statistics.median(v) for k, v in d.items()} for who, d in acc.items()
    }


# --------------------------------------------------------------------------------------------
# Matching to FPL
# --------------------------------------------------------------------------------------------


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _team(world: World, name: str) -> int | None:
    """names.team_id, plus an exact-name fallback: TEAM_ALIASES maps "ipswich town" to
    "ipswich", but FPL itself calls the club "Ipswich Town" in 2026/27."""
    tid = names.team_id(world, name)
    if tid is None:
        n = names.norm(name)
        tid = next(
            (t.id for t in world.teams.values() if names.norm(t.name) == n), None
        )
    return tid


def match_fixture(world: World, event: dict) -> Fixture | None:
    """The unfinished FPL fixture for an Odds API event: same home and away club, and kickoff
    within 36h of the bookmakers' start time."""
    h = _team(world, event.get("home_team") or "")
    a = _team(world, event.get("away_team") or "")
    start = _ts(event.get("commence_time"))
    if h is None or a is None or start is None:
        if h is None or a is None:
            log.info(
                "odds: unknown club in %r v %r",
                event.get("home_team"),
                event.get("away_team"),
            )
        return None
    best, gap = None, MATCH_WINDOW
    for f in world.fixtures:
        if f.finished or f.home != h or f.away != a:
            continue
        ko = _ts(f.kickoff)
        if ko is None:
            continue
        if abs(ko - start) <= gap:
            best, gap = f, abs(ko - start)
    return best


def map_players(
    world: World,
    fixture: Fixture,
    probs: dict[str, dict[str, float]],
    out: Signals,
    first: dict[int, int] | None = None,
) -> list[str]:
    """Write p_goal / p_assist for each mappable player; return the names that didn't map.

    `first` maps team id -> that team's first fixture id of the gameweek. When given, a player
    is written only from his team's first fixture, so in a double gameweek the second match
    never overwrites the first.
    """
    missed = []
    for who, d in probs.items():
        hits = {
            pid
            for t in (fixture.home, fixture.away)
            if (pid := names.player_id(world, who, team=t)) is not None
        }
        if len(hits) != 1:
            missed.append(who)
            continue
        pid = hits.pop()
        team = world.players[pid].team if pid in world.players else None
        if first is not None and first.get(team, fixture.id) != fixture.id:
            continue
        sig = out.player(pid)
        if "goal" in d:
            sig.p_goal = round(d["goal"], 4)
        if "assist" in d:
            sig.p_assist = round(d["assist"], 4)
    return missed


# --------------------------------------------------------------------------------------------
# Disk cache, ledger and HTTP
# --------------------------------------------------------------------------------------------


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _num(headers: dict, k: str) -> int | None:
    try:
        return int(float(headers[k]))
    except (KeyError, TypeError, ValueError):
        return None


def _urllib_http(key: str) -> Http:
    def get(path: str, params: dict) -> tuple[int, dict, object]:
        q = urllib.parse.urlencode({**params, "apiKey": key})
        req = urllib.request.Request(
            f"{API_BASE}{path}?{q}", headers={"User-Agent": "fpl-advisor/0.1"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, hdrs, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
            return e.code, hdrs, None
        except (OSError, HTTPException, ValueError) as e:
            # OSError covers URLError, TimeoutError and dropped connections; HTTPException
            # covers RemoteDisconnected, IncompleteRead and friends. Only the type is logged:
            # str(e) could contain the URL, and so the key. Status 0 = transport failure; the
            # caller counts its estimated credits, since the request may have been billed.
            log.warning("odds: request to %s failed: %s", path, type(e).__name__)
            return 0, {}, None

    return get


class _Client:
    def __init__(
        self,
        http: Http,
        ledger: usage.Usage,
        budget: Budget,
        sleep,
        now: datetime | None = None,
    ):
        self.http, self.ledger, self.budget, self.sleep = http, ledger, budget, sleep
        self.now = now
        self.called = False
        self.dead = False  # set on 401: the key is wrong, stop trying this run

    def get(self, path: str, params: dict, cost: int) -> object | None:
        """GET `path`; `cost` is the estimated credit cost (0 for free endpoints).

        Each attempt reserves `cost` in the shared ledger first (a refusal means no call) and
        records the real cost (`x-requests-last`) afterwards. One retry after a 429."""
        if self.dead:
            return None
        caps = self.budget.caps()
        status, body = 0, None
        for attempt in range(2):
            res = self.ledger.reserve("odds", cost, caps, now=self.now)
            if res is None:
                log.info("odds: skipping %s: spending cap reached", path)
                return None
            if self.called:
                self.sleep(self.budget.spacing * (1 + attempt))
            self.called = True
            try:
                status, headers, body = self.http(path, params)
            except Exception as e:  # noqa: BLE001 - never let a transport bug leak the URL
                status, headers, body = 0, {}, None
                log.warning("odds: request to %s failed: %s", path, type(e).__name__)
            headers = headers or {}
            last = _num(headers, "x-requests-last")
            if last is not None:
                spent = last
            elif status in (0, 200):
                # 200 without the header: the estimate. 0 = transport error or timeout: the
                # request may still have been served and billed, so count the estimate too.
                spent = cost
            else:
                spent = 0  # the API answered with an error and no charge header
            self.ledger.settle(
                res,
                spent,
                remaining=_num(headers, "x-requests-remaining"),
                caps=caps,
                now=self.now,
            )
            if status == 429 and attempt == 0:
                continue
            break
        if status == 401:
            log.warning("odds: ODDS_API_KEY was rejected (401); no more calls this run")
            self.dead = True
        if status != 200:
            log.warning("odds: %s returned HTTP %s", path, status)
            return None
        return body


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------


def _props_window(deadline: datetime, now: datetime) -> str | None:
    to_go = deadline - now
    if timedelta(0) < to_go <= PROPS_LATE:
        return "late"
    if PROPS_LATE < to_go <= PROPS_EARLY:
        return "early"
    return None


_PROP_FILE = re.compile(r"gw(\d+)_")


def _prune_props(pdir: Path, next_gw: int) -> None:
    """Delete cached prop files of gameweeks before `next_gw`; they are never read again."""
    if not pdir.exists():
        return
    for p in pdir.glob("gw*_*.json"):
        m = _PROP_FILE.match(p.name)
        if m and int(m.group(1)) < next_gw:
            try:
                p.unlink()
            except OSError:
                pass


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:80]


def fetch_signals(
    world: World,
    cache_dir: Path | str,
    now: datetime | None = None,
    budget: Budget | None = None,
    *,
    api_key: str | None = None,
    http: Http | None = None,
    sleep: Callable[[float], None] = time.sleep,
    ledger: usage.Usage | None = None,
) -> Signals:
    """Odds-based Signals for upcoming fixtures and next-gameweek players.

    Spends credits only when the cadence allows and the budget has room; otherwise answers from
    the on-disk cache. `http` and `sleep` are injectable for tests. No key -> empty Signals.
    `ledger` is the shared spend ledger (default: the one in FPL_DATA_DIR).
    """
    if api_key is None:
        keys.load_dotenv()
    key = api_key if api_key is not None else os.environ.get("ODDS_API_KEY", "")
    if not key.strip():
        return Signals()
    now = now or datetime.now(UTC)
    budget = budget or Budget()
    root = Path(cache_dir) / "odds"
    client = _Client(
        http or _urllib_http(key.strip()),
        ledger or usage.default(),
        budget,
        sleep,
        now,
    )
    out = Signals()
    fetched: list[str] = []

    # 1. Team odds: one call for every match, at most every 12h.
    team_path = root / "team_odds.json"
    team = _read(team_path)
    if (
        not team
        or now - (_ts(team.get("fetched")) or datetime.min.replace(tzinfo=UTC))
        >= TEAM_EVERY
    ):
        body = client.get(
            f"/sports/{SPORT}/odds",
            {
                "regions": TEAM_REGION,
                "markets": ",".join(TEAM_MARKETS),
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
            cost=len(TEAM_MARKETS),
        )
        if isinstance(body, list):
            team = {"fetched": now.isoformat(), "events": body}
            _write(team_path, team)
    events: list[dict] = []
    if team and now - (_ts(team.get("fetched")) or now) <= STALE_TEAM_ODDS:
        taken = _ts(team["fetched"])
        # Only pre-match prices: drop matches that had started when the odds were taken.
        events = [
            e
            for e in team.get("events") or []
            if (st := _ts(e.get("commence_time"))) and st > taken
        ]
        for e in events:
            f = match_fixture(world, e)
            probs = team_market_probs(e) if f else None
            if not f or not probs:
                continue
            lh, la = fit_lambdas(
                probs["home"],
                probs["draw"],
                probs["away"],
                probs["over"],
                probs["line"] or DEFAULT_LINE,
            )
            out.fixtures[f.id] = FixtureSignal(lh, la, source="odds")
        if out.fixtures:
            fetched.append(team["fetched"])

    # 2. Player props: next gameweek only, at most once per match per window.
    ev = world.next_event
    if ev is not None:
        deadline = (
            ev.deadline if ev.deadline.tzinfo else ev.deadline.replace(tzinfo=UTC)
        )
        window = _props_window(deadline, now)
        gw_fixtures = {f.id: f for f in world.fixtures_in(ev.id) if not f.finished}
        pdir = root / "props"
        _prune_props(pdir, ev.id)
        if window and gw_fixtures:
            if (
                not events
            ):  # no team odds to take event ids from: the events list is free
                body = client.get(
                    f"/sports/{SPORT}/events", {"dateFormat": "iso"}, cost=0
                )
                events = body if isinstance(body, list) else []
            for e in events:
                f = match_fixture(world, e)
                if not f or f.id not in gw_fixtures or not e.get("id"):
                    continue
                path = pdir / f"gw{ev.id}_{window}_{_safe(e['id'])}.json"
                if path.exists():
                    continue
                body = client.get(
                    f"/sports/{SPORT}/events/{urllib.parse.quote(e['id'])}/odds",
                    {
                        "regions": PROP_REGION,
                        "markets": ",".join(PROP_MARKETS),
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                    },
                    cost=len(PROP_MARKETS),
                )
                if isinstance(body, dict):
                    _write(
                        path,
                        {"fetched": now.isoformat(), "fixture": f.id, "event": body},
                    )
        # Build from cache: the late window wins over the early one.
        # In a double gameweek a player counts only from his team's first (earliest) fixture.
        never = datetime.max.replace(tzinfo=UTC)
        first: dict[int, int] = {}
        for f in sorted(
            gw_fixtures.values(), key=lambda f: (_ts(f.kickoff) or never, f.id)
        ):
            first.setdefault(f.home, f.id)
            first.setdefault(f.away, f.id)
        missed: list[str] = []
        for fid, f in gw_fixtures.items():
            for w in ("late", "early"):
                hits = (
                    sorted(pdir.glob(f"gw{ev.id}_{w}_*.json")) if pdir.exists() else []
                )
                got = [d for p in hits if (d := _read(p)) and d.get("fixture") == fid]
                probs = player_prop_probs(got[0].get("event") or {}) if got else {}
                if probs:  # an empty late file falls back to the early one
                    missed += map_players(world, f, probs, out, first)
                    fetched.append(got[0]["fetched"])
                    break
        if missed:
            log.info(
                "odds: %d bookmaker player names not matched to FPL: %s",
                len(missed),
                ", ".join(sorted(missed)[:20]),
            )

    if fetched:
        out.updated["odds"] = max(fetched)
    if ev is not None:
        out.updated["odds_gw"] = str(ev.id)
    return out

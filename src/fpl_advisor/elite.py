"""The elite: managers who finish near the top season after season, and what they do now.

This season's top ranks are mostly luck, so the elite are picked on *past* seasons only: the best
median overall rank across the last few completed seasons (all of them must be played). FPL has no
past-season leaderboards, so the candidate pool is this season's overall league (top `pool_size`),
and each candidate's public history gives their past ranks.

Picks for the upcoming gameweek stay hidden until its deadline, so the signal is what the elite
owned, captained, bought and sold in the latest gameweek whose deadline has passed.

`discover` is slow (one request per candidate: about 51,000 for a 50,000 pool, so roughly 1.8 hours
at the default 8 requests/second), polite (one rate limit shared by all threads, a shared cool-down
on 429/403 that honours Retry-After, a single layer of retries) and resumable (checkpoint file).
It runs about once a month in a background thread. `summarise` is cheap (two requests per elite
manager).
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .api import FPLError
from .signals import EliteSummary, Signals
from .world import World

log = logging.getLogger(__name__)

OVERALL_LEAGUE = 314  # FPL's public "Overall" classic league: every manager is in it
PAGE_SIZE = 50  # standings rows per page
CHECKPOINT_EVERY = 500  # histories between checkpoint writes
DEFAULT_MAX_RPS = (
    8.0  # requests/second across all scan threads; env ELITE_MAX_RPS overrides
)
COOLDOWN_START = 30.0  # seconds all threads pause after a 429/403 without Retry-After
COOLDOWN_MAX = 600.0
MIN_PICKS_SHARE = 0.8  # below this share of managers read, a summary is not trusted


class NotFound(Exception):
    pass


@dataclass
class EliteManager:
    id: int
    name: str  # team name from the standings
    player_name: str
    ranks: dict[str, int]  # season name -> final overall rank
    median_rank: float
    worst_rank: int


# ---------------------------------------------------------------- polite fetching


def max_rps_from_env() -> float:
    try:
        v = float(os.environ.get("ELITE_MAX_RPS", DEFAULT_MAX_RPS))
    except ValueError:
        return DEFAULT_MAX_RPS
    return v if v > 0 else DEFAULT_MAX_RPS


class Throttle:
    """One request budget shared by every scan thread, plus a shared cool-down.

    `acquire` blocks until this thread may send its next request: requests are spaced
    1/`max_rps` apart across all threads, and nobody sends during a cool-down. `cool_down` starts
    one for everybody after a 429/403: the server's Retry-After if given, else 30s, doubling (up to
    10 minutes) each time the server refuses again right after a cool-down. `ok` resets that.
    """

    def __init__(
        self,
        max_rps: float = DEFAULT_MAX_RPS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.interval = 1.0 / max_rps
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0  # earliest time the next request slot opens
        self._pause_until = 0.0
        self._cooldown = COOLDOWN_START

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self.clock()
                if now < self._pause_until:
                    wait, slot = self._pause_until - now, None
                else:
                    slot = max(now, self._next)
                    self._next = slot + self.interval
                    wait = slot - now
            if wait > 0:
                self.sleep(wait)
            if slot is None:
                continue  # cool-down over: queue for a slot
            with self._lock:
                if self.clock() >= self._pause_until:
                    return
            # A cool-down started while we waited for our slot: wait it out too.

    def cool_down(self, retry_after: float | None = None) -> float:
        """Pause every thread; returns the pause length. Refusals for requests already in
        flight during a cool-down neither extend nor escalate it (unless Retry-After says so)."""
        with self._lock:
            now = self.clock()
            if retry_after is not None:
                wait = min(max(retry_after, 0.0), COOLDOWN_MAX * 6)
            elif now < self._pause_until:
                return self._pause_until - now
            else:
                wait = self._cooldown
                self._cooldown = min(self._cooldown * 2, COOLDOWN_MAX)
            self._pause_until = max(self._pause_until, now + wait)
            # The next slot cannot open before the pause ends.
            self._next = max(self._next, self._pause_until)
            return self._pause_until - now

    def ok(self) -> None:
        with self._lock:
            self._cooldown = COOLDOWN_START


def _fetch(
    client,
    path: str,
    retries: int = 5,
    backoff: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    throttle: Throttle | None = None,
):
    """GET without caching; retry transient errors (429/5xx/network) with jittered backoff.

    This is the only retry layer: the client is asked for a single attempt per call. A 404 raises
    NotFound straight away (deleted or private team): retrying will not help. A 429 or 403 starts
    a cool-down shared by every thread using `throttle` instead of a private backoff.
    """
    for attempt in range(retries):
        if throttle:
            throttle.acquire()
        try:
            data = client.get_uncached(path, retries=1)
        except FPLError as e:
            status = getattr(e, "status", None)
            if status == 404 or str(e).startswith("Not found"):
                raise NotFound(path) from e
            if attempt == retries - 1:
                raise
            if throttle and status in (429, 403):
                wait = throttle.cool_down(getattr(e, "retry_after", None))
                log.warning(
                    "elite: %s refused (%s); all threads pause %.0fs", path, e, wait
                )
                continue
            wait = backoff * 2**attempt * (0.5 + random.random())
            log.warning("elite: %s failed (%s); retrying in %.1fs", path, e, wait)
            sleep(wait)
        else:
            if throttle:
                throttle.ok()
            return data
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- seasons


def season_name(start_year: int) -> str:
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def last_completed_seasons(client, n: int = 3) -> list[str]:
    """Names of the last `n` completed seasons, e.g. ["2023/24", "2024/25", "2025/26"]."""
    boot = client.bootstrap()
    first = min(datetime.fromisoformat(e["deadline_time"]) for e in boot["events"])
    # A season starts in August; GW1's deadline year is the season's start year.
    return [season_name(first.year - k) for k in range(n, 0, -1)]


def past_ranks(history: dict, seasons: list[str]) -> dict[str, int] | None:
    """Final overall ranks for `seasons`, or None if any season is missing (not played)."""
    by_season = {p.get("season_name"): p.get("rank") for p in history.get("past", [])}
    ranks = {s: by_season.get(s) for s in seasons}
    if any(not isinstance(r, int) or r <= 0 for r in ranks.values()):
        return None
    return ranks


def rank_managers(
    candidates: dict[int, dict], ranks: dict[int, dict[str, int] | None], top: int
) -> list[EliteManager]:
    """Best median rank first; ties broken by the better worst season, then by id."""
    out = []
    for eid, r in ranks.items():
        if not r:
            continue
        info = candidates.get(eid, {})
        vals = list(r.values())
        out.append(
            EliteManager(
                id=eid,
                name=info.get("entry_name", ""),
                player_name=info.get("player_name", ""),
                ranks=dict(r),
                median_rank=float(statistics.median(vals)),
                worst_rank=max(vals),
            )
        )
    out.sort(key=lambda m: (m.median_rank, m.worst_rank, m.id))
    return out[:top]


# ---------------------------------------------------------------- checkpoint


def _load_checkpoint(path: Path | None, seasons: list[str]) -> dict:
    empty = {"seasons": seasons, "pages": {}, "ranks": {}}
    if not path or not path.exists():
        return empty
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        log.warning("elite: unreadable checkpoint %s; starting over", path)
        return empty
    if d.get("seasons") != seasons:
        # Standings pages are still valid; past ranks were read for other seasons.
        return {"seasons": seasons, "pages": d.get("pages", {}), "ranks": {}}
    return {
        "seasons": seasons,
        "pages": d.get("pages", {}),
        "ranks": d.get("ranks", {}),
    }


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


# ---------------------------------------------------------------- discover


def discover(
    client,
    pool_size: int = 50_000,
    top: int = 100,
    seasons: int = 3,
    checkpoint_path: Path | str | None = None,
    workers: int = 8,
    season_names: list[str] | None = None,
    backoff: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    max_rps: float | None = None,
    throttle: Throttle | None = None,
) -> list[EliteManager]:
    """Scan this season's top `pool_size` managers; return the `top` with the best median past rank.

    Only managers who played every one of the last `seasons` completed seasons qualify. Progress is
    saved to `checkpoint_path` (if given) so a crash or restart carries on where it stopped.
    All `workers` share one rate limit of `max_rps` requests/second (default: env ELITE_MAX_RPS,
    else 8), so a 50,000 pool (about 51,000 requests) takes roughly 1.8 hours.
    Standings move while a gameweek is live, so paging can repeat or skip a few managers; the pool
    is then slightly under `pool_size`, which does not matter for this purpose.
    """
    names = season_names or last_completed_seasons(client, seasons)
    cp_path = Path(checkpoint_path) if checkpoint_path else None
    cp = _load_checkpoint(cp_path, names)
    lock = threading.Lock()
    if throttle is None:
        throttle = Throttle(max_rps or max_rps_from_env(), sleep=sleep)

    def save():
        if cp_path:
            with lock:
                snapshot = json.loads(json.dumps(cp))
            _write_json(cp_path, snapshot)

    def fetch(path):
        return _fetch(client, path, backoff=backoff, sleep=sleep, throttle=throttle)

    started = time.monotonic()
    try:
        # 1. The candidate pool: standings pages of the overall league.
        n_pages = math.ceil(pool_size / PAGE_SIZE)
        todo = [p for p in range(1, n_pages + 1) if str(p) not in cp["pages"]]
        log.info("elite: %d/%d standings pages to read", len(todo), n_pages)

        def page(p):
            d = fetch(f"leagues-classic/{OVERALL_LEAGUE}/standings/?page_standings={p}")
            rows = d.get("standings", {}).get("results", [])
            return p, [
                [r["entry"], r.get("entry_name", ""), r.get("player_name", "")]
                for r in rows
            ]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, fut in enumerate(
                as_completed(pool.submit(page, p) for p in todo), 1
            ):
                p, rows = fut.result()
                with lock:
                    cp["pages"][str(p)] = rows
                if i % 100 == 0:
                    log.info("elite: %d/%d pages", i, len(todo))
                    save()
        save()

        candidates: dict[int, dict] = {}
        for p in range(1, n_pages + 1):
            for eid, ename, pname in cp["pages"].get(str(p), []):
                if len(candidates) < pool_size:
                    candidates[eid] = {"entry_name": ename, "player_name": pname}

        # 2. Each candidate's past seasons.
        todo_ids = [eid for eid in candidates if str(eid) not in cp["ranks"]]
        log.info(
            "elite: %d candidates, %d histories to read", len(candidates), len(todo_ids)
        )

        def hist(eid):
            try:
                return eid, past_ranks(fetch(f"entry/{eid}/history/"), names)
            except NotFound:
                return eid, None

        hist_started = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [pool.submit(hist, eid) for eid in todo_ids]
            for i, fut in enumerate(as_completed(futures), 1):
                eid, r = fut.result()
                with lock:
                    cp["ranks"][str(eid)] = r
                if i % CHECKPOINT_EVERY == 0:
                    rate = i / max(1e-9, time.monotonic() - hist_started)
                    log.info("elite: %d/%d histories (%.0f/s)", i, len(todo_ids), rate)
                    save()
        finally:
            # On failure, stop queued requests at once; the checkpoint keeps what finished.
            pool.shutdown(wait=True, cancel_futures=True)
    finally:
        save()

    ranks = {
        eid: cp["ranks"].get(str(eid)) for eid in candidates if str(eid) in cp["ranks"]
    }
    qualified = sum(1 for r in ranks.values() if r)
    chosen = rank_managers(candidates, ranks, top)
    log.info(
        "elite: %d of %d candidates played all of %s; kept %d in %.0fs",
        qualified,
        len(candidates),
        ", ".join(names),
        len(chosen),
        time.monotonic() - started,
    )
    return chosen


def save_managers(
    path: Path | str, managers: list[EliteManager], seasons: list[str], pool_size: int
) -> None:
    _write_json(
        Path(path),
        {
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "seasons": seasons,
            "pool_size": pool_size,
            "managers": [asdict(m) for m in managers],
        },
    )


def load_managers(path: Path | str) -> tuple[datetime, list[EliteManager]] | None:
    try:
        d = json.loads(Path(path).read_text())
        created = datetime.fromisoformat(d["created"])
        return created, [EliteManager(**m) for m in d["managers"]]
    except (OSError, ValueError, TypeError, KeyError):
        return None


def load_or_discover(
    client,
    path: Path | str,
    max_age_days: float = 30,
    pool_size: int = 50_000,
    top: int = 100,
    seasons: int = 3,
    checkpoint_path: Path | str | None = None,
    workers: int = 8,
    now: datetime | None = None,
    **kw,
) -> list[EliteManager]:
    """Reuse the saved elite list if it is younger than `max_age_days`; otherwise rediscover.

    A rediscovery that fails, finds nobody, or finds under half as many managers as the saved list
    never replaces it: the old file is kept (and returned) and the attempt time is recorded, so the
    caller can back off with `discovery_backoff_active` instead of rescanning straight away.
    """
    now = now or datetime.now(UTC)
    saved = load_managers(path)
    if saved and now - saved[0] < timedelta(days=max_age_days) and saved[1]:
        return saved[1][:top]
    if checkpoint_path is None:
        checkpoint_path = Path(path).with_suffix(".checkpoint.json")
    try:
        managers = discover(
            client,
            pool_size=pool_size,
            top=top,
            seasons=seasons,
            checkpoint_path=checkpoint_path,
            workers=workers,
            **kw,
        )
    except Exception:
        _record_attempt(path, now)  # the checkpoint stays, so the retry resumes
        raise
    old = saved[1] if saved else []
    # The checkpoint has served its purpose; the next rediscovery should see fresh standings.
    Path(checkpoint_path).unlink(missing_ok=True)
    if not managers or len(managers) < len(old) / 2:
        log.warning(
            "elite: rediscovery found %d managers (saved list has %d); keeping the saved list",
            len(managers),
            len(old),
        )
        _record_attempt(path, now)
        return old[:top]
    names = list(managers[0].ranks)
    save_managers(path, managers, names, pool_size)
    attempt_path(path).unlink(missing_ok=True)
    return managers


def attempt_path(path: Path | str) -> Path:
    """Where the time of the last failed or empty discovery is kept."""
    return Path(path).with_suffix(".attempt.json")


def _record_attempt(path: Path | str, now: datetime) -> None:
    try:
        _write_json(attempt_path(path), {"at": now.isoformat(timespec="seconds")})
    except OSError:
        log.exception("elite: could not record the discovery attempt")


def discovery_backoff_active(
    path: Path | str, now: datetime | None = None, hours: float = 24
) -> bool:
    """True within `hours` after a discovery that failed or found too few managers."""
    now = now or datetime.now(UTC)
    try:
        at = datetime.fromisoformat(json.loads(attempt_path(path).read_text())["at"])
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return timedelta(0) <= now - at < timedelta(hours=hours)


# ---------------------------------------------------------------- summarise


def latest_started_gameweek(world: World, now: datetime | None = None) -> int | None:
    """The latest gameweek whose deadline has passed: its picks are public."""
    now = now or datetime.now(UTC)
    passed = [e.id for e in world.events if e.deadline <= now]
    return max(passed) if passed else None


def read_picks(
    client, managers: list[EliteManager], gameweek: int, workers: int = 8
) -> dict[int, dict]:
    """Picks of each manager for `gameweek`; managers whose picks 404 are left out."""

    def one(m):
        try:
            return m.id, client.picks(m.id, gameweek)
        except FPLError as e:
            log.info("elite: no picks for %d in GW%d (%s)", m.id, gameweek, e)
            return m.id, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return {eid: p for eid, p in pool.map(one, managers) if p}


def ownership(picks: dict[int, dict]) -> tuple[dict[int, float], dict[int, float]]:
    """(own share, captain share) per player across the managers in `picks`."""
    n = len(picks)
    own: dict[int, int] = {}
    cap: dict[int, int] = {}
    for p in picks.values():
        for row in p.get("picks", []):
            own[row["element"]] = own.get(row["element"], 0) + 1
            if row.get("is_captain"):
                cap[row["element"]] = cap.get(row["element"], 0) + 1
    if not n:
        return {}, {}
    return {k: v / n for k, v in own.items()}, {k: v / n for k, v in cap.items()}


def summarise(
    client,
    managers: list[EliteManager],
    world: World,
    gameweek: int | None = None,
    now: datetime | None = None,
    workers: int = 8,
) -> Signals:
    """What the elite owned, captained, bought and sold in the latest gameweek with a passed deadline.

    Transfers come from each manager's public transfer list filtered to the gameweek (one request,
    and correct across a Free Hit the week before, which a picks diff would not be). A Free Hit
    week's swaps revert, so they are not counted as buys or sells; its picks still count as owned.
    A player bought and sold in the same week (wildcard churn) counts as neither.

    If picks could be read for fewer than 80% of `managers` (FPL answers 503 for a while after
    each deadline), an empty `Signals()` is returned, with no `elite` and no `updated` stamp, so
    callers do not cache a half-empty week and try again later.
    """
    gw = gameweek or latest_started_gameweek(world, now)
    sig = Signals()
    if gw is None or not managers:
        return sig
    picks = read_picks(client, managers, gw, workers)
    if len(picks) < MIN_PICKS_SHARE * len(managers):
        log.warning(
            "elite: picks read for only %d of %d managers in GW%d; try again later",
            len(picks),
            len(managers),
            gw,
        )
        return Signals()
    own, cap = ownership(picks)

    ins: dict[int, int] = {}
    outs: dict[int, int] = {}

    def transfers(eid):
        if (picks[eid].get("active_chip") or "") == "freehit":
            return eid, []
        try:
            return eid, client.transfers(eid)
        except FPLError as e:
            log.info("elite: no transfers for %d (%s)", eid, e)
            return eid, []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _, rows in pool.map(transfers, list(picks)):
            week = [t for t in rows if t.get("event") == gw]
            bought = {t["element_in"] for t in week}
            sold = {t["element_out"] for t in week}
            for pid in bought - sold:
                ins[pid] = ins.get(pid, 0) + 1
            for pid in sold - bought:
                outs[pid] = outs.get(pid, 0) + 1

    for pid in set(world.players) | set(own) | set(ins) | set(outs):
        p = sig.player(pid)
        p.elite_own = round(own.get(pid, 0.0), 4)
        p.elite_captain = round(cap.get(pid, 0.0), 4)
        p.elite_in = ins.get(pid, 0)
        p.elite_out = outs.get(pid, 0)

    read = [m for m in managers if m.id in picks]
    sig.elite = EliteSummary(
        managers=len(read),
        gameweek=gw,
        seasons=list(managers[0].ranks),
        median_rank=(
            float(statistics.median(m.median_rank for m in read)) if read else None
        ),
    )
    sig.updated["elite"] = datetime.now(UTC).isoformat(timespec="seconds")
    return sig

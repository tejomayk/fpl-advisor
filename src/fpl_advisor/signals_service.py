"""Keeps the outside signals (odds, team news, elite managers, creator room) fresh and shared.

Each source caches itself and decides when it is due, so `refresh` is cheap to call often. The
merged result is saved to `<data dir>/signals.json` so the web server and the CLI share it.
Finding the elite managers scans tens of thousands of public FPL entries (about 2 hours at a
polite 8 requests a second),
so it runs in its own thread and is only repeated monthly.

Paid calls (odds, team news, creator room) happen only here, from the web server's scheduled
loop or a CLI run, never from a visitor's request: the web app may only `request_refresh()`,
which wakes that loop. Every paid call is capped by the shared usage ledger (usage.py) at FPL_USAGE_PATH
(default ~/.fpl-advisor/usage/usage.json; /data/usage/usage.json in Docker).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from . import creators, elite, news, odds, usage
from .signals import Signals
from .world import World

log = logging.getLogger("fpl_advisor.signals")

ELITE_TOP = 100
ELITE_REDISCOVER_DAYS = 30


class SignalsHub:
    def __init__(self, client, data_dir: Path | str):
        self.client = client
        self.dir = Path(data_dir) / "signals"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "signals.json"
        self._signals = Signals.load(self.path)
        self._lock = threading.Lock()
        self._discovering = threading.Event()
        self.usage = usage.default()  # FPL_USAGE_PATH: one ledger for every process
        self.wake = threading.Event()  # set by request_refresh(); read by the web loop
        self._last: dict[str, Signals] = {}  # last good result per source

    def current(self) -> Signals:
        return self._signals

    @property
    def version(self) -> str:
        """Changes whenever any source updates; part of the web advice cache key."""
        return "|".join(f"{k}={v}" for k, v in sorted(self._signals.updated.items()))

    def refresh(
        self, world: World, now: datetime | None = None, discover: bool = True
    ) -> Signals:
        """Ask every source for its latest signals and merge them. Never raises."""
        now = now or datetime.now(UTC)
        merged = Signals()
        for name, fetch in (
            (
                "odds",
                lambda: odds.fetch_signals(world, self.dir, now=now, ledger=self.usage),
            ),
            (
                "news",
                lambda: news.fetch_signals(world, self.dir, now=now, ledger=self.usage),
            ),
            ("elite", lambda: self._elite(world, now, discover)),
            (
                "creators",
                lambda: creators.fetch_signals(
                    world, self.dir, now=now, client=self.client, ledger=self.usage
                ),
            ),
        ):
            try:
                self._last[name] = fetch()
            except Exception:
                log.exception("Signal source %s failed; reusing its last result", name)
            merged.merge(self._last.get(name, Signals()))
        merged.gameweek = world.next_event.id if world.next_event else None
        with self._lock:
            self._signals = merged
            merged.save(self.path)
        return merged

    def request_refresh(self) -> None:
        """Ask the scheduled loop to refresh soon (used right after a deadline).

        Never refreshes in the caller's thread: a web request must not start paid calls."""
        self.wake.set()

    # ---------------------------------------------------------------- elite

    def _elite(self, world: World, now: datetime, discover: bool) -> Signals:
        saved = elite.load_managers(self._elite_path)
        stale = (
            not saved or (now - saved[0]).days >= ELITE_REDISCOVER_DAYS or not saved[1]
        )
        if (
            stale
            and discover
            and not elite.discovery_backoff_active(self._elite_path, now)
        ):
            self._start_discovery()
        if not saved or not saved[1]:
            return Signals()
        gw = elite.latest_started_gameweek(world, now)
        if gw is None:
            return Signals()
        cache = self.dir / f"elite_gw{gw}.json"
        if cache.exists():
            return Signals.load(cache)
        sig = elite.summarise(
            self.client, saved[1][:ELITE_TOP], world, gameweek=gw, now=now
        )
        if (
            sig.elite and sig.elite.managers
        ):  # a partial read (FPL updating) is retried next time
            sig.save(cache)
        return sig

    @property
    def _elite_path(self) -> Path:
        return self.dir / "elite_managers.json"

    def _start_discovery(self) -> None:
        if self._discovering.is_set():
            return
        self._discovering.set()

        def run():
            try:
                elite.load_or_discover(
                    self.client,
                    self._elite_path,
                    max_age_days=ELITE_REDISCOVER_DAYS,
                    pool_size=int(os.environ.get("ELITE_POOL_SIZE", "50000")),
                    top=ELITE_TOP,
                )
                log.info("Elite managers ready")
            except Exception:
                log.exception("Finding the elite managers failed; will retry later")
            finally:
                self._discovering.clear()

        threading.Thread(target=run, daemon=True, name="elite-discovery").start()

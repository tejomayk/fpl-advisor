"""Scheduled jobs for hosting without an always-on server (Vercel + GitHub Actions).

- `refresh`: load the FPL world, refresh the outside signals (odds, team news, elite picks,
  creator room) with every spend cap enforced, and write the web snapshot the website reads.
  `fpl-advisor refresh`, every 30 minutes from .github/workflows/refresh.yml.
- `discover_elite`: the monthly scan for consistently good managers (tens of thousands of public
  FPL requests, about 2 hours). `fpl-advisor discover-elite`, from .github/workflows/elite.yml.

Both work in the store's job folder (see store.py): with a LocalStore (no Blob token) that is
FPL_DATA_DIR itself, exactly as the always-on server uses it; with Blob it is a copy of
`job/state.tar.gz`. Only these jobs write to Blob and only one runs at a time (GitHub Actions
concurrency group), so the spend ledger has a single writer.

The website never runs these: visitors still cannot cause a paid call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import elite, store, usage
from .signals import Signals
from .signals_service import ELITE_REDISCOVER_DAYS, ELITE_TOP, SignalsHub
from .world import World, load_world

log = logging.getLogger("fpl_advisor.jobs")

SNAPSHOT_VERSION = 1
# Rewrite the web snapshot at least this often even if nothing material changed (prices'
# transfer counts drift all day). Uploads are scarce on Blob's free plan (2,000 a month).
SNAPSHOT_MAX_AGE = timedelta(hours=3)
# Player fields that change constantly and alone don't justify a new snapshot.
VOLATILE_PLAYER_FIELDS = (
    "transfers_in_event",
    "transfers_out_event",
    "selected_by",
    "price_projection",
)
DISCOVERY_PUSH_SECONDS = 30 * 60  # save the elite scan's checkpoint this often


class JobError(RuntimeError):
    """The job could not do its work safely (e.g. the store could not be read)."""


# ---------------------------------------------------------------- web snapshot


def material_digest(world_dict: dict, signals_dict: dict) -> str:
    """Hash of what matters for advice: everything except fast-drifting transfer counts."""
    players = [
        {k: v for k, v in p.items() if k not in VOLATILE_PLAYER_FIELDS}
        for p in world_dict["players"]
    ]
    blob = json.dumps(
        [{**world_dict, "players": players}, signals_dict],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def make_snapshot(world: World, signals: Signals, now: datetime) -> dict:
    wd = world.to_dict()
    sd = json.loads(signals.to_json())
    return {
        "version": SNAPSHOT_VERSION,
        "saved_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "gameweek": world.next_event.id if world.next_event else None,
        "digest": material_digest(wd, sd),
        "world": wd,
        "signals": sd,
    }


@dataclass
class Snapshot:
    world: World
    signals: Signals
    saved_at: datetime
    gameweek: int | None


def read_snapshot(st: store.Store, fresh: bool = False) -> Snapshot | None:
    """The web snapshot, or None if there is none or it can't be parsed."""
    d = st.get_json(store.WEB_SNAPSHOT_KEY, fresh=fresh)
    if not d:
        return None
    return parse_snapshot(d)


def parse_snapshot(d: dict) -> Snapshot | None:
    try:
        if d.get("version") != SNAPSHOT_VERSION:
            return None
        return Snapshot(
            world=World.from_dict(d["world"]),
            signals=Signals.from_json(json.dumps(d["signals"])),
            saved_at=datetime.fromisoformat(d["saved_at"]),
            gameweek=d.get("gameweek"),
        )
    except (KeyError, TypeError, ValueError):
        log.exception("web snapshot is unreadable; ignoring it")
        return None


def snapshot_due(prev: dict | None, new: dict, now: datetime) -> bool:
    if not prev or prev.get("version") != SNAPSHOT_VERSION:
        return True
    if prev.get("digest") != new["digest"] or prev.get("gameweek") != new["gameweek"]:
        return True
    try:
        saved = datetime.fromisoformat(prev["saved_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return now - saved >= SNAPSHOT_MAX_AGE


# ---------------------------------------------------------------- the job folder


def _use_job_ledger(js: store.JobState) -> None:
    """With Blob, the spend ledger must be the one inside the job folder (the copy that is
    uploaded again), whatever FPL_USAGE_PATH says."""
    if not js.remote:
        return
    path = js.root / "usage" / "usage.json"
    mirror = js.root / "usage.mirror" / "usage.json"
    for var, want in (("FPL_USAGE_PATH", path), ("FPL_USAGE_MIRROR", mirror)):
        if os.environ.get(var) and Path(os.environ[var]) != want:
            log.warning(
                "%s is ignored with Vercel Blob: the ledger lives in the store", var
            )
        os.environ[var] = str(want)


@contextmanager
def job_folder(st: store.Store, workdir: Path | str | None = None):
    """The job folder, filled from the store, with the ledger pointed at it. Refuses to start
    (JobError) if the store can't be read: a run that can't see the ledger must not spend."""
    with store.job_state(st, workdir) as js:
        try:
            js.pull()
        except store.StoreError as e:
            raise JobError(f"could not read the job state from the store: {e}") from e
        _use_job_ledger(js)
        yield js


def push_job_state(js: store.JobState) -> None:
    try:
        if js.push():
            log.info("job state uploaded")
    except store.StoreError as e:
        # The ledger copy that counts spend from this run did not reach the store. Say so
        # loudly: the next run would otherwise start from the older copy.
        usage.owner_alert(
            "FPL advisor: spend ledger upload failed",
            "The refresh job could not save its spend ledger to Vercel Blob. Check the "
            "GitHub Actions log and each provider's dashboard.",
        )
        raise JobError(f"could not save the job state to the store: {e}") from e


# ---------------------------------------------------------------- refresh


@dataclass
class RefreshResult:
    gameweek: int | None
    sources: dict[str, str]
    snapshot_written: bool
    state_written: bool
    store: str


def refresh(
    client,
    st: store.Store | None = None,
    now: datetime | None = None,
    workdir: Path | str | None = None,
    force_snapshot: bool = False,
) -> RefreshResult:
    st = st or store.default()
    now = now or datetime.now(UTC)
    with job_folder(st, workdir) as js:
        world = load_world(client)  # no paid call happens before this succeeds
        hub = SignalsHub(client, js.root)
        try:
            signals = hub.refresh(world, now=now, discover=False)
        finally:
            push_job_state(js)  # the ledger first, even if a source blew up
        state_written = bool(js.pushed)

    snap = make_snapshot(world, signals, now)
    try:
        prev = st.get_json(store.WEB_SNAPSHOT_KEY, fresh=True)
    except (store.StoreError, ValueError):
        prev = None
    written = False
    if force_snapshot or snapshot_due(prev, snap, now):
        st.put_json(store.WEB_SNAPSHOT_KEY, snap)
        written = True
    return RefreshResult(
        gameweek=snap["gameweek"],
        sources=dict(signals.updated),
        snapshot_written=written,
        state_written=state_written,
        store=repr(st),
    )


# ---------------------------------------------------------------- elite discovery


def discover_elite(
    client,
    st: store.Store | None = None,
    workdir: Path | str | None = None,
    force: bool = False,
    push_every: float = DISCOVERY_PUSH_SECONDS,
) -> int:
    """Find the elite managers (monthly). Returns how many are saved.

    The scan checkpoints as it goes; the checkpoint is uploaded every `push_every` seconds and
    on exit (also on SIGTERM), so a run that hits the job time limit resumes next time."""
    st = st or store.default()
    with job_folder(st, workdir) as js:
        path = js.root / "signals" / "elite_managers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = threading.Lock()
        stop = threading.Event()

        def push_now():
            with lock:
                push_job_state(js)

        def pusher():
            while not stop.wait(push_every):
                try:
                    push_now()
                except JobError:
                    log.exception("checkpoint upload failed; will try again")

        def on_term(signum, _frame):
            raise SystemExit(128 + signum)

        old = signal.signal(signal.SIGTERM, on_term)
        t = threading.Thread(target=pusher, daemon=True, name="elite-push")
        if js.remote:
            t.start()
        try:
            managers = elite.load_or_discover(
                client,
                path,
                max_age_days=0 if force else ELITE_REDISCOVER_DAYS,
                pool_size=int(os.environ.get("ELITE_POOL_SIZE", "50000")),
                top=ELITE_TOP,
            )
        finally:
            stop.set()
            signal.signal(signal.SIGTERM, old)
            push_now()
        return len(managers)

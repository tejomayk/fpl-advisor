"""Scheduled jobs for hosting without an always-on server (Vercel + GitHub Actions).

- `refresh`: load the FPL world, refresh the outside signals (odds, team news, elite picks,
  creator room) with every spend cap enforced, and write the web snapshot the website reads.
  `fpl-advisor refresh`, every 30 minutes from .github/workflows/refresh.yml.
- `discover_elite`: the monthly scan for consistently good managers (tens of thousands of public
  FPL requests, about 2 hours). `fpl-advisor discover-elite`, from .github/workflows/elite.yml.

Both work in FPL_DATA_DIR (a LocalStore), exactly as the always-on server uses it. In GitHub
Actions that folder, spend ledger included, is kept private in the Actions cache between runs,
and only one job runs at a time (one concurrency group), so the ledger has a single writer. The
refresh workflow then publishes `web/snapshot.json.gz` as a GitHub release asset for the website.

The website never runs these: visitors still cannot cause a paid call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
# transfer counts drift all day). Each new snapshot is one release-asset upload.
SNAPSHOT_MAX_AGE = timedelta(hours=3)
# Player fields that change constantly and alone don't justify a new snapshot.
VOLATILE_PLAYER_FIELDS = (
    "transfers_in_event",
    "transfers_out_event",
    "selected_by",
    "price_projection",
)


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


def job_folder(st: store.Store) -> Path:
    """The folder the signal sources and the spend ledger work in: the LocalStore's root.

    Refuses (JobError) a store the job can't write to, and, in GitHub Actions, a spend ledger
    outside that folder: only the folder is saved between runs, so a ledger elsewhere would
    start from zero every run and the spending caps would not hold."""
    if not isinstance(st, store.LocalStore):
        raise JobError(f"the scheduled jobs need a local data folder, not {st!r}")
    root = st.root.absolute()
    if os.environ.get("GITHUB_ACTIONS") == "true":
        ledger = usage.ledger_path()
        mirror = usage.mirror_path(ledger)
        for p in (ledger, mirror):
            if not p.is_relative_to(root):
                raise JobError(
                    "the spend ledger must live inside FPL_DATA_DIR in GitHub Actions "
                    "(set FPL_USAGE_PATH and FPL_USAGE_MIRROR under it), or it is lost "
                    "after every run"
                )
    return st.root


# ---------------------------------------------------------------- refresh


@dataclass
class RefreshResult:
    gameweek: int | None
    sources: dict[str, str]
    snapshot_written: bool
    store: str


def refresh(
    client,
    st: store.Store | None = None,
    now: datetime | None = None,
    force_snapshot: bool = False,
) -> RefreshResult:
    st = st or store.default()
    now = now or datetime.now(UTC)
    root = job_folder(st)
    world = load_world(client)  # no paid call happens before this succeeds
    signals = SignalsHub(client, root).refresh(world, now=now, discover=False)

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
        store=repr(st),
    )


# ---------------------------------------------------------------- elite discovery


def discover_elite(client, st: store.Store | None = None, force: bool = False) -> int:
    """Find the elite managers (monthly). Returns how many are saved.

    The scan checkpoints to the job folder as it goes, and the workflow saves that folder even
    when a run is cut off by its time limit, so the next run resumes."""
    st = st or store.default()
    path = job_folder(st) / "signals" / "elite_managers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    managers = elite.load_or_discover(
        client,
        path,
        max_age_days=0 if force else ELITE_REDISCOVER_DAYS,
        pool_size=int(os.environ.get("ELITE_POOL_SIZE", "50000")),
        top=ELITE_TOP,
    )
    return len(managers)

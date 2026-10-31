"""Hosted alert sign-up API and the background scheduler that sends reminders.

Routes: POST/DELETE /api/alerts, POST /api/alerts/status (tokens never go in URLs).
Environment: NTFY_SERVER, NTFY_TOKEN, PUBLIC_URL, MAX_SUBSCRIPTIONS, ALERT_RUN_MINUTE,
DISABLE_ALERT_SCHEDULER, FPL_DATA_DIR. Needs a single running instance (see docs/deploy.md).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .advise import build_advice
from .api import FPLError
from .cli import collect_alerts
from .config import Config
from .net import client_ip
from .notify import Notification, NotifyError, NtfyNotifier
from .subscriptions import DbAlertState, SubscriptionStore, check_data_dir

log = logging.getLogger("fpl_advisor.alerts")

router = APIRouter()

REMINDER_HOURS = [24, 2]
CREATE_LIMIT = (5, 3600)  # creations per IP per window (seconds)
MAX_FAILURES = 48
FIRST_RUN_DELAY = 60.0
POLITE_DELAY = 0.2
MAX_PER_TEAM = 3  # active subscriptions per FPL team id
FULL_BUILD_EVERY_HOURS = 6  # price changes and news can matter between deadlines
RUN_MINUTE = 7  # scheduler runs at this minute of every hour
MAX_TRACKED_IPS = 50_000
_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def ntfy_server() -> str:
    return os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")


def max_subscriptions() -> int:
    try:
        return int(os.environ.get("MAX_SUBSCRIPTIONS", "300"))
    except ValueError:
        return 300


def ntfy_token() -> str | None:
    return os.environ.get("NTFY_TOKEN") or None


class _Limiter:
    """Sliding-window limiter. Memory is bounded: expired entries are dropped."""

    def __init__(self, limit: int, window: int, max_ips: int = MAX_TRACKED_IPS):
        self.limit, self.window, self.max_ips = limit, window, max_ips
        self._hits: dict[str, deque] = {}
        self._last_sweep = 0.0
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        for ip in list(self._hits):
            q = self._hits[ip]
            while q and now - q[0] > self.window:
                q.popleft()
            if not q:
                del self._hits[ip]
        self._last_sweep = now

    def allow(self, ip: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            if now - self._last_sweep > 60 or len(self._hits) >= self.max_ips:
                self._sweep(now)
            q = self._hits.get(ip)
            if q is not None:
                while q and now - q[0] > self.window:
                    q.popleft()
                if not q:
                    del self._hits[ip]
                    q = None
            if q is not None and len(q) >= self.limit:
                return False
            if q is None:
                if len(self._hits) >= self.max_ips:
                    return False  # table full of live entries: refuse rather than grow
                q = self._hits[ip] = deque()
            q.append(now)
            return True


_init_lock = threading.Lock()


def get_store(app: FastAPI) -> SubscriptionStore:
    """The app's store, created lazily so FPL_DATA_DIR is read at first use."""
    with _init_lock:
        if getattr(app.state, "alert_store", None) is None:
            app.state.alert_store = SubscriptionStore()
        return app.state.alert_store


def _limiter(app: FastAPI) -> _Limiter:
    with _init_lock:
        if getattr(app.state, "alert_limiter", None) is None:
            app.state.alert_limiter = _Limiter(*CREATE_LIMIT)
        return app.state.alert_limiter


class CreateBody(BaseModel):
    team: int = Field(ge=1, le=99_999_999)
    league: int | None = Field(default=None, ge=1, le=99_999_999)


class DeleteBody(BaseModel):
    topic: str
    manage_token: str


class StatusBody(BaseModel):
    topic: str = Field(max_length=100)
    manage_token: str = Field(max_length=200)


@router.post("/api/alerts", status_code=201)
def create_alert(body: CreateBody, request: Request):
    app = request.app
    store = get_store(app)
    # Cheap capacity checks first, so a refused request doesn't use up the visitor's quota.
    if store.count() >= max_subscriptions():
        raise HTTPException(
            503,
            "We're at capacity for alert sign-ups right now. "
            "Please check back later, or run your own copy (see the README).",
        )
    if store.count_for_team(body.team) >= MAX_PER_TEAM:
        raise HTTPException(
            429,
            f"That team already has {MAX_PER_TEAM} active alert subscriptions. "
            "Remove one you no longer use, or reuse the one you have.",
        )
    if not _limiter(app).allow(client_ip(request)):
        raise HTTPException(
            429, "Too many sign-ups from your network. Please try again in an hour."
        )
    try:
        entry = app.state.client.entry(body.team)
    except FPLError as e:
        msg = str(e)
        if "Not found" in msg:
            raise HTTPException(
                404,
                "We couldn't find that team. Check the ID (it's the number in your FPL link).",
            ) from e
        raise HTTPException(
            503, "We couldn't reach FPL just now. Please try again shortly."
        ) from e
    team_name = (entry or {}).get("name") or f"team {body.team}"

    topic = "fpl-" + secrets.token_urlsafe(15).lower()
    token = secrets.token_urlsafe(24)
    store.create(topic, token, body.team, body.league, REMINDER_HOURS)
    server = ntfy_server()

    try:
        NtfyNotifier(server, topic, ntfy_token()).send(
            Notification(
                title=f"You're set: FPL reminders for {team_name}",
                body=(
                    "You'll get a reminder 24 hours and 2 hours before each "
                    "gameweek deadline, plus a nudge when a price change affects "
                    "your plan. Tap to open your advice."
                ),
                tags=["white_check_mark"],
                click=_click_url(body.team, body.league),
            )
        )
    except Exception:
        log.warning("Welcome notification failed for a new subscription", exc_info=True)

    return {
        "topic": topic,
        "subscribe_url": f"{server}/{topic}",
        "manage_token": token,
        "reminder_hours": REMINDER_HOURS,
    }


@router.delete("/api/alerts", status_code=204)
def delete_alert(body: DeleteBody, request: Request):
    store = get_store(request.app)
    if not store.verify(body.topic, body.manage_token):
        raise HTTPException(404, "No matching alert subscription found.")
    store.delete(body.topic)
    return Response(status_code=204)


@router.post("/api/alerts/status")
def alert_status(body: StatusBody, request: Request):
    store = get_store(request.app)
    if not store.verify(body.topic, body.manage_token):
        raise HTTPException(404, "No matching alert subscription found.")
    sub = store.get(body.topic)
    if sub is None:
        raise HTTPException(404, "No matching alert subscription found.")
    return {
        "team": sub.team_id,
        "league": sub.league_id,
        "reminder_hours": sub.reminder_hours,
        "created": sub.created,
        "active": sub.active,
        "inactive_reason": sub.inactive_reason,
    }


# ---------------------------------------------------------------- scheduler


def _click_url(team: int, league: int | None) -> str | None:
    base = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not base:
        return None
    params = {"team": team}
    if league:
        params["league"] = league
    return f"{base}/?{urlencode(params)}"


def _is_transient(e: Exception) -> bool:
    """Problems that aren't this subscriber's fault: ntfy errors, FPL being down."""
    if isinstance(e, NotifyError):
        return True
    return isinstance(e, FPLError) and "Not found" not in str(e)


def _needs_full_build(sub, state: DbAlertState, ev, now: datetime) -> bool:
    if not sub.last_full_build:
        return True
    try:
        last = datetime.fromisoformat(sub.last_full_build)
    except ValueError:
        return True
    if (now - last).total_seconds() >= FULL_BUILD_EVERY_HOURS * 3600:
        return True
    hours = (ev.deadline - now).total_seconds() / 3600
    due = sorted(h for h in sub.reminder_hours if 0 < hours <= h)
    return bool(due) and not state.seen(f"deadline:{ev.id}:{due[0]}")


def run_alert_cycle(app: FastAPI, sleep: float = POLITE_DELAY) -> dict[str, int]:
    """Check every active subscriber once. Never raises for a single subscriber's problem."""
    started = time.monotonic()
    store = get_store(app)
    stats = {
        "subscribers": 0,
        "built": 0,
        "reused": 0,
        "skipped": 0,
        "sent": 0,
        "failed": 0,
        "transient": 0,
    }
    pruned = store.prune_inactive()
    subs = store.list_active()
    stats["subscribers"] = len(subs)
    if not subs:
        return stats
    world = app.state.worlds.get()
    ev = world.next_event
    if ev is None:
        # FPL reassigns team ids every season, so nothing carries over.
        n = store.deactivate_all("season_ended")
        log.info("Season is over: deactivated %d subscriptions", n)
        stats["deactivated"] = n
        return stats
    server, token = ntfy_server(), ntfy_token()
    now = datetime.now(UTC)
    results: dict[
        tuple, tuple[str, object]
    ] = {}  # (team, league) -> ("ok"|"err", value)
    rate_limited = False
    for sub in subs:
        state = DbAlertState(store, sub.topic)
        if not _needs_full_build(sub, state, ev, now):
            stats["skipped"] += 1
            continue
        cfg = Config(
            team_id=sub.team_id,
            league_id=sub.league_id,
            reminder_hours=sub.reminder_hours,
            notify="ntfy",
            ntfy_topic=sub.topic,
            ntfy_server=server,
        )
        shared = (sub.team_id, sub.league_id)
        try:
            if shared in results:
                kind, value = results[shared]
                stats["reused"] += 1
                if kind == "err":
                    raise value  # type: ignore[misc]
            else:
                try:
                    value = build_advice(cfg, app.state.client, world=world)
                    results[shared] = ("ok", value)
                except Exception as e:
                    results[shared] = ("err", e)
                    raise
                finally:
                    stats["built"] += 1
                    if sleep:
                        time.sleep(sleep)  # be polite to FPL, only after real fetches
            store.set_last_full_build(sub.topic)
            alerts = collect_alerts(
                value, cfg, state, click=_click_url(sub.team_id, sub.league_id)
            )
            notifier = NtfyNotifier(server, sub.topic, token)
            for keys, n in alerts:
                notifier.send(n)
                # Save after every send so a later failure doesn't repeat this alert.
                for key in keys:
                    state.mark(key)
                state.save()
                stats["sent"] += 1
            store.record_success(sub.topic)
        except Exception as e:  # noqa: BLE001  (isolate every subscriber)
            if isinstance(e, NotifyError) and e.status == 429:
                log.warning(
                    "ntfy rate limit hit (429): stopping sends for this cycle; "
                    "unsent alerts retry next cycle"
                )
                rate_limited = True
                stats["transient"] += 1
                break
            if _is_transient(e):
                stats["transient"] += 1
                log.warning("Transient error for a subscriber (not counted): %s", e)
                continue
            stats["failed"] += 1
            log.warning("Alert check failed for a subscriber: %s", e)
            try:
                n = store.record_failure(sub.topic, MAX_FAILURES)
                if n >= MAX_FAILURES:
                    log.warning("Deactivated a subscription after %d failures", n)
            except Exception:
                log.exception("Could not record failure")
    stats["rate_limited"] = int(rate_limited)
    log.info(
        "Alert cycle: %s, pruned=%d, %.1fs", stats, pruned, time.monotonic() - started
    )
    return stats


def seconds_until_run(now: datetime, minute: int = RUN_MINUTE) -> float:
    """Seconds from `now` to the next time the wall clock reads HH:`minute`:00."""
    nxt = now.replace(minute=minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(hours=1)
    return (nxt - now).total_seconds()


def start_scheduler(app: FastAPI) -> threading.Thread | None:
    check_data_dir()
    if os.environ.get("DISABLE_ALERT_SCHEDULER"):
        log.info("Alert scheduler disabled")
        return None
    try:
        minute = int(os.environ.get("ALERT_RUN_MINUTE", str(RUN_MINUTE))) % 60
    except ValueError:
        minute = RUN_MINUTE

    def loop() -> None:
        time.sleep(FIRST_RUN_DELAY)
        while True:
            try:
                run_alert_cycle(app)
            except Exception:
                log.exception("Alert cycle crashed; will retry next hour")
            time.sleep(seconds_until_run(datetime.now(UTC), minute))

    t = threading.Thread(target=loop, name="alert-scheduler", daemon=True)
    t.start()
    return t

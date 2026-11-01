"""The hosted web app: one page plus a small JSON API around the advice engine.

Run locally:  uv run fpl-advisor-web        (http://localhost:8000)
Environment:  PORT (default 8000), FPL_DATA_DIR (default ./data) for caches, FPL_ALERTS=1 for phone alerts (off),
              FORWARDED_ALLOW_IPS (set to "*" behind a hosting proxy so visitors' real IPs are used).

Two modes:
- **Server** (default; Docker, Railway, `fpl-advisor-web`): one always-on process. A background
  thread keeps the FPL data fresh and refreshes the paid signals every 30 minutes.
- **Serverless** (Vercel, or FPL_SERVERLESS=1): no background work at all. Each instance reads the
  web snapshot (world + signals) that the scheduled `fpl-advisor refresh` job saved in the store,
  keeps it in memory for SNAPSHOT_TTL_SECONDS, and loads the world live from FPL only if the
  snapshot is missing, older than SNAPSHOT_MAX_AGE or from before the latest deadline. It holds no
  paid API keys (it refuses to start if one is set) and never calls a paid API.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import jobs, usage
from . import store as store_mod
from .advise import build_advice
from .api import FPLClient, FPLError, prune_cache
from .config import Config
from .keys import load_dotenv, redact
from .net import client_ip
from .serialize import advice_to_dict
from .signals import Signals
from .signals_service import SignalsHub
from .world import World, load_world

log = logging.getLogger("fpl_advisor.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"  # shipped inside the package
WORLD_REFRESH_SECONDS = 30 * 60
RETRY_AFTER_FAILURE_SECONDS = 60
ADVICE_TTL_SECONDS = 10 * 60
RATE_LIMIT = (20, 60)  # requests per IP per window (seconds) for /api/advice
CACHE_PRUNE_SECONDS = 3600
SIGNALS_REFRESH_SECONDS = 30 * 60
SIGNALS_MIN_GAP_SECONDS = 5 * 60
SNAPSHOT_TTL_SECONDS = 5 * 60  # serverless: how long a warm instance trusts its copy
SNAPSHOT_MAX_AGE_SECONDS = 6 * 3600  # serverless: older than this, load the world live
# API keys for paid services. The serverless web function must never hold one.
PAID_KEY_VARS = ("ODDS_API_KEY", "OPENROUTER_API_KEY", "YOUTUBE_API_KEY")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def data_dir() -> Path:
    d = Path(os.environ.get("FPL_DATA_DIR", "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


class WorldCache:
    """One shared FPL snapshot for every visitor.

    Only the very first load blocks. After that a stale snapshot keeps being served while a
    single background thread refreshes it, and a failed refresh keeps the old snapshot.
    """

    def __init__(self, client: FPLClient):
        self.client = client
        self._world: World | None = None
        self._loaded_at = 0.0
        self._failed_at = 0.0
        self._error: Exception | None = None
        self._lock = threading.Lock()  # guards the first (blocking) load
        self._refreshing = threading.Event()

    @property
    def loaded(self) -> bool:
        return self._world is not None

    def _stale(self) -> bool:
        if time.time() - self._loaded_at > WORLD_REFRESH_SECONDS:
            return True
        ev = self._world.next_event if self._world else None
        # Right after a deadline the "next" gameweek changes; don't wait 30 minutes to notice.
        return ev is not None and datetime.now(UTC) >= ev.deadline

    def get(self) -> World:
        if self._world is None:
            with self._lock:
                if self._world is None:
                    if (
                        self._error
                        and time.time() - self._failed_at < RETRY_AFTER_FAILURE_SECONDS
                    ):
                        raise self._error  # don't hammer FPL while it's down
                    self._load()
        elif self._stale():
            self.refresh_in_background()
        return self._world

    def _load(self) -> None:
        try:
            world = load_world(self.client)
        except Exception as e:
            self._error, self._failed_at = e, time.time()
            raise
        self._world, self._loaded_at, self._error = world, time.time(), None

    def refresh_in_background(self) -> None:
        if (
            self._refreshing.is_set()
            or time.time() - self._failed_at < RETRY_AFTER_FAILURE_SECONDS
        ):
            return
        self._refreshing.set()
        threading.Thread(target=self._refresh, daemon=True).start()

    def _refresh(self) -> None:
        try:
            self._load()
        except Exception:
            log.exception(
                "FPL data refresh failed; still serving the previous snapshot"
            )
        finally:
            self._refreshing.clear()

    def warm_in_background(self) -> None:
        def warm():
            try:
                self.get()
            except Exception:
                log.exception("World warm-up failed; retrying on the first request")

        threading.Thread(target=warm, daemon=True).start()


def serverless_mode(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("FPL_SERVERLESS", "").strip() == "1" or env.get("VERCEL") == "1"


class SnapshotSource:
    """Serverless: the world and signals from the refresh job's web snapshot.

    Stands in for both WorldCache (`get`, `loaded`) and SignalsHub (`current`, `version`,
    `request_refresh`). Never starts a thread and never makes a paid call: the only network
    traffic is one store read per SNAPSHOT_TTL_SECONDS and, when the snapshot is unusable, the
    public FPL API."""

    def __init__(self, st: store_mod.Store, client: FPLClient):
        self.store, self.client = st, client
        self._snap: jobs.Snapshot | None = None
        self._checked = 0.0
        self._live: World | None = None
        self._live_at = 0.0
        self._failed_at = 0.0
        self._error: Exception | None = None
        self._lock = threading.Lock()

    def _snapshot(self) -> jobs.Snapshot | None:
        if time.time() - self._checked >= SNAPSHOT_TTL_SECONDS:
            try:
                snap = jobs.read_snapshot(self.store, fresh=False)
            except Exception:
                log.exception("Could not read the web snapshot; keeping the last one")
                snap = None
            if snap is not None:
                self._snap = snap
            self._checked = time.time()
        return self._snap

    @staticmethod
    def _current(world: World, loaded_at: datetime, max_age: float) -> bool:
        now = datetime.now(UTC)
        if (now - loaded_at).total_seconds() >= max_age:
            return False
        ev = world.next_event
        return ev is None or now < ev.deadline  # after a deadline the next GW changed

    @property
    def loaded(self) -> bool:
        return self._snap is not None or self._live is not None

    def get(self) -> World:
        snap = self._snapshot()
        if snap and self._current(snap.world, snap.saved_at, SNAPSHOT_MAX_AGE_SECONDS):
            return snap.world
        with self._lock:
            live = self._live
            if live and self._current(
                live,
                datetime.fromtimestamp(self._live_at, UTC),
                WORLD_REFRESH_SECONDS,
            ):
                return live
            if (
                self._error
                and time.time() - self._failed_at < RETRY_AFTER_FAILURE_SECONDS
            ):
                if snap or live:
                    return live or snap.world  # type: ignore[union-attr]
                raise self._error  # don't hammer FPL while it's down
            try:
                world = load_world(self.client)
            except Exception as e:
                self._error, self._failed_at = e, time.time()
                if snap or live:  # stale beats nothing
                    log.warning("Live FPL load failed; serving the older snapshot")
                    return live or snap.world  # type: ignore[union-attr]
                raise
            self._live, self._live_at, self._error = world, time.time(), None
            return world

    def current(self) -> Signals:
        snap = self._snapshot()
        return snap.signals if snap else Signals()

    @property
    def version(self) -> str:
        snap = self._snap
        if not snap:
            return "none"
        return f"{snap.saved_at.isoformat()}|" + "|".join(
            f"{k}={v}" for k, v in sorted(snap.signals.updated.items())
        )

    def request_refresh(self) -> None:
        """Nothing to wake here: the scheduled refresh job runs every 30 minutes."""


def _refuse_paid_keys(env=None) -> None:
    env = os.environ if env is None else env
    held = [k for k in PAID_KEY_VARS if (env.get(k) or "").strip()]
    if held:
        raise RuntimeError(
            "The serverless web app must not hold paid API keys; remove "
            + ", ".join(held)
            + " from its environment (they belong to the refresh job only)."
        )


class AdviceCache:
    def __init__(self, max_items: int = 2000):
        self._items: dict[tuple, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self.max_items = max_items

    def get(self, key: tuple) -> dict | None:
        with self._lock:
            hit = self._items.get(key)
            if hit and time.time() - hit[0] < ADVICE_TTL_SECONDS:
                return hit[1]
            return None

    def put(self, key: tuple, value: dict) -> None:
        with self._lock:
            if len(self._items) >= self.max_items:  # drop the oldest half
                for k, _ in sorted(self._items.items(), key=lambda kv: kv[1][0])[
                    : self.max_items // 2
                ]:
                    del self._items[k]
            self._items[key] = (time.time(), value)


class RateLimiter:
    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.time()

    def allow(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            if now - self._last_sweep > self.window:  # keep memory bounded
                self._hits = {
                    k: q
                    for k, q in self._hits.items()
                    if q and now - q[-1] <= self.window
                }
                self._last_sweep = now
            q = self._hits.setdefault(ip, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def create_app(
    client: FPLClient | None = None,
    start_background: bool = True,
    alerts: bool | None = None,
    serverless: bool | None = None,
    store: store_mod.Store | None = None,
) -> FastAPI:
    """`alerts` turns on the phone-alert sign-up routes and scheduler. They're off on the hosted
    site for now (advice is website-only); FPL_ALERTS=1 turns them back on. `serverless` (default:
    on under Vercel or with FPL_SERVERLESS=1) reads a stored snapshot instead of running any
    background work; see the module docstring."""
    if serverless is None:
        serverless = serverless_mode()
    if serverless:
        _refuse_paid_keys()
        start_background, alerts = False, False  # nothing may outlive a request
        from .keys import install_redaction

        install_redaction()  # no .env on a serverless host
        client = client or FPLClient(Path(tempfile.gettempdir()) / "fpl-cache")
        source = SnapshotSource(store or store_mod.default(), client)
        worlds, hub = source, source
    else:
        load_dotenv()  # also installs key redaction for every log record
        if start_background:
            usage.warn_if_ephemeral()
        client = client or FPLClient(data_dir() / "cache")
        worlds = WorldCache(client)
        hub = SignalsHub(client, data_dir())
    if alerts is None:
        alerts = os.environ.get("FPL_ALERTS", "").strip() == "1"
    advice_cache = AdviceCache()
    limiter = RateLimiter(*RATE_LIMIT)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            worlds.warm_in_background()
            _start_cache_pruner(client)
            _start_signals(worlds, hub)
            if alerts:
                _start_alerts(app)
        yield

    app = FastAPI(title="FPL Advisor", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.client = client
    app.state.worlds = worlds
    app.state.signals = hub
    app.state.serverless = serverless

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if serverless:  # Vercel's per-request OIDC token, for reading the Blob store
            store_mod.request_oidc_token.set(request.headers.get(store_mod.OIDC_HEADER))
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.get("/api/health")
    async def health():  # async: must answer even when every worker thread is busy
        return {
            "ok": True,
            "world_loaded": worlds.loaded,
            "mode": "serverless" if serverless else "server",
        }

    @app.get("/api/advice")
    def advice(
        request: Request,
        team: int = Query(..., ge=1, le=99_999_999),
        league: int | None = Query(None, ge=1, le=99_999_999),
        risk: str = Query("auto", pattern="^(auto|low|medium|high)$"),
    ):
        if not limiter.allow(client_ip(request)):
            raise HTTPException(
                429, "Too many requests. Please wait a minute and try again."
            )
        try:
            world = worlds.get()
            gw = world.next_event.id if world.next_event else None
            if gw is not None and hub.current().gameweek not in (None, gw):
                # A deadline passed: odds/news are stale. Only wakes the scheduled loop;
                # a visitor's request never makes a paid call itself.
                hub.request_refresh()
            key = (gw, team, league, risk, hub.version)
            cached = advice_cache.get(key)
            if cached:
                return cached
            a = build_advice(
                Config(team_id=team, league_id=league, risk=risk),
                client,
                world=world,
                signals=hub.current(),
            )
        except FPLError as e:
            msg = str(e)
            raise HTTPException(
                404 if "Not found" in msg else 503, _friendly(msg)
            ) from e
        except ValueError as e:
            raise HTTPException(400, redact(str(e))) from e
        data = advice_to_dict(a)
        advice_cache.put(key, data)
        return data

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        detail = redact(exc.detail) if isinstance(exc.detail, str) else exc.detail
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception):
        # Never echo an unexpected error: its text could hold a URL or a key. The log
        # (redacted) has the details.
        log.error("Unhandled %s in a request", type(exc).__name__, exc_info=exc)
        return JSONResponse(
            {"error": "Something went wrong on our side. Please try again shortly."},
            status_code=500,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        if request.url.path.startswith("/api/advice"):
            msg = "That doesn't look like a valid FPL ID. Use the number from your FPL link."
        else:
            msg = "That request wasn't in the expected format."
        return JSONResponse({"error": msg}, status_code=422)

    if alerts:
        _include_alerts(app)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(STATIC_DIR / "index.html")

    return app


def _friendly(msg: str) -> str:
    if "Not found" in msg:
        return "We couldn't find that team or league. Check the ID (it's the number in your FPL link)."
    if "updating" in msg:
        return "FPL is updating right now (this happens around deadlines). Try again in a few minutes."
    return "We couldn't reach FPL just now. Please try again shortly."


def _start_cache_pruner(client: FPLClient) -> None:
    """Delete per-team cache files that have expired, so random IDs can't fill the disk."""

    def loop():
        while True:
            time.sleep(CACHE_PRUNE_SECONDS)
            try:
                removed = prune_cache(client.cache_dir, max_age_seconds=24 * 3600)
                if removed:
                    log.info("Pruned %d expired cache files", removed)
            except Exception:
                log.exception("Cache prune failed")

    if client.cache_dir:
        threading.Thread(target=loop, daemon=True).start()


def _start_signals(worlds: WorldCache, hub: SignalsHub) -> None:
    """Refresh odds, team news and the elite managers in the background.

    The only place the web server makes paid API calls. Runs every 30 minutes, or sooner when
    `hub.request_refresh()` is called, but never more often than SIGNALS_MIN_GAP_SECONDS."""

    def loop():
        while True:
            try:
                hub.refresh(worlds.get())
            except Exception:
                log.exception("Signals refresh failed")
            time.sleep(SIGNALS_MIN_GAP_SECONDS)
            hub.wake.wait(SIGNALS_REFRESH_SECONDS - SIGNALS_MIN_GAP_SECONDS)
            hub.wake.clear()

    threading.Thread(target=loop, daemon=True, name="signals").start()


def _include_alerts(app: FastAPI) -> None:
    """Alert sign-up routes, if the alerts module is present."""
    try:
        from .alerts_service import router
    except ImportError:
        return
    app.include_router(router)


def _start_alerts(app: FastAPI) -> None:
    try:
        from .alerts_service import start_scheduler
    except ImportError:
        return
    start_scheduler(app)


def main() -> None:
    import uvicorn

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "fpl_advisor.web:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        # Behind Fly/Render/Railway set FORWARDED_ALLOW_IPS="*" so request.client is the visitor.
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )

"""The hosted web app: one page plus a small JSON API around the advice engine.

Run locally:  uv run fpl-advisor-web        (http://localhost:8000)
Environment:  PORT (default 8000), FPL_DATA_DIR (default ./data) for caches and alert subscriptions,
              FORWARDED_ALLOW_IPS (set to "*" behind a hosting proxy so visitors' real IPs are used).
"""

from __future__ import annotations

import logging
import os
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

from . import usage
from .advise import build_advice
from .api import FPLClient, FPLError, prune_cache
from .config import Config
from .keys import load_dotenv, redact
from .net import client_ip
from .serialize import advice_to_dict
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
    client: FPLClient | None = None, start_background: bool = True
) -> FastAPI:
    load_dotenv()  # also installs key redaction for every log record
    if start_background:
        usage.warn_if_ephemeral()
    client = client or FPLClient(data_dir() / "cache")
    worlds = WorldCache(client)
    advice_cache = AdviceCache()
    limiter = RateLimiter(*RATE_LIMIT)
    hub = SignalsHub(client, data_dir())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            worlds.warm_in_background()
            _start_cache_pruner(client)
            _start_signals(worlds, hub)
            _start_alerts(app)
        yield

    app = FastAPI(title="FPL Advisor", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.client = client
    app.state.worlds = worlds
    app.state.signals = hub

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.get("/api/health")
    async def health():  # async: must answer even when every worker thread is busy
        return {"ok": True, "world_loaded": worlds.loaded}

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

"""Spend caps for the paid APIs (The Odds API, OpenRouter, YouTube Data API), shared on disk.

One ledger file is the single source of truth for every process (the web server's background
refresh and any CLI or cron run at the same time): FPL_USAGE_PATH, default
~/.fpl-advisor/usage/usage.json, never relative to the working directory. Every
read-modify-write holds an exclusive `fcntl` lock, and the file is replaced atomically and
mirrored to a second folder (FPL_USAGE_MIRROR), so losing one copy restores from the other.

How a paid call works:
1. `reserve(provider, worst_case, caps)` BEFORE the call. If today's or this month's spend plus
   everything already reserved plus `worst_case` would pass a cap, the answer is None: do not
   call (fail closed) and keep serving cached data. The refusal is logged once per period.
2. Make the call.
3. `settle(reservation, actual)` records what it really cost and releases the rest.

A reservation that is never settled (the process died mid-call) counts as fully spent after
`PENDING_TTL`. If neither copy can be read it is copied aside, logged loudly and every paid call
is refused until a person runs `fpl-advisor usage --reset-confirm`. If both copies vanished but
the lock file shows a ledger existed this month, the same happens: it is never silently reset.
Periods only move forward (a clock stepping back keeps counting into the newer period), and the
strictest cap any process used in a period is the one enforced for that period.

Crossing 80% and 100% of a daily or monthly cap sends one push alert per threshold per period
to the owner's ntfy topic (`NTFY_TOPIC`), if one is configured.

Days end at midnight UTC, except YouTube, whose quota resets at midnight Pacific time.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: threads in one process are still safe
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

# ------------------------------------------------------------------------------ the caps

DEFAULT_ODDS_MONTHLY = 400  # credits; the free plan has 500 a month
DEFAULT_ODDS_DAILY = 40  # credits; a normal day uses ~10, a props-window day ~25
ODDS_FLOOR = 50  # stop when The Odds API says fewer credits than this are left
DEFAULT_OPENROUTER_MONTHLY_USD = 5.00
DEFAULT_OPENROUTER_DAILY_USD = 0.30
DEFAULT_OPENROUTER_MAX_CALLS_PER_DAY = 100  # a normal day makes ~20
DEFAULT_YOUTUBE_DAILY_UNITS = 2000  # Google's free quota is 10,000 a day

ALERT_THRESHOLDS = (80, 100)  # percent of a cap
PENDING_TTL = timedelta(
    minutes=15
)  # an unsettled reservation older than this was spent
LEDGER_VERSION = 1


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    unit: str  # "credits", "USD", "units"
    tz: str  # where its day boundary is


PROVIDERS = {
    "odds": Provider("odds", "The Odds API", "credits", "UTC"),
    "openrouter": Provider("openrouter", "OpenRouter", "USD", "UTC"),
    "youtube": Provider("youtube", "YouTube Data API", "units", "America/Los_Angeles"),
}


@dataclass(frozen=True)
class Caps:
    daily: float | None = None
    monthly: float | None = None
    calls_per_day: int | None = None
    floor: float | None = None  # least the provider may report as left


def _env_num(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("%s is not a number; using the default %s", name, default)
        return default
    if not math.isfinite(v) or v < 0:
        log.warning("%s must be zero or more; using the default %s", name, default)
        return default
    return v


def caps_for(provider: str, env: Mapping[str, str] | None = None) -> Caps:
    """The caps for `provider`, from the environment or the defaults above."""
    env = os.environ if env is None else env
    if provider == "odds":
        return Caps(
            daily=_env_num(env, "ODDS_DAILY_BUDGET", DEFAULT_ODDS_DAILY),
            monthly=_env_num(env, "ODDS_MONTHLY_BUDGET", DEFAULT_ODDS_MONTHLY),
            floor=ODDS_FLOOR,
        )
    if provider == "openrouter":
        return Caps(
            daily=_env_num(
                env, "OPENROUTER_DAILY_BUDGET_USD", DEFAULT_OPENROUTER_DAILY_USD
            ),
            monthly=_env_num(
                env, "OPENROUTER_MONTHLY_BUDGET_USD", DEFAULT_OPENROUTER_MONTHLY_USD
            ),
            calls_per_day=int(
                _env_num(
                    env,
                    "OPENROUTER_MAX_CALLS_PER_DAY",
                    DEFAULT_OPENROUTER_MAX_CALLS_PER_DAY,
                )
            ),
        )
    if provider == "youtube":
        return Caps(
            daily=_env_num(env, "YOUTUBE_DAILY_UNITS", DEFAULT_YOUTUBE_DAILY_UNITS)
        )
    raise KeyError(provider)


# ------------------------------------------------------------------------ OpenRouter cost

# US$ per million tokens (input, output): the ONLY models fpl-advisor will call. Anything else
# (a typo, a pricier model, `openrouter/auto` or another router, a `:online`/`:nitro` variant
# that adds fees) is refused, because its worst case cannot be known in advance. To use another
# plain model, set OPENROUTER_MODEL_PRICE="<in>,<out>" (US$ per million tokens, from the
# model's OpenRouter page); that declares it and its price for the reservation.
OPENROUTER_PRICES = {
    "openai/gpt-6.1-terra": (1.00, 5.00),  # the default; checked 2026-11-01
    "google/gemini-2.5-flash-lite": (0.10, 0.40),  # checked 2026-10-31
    "openai/gpt-4o-mini": (0.15, 0.60),
}
PROMPT_OVERHEAD_TOKENS = 50  # chat template and message framing


def model_price(
    model: str, env: Mapping[str, str] | None = None
) -> tuple[float, float] | None:
    """(input, output) US$ per million tokens, or None: this model must not be called."""
    env = os.environ if env is None else env
    model = (model or "").strip()
    if not model or model.startswith("openrouter/") or ":" in model or "/" not in model:
        return None
    raw = (env.get("OPENROUTER_MODEL_PRICE") or "").strip()
    if raw:
        try:
            p_in, p_out = (float(x) for x in raw.split(","))
        except ValueError:
            return None
        if (
            _num_ok(p_in)
            and _num_ok(p_out)
            and model == (env.get("OPENROUTER_MODEL") or "").strip()
        ):
            return p_in, p_out
    return OPENROUTER_PRICES.get(model)


def openrouter_worst_case(model: str, prompt: str, max_tokens: int) -> float | None:
    """Most a call can cost: every UTF-8 byte of the prompt counted as a token (a token is
    never shorter than one byte), plus `max_tokens` out. None for a model that is not allowed."""
    price = model_price(model)
    if price is None:
        return None
    tokens_in = len(prompt.encode("utf-8")) + PROMPT_OVERHEAD_TOKENS
    return round((tokens_in * price[0] + max_tokens * price[1]) / 1e6, 8)


def openrouter_cost(model: str, reply: object, worst_case: float) -> float:
    """What a call cost: OpenRouter's own `usage.cost`, else tokens x price, else worst case."""
    u = reply.get("usage") if isinstance(reply, dict) else None
    if isinstance(u, dict):
        cost = u.get("cost")
        if _num_ok(cost):
            return float(cost)
        tin, tout = u.get("prompt_tokens"), u.get("completion_tokens")
        price = model_price(model)
        if price and _num_ok(tin) and _num_ok(tout):
            return (float(tin) * price[0] + float(tout) * price[1]) / 1e6
    return worst_case


def _num_ok(v: object) -> bool:
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
        and v >= 0
    )


# ------------------------------------------------------------------------------- periods


def _tz(name: str) -> tzinfo:
    if name == "UTC":
        return UTC
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - no tz database: Pacific standard time is close enough
        return timezone(timedelta(hours=-8), "PST")


def periods(provider: str, now: datetime) -> tuple[str, str]:
    """(day, month) keys for `now` in the provider's time zone."""
    local = now.astimezone(_tz(PROVIDERS[provider].tz))
    return local.strftime("%Y-%m-%d"), local.strftime("%Y-%m")


def resets(provider: str, now: datetime) -> tuple[datetime, datetime]:
    """When the current day and month end, in the provider's time zone."""
    tz = _tz(PROVIDERS[provider].tz)
    local = now.astimezone(tz)
    day = datetime(local.year, local.month, local.day, tzinfo=tz) + timedelta(days=1)
    y, m = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return day, datetime(y, m, 1, tzinfo=tz)


# -------------------------------------------------------------------------------- ledger


DEFAULT_LEDGER = Path("~/.fpl-advisor/usage/usage.json")


def ledger_path() -> Path:
    """Where the ledger lives: FPL_USAGE_PATH, else ~/.fpl-advisor/usage/usage.json.

    Deliberately independent of the working directory and of FPL_DATA_DIR, so every process
    using the same keys (web server, CLI, cron) shares one ledger and one set of caps. The
    Docker image sets FPL_USAGE_PATH=/data/usage/usage.json; a CLI on the host that uses the
    same keys should point FPL_USAGE_PATH at that same file."""
    raw = (os.environ.get("FPL_USAGE_PATH") or "").strip()
    return Path(raw or DEFAULT_LEDGER).expanduser().absolute()


def mirror_path(path: Path) -> Path:
    """The second copy of a ledger, in a different folder (FPL_USAGE_MIRROR for the main one).

    Losing one file (deleted, damaged, a half-copied folder) restores from the other."""
    raw = (os.environ.get("FPL_USAGE_MIRROR") or "").strip()
    if raw and path == ledger_path():
        return Path(raw).expanduser().absolute()
    return path.parent.with_name(path.parent.name + ".mirror") / path.name


def warn_if_ephemeral() -> bool:
    """Loud warning when the ledger's volume is expected but not mounted (Docker without a
    persistent /data volume: every redeploy would forget what was spent). True if fine."""
    vol = (os.environ.get("FPL_EXPECT_VOLUME") or "").strip()
    if not vol or os.path.ismount(vol):
        return True
    log.error(
        "%s IS NOT A MOUNTED VOLUME. The usage ledger (%s) and caches will be lost on every "
        "redeploy, so spending caps restart from zero each time. Mount a persistent volume "
        "at %s, and keep the provider-side caps set (see the README).",
        vol,
        ledger_path(),
        vol,
    )
    return False


class LedgerError(RuntimeError):
    """The ledger file exists but cannot be read or does not look like a ledger."""


@dataclass(frozen=True)
class Reservation:
    id: str
    provider: str
    amount: float


AlertFn = Callable[[str, str], None]  # (title, body)

_path_locks: dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()
_logged: set[tuple] = (
    set()
)  # (path, provider, reason, period): refusals logged this process


def _thread_lock(path: Path) -> threading.Lock:
    with _path_locks_guard:
        return _path_locks.setdefault(str(path.resolve()), threading.Lock())


def _blank(provider: str, now: datetime) -> dict:
    day, month = periods(provider, now)
    return {
        "day": day,
        "month": month,
        "spent_day": 0,
        "spent_month": 0,
        "calls_day": 0,
        "calls_month": 0,
        "remaining": None,
        "pending": {},
    }


# The strictest cap any process used this period (see Usage._pin).
_CAP_KEYS = ("cap_day", "cap_month", "cap_calls_day", "cap_floor")


def _validate(d: object) -> dict:
    if not isinstance(d, dict) or not isinstance(d.get("providers"), dict):
        raise LedgerError("not a usage ledger")
    for name, rec in d["providers"].items():
        if name not in PROVIDERS or not isinstance(rec, dict):
            raise LedgerError(f"bad provider entry {name!r}")
        for k in ("spent_day", "spent_month", "calls_day", "calls_month"):
            if not _num_ok(rec.get(k)):
                raise LedgerError(f"bad {k} for {name}")
        if not isinstance(rec.get("day"), str) or not isinstance(rec.get("month"), str):
            raise LedgerError(f"bad period for {name}")
        if not isinstance(rec.get("pending", {}), dict):
            raise LedgerError(f"bad pending for {name}")
        rem = rec.get("remaining")
        if rem is not None and not _num_ok(rem):
            raise LedgerError(f"bad remaining for {name}")
        for k in _CAP_KEYS:
            if rec.get(k) is not None and not _num_ok(rec.get(k)):
                raise LedgerError(f"bad {k} for {name}")
    if not _num_ok(d.get("seq", 0)):
        raise LedgerError("bad seq")
    if not isinstance(d.get("alerts", []), list):
        raise LedgerError("bad alerts list")
    return d


class Usage:
    """The shared spend ledger. Cheap to create; all state lives in the file."""

    def __init__(
        self,
        path: Path | str,
        alert: AlertFn | None = None,
        mirror: Path | str | None = None,
    ):
        self.path = Path(path).absolute()
        self.mirror = Path(mirror).absolute() if mirror else mirror_path(self.path)
        # The lock file doubles as a marker: it remembers that a ledger existed, and when.
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._alert = alert

    # ------------------------------------------------------------------ file handling

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _thread_lock(self.path):
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                self._lock_fd = fd
                yield
            finally:
                self._lock_fd = None
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    @staticmethod
    def _read(path: Path) -> tuple[str, dict | None]:
        """("ok", data) / ("missing", None) / ("bad: why", None)."""
        try:
            raw = path.read_text()
        except FileNotFoundError:
            return "missing", None
        except OSError as e:
            return f"cannot read it ({type(e).__name__})", None
        try:
            return "ok", _validate(json.loads(raw))
        except ValueError:
            return "it is not valid JSON", None
        except LedgerError as e:
            return str(e), None

    def _marker(self) -> dict:
        try:
            with open(self.lock_path) as f:
                d = json.loads(f.read() or "{}")
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _load(self, now: datetime | None = None) -> dict:
        """The newest good copy of the ledger. Raises LedgerError rather than ever silently
        starting again from zero over a ledger that existed."""
        now = now or datetime.now(UTC)
        main, d1 = self._read(self.path)
        mirr, d2 = self._read(self.mirror)
        good = [d for d in (d1, d2) if d is not None]
        if good:
            data = max(good, key=lambda d: d.get("seq", 0))
            for where, state in ((self.path, main), (self.mirror, mirr)):
                if state not in ("ok", "missing"):
                    self._backup(where, "corrupt", now)
                key = (str(where), "*", f"restored:{state}", "")
                if state != "ok" and key not in _logged:
                    _logged.add(key)
                    log.error(
                        "usage: ledger copy %s was %s; restored it from the other copy",
                        where,
                        "missing" if state == "missing" else f"unreadable ({state})",
                    )
            return data
        if main != "missing" or mirr != "missing":
            raise LedgerError(main if main != "missing" else mirr)
        mark = self._marker()
        if mark.get("month") == now.astimezone(UTC).strftime("%Y-%m"):
            raise LedgerError(
                "both copies are missing, but a ledger existed this month; run "
                "`fpl-advisor usage --reset-confirm` once you have checked the spend at "
                "each provider"
            )
        return {"version": LEDGER_VERSION, "seq": 0, "providers": {}, "alerts": []}

    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _save(self, data: dict, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        data["seq"] = int(data.get("seq", 0)) + 1
        data["version"] = LEDGER_VERSION
        self._write(self.path, data)
        try:
            self._write(self.mirror, data)
        except OSError as e:
            key = (str(self.mirror), "*", "mirror", "")
            if key not in _logged:
                _logged.add(key)
                log.warning(
                    "usage: could not write the ledger mirror %s (%s)",
                    self.mirror,
                    type(e).__name__,
                )
        fd = getattr(self, "_lock_fd", None)
        if fd is not None:
            marker = json.dumps(
                {"month": now.astimezone(UTC).strftime("%Y-%m"), "seq": data["seq"]}
            ).encode()
            os.ftruncate(fd, 0)
            os.pwrite(fd, marker, 0)

    def _backup(self, path: Path, why: str, now: datetime) -> Path | None:
        backup = path.with_name(f"{path.name}.{why}-{now.strftime('%Y%m%dT%H%M%S')}")
        try:
            if path.exists() and not backup.exists():
                shutil.copy2(path, backup)
            return backup if backup.exists() else None
        except OSError:
            return None

    def reset(self, now: datetime | None = None) -> None:
        """Start counting from zero (`fpl-advisor usage --reset-confirm`). Old copies are kept."""
        now = now or datetime.now(UTC)
        with self._locked():
            for p in (self.path, self.mirror):
                self._backup(p, "reset", now)
            seq = int(self._marker().get("seq") or 0)
            fresh = {
                "version": LEDGER_VERSION,
                "seq": seq,
                "providers": {},
                "alerts": [],
            }
            self._save(fresh, now)
        for k in [k for k in _logged if k[0] == str(self.path)]:
            _logged.discard(k)
        log.warning("usage: ledger %s reset to zero by request", self.path)

    def _broken(self, err: LedgerError, now: datetime) -> None:
        """Copy an unreadable ledger aside (once per process) and log loudly."""
        key = (str(self.path), "*", "corrupt", "")
        if key in _logged:
            return
        _logged.add(key)
        backup = self._backup(self.path, "corrupt", now)
        log.error(
            "USAGE LEDGER %s IS UNREADABLE (%s). Every paid API call is stopped until it is "
            "fixed. %s Run `fpl-advisor usage --reset-confirm` to start counting from zero "
            "(this forgets what was already spent this month), or repair it.",
            self.path,
            err,
            f"A copy is at {backup}." if backup else "",
        )
        self._send(
            "fpl-advisor: paid API calls stopped",
            "The usage ledger could not be read, so every paid API call is stopped. "
            "Run `fpl-advisor usage` for details.",
        )

    # ------------------------------------------------------------------ records

    @staticmethod
    def _rec(data: dict, provider: str, now: datetime) -> dict:
        rec = data["providers"].setdefault(provider, _blank(provider, now))
        day, month = periods(provider, now)
        rec.setdefault("pending", {})
        # Periods only move forward: if the clock steps back, keep counting into the newer
        # stored period instead of starting a fresh (empty) old one.
        if month > rec["month"]:
            rec.update(month=month, spent_month=0, calls_month=0, remaining=None)
            rec.pop("cap_month", None)
            rec.pop("cap_floor", None)
        if day > rec["day"]:
            rec.update(day=day, spent_day=0, calls_day=0)
            rec.pop("cap_day", None)
            rec.pop("cap_calls_day", None)
        # Reservations never settled (crash mid-call) are counted as spent.
        for rid, p in list(rec["pending"].items()):
            at = _iso(p.get("at") if isinstance(p, dict) else None)
            if at is None or now - at > PENDING_TTL:
                amount = p.get("amount") if isinstance(p, dict) else None
                amount = float(amount) if _num_ok(amount) else 0.0
                rec["spent_day"] += amount
                rec["spent_month"] += amount
                del rec["pending"][rid]
                log.warning(
                    "usage: a call to %s never reported back; counting its reserved %s as spent",
                    PROVIDERS[provider].name,
                    _fmt(provider, amount),
                )
        return rec

    @staticmethod
    def _pin(rec: dict, caps: Caps) -> Caps:
        """The strictest caps any process has used this period, so one process with looser
        env settings cannot spend more than another already agreed to."""

        def pin(key, value, strict=min):
            old = rec.get(key)
            new = old if value is None else value if old is None else strict(old, value)
            if new is not None:
                rec[key] = new
            return new

        calls = pin("cap_calls_day", caps.calls_per_day)
        return Caps(
            daily=pin("cap_day", caps.daily),
            monthly=pin("cap_month", caps.monthly),
            calls_per_day=None if calls is None else int(calls),
            floor=pin("cap_floor", caps.floor, max),
        )

    @staticmethod
    def _held(rec: dict) -> tuple[float, int]:
        amounts = [
            float(p["amount"])
            for p in rec["pending"].values()
            if isinstance(p, dict) and _num_ok(p.get("amount"))
        ]
        return sum(amounts), len(rec["pending"])

    # ------------------------------------------------------------------ public API

    def reserve(
        self,
        provider: str,
        amount: float,
        caps: Caps | None = None,
        now: datetime | None = None,
    ) -> Reservation | None:
        """Hold `amount` (the worst case) for one call. None means: do not make the call.

        A zero-cost call (a free endpoint) skips the caps but is still refused when the
        ledger is unreadable."""
        now = now or datetime.now(UTC)
        caps = caps or caps_for(provider)
        amount = max(0.0, float(amount))
        alerts: list[tuple[str, str]] = []
        try:
            with self._locked():
                data = self._load(now)
                rec = self._rec(data, provider, now)
                caps = self._pin(rec, caps)
                why = None if amount == 0 else self._refusal(rec, amount, caps)
                if why is not None:
                    reason, period, cap = why
                    alerts = self._mark(data, provider, rec, period, cap, force=True)
                    self._save(data, now)
                    self._log_refusal(provider, reason, rec, period, cap, amount)
                    res = None
                else:
                    res = Reservation(uuid.uuid4().hex, provider, amount)
                    rec["pending"][res.id] = {
                        "amount": amount,
                        "at": now.isoformat(timespec="seconds"),
                    }
                    self._save(data, now)
        except LedgerError as e:
            self._broken(e, now)
            return None
        for title, body in alerts:
            self._send(title, body)
        return res

    def settle(
        self,
        res: Reservation,
        actual: float,
        *,
        calls: int = 1,
        remaining: float | None = None,
        caps: Caps | None = None,
        now: datetime | None = None,
    ) -> None:
        """Record what a reserved call really cost and free the rest of the hold."""
        now = now or datetime.now(UTC)
        caps = caps or caps_for(res.provider)
        actual = max(0.0, float(actual)) if _num_ok(actual) else res.amount
        alerts: list[tuple[str, str]] = []
        try:
            with self._locked():
                data = self._load(now)
                rec = self._rec(data, res.provider, now)
                caps = self._pin(rec, caps)
                if rec["pending"].pop(res.id, None) is not None:
                    rec["spent_day"] = round(rec["spent_day"] + actual, 8)
                    rec["spent_month"] = round(rec["spent_month"] + actual, 8)
                # else: it timed out and was already counted in full
                rec["calls_day"] += calls
                rec["calls_month"] += calls
                if remaining is not None and _num_ok(remaining):
                    rec["remaining"] = remaining
                for period, cap in (("day", caps.daily), ("month", caps.monthly)):
                    alerts += self._mark(data, res.provider, rec, period, cap)
                self._save(data, now)
        except LedgerError as e:
            self._broken(e, now)
            return
        for title, body in alerts:
            self._send(title, body)

    def release(self, res: Reservation, now: datetime | None = None) -> None:
        """The call was not made after all."""
        self.settle(res, 0, calls=0, now=now)

    def snapshot(self, now: datetime | None = None) -> dict:
        """Spend per provider for today and this month (read-only). Raises LedgerError."""
        now = now or datetime.now(UTC)
        with self._locked():
            data = self._load(now)
        out = {}
        for p in PROVIDERS:
            rec = data["providers"].get(p)
            day, month = periods(p, now)
            if rec is None:
                rec = _blank(p, now)
            held, n = self._held({"pending": rec.get("pending") or {}})
            this_day, this_month = rec["day"] >= day, rec["month"] >= month
            out[p] = {
                "spent_day": rec["spent_day"] if this_day else 0,
                "spent_month": rec["spent_month"] if this_month else 0,
                "calls_day": rec["calls_day"] if this_day else 0,
                "calls_month": rec["calls_month"] if this_month else 0,
                "remaining": rec.get("remaining") if this_month else None,
                "pending": held,
                "pending_calls": n,
                # strictest caps used this period by any process (None: not pinned yet)
                "cap_day": rec.get("cap_day") if this_day else None,
                "cap_month": rec.get("cap_month") if this_month else None,
                "cap_calls_day": rec.get("cap_calls_day") if this_day else None,
            }
        return out

    # ------------------------------------------------------------------ internals

    def _refusal(
        self, rec: dict, amount: float, caps: Caps
    ) -> tuple[str, str, float | None] | None:
        held, n = self._held(rec)
        if caps.daily is not None and rec["spent_day"] + held + amount > caps.daily:
            return "daily cap", "day", caps.daily
        if (
            caps.monthly is not None
            and rec["spent_month"] + held + amount > caps.monthly
        ):
            return "monthly cap", "month", caps.monthly
        if caps.calls_per_day is not None and rec["calls_day"] + n + 1 > (
            caps.calls_per_day
        ):
            return "daily call limit", "day", None
        rem = rec.get("remaining")
        if caps.floor is not None and rem is not None and rem - amount < caps.floor:
            return "credits floor", "month", None
        return None

    def _log_refusal(self, provider, reason, rec, period, cap, amount) -> None:
        key = (str(self.path), provider, reason, rec[period])
        if key in _logged:
            return
        _logged.add(key)
        name = PROVIDERS[provider].name
        if reason == "daily call limit":
            detail = f"{rec['calls_day']} calls made today"
        elif reason == "credits floor":
            detail = f"only {rec.get('remaining')} credits left on the account"
        else:
            spent = rec["spent_day"] if period == "day" else rec["spent_month"]
            detail = (
                f"{_fmt(provider, spent)} of {_fmt(provider, cap)} spent "
                f"{'today' if period == 'day' else 'this month'}, next call could cost "
                f"up to {_fmt(provider, amount)}"
            )
        log.warning(
            "usage: %s %s reached (%s). Paid calls are paused until it resets; cached "
            "data is still used.",
            name,
            reason,
            detail,
        )

    def _mark(
        self,
        data: dict,
        provider: str,
        rec: dict,
        period: str,
        cap: float | None,
        force: bool = False,
    ) -> list[tuple[str, str]]:
        """Alerts due for one cap: 80% / 100% crossed (or a refusal, `force`)."""
        if not cap:
            return []
        spent = rec["spent_day"] if period == "day" else rec["spent_month"]
        pct = 100.0 if force else 100.0 * spent / cap
        sent = data.setdefault("alerts", [])
        # Keep only this period's markers so the list stays small.
        live = {f"{provider}:day:{rec['day']}:", f"{provider}:month:{rec['month']}:"}
        sent[:] = [
            k
            for k in sent
            if isinstance(k, str)
            and (not k.startswith(f"{provider}:") or k[: k.rfind(":") + 1] in live)
        ]
        due = [t for t in ALERT_THRESHOLDS if pct >= t]
        new = [t for t in due if f"{provider}:{period}:{rec[period]}:{t}" not in sent]
        if not new:
            return []
        for t in due:
            k = f"{provider}:{period}:{rec[period]}:{t}"
            if k not in sent:
                sent.append(k)
        top = max(new)
        name = PROVIDERS[provider].name
        when = "today" if period == "day" else "this month"
        if top >= 100:
            title = f"fpl-advisor: {name} {('daily' if period == 'day' else 'monthly')} cap reached"
            body = (
                f"{name}: {_fmt(provider, spent)} of the {_fmt(provider, cap)} cap used {when}. "
                "Paid calls are paused until it resets; cached data is still shown."
            )
        else:
            title = f"fpl-advisor: {name} at {top}% of its {('daily' if period == 'day' else 'monthly')} cap"
            body = f"{name}: {_fmt(provider, spent)} of the {_fmt(provider, cap)} cap used {when}."
        return [(title, body)]

    def _send(self, title: str, body: str) -> None:
        fn = self._alert or owner_alert
        try:
            fn(title, body)
        except Exception as e:  # noqa: BLE001 - an alert must never break a refresh
            log.warning("usage: could not send the spend alert (%s)", type(e).__name__)


def default() -> Usage:
    """The ledger in FPL_DATA_DIR (the same one the web server and the CLI use)."""
    return Usage(ledger_path())


def owner_alert(title: str, body: str) -> None:
    """Push a spend alert to the owner's ntfy topic (NTFY_TOPIC / ntfy_topic), if set."""
    from . import config
    from .notify import Notification, NtfyNotifier

    try:
        toml = config._read_toml(None)
    except config.ConfigError:
        toml = {}
    topic = (os.environ.get("NTFY_TOPIC") or toml.get("ntfy_topic") or "").strip()
    log.warning("usage: %s", body)
    if not topic:
        return
    server = (
        os.environ.get("NTFY_SERVER") or toml.get("ntfy_server") or "https://ntfy.sh"
    )
    token = os.environ.get("NTFY_TOKEN") or toml.get("ntfy_token") or None
    NtfyNotifier(str(server), str(topic), token).send(
        Notification(title=title, body=body, priority=4, tags=["money_with_wings"])
    )


def _fmt(provider: str, amount: float | None) -> str:
    if amount is None:
        return "?"
    unit = PROVIDERS[provider].unit
    if unit == "USD":
        return f"${amount:.4f}" if amount < 0.01 and amount else f"${amount:.2f}"
    return f"{amount:g} {unit}"


def _iso(s: object) -> datetime | None:
    try:
        d = datetime.fromisoformat(s) if isinstance(s, str) and s else None
    except ValueError:
        return None
    return d if d is None or d.tzinfo else d.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------- the report


def _until(when: datetime, now: datetime) -> str:
    secs = max(0, int((when - now).total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {rem // 60}m"


def report(ledger: Usage | None = None, now: datetime | None = None) -> str:
    """Plain-English spend vs caps for `fpl-advisor usage`. Never shows key values."""
    ledger = ledger or default()
    now = now or datetime.now(UTC)
    lines = [
        f"Spending on paid APIs (ledger: {ledger.path}, copy: {ledger.mirror})",
        (
            "Every process using these keys (web server, CLI, cron) must use this same "
            "ledger file: set FPL_USAGE_PATH to it."
        ),
        "",
    ]
    try:
        snap = ledger.snapshot(now)
    except LedgerError as e:
        return "\n".join(
            lines
            + [
                f"The ledger cannot be read ({e}), so every paid call is stopped.",
                (
                    "Look for a usage.json.corrupt-* copy next to it. Check what was spent at "
                    "each provider, then run `fpl-advisor usage --reset-confirm` to start "
                    "counting from zero (this forgets what was spent this month)."
                ),
            ]
        )
    for p, prov in PROVIDERS.items():
        s = snap[p]
        env_caps = caps_for(p)

        def strict(a, b):
            return b if a is None else a if b is None else min(a, b)

        caps = Caps(  # what is really enforced: the env caps, or stricter ones pinned today
            daily=strict(env_caps.daily, s["cap_day"]),
            monthly=strict(env_caps.monthly, s["cap_month"]),
            calls_per_day=strict(env_caps.calls_per_day, s["cap_calls_day"]),
            floor=env_caps.floor,
        )
        day_end, month_end = resets(p, now)
        tzname = "Pacific time" if prov.tz != "UTC" else "UTC"
        on = bool((os.environ.get(_KEY_VARS[p]) or "").strip())
        lines.append(f"{prov.name} ({'key set' if on else 'no key set: off'})")

        def row(label, spent, cap, calls, p=p):
            used = _fmt(p, spent)
            if cap:
                pct = 100 * spent / cap
                return (
                    f"  {label}: {used} of {_fmt(p, cap)} ({pct:.0f}%), {calls} calls"
                )
            if cap == 0:
                return (
                    f"  {label}: {used}, {calls} calls (cap is 0: paid calls are off)"
                )
            return f"  {label}: {used}, {calls} calls (no cap)"

        lines.append(row("Today", s["spent_day"], caps.daily, s["calls_day"]))
        if caps.calls_per_day is not None:
            lines.append(
                f"  Calls today: {s['calls_day']} of {caps.calls_per_day} allowed"
            )
        lines.append(f"  Today resets in {_until(day_end, now)} (midnight {tzname})")
        lines.append(
            row("This month", s["spent_month"], caps.monthly, s["calls_month"])
        )
        lines.append(f"  This month resets in {_until(month_end, now)}")
        if s["remaining"] is not None:
            lines.append(
                f"  The provider says {s['remaining']:g} {prov.unit} are left on the account"
                + (f" (calls stop below {caps.floor:g})" if caps.floor else "")
            )
        if s["pending_calls"]:
            lines.append(
                f"  {s['pending_calls']} call(s) in progress, holding up to {_fmt(p, s['pending'])}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


_KEY_VARS = {
    "odds": "ODDS_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "youtube": "YOUTUBE_API_KEY",
}

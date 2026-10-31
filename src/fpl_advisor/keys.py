"""API keys: which environment variables hold secrets, the shared `.env` loader, and redaction.

Rules this module enforces:
- Key values are never logged. `install_redaction()` scrubs every log record (message, args,
  exception text and stack) before any handler sees it, plus uncaught exceptions printed by
  Python itself. It is installed by `load_dotenv()`, which every entry point and every paid
  source calls, so it cannot be forgotten.
- `redact()` removes the values currently loaded from the environment and anything shaped like
  a key (`apiKey=` / `key=` query params, `Bearer` tokens, OpenRouter `sk-or-` keys, Google
  `AIza` keys, an `X-Goog-Api-Key` header).
- The loader never overrides real environment variables, never logs values and warns when
  `.env` can be read by other users on the machine.

Named `keys` because `secrets` would shadow the standard library module.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import sys
import threading
import traceback
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)

# Environment variables whose values are secrets. Add new keys here.
SECRET_ENV_VARS = (
    "ODDS_API_KEY",
    "OPENROUTER_API_KEY",
    "YOUTUBE_API_KEY",
    "NTFY_TOKEN",
)

# Everything the repo-root `.env` may set. Other lines are ignored.
DOTENV_VARS = frozenset(
    {
        *SECRET_ENV_VARS,
        "OPENROUTER_MODEL",
        "OPENROUTER_MODEL_PRICE",
        "FPL_DATA_DIR",
        "FPL_ALERTS",
        "FPL_USAGE_PATH",
        "FPL_USAGE_MIRROR",
        "FPL_NEWS_FEEDS",
        "FPL_CREATORS",
        "ODDS_MONTHLY_BUDGET",
        "ODDS_DAILY_BUDGET",
        "OPENROUTER_MONTHLY_BUDGET_USD",
        "OPENROUTER_DAILY_BUDGET_USD",
        "OPENROUTER_MAX_CALLS_PER_DAY",
        "YOUTUBE_DAILY_UNITS",
    }
)

DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"
MASK = "***"
MIN_SECRET_LEN = (
    8  # shorter values are not treated as keys (they would mask ordinary words)
)

_PATTERNS = (
    # ?apiKey=..., &key=..., ;token=...
    (
        re.compile(
            r"(?i)([?&;](?:api[_-]?key|key|access[_-]?token|token)=)[^&\s#\"'<>]+"
        ),
        r"\1" + MASK,
    ),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{6,}"), r"\1" + MASK),
    (
        re.compile(r"(?i)(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;}]+"),
        r"\1" + MASK,
    ),
    (re.compile(r"sk-or-[A-Za-z0-9_-]{8,}"), MASK),
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), MASK),
)


def secret_values(env: Mapping[str, str] | None = None) -> list[str]:
    """The secret values currently set (longest first, so overlaps are fully masked)."""
    env = os.environ if env is None else env
    vals = {
        v.strip()
        for k in SECRET_ENV_VARS
        if len((v := env.get(k) or "").strip()) >= MIN_SECRET_LEN
    }
    return sorted(vals, key=len, reverse=True)


def redact(text: object) -> str:
    """`text` with every loaded key value and every key-shaped string replaced by ***."""
    s = text if isinstance(text, str) else str(text)
    if not s:
        return s
    for v in secret_values():
        s = s.replace(v, MASK)
        enc = quote(v, safe="")
        if enc != v:
            s = s.replace(enc, MASK)
    for pat, rep in _PATTERNS:
        s = pat.sub(rep, s)
    return s


# --------------------------------------------------------------------------------- logging


_PLAIN = (int, float, bool, type(None))


def _scrub_arg(a: object) -> object:
    """A log argument with keys removed, keeping its type where it is safe to.

    Strings are redacted; numbers and None are kept as they are (formatters such as
    uvicorn's access log rely on them); any other object is kept unless its text holds a key,
    in which case it becomes the redacted text."""
    if isinstance(a, str):
        return redact(a)
    if isinstance(a, _PLAIN):
        return a
    try:
        text, rtext = str(a), repr(a)
    except Exception:  # noqa: BLE001 - an object that cannot render cannot leak
        return a
    if redact(text) != text or redact(rtext) != rtext:
        return redact(text)
    return a


def scrub_record(record: logging.LogRecord) -> None:
    """Redact a record in place: message, each argument (same tuple/mapping shape),
    exception and stack text."""
    if getattr(record, "_fpl_redacted", False):
        return
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    elif not isinstance(record.msg, _PLAIN):
        record.msg = _scrub_arg(record.msg)
    args = record.args
    if isinstance(args, tuple):
        record.args = tuple(_scrub_arg(a) for a in args)
    elif isinstance(args, Mapping):
        record.args = {k: _scrub_arg(v) for k, v in args.items()}
    if record.exc_info:
        text = record.exc_text or logging.Formatter().formatException(record.exc_info)
        # Drop the live exception: a handler with its own formatter must not re-render it.
        record.exc_info, record.exc_text = None, redact(text)
    elif record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    record._fpl_redacted = True


class RedactingFilter(logging.Filter):
    """Logging filter that redacts keys. Never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        scrub_record(record)
        return True


_install_lock = threading.Lock()
_installed = False


def install_redaction() -> None:
    """Redact every log record and uncaught traceback in this process. Safe to call often.

    A filter on the root logger alone is not enough: Python skips logger filters for records
    that propagate up from child loggers. So the record factory is wrapped too, which covers
    every logger and every handler (uvicorn's, pytest's, ones added later).
    """
    global _installed
    with _install_lock:
        if _installed:
            return
        _installed = True
        old = logging.getLogRecordFactory()

        def factory(*a, **k):
            rec = old(*a, **k)
            scrub_record(rec)
            return rec

        logging.setLogRecordFactory(factory)
        root = logging.getLogger()
        filt = RedactingFilter()
        root.addFilter(filt)
        for h in root.handlers:
            h.addFilter(filt)

        def excepthook(t, v, tb):
            sys.stderr.write(redact("".join(traceback.format_exception(t, v, tb))))

        def thread_hook(args):
            if args.exc_type is SystemExit:
                return
            name = args.thread.name if args.thread else "?"
            tb = "".join(
                traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback
                )
            )
            sys.stderr.write(redact(f"Exception in thread {name}:\n{tb}"))

        sys.excepthook = excepthook
        threading.excepthook = thread_hook


# ----------------------------------------------------------------------------------- .env

_warned_perms: set[str] = set()


def check_permissions(path: Path) -> bool:
    """Warn (once) if `path` can be read or written by group/others. True if it is private."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return True
    if not mode & (stat.S_IRWXG | stat.S_IRWXO):
        return True
    if str(path) not in _warned_perms:
        _warned_perms.add(str(path))
        log.warning(
            "%s can be read by other users on this machine. Make it private with: "
            "chmod 600 %s",
            path.name,
            path,
        )
    return False


def load_dotenv(path: Path | None = None) -> None:
    """Load known settings from the repo-root `.env` (if present) and install redaction.

    Never overrides real environment variables and never logs values."""
    install_redaction()
    path = Path(path) if path is not None else DOTENV_PATH
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    check_permissions(path)
    for line in lines:
        k, sep, v = line.strip().removeprefix("export ").partition("=")
        k = k.strip()
        if sep and k in DOTENV_VARS and k not in os.environ:
            os.environ[k] = v.strip().strip("'\"")

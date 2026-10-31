"""Configuration: defaults < TOML file < environment variables < explicit overrides."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

NOTIFY_CHOICES = ("stdout", "ntfy")
RISK_CHOICES = ("auto", "low", "medium", "high")
# TOML tables read by other modules (not Config fields), e.g. [creators] in creators.py.
OTHER_TABLES = ("creators",)


class ConfigError(ValueError):
    """Raised when the configuration is missing or invalid."""


@dataclass
class Config:
    team_id: int
    league_id: int | None = None
    notify: str = "stdout"
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str | None = None
    ntfy_token: str | None = None
    reminder_hours: list[int] = field(default_factory=lambda: [24, 2])
    risk: str = "auto"
    max_hits: int = 2
    cache_dir: str = ".cache"
    state_path: str = "state/alerts.json"


# env var name -> Config field
ENV_VARS = {
    "FPL_TEAM_ID": "team_id",
    "FPL_LEAGUE_ID": "league_id",
    "FPL_NOTIFY": "notify",
    "NTFY_SERVER": "ntfy_server",
    "NTFY_TOPIC": "ntfy_topic",
    "NTFY_TOKEN": "ntfy_token",
    "FPL_REMINDER_HOURS": "reminder_hours",
    "FPL_RISK": "risk",
    "FPL_MAX_HITS": "max_hits",
}

_TEAM_ID_HELP = (
    "No FPL team ID configured. Find yours by opening the 'Points' page on "
    "fantasy.premierleague.com: the URL looks like .../entry/1234567/event/10 and "
    "the number after /entry/ is your team ID. Then set it with FPL_TEAM_ID=<id>, "
    "pass --team, or put team_id = <id> in fpl-advisor.toml."
)


def _default_paths() -> list[Path]:
    return [
        Path("fpl-advisor.toml"),
        Path.home() / ".config" / "fpl-advisor" / "config.toml",
    ]


def _read_toml(path: str | None) -> dict[str, Any]:
    if path is not None:
        p = Path(path).expanduser()
        if not p.is_file():
            raise ConfigError(f"Config file not found: {p}")
        candidates = [p]
    else:
        candidates = [p for p in _default_paths() if p.is_file()]
    if not candidates:
        return {}
    try:
        with candidates[0].open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Could not parse {candidates[0]}: {e}") from e


def read_table(name: str, path: str | None = None) -> dict[str, Any]:
    """One top-level table (e.g. "creators") from the config file, or {} if absent/invalid."""
    try:
        value = _read_toml(path).get(name)
    except ConfigError:
        return {}
    return value if isinstance(value, dict) else {}


def _parse_hours(value: Any) -> list[int]:
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = [value]
    try:
        hours = [int(p) for p in parts]
    except (TypeError, ValueError):
        raise ConfigError(
            f"reminder_hours must be whole numbers like '24,2', got {value!r}"
        ) from None
    if any(h <= 0 for h in hours):
        raise ConfigError(f"reminder_hours must be positive, got {value!r}")
    return hours


def _int(name: str, value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a whole number, got {value!r}") from None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def load_config(
    path: str | None = None,
    env: Mapping[str, str] = os.environ,
    **overrides: Any,
) -> Config:
    """Build a Config. Precedence: overrides > env > TOML file > defaults."""
    known = {f.name for f in fields(Config)}
    merged: dict[str, Any] = {}

    for key, value in _read_toml(path).items():
        if key in OTHER_TABLES:
            continue
        if key not in known:
            raise ConfigError(
                f"Unknown setting {key!r} in config file. Valid settings: "
                + ", ".join(sorted(known))
            )
        if not _blank(value):
            merged[key] = value

    for var, key in ENV_VARS.items():
        value = env.get(var)
        if not _blank(value):
            merged[key] = value.strip()  # type: ignore[union-attr]

    for key, value in overrides.items():
        if key not in known:
            raise ConfigError(f"Unknown setting {key!r}")
        if not _blank(value):
            merged[key] = value

    if "team_id" not in merged:
        raise ConfigError(_TEAM_ID_HELP)

    merged["team_id"] = _int("team_id", merged["team_id"])
    if "league_id" in merged:
        merged["league_id"] = _int("league_id", merged["league_id"])
    if "max_hits" in merged:
        merged["max_hits"] = _int("max_hits", merged["max_hits"])
        if merged["max_hits"] < 0:
            raise ConfigError("max_hits cannot be negative")
    if "reminder_hours" in merged:
        merged["reminder_hours"] = _parse_hours(merged["reminder_hours"])

    cfg = Config(**merged)

    cfg.notify = cfg.notify.lower()
    cfg.risk = cfg.risk.lower()
    if cfg.notify not in NOTIFY_CHOICES:
        raise ConfigError(f"notify must be one of {NOTIFY_CHOICES}, got {cfg.notify!r}")
    if cfg.risk not in RISK_CHOICES:
        raise ConfigError(f"risk must be one of {RISK_CHOICES}, got {cfg.risk!r}")
    if cfg.notify == "ntfy" and not cfg.ntfy_topic:
        raise ConfigError(
            "notify is 'ntfy' but no topic is set. Pick a long, hard-to-guess topic "
            "name (it acts like a password), subscribe to it in the ntfy phone app, "
            'then set NTFY_TOPIC=<topic> or ntfy_topic = "<topic>" in fpl-advisor.toml.'
        )
    cfg.ntfy_server = cfg.ntfy_server.rstrip("/")
    return cfg

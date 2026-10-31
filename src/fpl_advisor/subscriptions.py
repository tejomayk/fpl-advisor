"""SQLite storage for hosted alert subscriptions and their sent-alert memory."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .state import PRUNE_AFTER

log = logging.getLogger("fpl_advisor.alerts")

INACTIVE_KEEP = timedelta(days=30)
# Columns added after the first release; existing databases get them via ALTER TABLE.
_MIGRATIONS = {
    "inactive_reason": "TEXT",
    "deactivated_at": "TEXT",
    "last_full_build": "TEXT",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    topic TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    league_id INTEGER,
    reminder_hours TEXT NOT NULL,
    created TEXT NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    inactive_reason TEXT,
    deactivated_at TEXT,
    last_full_build TEXT
);
CREATE TABLE IF NOT EXISTS alert_state (
    topic TEXT NOT NULL,
    key TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (topic, key)
);
"""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def default_db_path() -> Path:
    return Path(os.environ.get("FPL_DATA_DIR", "data")) / "alerts.db"


@dataclass
class Subscription:
    topic: str
    team_id: int
    league_id: int | None
    reminder_hours: list[int]
    created: str
    failures: int = 0
    active: bool = True
    inactive_reason: str | None = None
    last_full_build: str | None = None


def _row_to_sub(r: sqlite3.Row) -> Subscription:
    return Subscription(
        topic=r["topic"],
        team_id=r["team_id"],
        league_id=r["league_id"],
        reminder_hours=[int(h) for h in r["reminder_hours"].split(",") if h],
        created=r["created"],
        failures=r["failures"],
        active=bool(r["active"]),
        inactive_reason=r["inactive_reason"],
        last_full_build=r["last_full_build"],
    )


def check_data_dir(path: str | os.PathLike[str] | None = None) -> bool:
    """Log a clear error and return False if the data directory can't be written to."""
    d = Path(path) if path is not None else default_db_path().parent
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".write-test-{os.getpid()}"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError as e:
        log.error(
            "Data directory %s is not writable (%s). Alerts and the cache need a writable "
            "persistent volume at FPL_DATA_DIR; fix the volume's permissions (see docs/deploy.md).",
            d,
            e,
        )
        return False


class SubscriptionStore:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path is not None else default_db_path()
        check_data_dir(self.path.parent)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._conn()) as c, c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(subscriptions)")}
            for col, typ in _MIGRATIONS.items():
                if col not in have:
                    c.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} {typ}")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _run(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock, closing(self._conn()) as c, c:
            return c.execute(sql, args).fetchall()

    def create(
        self,
        topic: str,
        token: str,
        team_id: int,
        league_id: int | None,
        reminder_hours: list[int],
    ) -> None:
        self._run(
            "INSERT INTO subscriptions (topic, token_hash, team_id, league_id,"
            " reminder_hours, created) VALUES (?,?,?,?,?,?)",
            (
                topic,
                hash_token(token),
                team_id,
                league_id,
                ",".join(str(h) for h in reminder_hours),
                datetime.now(UTC).isoformat(),
            ),
        )

    def get(self, topic: str) -> Subscription | None:
        rows = self._run("SELECT * FROM subscriptions WHERE topic=?", (topic,))
        return _row_to_sub(rows[0]) if rows else None

    def verify(self, topic: str, token: str) -> bool:
        rows = self._run("SELECT token_hash FROM subscriptions WHERE topic=?", (topic,))
        if not rows:
            return False
        return hmac.compare_digest(rows[0]["token_hash"], hash_token(token))

    def delete(self, topic: str) -> None:
        with self._lock, closing(self._conn()) as c, c:
            c.execute("DELETE FROM subscriptions WHERE topic=?", (topic,))
            c.execute("DELETE FROM alert_state WHERE topic=?", (topic,))

    def count(self) -> int:
        """Active subscriptions only: inactive rows don't use up capacity."""
        return self._run("SELECT COUNT(*) AS n FROM subscriptions WHERE active=1")[0][
            "n"
        ]

    def count_for_team(self, team_id: int) -> int:
        rows = self._run(
            "SELECT COUNT(*) AS n FROM subscriptions WHERE active=1 AND team_id=?",
            (team_id,),
        )
        return rows[0]["n"]

    def deactivate_all(self, reason: str) -> int:
        """Deactivate every active subscription. Returns how many changed."""
        with self._lock, closing(self._conn()) as c, c:
            cur = c.execute(
                "UPDATE subscriptions SET active=0, inactive_reason=?, deactivated_at=?"
                " WHERE active=1",
                (reason, datetime.now(UTC).isoformat()),
            )
            return cur.rowcount

    def prune_inactive(self, now: datetime | None = None) -> int:
        """Delete subscriptions that have been inactive for over 30 days."""
        cutoff = ((now or datetime.now(UTC)) - INACTIVE_KEEP).isoformat()
        with self._lock, closing(self._conn()) as c, c:
            c.execute(
                "DELETE FROM alert_state WHERE topic IN (SELECT topic FROM subscriptions"
                " WHERE active=0 AND COALESCE(deactivated_at, created) < ?)",
                (cutoff,),
            )
            cur = c.execute(
                "DELETE FROM subscriptions WHERE active=0"
                " AND COALESCE(deactivated_at, created) < ?",
                (cutoff,),
            )
            return cur.rowcount

    def set_last_full_build(self, topic: str, when: datetime | None = None) -> None:
        self._run(
            "UPDATE subscriptions SET last_full_build=? WHERE topic=?",
            ((when or datetime.now(UTC)).isoformat(), topic),
        )

    def list_active(self) -> list[Subscription]:
        rows = self._run("SELECT * FROM subscriptions WHERE active=1 ORDER BY created")
        return [_row_to_sub(r) for r in rows]

    def record_success(self, topic: str) -> None:
        self._run("UPDATE subscriptions SET failures=0 WHERE topic=?", (topic,))

    def record_failure(self, topic: str, max_failures: int = 48) -> int:
        """Count a failure; deactivate at max_failures. Returns the new count."""
        self._run(
            "UPDATE subscriptions SET failures=failures+1,"
            " active=CASE WHEN failures+1>=? THEN 0 ELSE active END,"
            " inactive_reason=CASE WHEN failures+1>=? AND active=1"
            " THEN 'too_many_failures' ELSE inactive_reason END,"
            " deactivated_at=CASE WHEN failures+1>=? AND active=1"
            " THEN ? ELSE deactivated_at END WHERE topic=?",
            (
                max_failures,
                max_failures,
                max_failures,
                datetime.now(UTC).isoformat(),
                topic,
            ),
        )
        rows = self._run("SELECT failures FROM subscriptions WHERE topic=?", (topic,))
        return rows[0]["failures"] if rows else 0

    # alert_state helpers
    def state_seen(self, topic: str, key: str) -> bool:
        return bool(
            self._run("SELECT 1 FROM alert_state WHERE topic=? AND key=?", (topic, key))
        )

    def state_write(self, topic: str, keys: dict[str, str]) -> None:
        cutoff = (datetime.now(UTC) - PRUNE_AFTER).isoformat()
        with self._lock, closing(self._conn()) as c, c:
            c.executemany(
                "INSERT OR REPLACE INTO alert_state (topic, key, ts) VALUES (?,?,?)",
                [(topic, k, ts) for k, ts in keys.items()],
            )
            c.execute("DELETE FROM alert_state WHERE topic=? AND ts<?", (topic, cutoff))


class DbAlertState:
    """Same interface as state.AlertState, backed by the subscriptions database."""

    def __init__(self, store: SubscriptionStore, topic: str):
        self.store, self.topic = store, topic
        self._pending: dict[str, str] = {}

    def seen(self, key: str) -> bool:
        return key in self._pending or self.store.state_seen(self.topic, key)

    def mark(self, key: str) -> None:
        self._pending[key] = datetime.now(UTC).isoformat()

    def save(self) -> None:
        if self._pending:
            self.store.state_write(self.topic, self._pending)
            self._pending = {}

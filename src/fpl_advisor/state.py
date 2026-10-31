"""Remembers which alerts were already sent, so each fires once."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PRUNE_AFTER = timedelta(days=45)


class AlertState:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._data: dict[str, str] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): str(v) for k, v in raw.items()}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass  # missing or corrupt state just means "nothing sent yet"

    def seen(self, key: str) -> bool:
        return key in self._data

    def mark(self, key: str) -> None:
        self._data[key] = datetime.now(UTC).isoformat()

    def _prune(self) -> None:
        cutoff = datetime.now(UTC) - PRUNE_AFTER
        keep = {}
        for k, v in self._data.items():
            try:
                ts = datetime.fromisoformat(v)
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if ts >= cutoff:
                keep[k] = v
        self._data = keep

    def save(self) -> None:
        self._prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=".alerts-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

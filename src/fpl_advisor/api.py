"""Thin client for FPL's public API, with a small on-disk cache to stay polite."""

from __future__ import annotations

import email.utils
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://fantasy.premierleague.com/api/"
USER_AGENT = "fpl-advisor/0.1 (open-source FPL helper)"


class FPLError(RuntimeError):
    """A failed FPL request. `status` is the HTTP code (None for network errors); `retry_after`
    is the server's Retry-After in seconds, when it sent one."""

    def __init__(
        self, msg: str, status: int | None = None, retry_after: float | None = None
    ):
        super().__init__(msg)
        self.status = status
        self.retry_after = retry_after


def _retry_after(value: str | None) -> float | None:
    """Retry-After as seconds: either a number or an HTTP date."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


class FPLClient:
    def __init__(self, cache_dir: Path | str | None = ".cache", ttl_seconds: int = 900):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.ttl = ttl_seconds

    def get(self, path: str, ttl: int | None = None) -> dict | list:
        ttl = self.ttl if ttl is None else ttl
        cache_file = None
        if self.cache_dir:
            cache_file = self.cache_dir / (path.strip("/").replace("/", "_") + ".json")
            if cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
                return json.loads(cache_file.read_text())
        data = self._fetch(BASE + path)
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so concurrent requests never read a half-written file.
            tmp = cache_file.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, cache_file)
        return data

    def get_uncached(self, path: str, retries: int = 3) -> dict | list:
        """Fetch without touching the disk cache (for bulk scans of thousands of entries).

        Bulk scans pass `retries=1` and do their own (single layer of) retrying.
        """
        return self._fetch(BASE + path, retries=retries)

    def _fetch(self, url: str, retries: int = 3) -> dict | list:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        status = retry_after = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise FPLError(
                        f"Not found: {url} (check your team/league ID)", status=404
                    ) from e
                if e.code == 503:
                    raise FPLError(
                        "FPL is updating (503). Try again in a few minutes.",
                        status=503,
                    ) from e
                err, status = e, e.code
                retry_after = _retry_after(
                    e.headers.get("Retry-After") if e.headers else None
                )
            except (urllib.error.URLError, TimeoutError) as e:
                err, status, retry_after = e, None, None
            if attempt < retries - 1:
                time.sleep(2**attempt)
        raise FPLError(
            f"Failed to fetch {url}: {err}", status=status, retry_after=retry_after
        )

    # Convenience wrappers
    def bootstrap(self):
        return self.get("bootstrap-static/")

    def fixtures(self):
        return self.get("fixtures/")

    def entry(self, team_id: int):
        return self.get(f"entry/{team_id}/")

    def history(self, team_id: int):
        return self.get(f"entry/{team_id}/history/")

    def transfers(self, team_id: int):
        return self.get(f"entry/{team_id}/transfers/")

    def picks(self, team_id: int, event: int):
        return self.get(f"entry/{team_id}/event/{event}/picks/", ttl=86400)

    def element_summary(self, player_id: int, ttl: int = 6 * 3600):
        return self.get(f"element-summary/{player_id}/", ttl=ttl)

    def element_summaries(
        self, player_ids, workers: int = 8, ttl: int = 6 * 3600
    ) -> dict[int, dict]:
        from concurrent.futures import ThreadPoolExecutor

        ids = list(player_ids)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = pool.map(lambda pid: self.element_summary(pid, ttl), ids)
            return dict(zip(ids, results))

    def live(self, event: int):
        return self.get(f"event/{event}/live/", ttl=3600)

    def league(self, league_id: int, page: int = 1):
        return self.get(f"leagues-classic/{league_id}/standings/?page_standings={page}")


def prune_cache(cache_dir: Path | str | None, max_age_seconds: float) -> int:
    """Delete cache files older than `max_age_seconds`. Returns how many were removed."""
    if not cache_dir or not Path(cache_dir).exists():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for f in Path(cache_dir).iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except FileNotFoundError:  # another thread got there first
            continue
    return removed

"""Where shared state lives: local files, plus a read-only HTTPS copy of the web snapshot.

One small interface (`get`/`put` bytes or JSON by key, plus `delete`), two backends:

- `LocalStore(root)`: files under `root` (FPL_DATA_DIR, default ./data). Used by Docker, the CLI,
  the tests and the scheduled jobs. In GitHub Actions the jobs keep this folder (signal caches,
  the elite list, the spend ledger, the last web snapshot) private in the Actions cache between
  runs; see .github/workflows/refresh.yml and docs/deploy.md. The usage ledger keeps its own
  `fcntl` lock on its files.
- `HttpSnapshotStore(url)`: read-only. The website (serverless mode) reads the web snapshot the
  refresh job published as a GitHub release asset. `web_store()` picks it.

What is stored, by key:

- `web/snapshot.json.gz`: what a web function needs, i.e. the FPL world the refresh job loaded and
  the merged signals (`jobs.make_snapshot`). Written by the refresh job, read by the website.
  Public: it holds only what the site shows, never keys, the ledger or raw paid-API responses.

Concurrency: one writer only. The scheduled jobs share one GitHub Actions `concurrency: refresh`
group that never cancels a running job; web functions only read.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

log = logging.getLogger(__name__)

WEB_SNAPSHOT_KEY = "web/snapshot.json.gz"

# The website's copy of the snapshot: a GitHub release asset (see refresh.yml).
SNAPSHOT_URL_VAR = "FPL_SNAPSHOT_URL"
SNAPSHOT_RELEASE_TAG = "snapshot"
SNAPSHOT_ASSET = "snapshot.json.gz"
TIMEOUT = 15
RETRIES = 3


class StoreError(RuntimeError):
    """A read or write that could not be completed. Its text never holds a token or URL."""


class ConflictError(StoreError):
    """`put(..., if_match=...)` found a copy other than the one that was read."""


@dataclass
class Item:
    data: bytes
    etag: str | None = None


def _check_key(key: str) -> str:
    p = PurePosixPath(key)
    if not key or p.is_absolute() or ".." in p.parts or key != str(p):
        raise ValueError(f"bad store key {key!r}")
    return key


class Store:
    """Subclasses implement `_read`, `_put` and `_delete`."""

    kind = "base"

    def read(self, key: str, fresh: bool = True) -> Item | None:
        """None when the key doesn't exist. `fresh=False` marks a read that may be served from a
        cache (website reads); the current backends always read the current copy."""
        return self._read(_check_key(key), fresh)

    def get(self, key: str, fresh: bool = True) -> bytes | None:
        item = self.read(key, fresh)
        return item.data if item else None

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        if_match: str | None = None,
    ) -> str | None:
        """Write `data`; returns the new ETag. With `if_match`, refuse (ConflictError) unless
        the stored copy still has that ETag."""
        return self._put(_check_key(key), data, content_type, if_match)

    def delete(self, key: str) -> None:
        self._delete(_check_key(key))

    # JSON helpers; a key ending in .gz is gzipped.
    def get_json(self, key: str, fresh: bool = True):
        raw = self.get(key, fresh)
        if raw is None:
            return None
        return json.loads(gzip.decompress(raw) if key.endswith(".gz") else raw)

    def put_json(self, key: str, value, if_match: str | None = None) -> str | None:
        data = json.dumps(value, separators=(",", ":")).encode()
        if key.endswith(".gz"):
            # mtime=0: the same JSON always gives the same bytes.
            return self.put(
                key, gzip.compress(data, mtime=0), "application/gzip", if_match
            )
        return self.put(key, data, "application/json", if_match)

    def _read(self, key: str, fresh: bool) -> Item | None:  # pragma: no cover
        raise NotImplementedError

    def _put(self, key, data, content_type, if_match) -> str | None:  # pragma: no cover
        raise NotImplementedError

    def _delete(self, key: str) -> None:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------- local files


class LocalStore(Store):
    kind = "local"

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def __repr__(self) -> str:
        return f"LocalStore({str(self.root)!r})"

    @staticmethod
    def _etag(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()[:32]

    def _read(self, key, fresh):
        try:
            data = (self.root / key).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise StoreError(f"cannot read {key} ({type(e).__name__})") from e
        return Item(data, self._etag(data))

    def _put(self, key, data, content_type, if_match):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if if_match is not None:
            current = self._read(key, True)
            if current is not None and current.etag != if_match:
                raise ConflictError(f"{key} changed since it was read")
        # Write-then-rename: readers never see half a file.
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return self._etag(data)

    def _delete(self, key):
        (self.root / key).unlink(missing_ok=True)


# ---------------------------------------------------------------- read-only over HTTPS


class HttpSnapshotStore(Store):
    """Read-only: the web snapshot at one HTTPS URL (a GitHub release asset).

    GitHub answers the release download URL with a redirect to its asset host, which urllib
    follows. 404 means no snapshot yet (None). Other failures are retried a couple of times, then
    raised as StoreError naming only the status. Writes are refused."""

    kind = "http"

    def __init__(
        self,
        url: str,
        key: str = WEB_SNAPSHOT_KEY,
        opener=None,
        sleep=time.sleep,
        timeout: float = TIMEOUT,
    ):
        if not url.startswith("https://") and not url.startswith("http://"):
            raise StoreError(f"{SNAPSHOT_URL_VAR} must be an http(s) URL")
        self.url, self.key = url, _check_key(key)
        self._open = opener or urllib.request.urlopen
        self._sleep, self._timeout = sleep, timeout

    def __repr__(self) -> str:
        return f"HttpSnapshotStore({self.url!r})"

    def _read(self, key, fresh):
        if key != self.key:
            return None  # this store holds the web snapshot only
        last = "unknown error"
        for attempt in range(RETRIES):
            req = urllib.request.Request(
                self.url,
                headers={"user-agent": "fpl-advisor", "accept": "*/*"},
            )
            try:
                with self._open(req, timeout=self._timeout) as resp:
                    hdrs = {k.lower(): v for k, v in dict(resp.headers).items()}
                    return Item(resp.read(), hdrs.get("etag"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                last = f"HTTP {e.code}"
                if e.code in (400, 401, 403, 410):
                    break  # retrying won't help
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = type(e).__name__
            if attempt < RETRIES - 1:
                self._sleep(2**attempt)
        raise StoreError(f"snapshot download failed ({last})")

    def _put(self, key, data, content_type, if_match):
        raise StoreError("the snapshot URL is read-only")

    def _delete(self, key):
        raise StoreError("the snapshot URL is read-only")


# ---------------------------------------------------------------- choosing one

_GH_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def snapshot_url(env: Mapping[str, str] | None = None) -> str | None:
    """Where the website reads the snapshot: FPL_SNAPSHOT_URL, else the `snapshot` release of the
    GitHub repo Vercel deployed from (VERCEL_GIT_REPO_OWNER / VERCEL_GIT_REPO_SLUG, which Vercel
    sets at build and run time), else None."""
    env = os.environ if env is None else env
    url = (env.get(SNAPSHOT_URL_VAR) or "").strip()
    if url:
        return url
    provider = (env.get("VERCEL_GIT_PROVIDER") or "github").strip().lower()
    owner = (env.get("VERCEL_GIT_REPO_OWNER") or "").strip()
    repo = (env.get("VERCEL_GIT_REPO_SLUG") or "").strip()
    if provider != "github" or not (_GH_NAME.match(owner) and _GH_NAME.match(repo)):
        return None
    return (
        f"https://github.com/{owner}/{repo}/releases/download/"
        f"{SNAPSHOT_RELEASE_TAG}/{SNAPSHOT_ASSET}"
    )


def default(env: Mapping[str, str] | None = None) -> LocalStore:
    """Files under FPL_DATA_DIR (default ./data): the jobs, the CLI and the server."""
    env = os.environ if env is None else env
    return LocalStore(env.get("FPL_DATA_DIR") or "data")


def web_store(env: Mapping[str, str] | None = None) -> Store:
    """What the serverless website reads its snapshot from: the published HTTPS copy when a URL
    is known (see `snapshot_url`), otherwise local files as `default`."""
    url = snapshot_url(env)
    return HttpSnapshotStore(url) if url else default(env)

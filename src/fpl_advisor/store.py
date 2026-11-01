"""Where shared state lives: local files (default) or Vercel Blob (BLOB_READ_WRITE_TOKEN or BLOB_STORE_ID).

One small interface (`get`/`put` bytes or JSON by key, plus `delete`), two backends:

- `LocalStore(root)`: files under `root` (FPL_DATA_DIR, default ./data). Today's behaviour, used by
  Docker, the CLI and the tests. The usage ledger keeps its own `fcntl` lock on its files.
- `BlobStore(token)`: Vercel Blob over its REST API (standard library `urllib`, no SDK). Blobs are
  written with **private** access, so nothing (in particular the spend ledger) is publicly
  readable; every read sends the token.
- `BlobStore.oidc(store_id)`: the same store reached the way Vercel now connects stores to a
  project: `BLOB_STORE_ID` plus the short-lived OIDC token Vercel hands each function request in
  the `x-vercel-oidc-token` header (the website puts it in `request_oidc_token`), or
  `VERCEL_OIDC_TOKEN` outside a request. No long-lived token sits in the Vercel project. The
  GitHub job runs outside Vercel, so it still uses the read-write token.

What is stored, by key:

- `web/snapshot.json.gz`: what a web function needs, i.e. the FPL world the refresh job loaded and
  the merged signals. Written by the refresh job, read by the website.
- `job/state.tar.gz` (Blob only): the refresh job's working folder (`signals/` with every source's
  cache and the elite list, `usage/` and `usage.mirror/` with the spend ledger). Packed into one
  blob because Vercel Blob's free plan allows only 2,000 uploads a month; a job run downloads it
  once and uploads it only if a byte changed. With a LocalStore those folders are simply used in
  place (`job_state` below is then a no-op), exactly as before.

Concurrency. Blob has no file locks, so the design relies on a **single writer**: only the
scheduled job (GitHub Actions, one `concurrency: refresh` group that never cancels a running job)
writes; web functions only read. As a second guard the job state is written with `x-if-match`
on the ETag it was read with, so a copy written by anyone else in the meantime is never replaced
(the write fails loudly instead).

Operation budget per refresh run on Blob: 2 reads (job state, previous web snapshot) and 0-2
uploads (only what changed). No `list` calls (they count as uploads on the free plan).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import os
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

log = logging.getLogger(__name__)

TOKEN_VAR = "BLOB_READ_WRITE_TOKEN"
STORE_ID_VAR = "BLOB_STORE_ID"  # set by Vercel when a store is connected; not a secret
OIDC_VAR = "VERCEL_OIDC_TOKEN"
OIDC_HEADER = "x-vercel-oidc-token"
# The OIDC token of the request being served (set by the website per request).
request_oidc_token: ContextVar[str | None] = ContextVar(
    "request_oidc_token", default=None
)
BLOB_API = "https://vercel.com/api/blob"
BLOB_API_VERSION = "13"  # what the official @vercel/blob SDK sends
TIMEOUT = 30
RETRIES = 3
# CDN cache time for reads that allow it (the website's). Overwrites are purged by Vercel, and the
# refresh job always reads uncached, so this only saves read operations.
CACHE_SECONDS = 1800

WEB_SNAPSHOT_KEY = "web/snapshot.json.gz"
JOB_STATE_KEY = "job/state.tar.gz"
JOB_FOLDERS = ("signals", "usage", "usage.mirror")


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


def _norm_etag(etag: str | None) -> str | None:
    if not etag:
        return None
    return etag.removeprefix("W/").strip('"')


class Store:
    """Subclasses implement `_read`, `_put` and `_delete`."""

    kind = "base"

    def read(self, key: str, fresh: bool = True) -> Item | None:
        """None when the key doesn't exist. `fresh=False` lets a CDN answer (website reads)."""
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


# ---------------------------------------------------------------- Vercel Blob


def _store_id(token: str) -> str:
    # Tokens look like vercel_blob_rw_<storeId>_<secret>. The store id is not secret (it is the
    # first label of every blob URL); the last part is.
    parts = token.split("_")
    if len(parts) < 5 or parts[:3] != ["vercel", "blob", "rw"] or not parts[3]:
        raise StoreError(f"{TOKEN_VAR} is not in the expected vercel_blob_rw_... form")
    return parts[3].lower().removeprefix("store_")


class BlobStore(Store):
    """Vercel Blob, private access, via the REST API the official SDK uses."""

    kind = "blob"

    def __init__(self, token: str, opener=None, sleep=time.sleep):
        self._token: Callable[[], str | None] = lambda: token
        self._oidc = False
        self.store_id = _store_id(token)
        self.host = f"https://{self.store_id}.private.blob.vercel-storage.com"
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep

    @classmethod
    def oidc(
        cls, store_id: str, token=None, opener=None, sleep=time.sleep
    ) -> BlobStore:
        """Reach the store with Vercel's OIDC token. `token` returns the current one (default:
        this request's header, else VERCEL_OIDC_TOKEN); it is looked up on every call because
        Vercel rotates it."""
        sid = store_id.strip().removeprefix("store_").lower()
        if not sid or not sid.isalnum():
            raise StoreError(f"{STORE_ID_VAR} is not a Blob store id")
        st = cls.__new__(cls)
        st._token = token or _current_oidc_token
        st._oidc = True
        st._store_header = store_id.strip().removeprefix("store_")
        st.store_id = sid
        st.host = f"https://{sid}.private.blob.vercel-storage.com"
        st._open = opener or urllib.request.urlopen
        st._sleep = sleep
        return st

    def __repr__(self) -> str:  # never show the token
        return f"BlobStore(store_id={self.store_id!r})"

    def _call(self, method: str, url: str, data: bytes | None = None, headers=None):
        """(status, headers, body). A 404 is returned, not raised. Other failures are retried,
        then raised as StoreError naming only the status (URL and headers carry the token)."""
        token = (self._token() or "").strip()
        if not token:
            raise StoreError(
                f"Vercel Blob {method} failed (no OIDC token for this request)"
            )
        auth = {"authorization": f"Bearer {token}"}
        if self._oidc:  # an OIDC token doesn't name the store, so say which one
            auth["x-vercel-blob-store-id"] = self._store_header
        last = "unknown error"
        for attempt in range(RETRIES):
            h = {**auth, "x-api-version": BLOB_API_VERSION, **(headers or {})}
            req = urllib.request.Request(url, data=data, method=method, headers=h)
            try:
                with self._open(req, timeout=TIMEOUT) as resp:
                    hdrs = {k.lower(): v for k, v in dict(resp.headers).items()}
                    return resp.status, hdrs, resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return 404, {}, b""
                if e.code in (409, 412):
                    raise ConflictError(
                        f"Vercel Blob refused the {method}: the blob changed since it was read "
                        f"(HTTP {e.code})"
                    ) from None
                last = f"HTTP {e.code}"
                if e.code in (400, 401, 403):
                    break  # retrying won't help
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = type(e).__name__
            if attempt < RETRIES - 1:
                self._sleep(2**attempt)
        raise StoreError(f"Vercel Blob {method} failed ({last})")

    def _read(self, key, fresh):
        url = f"{self.host}/{urllib.parse.quote(key)}" + ("?cache=0" if fresh else "")
        status, headers, body = self._call("GET", url)
        if status == 404:
            return None
        return Item(body, headers.get("etag"))

    def head_etag(self, key: str) -> str | None:
        """The ETag from Blob's metadata API (the form `x-if-match` expects)."""
        q = urllib.parse.urlencode({"url": f"{self.host}/{_check_key(key)}"})
        status, _h, body = self._call("GET", f"{BLOB_API}?{q}")
        if status == 404:
            return None
        try:
            return json.loads(body).get("etag")
        except ValueError:
            return None

    def _put(self, key, data, content_type, if_match):
        extra = {
            "x-vercel-blob-access": "private",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
            "x-content-type": content_type,
            "x-cache-control-max-age": str(CACHE_SECONDS),
            "content-type": content_type,
        }
        url = f"{BLOB_API}/?{urllib.parse.urlencode({'pathname': key})}"
        if if_match is None:
            return self._put_once(url, data, extra)
        try:
            return self._put_once(url, data, {**extra, "x-if-match": if_match})
        except ConflictError:
            # The ETag a download returns may be quoted differently from the one the API
            # compares. If the metadata API reports the same tag, it is the same copy: retry
            # with its spelling. A really different copy still fails.
            canonical = self.head_etag(key)
            if canonical is None or _norm_etag(canonical) != _norm_etag(if_match):
                raise
            if canonical == if_match:
                raise
            return self._put_once(url, data, {**extra, "x-if-match": canonical})

    def _put_once(self, url, data, headers):
        _status, h, body = self._call("PUT", url, data, headers)
        try:
            return json.loads(body).get("etag") or h.get("etag")
        except ValueError:
            return h.get("etag")

    def _delete(self, key):
        body = json.dumps({"urls": [f"{self.host}/{key}"]}).encode()
        self._call(
            "POST", f"{BLOB_API}/delete", body, {"content-type": "application/json"}
        )


# ---------------------------------------------------------------- choosing one


def _current_oidc_token() -> str | None:
    return request_oidc_token.get() or os.environ.get(OIDC_VAR)


def default(env: Mapping[str, str] | None = None) -> Store:
    """Blob when BLOB_READ_WRITE_TOKEN is set, else Blob over OIDC when BLOB_STORE_ID is set (a
    Vercel project with a connected store), otherwise files under FPL_DATA_DIR."""
    env = os.environ if env is None else env
    token = (env.get(TOKEN_VAR) or "").strip()
    if token:
        return BlobStore(token)
    store_id = (env.get(STORE_ID_VAR) or "").strip()
    if store_id:
        return BlobStore.oidc(store_id)
    return LocalStore(env.get("FPL_DATA_DIR") or "data")


# ---------------------------------------------------------------- the job's working folder


def pack(root: Path, folders: tuple[str, ...] = JOB_FOLDERS) -> bytes:
    """A tar.gz of `folders` under `root`. Same files in, same bytes out (sorted, no times)."""
    buf = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as tar,
    ):
        for folder in folders:
            base = root / folder
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if not p.is_file() or p.name.endswith(".tmp"):
                    continue
                data = p.read_bytes()
                info = tarfile.TarInfo(p.relative_to(root).as_posix())
                info.size, info.mode, info.mtime = len(data), 0o600, 0
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def unpack(data: bytes, root: Path, folders: tuple[str, ...] = JOB_FOLDERS) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = [
            m
            for m in tar.getmembers()
            if m.isfile() and PurePosixPath(m.name).parts[:1] in [(f,) for f in folders]
        ]
        tar.extractall(root, members=members, filter="data")


@dataclass
class JobState:
    """The job's working folder. For Blob, a copy of `job/state.tar.gz` unpacked into `root`."""

    store: Store
    root: Path
    etag: str | None = None
    pulled_sha: str | None = None
    pushed: list[str] = field(default_factory=list)

    @property
    def remote(self) -> bool:
        return not isinstance(self.store, LocalStore)

    def pull(self) -> None:
        if not self.remote:
            return
        item = self.store.read(
            JOB_STATE_KEY, fresh=True
        )  # raises StoreError: caller stops
        if item is None:
            log.warning(
                "store: no job state yet in %r; starting from empty", self.store
            )
            return
        unpack(item.data, self.root)
        self.etag, self.pulled_sha = item.etag, hashlib.sha256(item.data).hexdigest()

    def push(self) -> bool:
        """Upload the folder if any byte changed. Returns whether it uploaded."""
        if not self.remote:
            return False
        data = pack(self.root)
        sha = hashlib.sha256(data).hexdigest()
        if sha == self.pulled_sha:
            return False
        self.etag = self.store.put(
            JOB_STATE_KEY, data, "application/gzip", if_match=self.etag
        )
        self.pulled_sha = sha
        self.pushed.append(JOB_STATE_KEY)
        return True


@contextmanager
def job_state(store: Store, workdir: Path | str | None = None) -> Iterator[JobState]:
    """The folder the signal sources and the usage ledger work in, not yet pulled.

    LocalStore: its own root, used in place (pull/push do nothing). Blob: `workdir`, or a
    temporary folder removed afterwards. The caller pulls, and decides what a failed push means
    (for the spend ledger: fail loudly)."""
    if isinstance(store, LocalStore):
        yield JobState(store, store.root)
    elif workdir is not None:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        yield JobState(store, Path(workdir))
    else:
        with tempfile.TemporaryDirectory(prefix="fpl-job-") as tmp:
            yield JobState(store, Path(tmp))

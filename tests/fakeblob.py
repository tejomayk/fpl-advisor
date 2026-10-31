"""An in-memory stand-in for the Vercel Blob REST API, used as BlobStore's `opener`."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from urllib.parse import parse_qs, unquote, urlsplit

# Built at runtime so no file holds a token-shaped string.
STORE_ID = "fakestore123"
TOKEN = "_".join(["vercel", "blob", "rw", STORE_ID, "s3cr3t" + "Q" * 20])


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _err(url: str, code: int):
    return urllib.error.HTTPError(url, code, f"HTTP {code}", {}, io.BytesIO(b""))


class FakeBlob:
    def __init__(self):
        self.blobs: dict[str, tuple[bytes, str]] = {}
        self.calls: list[tuple[str, str]] = []  # (kind, pathname)
        self.requests: list = []
        self.fail_next: list[
            int | Exception
        ] = []  # HTTP codes / exceptions to raise next

    @property
    def uploads(self) -> int:
        return sum(1 for k, _ in self.calls if k in ("put", "list"))

    @property
    def reads(self) -> int:
        return sum(1 for k, _ in self.calls if k in ("get", "head"))

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        url = req.full_url
        headers = {k.lower(): v for k, v in req.header_items()}
        if headers.get("authorization") != f"Bearer {TOKEN}":
            raise _err(url, 403)
        if self.fail_next:
            f = self.fail_next.pop(0)
            if isinstance(f, Exception):
                raise f
            raise _err(url, f)
        u = urlsplit(url)
        q = parse_qs(u.query)
        if u.hostname == f"{STORE_ID}.private.blob.vercel-storage.com":
            assert req.get_method() == "GET"
            path = unquote(u.path.lstrip("/"))
            self.calls.append(("get", path))
            if path not in self.blobs:
                raise _err(url, 404)
            data, etag = self.blobs[path]
            return _Resp(data, headers={"ETag": f'"{etag}"'})
        assert u.hostname == "vercel.com" and u.path.startswith("/api/blob")
        assert headers.get("x-api-version")
        if req.get_method() == "PUT":
            path = q["pathname"][0]
            self.calls.append(("put", path))
            assert headers["x-vercel-blob-access"] == "private"
            assert headers["x-add-random-suffix"] == "0"
            assert headers["x-allow-overwrite"] == "1"
            want = headers.get("x-if-match")
            if want is not None:
                have = self.blobs.get(path, (b"", None))[1]
                if want != have:
                    raise _err(url, 412)
            etag = hashlib.sha256(req.data).hexdigest()[:16]
            self.blobs[path] = (req.data, etag)
            return _Resp(json.dumps({"pathname": path, "etag": etag}).encode())
        if req.get_method() == "GET" and "url" in q:
            path = urlsplit(q["url"][0]).path.lstrip("/")
            self.calls.append(("head", path))
            if path not in self.blobs:
                raise _err(url, 404)
            return _Resp(
                json.dumps({"pathname": path, "etag": self.blobs[path][1]}).encode()
            )
        if req.get_method() == "POST" and u.path.endswith("/delete"):
            for x in json.loads(req.data)["urls"]:
                path = urlsplit(x).path.lstrip("/")
                self.calls.append(("delete", path))
                self.blobs.pop(path, None)
            return _Resp(b"{}")
        raise AssertionError(f"unexpected Blob call {req.get_method()} {url}")

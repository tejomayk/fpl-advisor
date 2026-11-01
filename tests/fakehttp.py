"""An in-memory stand-in for `urllib.request.urlopen`, used as HttpSnapshotStore's `opener`."""

from __future__ import annotations

import io
import urllib.error


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, headers: dict | None = None):
        super().__init__(body)
        self.status = 200
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHTTP:
    """Serves `files` (url -> bytes); anything else is a 404. `fail_next` holds HTTP status codes
    or exceptions to raise on the next requests, in order."""

    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = dict(files or {})
        self.requests: list = []
        self.fail_next: list[int | Exception] = []

    @property
    def reads(self) -> int:
        return len(self.requests)

    def __call__(self, req, timeout=None):
        assert timeout is not None, "every request needs a timeout"
        self.requests.append(req)
        url = req.full_url
        if self.fail_next:
            err = self.fail_next.pop(0)
            if isinstance(err, Exception):
                raise err
            raise urllib.error.HTTPError(url, err, f"HTTP {err}", {}, io.BytesIO(b""))
        if url not in self.files:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        return _Resp(self.files[url], {"ETag": '"abc"'})

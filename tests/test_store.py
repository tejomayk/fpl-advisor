"""The storage layer: local files, and the read-only HTTPS copy of the web snapshot (faked; no
network)."""

from __future__ import annotations

import gzip
import json
import urllib.error

import pytest
from fakehttp import FakeHTTP

from fpl_advisor import store

URL = (
    "https://github.com/example/fpl-advisor/releases/download/snapshot/snapshot.json.gz"
)


@pytest.fixture
def local(tmp_path):
    return store.LocalStore(tmp_path / "data")


def test_round_trip_bytes_and_json(local):
    assert local.get("a/b.json") is None
    local.put("a/b.bin", b"\x00\x01")
    assert local.get("a/b.bin") == b"\x00\x01"
    local.put_json("a/b.json", {"x": 1})
    assert local.get_json("a/b.json") == {"x": 1}
    local.put_json("big.json.gz", {"y": [1, 2, 3]})
    assert local.get_json("big.json.gz") == {"y": [1, 2, 3]}
    local.put("a/b.bin", b"new")  # overwrite
    assert local.get("a/b.bin") == b"new"
    local.delete("a/b.bin")
    assert local.get("a/b.bin") is None


def test_gzipped_json_is_deterministic(local):
    local.put_json("x.json.gz", {"a": 1})
    one = local.get("x.json.gz")
    local.put_json("x.json.gz", {"a": 1})
    assert local.get("x.json.gz") == one  # no timestamp inside: same JSON, same bytes


def test_if_match_refuses_a_changed_copy(local):
    local.put("k.json", b"1")
    first = local.read("k.json")
    local.put("k.json", b"2")  # someone else wrote meanwhile
    with pytest.raises(store.ConflictError):
        local.put("k.json", b"3", if_match=first.etag)
    assert local.get("k.json") == b"2"
    current = local.read("k.json")
    local.put("k.json", b"4", if_match=current.etag)
    assert local.get("k.json") == b"4"


@pytest.mark.parametrize("key", ["", "/abs", "../up", "a/../b", "a//b"])
def test_bad_keys_are_refused(local, key):
    with pytest.raises(ValueError):
        local.get(key)
    with pytest.raises(ValueError):
        store.HttpSnapshotStore(URL, opener=FakeHTTP()).get(key)


# ------------------------------------------------------------------ the HTTPS snapshot


def _http(files=None):
    fake = FakeHTTP(files)
    return fake, store.HttpSnapshotStore(URL, opener=fake, sleep=lambda s: None)


def test_http_store_reads_the_snapshot(tmp_path):
    body = gzip.compress(json.dumps({"v": 1}).encode(), mtime=0)
    fake, st = _http({URL: body})
    assert st.get_json(store.WEB_SNAPSHOT_KEY, fresh=False) == {"v": 1}
    assert fake.requests[0].full_url == URL
    assert st.get("signals/other.json") is None  # it holds the snapshot only
    assert fake.reads == 1


def test_http_store_missing_snapshot_is_none():
    _fake, st = _http()
    assert st.get(store.WEB_SNAPSHOT_KEY) is None


def test_http_store_retries_transient_errors_then_fails_without_the_url():
    fake, st = _http({URL: b"x"})
    fake.fail_next = [502, urllib.error.URLError("down")]
    assert st.get(store.WEB_SNAPSHOT_KEY) == b"x"
    assert fake.reads == 3
    fake.fail_next = [500, 500, 500]
    with pytest.raises(store.StoreError) as e:
        st.get(store.WEB_SNAPSHOT_KEY)
    assert "HTTP 500" in str(e.value) and "github.com" not in str(e.value)


def test_http_store_does_not_retry_refusals():
    fake, st = _http({URL: b"x"})
    fake.fail_next = [403, 403, 403]
    with pytest.raises(store.StoreError):
        st.get(store.WEB_SNAPSHOT_KEY)
    assert len(fake.fail_next) == 2


def test_http_store_is_read_only():
    fake, st = _http()
    for write in (
        lambda: st.put(store.WEB_SNAPSHOT_KEY, b"x"),
        lambda: st.put_json(store.WEB_SNAPSHOT_KEY, {}),
        lambda: st.delete(store.WEB_SNAPSHOT_KEY),
    ):
        with pytest.raises(store.StoreError):
            write()
    assert fake.requests == []
    with pytest.raises(store.StoreError):
        store.HttpSnapshotStore("file:///etc/passwd")


# ------------------------------------------------------------------ choosing one


def test_default_is_local_files(tmp_path):
    st = store.default({"FPL_DATA_DIR": str(tmp_path)})
    assert isinstance(st, store.LocalStore) and st.root == tmp_path
    assert store.default({}).root.name == "data"


def test_snapshot_url_from_env_or_vercel_repo_vars():
    vercel = {
        "VERCEL_GIT_PROVIDER": "github",
        "VERCEL_GIT_REPO_OWNER": "someone",
        "VERCEL_GIT_REPO_SLUG": "fpl-advisor",
    }
    assert store.snapshot_url(vercel) == (
        "https://github.com/someone/fpl-advisor/releases/download/snapshot/"
        "snapshot.json.gz"
    )
    # an explicit URL wins
    assert store.snapshot_url({**vercel, "FPL_SNAPSHOT_URL": URL}) == URL
    # not GitHub, odd names or nothing at all: no URL
    assert store.snapshot_url({**vercel, "VERCEL_GIT_PROVIDER": "gitlab"}) is None
    assert store.snapshot_url({**vercel, "VERCEL_GIT_REPO_SLUG": "a/../b"}) is None
    assert store.snapshot_url({}) is None


def test_web_store_reads_https_when_a_url_is_known(tmp_path):
    st = store.web_store({"FPL_SNAPSHOT_URL": URL})
    assert isinstance(st, store.HttpSnapshotStore) and st.url == URL
    local = store.web_store({"FPL_DATA_DIR": str(tmp_path)})
    assert isinstance(local, store.LocalStore) and local.root == tmp_path

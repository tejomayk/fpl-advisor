"""The storage layer: local files and Vercel Blob (faked over HTTP; no network)."""

from __future__ import annotations

import logging

import pytest
from fakeblob import STORE_ID, TOKEN, FakeBlob

from fpl_advisor import keys, store


@pytest.fixture
def blob():
    fake = FakeBlob()
    return fake, store.BlobStore(TOKEN, opener=fake, sleep=lambda s: None)


@pytest.fixture(params=["local", "blob"])
def any_store(request, tmp_path):
    if request.param == "local":
        return store.LocalStore(tmp_path / "data")
    fake = FakeBlob()
    return store.BlobStore(TOKEN, opener=fake, sleep=lambda s: None)


def test_round_trip_bytes_and_json(any_store):
    assert any_store.get("a/b.json") is None
    any_store.put("a/b.bin", b"\x00\x01")
    assert any_store.get("a/b.bin") == b"\x00\x01"
    any_store.put_json("a/b.json", {"x": 1})
    assert any_store.get_json("a/b.json") == {"x": 1}
    any_store.put_json("big.json.gz", {"y": [1, 2, 3]})
    assert any_store.get_json("big.json.gz") == {"y": [1, 2, 3]}
    any_store.put("a/b.bin", b"new")  # overwrite
    assert any_store.get("a/b.bin") == b"new"
    any_store.delete("a/b.bin")
    assert any_store.get("a/b.bin") is None


def test_if_match_refuses_a_changed_copy(any_store):
    any_store.put("k.json", b"1")
    first = any_store.read("k.json")
    any_store.put("k.json", b"2")  # someone else wrote meanwhile
    with pytest.raises(store.ConflictError):
        any_store.put("k.json", b"3", if_match=first.etag)
    assert any_store.get("k.json") == b"2"
    current = any_store.read("k.json")
    any_store.put("k.json", b"4", if_match=current.etag)
    assert any_store.get("k.json") == b"4"


@pytest.mark.parametrize("key", ["", "/abs", "../up", "a/../b", "a//b"])
def test_bad_keys_are_refused(any_store, key):
    with pytest.raises(ValueError):
        any_store.get(key)


def test_default_picks_blob_only_with_a_token(tmp_path):
    assert isinstance(store.default({"FPL_DATA_DIR": str(tmp_path)}), store.LocalStore)
    st = store.default({"BLOB_READ_WRITE_TOKEN": TOKEN})
    assert isinstance(st, store.BlobStore) and st.store_id == STORE_ID


def test_blob_writes_are_private_and_never_random(blob):
    fake, st = blob
    st.put_json("signals/x.json", {"a": 1})
    req = fake.requests[-1]
    h = {k.lower(): v for k, v in req.header_items()}
    assert h["x-vercel-blob-access"] == "private"
    assert h["x-add-random-suffix"] == "0" and h["x-allow-overwrite"] == "1"
    assert "signals%2Fx.json" in req.full_url or "signals/x.json" in req.full_url


def test_blob_reads_go_to_the_private_host_with_the_token(blob):
    fake, st = blob
    st.put("a.json", b"{}")
    st.get("a.json")
    req = fake.requests[-1]
    assert req.full_url.startswith(
        f"https://{STORE_ID}.private.blob.vercel-storage.com/"
    )
    assert req.full_url.endswith("?cache=0")  # fresh by default
    st.get("a.json", fresh=False)
    assert "cache=0" not in fake.requests[-1].full_url


def test_blob_errors_never_show_the_token(blob, caplog):
    fake, st = blob
    fake.fail_next = [500, 500, 500]
    with caplog.at_level(logging.DEBUG), pytest.raises(store.StoreError) as e:
        st.get("a.json")
    assert TOKEN not in str(e.value) and "s3cr3t" not in str(e.value)
    assert "HTTP 500" in str(e.value)
    assert TOKEN not in repr(st)
    assert keys.redact(f"oops {TOKEN}") == "oops ***"


def test_blob_retries_transient_errors(blob):
    fake, st = blob
    st.put("a.json", b"1")
    fake.fail_next = [502]
    assert st.get("a.json") == b"1"


def test_blob_auth_errors_are_not_retried(blob):
    fake, st = blob
    fake.fail_next = [401, 401, 401]
    with pytest.raises(store.StoreError):
        st.get("a.json")
    assert len(fake.fail_next) == 2


def test_bad_token_shape_is_refused():
    with pytest.raises(store.StoreError) as e:
        store.BlobStore("not-a-token")
    assert "not-a-token" not in str(e.value)


def test_pack_is_deterministic_and_round_trips(tmp_path):
    root = tmp_path / "a"
    (root / "signals" / "odds").mkdir(parents=True)
    (root / "signals" / "odds" / "x.json").write_text("1")
    (root / "usage").mkdir()
    (root / "usage" / "usage.json").write_text("{}")
    (root / "usage" / "usage.json.lock").write_text("")
    (root / "other").mkdir()
    (root / "other" / "skip.txt").write_text("no")
    one, two = store.pack(root), store.pack(root)
    assert one == two
    out = tmp_path / "b"
    store.unpack(one, out)
    assert (out / "signals" / "odds" / "x.json").read_text() == "1"
    assert (out / "usage" / "usage.json.lock").exists()
    assert not (out / "other").exists()


def test_job_state_pushes_only_when_something_changed(blob, tmp_path):
    fake, st = blob
    with store.job_state(st, tmp_path / "w1") as js:
        js.pull()  # nothing there yet
        (js.root / "signals").mkdir()
        (js.root / "signals" / "signals.json").write_text("{}")
        assert js.push() is True
    with store.job_state(st, tmp_path / "w2") as js:
        js.pull()
        assert (js.root / "signals" / "signals.json").read_text() == "{}"
        puts = fake.uploads
        assert js.push() is False  # unchanged: no upload spent
        assert fake.uploads == puts
        (js.root / "signals" / "signals.json").write_text('{"a":1}')
        assert js.push() is True


def test_job_state_never_overwrites_a_newer_copy(blob, tmp_path):
    _fake, st = blob
    with store.job_state(st, tmp_path / "w0") as js:
        (js.root / "usage").mkdir()
        (js.root / "usage" / "usage.json").write_text("0")
        js.push()
    with store.job_state(st, tmp_path / "w1") as a:
        a.pull()
        with store.job_state(st, tmp_path / "w2") as b:
            b.pull()
            (b.root / "usage" / "usage.json").write_text("b")
            b.push()
        (a.root / "usage" / "usage.json").write_text("a")
        with pytest.raises(store.ConflictError):
            a.push()


def test_local_job_state_is_used_in_place(tmp_path):
    st = store.LocalStore(tmp_path)
    with store.job_state(st) as js:
        js.pull()
        assert js.root == tmp_path and not js.remote and js.push() is False

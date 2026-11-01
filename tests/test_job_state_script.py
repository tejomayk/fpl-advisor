"""scripts/job_state.py: the workflows only cache a job folder whose spend ledger is sane."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fpl_advisor import usage

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "job_state.py"
spec = importlib.util.spec_from_file_location("job_state", SCRIPT)
js = importlib.util.module_from_spec(spec)
spec.loader.exec_module(js)


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("FPL_USAGE_PATH", str(root / "usage" / "usage.json"))
    monkeypatch.setenv("FPL_USAGE_MIRROR", str(root / "usage.mirror" / "usage.json"))
    return root


def _spend(n=1):
    ledger = usage.default()
    for _ in range(n):
        res = ledger.reserve("odds", 1, now=datetime.now(UTC))
        assert res is not None
        ledger.settle(res, 1, now=datetime.now(UTC))
    return ledger


def test_empty_folder_is_fine_on_a_first_run(root):
    assert js.ledger_seq(root) == 0
    assert js.problems(root, 0) == []
    assert js.problems(root, 3) == ["the spend ledger disappeared"]


def test_a_real_ledger_reads_and_only_grows(root):
    _spend(2)
    seq = js.ledger_seq(root)
    assert seq > 0 and js.problems(root, seq) == []
    _spend(1)
    assert js.ledger_seq(root) > seq and js.problems(root, seq) == []
    usage.default().reset()  # a reset keeps counting up, so it still saves
    assert js.problems(root, seq) == []
    assert js.problems(root, js.ledger_seq(root) + 1)  # went backwards: refuse


def test_one_broken_copy_is_fine_both_are_not(root):
    _spend(1)
    seq = js.ledger_seq(root)
    main, mirror = js.ledger_copies(root)
    main.write_text("{not json")
    assert js.problems(root, seq) == []  # the mirror still holds it
    mirror.write_text(json.dumps([1, 2]))
    assert js.problems(root, seq) == ["no readable copy of the spend ledger"]
    assert js.main(["check", str(root), "--min-seq", str(seq)]) == 1


def test_digest_tracks_content_and_ignores_temp_files(root):
    _spend(1)
    one = js.digest(root)
    (root / "usage" / ".x.tmp").write_text("half")
    assert js.digest(root) == one
    (root / "signals").mkdir()
    (root / "signals" / "a.json").write_text("{}")
    assert js.digest(root) != one

import json
from datetime import UTC, datetime, timedelta

from fpl_advisor.state import AlertState


def test_mark_seen_roundtrip(tmp_path):
    p = tmp_path / "sub" / "alerts.json"
    s = AlertState(p)
    assert not s.seen("a")
    s.mark("a")
    assert s.seen("a")
    s.save()
    assert AlertState(p).seen("a")
    assert [f.name for f in p.parent.iterdir()] == ["alerts.json"]  # no temp leftovers


def test_prunes_old_entries(tmp_path):
    p = tmp_path / "alerts.json"
    old = (datetime.now(UTC) - timedelta(days=46)).isoformat()
    new = (datetime.now(UTC) - timedelta(days=44)).isoformat()
    p.write_text(json.dumps({"old": old, "new": new}))
    s = AlertState(p)
    s.save()
    data = json.loads(p.read_text())
    assert "old" not in data and "new" in data


def test_corrupt_file_ignored(tmp_path):
    p = tmp_path / "alerts.json"
    p.write_text("{not json")
    s = AlertState(p)
    assert not s.seen("x")
    s.mark("x")
    s.save()
    assert AlertState(p).seen("x")

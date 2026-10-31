import sqlite3
from datetime import UTC, datetime, timedelta

from fpl_advisor.subscriptions import DbAlertState, SubscriptionStore


def test_create_get_verify_delete(tmp_path):
    s = SubscriptionStore(tmp_path / "a.db")
    s.create("t1", "secret", 10, None, [24, 2])
    sub = s.get("t1")
    assert sub and sub.team_id == 10 and sub.league_id is None
    assert sub.reminder_hours == [24, 2]
    assert s.verify("t1", "secret")
    assert not s.verify("t1", "nope")
    assert not s.verify("zzz", "secret")
    s.delete("t1")
    assert s.get("t1") is None and s.count() == 0


def test_failures_deactivate_and_reset(tmp_path):
    s = SubscriptionStore(tmp_path / "a.db")
    s.create("t1", "x", 1, 2, [24])
    assert s.record_failure("t1", 3) == 1
    s.record_success("t1")
    assert s.get("t1").failures == 0
    for _ in range(3):
        s.record_failure("t1", 3)
    assert s.list_active() == []
    assert s.get("t1").active is False


def test_db_alert_state_round_trip_and_prune(tmp_path):
    s = SubscriptionStore(tmp_path / "a.db")
    st = DbAlertState(s, "t1")
    assert not st.seen("k")
    st.mark("k")
    assert st.seen("k")  # buffered
    assert not DbAlertState(s, "t1").seen("k")  # not yet written
    st.save()
    assert DbAlertState(s, "t1").seen("k")
    assert not DbAlertState(s, "other").seen("k")
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    s.state_write("t1", {"old": old})
    DbAlertState(s, "t1").save()  # nothing pending
    s.state_write("t1", {"k2": datetime.now(UTC).isoformat()})  # triggers prune
    assert not s.state_seen("t1", "old")
    assert s.state_seen("t1", "k")


def test_migrates_old_database(tmp_path):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript(
        """CREATE TABLE subscriptions (topic TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
        team_id INTEGER NOT NULL, league_id INTEGER, reminder_hours TEXT NOT NULL,
        created TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1);
        INSERT INTO subscriptions VALUES ('t1','h',1,NULL,'24','2025-01-01',0,1);"""
    )
    c.commit()
    c.close()
    s = SubscriptionStore(path)
    sub = s.get("t1")
    assert sub.inactive_reason is None and sub.last_full_build is None
    s.deactivate_all("season_ended")
    assert s.get("t1").inactive_reason == "season_ended"
    SubscriptionStore(path)  # second open is a no-op


def test_count_only_active_and_per_team(tmp_path):
    s = SubscriptionStore(tmp_path / "a.db")
    s.create("a", "x", 1, None, [24])
    s.create("b", "x", 1, None, [24])
    s.create("c", "x", 2, None, [24])
    assert s.count() == 3 and s.count_for_team(1) == 2
    s.record_failure("a", 1)
    assert s.get("a").inactive_reason == "too_many_failures"
    assert s.count() == 2 and s.count_for_team(1) == 1


def test_prune_inactive(tmp_path):
    s = SubscriptionStore(tmp_path / "a.db")
    s.create("a", "x", 1, None, [24])
    s.create("b", "x", 2, None, [24])
    s.state_write("a", {"k": datetime.now(UTC).isoformat()})
    s.deactivate_all("season_ended")
    assert s.prune_inactive() == 0
    assert s.prune_inactive(datetime.now(UTC) + timedelta(days=31)) == 2
    assert s.get("a") is None and not s.state_seen("a", "k")


def test_unwritable_data_dir_logged(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    from fpl_advisor.subscriptions import check_data_dir

    assert check_data_dir(blocker / "sub") is False
    assert "not writable" in caplog.text
    assert check_data_dir(tmp_path / "ok") is True

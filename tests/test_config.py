import pytest

from fpl_advisor.config import Config, ConfigError, load_config


def write(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return str(p)


def test_defaults_with_env_team():
    cfg = load_config(path=None, env={"FPL_TEAM_ID": "42"})
    assert cfg == Config(team_id=42)
    assert cfg.reminder_hours == [24, 2]


def test_missing_team_id_explains(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ConfigError, match="/entry/"):
        load_config(env={})


def test_precedence(tmp_path):
    path = write(tmp_path, 'team_id = 1\nleague_id = 2\nmax_hits = 3\nrisk = "low"\n')
    env = {"FPL_TEAM_ID": "10", "FPL_MAX_HITS": "1"}
    cfg = load_config(path, env, team_id=99, max_hits=None)
    assert cfg.team_id == 99  # override beats env
    assert cfg.max_hits == 1  # None override ignored; env beats file
    assert cfg.league_id == 2  # file value
    assert cfg.risk == "low"


def test_empty_env_vars_are_unset(tmp_path):
    path = write(tmp_path, "team_id = 5\nleague_id = 6\n")
    env = {"FPL_TEAM_ID": "", "FPL_LEAGUE_ID": "", "NTFY_TOPIC": "", "FPL_NOTIFY": ""}
    cfg = load_config(path, env)
    assert (cfg.team_id, cfg.league_id, cfg.notify, cfg.ntfy_topic) == (
        5,
        6,
        "stdout",
        None,
    )


def test_reminder_hours_env():
    cfg = load_config(env={"FPL_TEAM_ID": "1", "FPL_REMINDER_HOURS": "48, 6,1"})
    assert cfg.reminder_hours == [48, 6, 1]


def test_bad_values():
    with pytest.raises(ConfigError):
        load_config(env={"FPL_TEAM_ID": "abc"})
    with pytest.raises(ConfigError):
        load_config(env={"FPL_TEAM_ID": "1", "FPL_REMINDER_HOURS": "x"})
    with pytest.raises(ConfigError):
        load_config(env={"FPL_TEAM_ID": "1", "FPL_RISK": "yolo"})


def test_ntfy_requires_topic():
    with pytest.raises(ConfigError, match="topic"):
        load_config(env={"FPL_TEAM_ID": "1", "FPL_NOTIFY": "ntfy"})
    cfg = load_config(env={"FPL_TEAM_ID": "1", "FPL_NOTIFY": "ntfy", "NTFY_TOPIC": "t"})
    assert cfg.ntfy_topic == "t"


def test_unknown_key_and_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Unknown"):
        load_config(write(tmp_path, "team_id = 1\nbogus = 2\n"), {})
    with pytest.raises(ConfigError, match="not found"):
        load_config(str(tmp_path / "nope.toml"), {})


def test_cwd_file_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fpl-advisor.toml").write_text("team_id = 77\n")
    assert load_config(env={}).team_id == 77

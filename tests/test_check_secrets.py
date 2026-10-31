"""scripts/check_secrets.py on a throwaway git repo. Keys here are fake and built at runtime."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fpl_advisor import keys

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_secrets.py"
FAKE_VALUE = "plain" + "value" * 4  # no special shape: only found because it is loaded


def run(
    repo: Path, env_extra: dict | None = None, *args: str
) -> subprocess.CompletedProcess:
    import os

    env = {k: v for k, v in os.environ.items() if k not in keys.SECRET_ENV_VARS}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".env\nignored/\n")
    (tmp_path / "ok.py").write_text("print('hello')\n")
    return tmp_path


def test_clean_repo_passes(repo):
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "clean" in r.stdout


@pytest.mark.parametrize(
    "line",
    [
        "KEY = 'sk-or-v1-" + "a" * 64 + "'",
        "yt = 'AIza" + "B" * 35 + "'",
        "ODDS_API_KEY=" + "0123456789abcdef" * 2,
        "url = '...?apiKey=" + "fedcba9876543210" * 2 + "'",
    ],
)
def test_key_shapes_fail_without_printing_them(repo, line):
    (repo / "leak.py").write_text("x = 1\n" + line + "\n")
    r = run(repo)
    assert r.returncode == 1
    assert "leak.py:2:" in r.stdout
    secret = line.split("=", 1)[1].strip(" '").split("apiKey=")[-1]
    assert secret not in r.stdout + r.stderr


def test_loaded_values_are_found_by_variable_name(repo):
    (repo / ".env").write_text(f"YOUTUBE_API_KEY={FAKE_VALUE}\n")  # ignored, but read
    (repo / "notes.md").write_text(f"my key is {FAKE_VALUE}\n")
    r = run(repo)
    assert r.returncode == 1
    assert "notes.md:1: the value of YOUTUBE_API_KEY" in r.stdout
    assert FAKE_VALUE not in r.stdout + r.stderr
    # From the environment too.
    (repo / ".env").unlink()
    r = run(repo, {"OPENROUTER_API_KEY": FAKE_VALUE})
    assert r.returncode == 1 and "OPENROUTER_API_KEY" in r.stdout


def test_ignored_files_are_not_scanned(repo):
    (repo / "ignored").mkdir()
    (repo / "ignored" / "x.txt").write_text("AIza" + "C" * 35 + "\n")
    assert run(repo).returncode == 0


def test_allow_pragma(repo):
    (repo / "doc.md").write_text(
        "AIza" + "D" * 35 + "  <!-- check-secrets: allow -->\n"
    )
    assert run(repo).returncode == 0


def test_variable_list_matches_the_package():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_secrets", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.SECRET_ENV_VARS == keys.SECRET_ENV_VARS


def git(repo: Path, *args: str) -> None:
    # -c only for this command: no git config is changed anywhere.
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


GOOGLE = "AIza" + "E" * 35


def test_staged_mode_scans_the_staged_blob_not_the_working_tree(repo):
    f = repo / "app.py"
    f.write_text(f"k = '{GOOGLE}'\n")
    git(repo, "add", "app.py")
    f.write_text("k = 'clean now'\n")  # working tree is clean, the commit would not be
    assert run(repo).returncode == 0
    r = run(repo, None, "--staged")
    assert r.returncode == 1 and "app.py:1:" in r.stdout and GOOGLE not in r.stdout
    git(repo, "add", "app.py")
    assert run(repo, None, "--staged").returncode == 0


def test_env_files_are_flagged_when_tracked_or_staged(repo):
    (repo / ".env").write_text("NOTHING=1\n")
    (repo / ".env.example").write_text("ODDS_API_KEY=\n")
    assert run(repo).returncode == 0  # ignored .env, and examples are fine
    git(repo, "add", "-f", ".env", ".env.example")
    r = run(repo, None, "--staged")
    assert r.returncode == 1 and ".env: a .env file is staged" in r.stdout
    assert ".env.example" not in r.stdout
    assert "a .env file is tracked" in run(repo).stdout


def test_range_and_history_scan_added_lines_in_commits(repo):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    (repo / "cfg.py").write_text("a = 1\nb = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "clean")
    (repo / "cfg.py").write_text(f"a = 1\nb = '{GOOGLE}'\n")
    git(repo, "commit", "-qam", "leak")
    (repo / "cfg.py").write_text("a = 1\nb = 2\n")
    git(repo, "commit", "-qam", "remove it again")
    assert run(repo).returncode == 0  # the tree is clean ...
    r = run(repo, None, "--range", "HEAD~3..HEAD")
    assert r.returncode == 1 and "cfg.py:2:" in r.stdout and GOOGLE not in r.stdout
    assert run(repo, None, "--range", "HEAD~1..HEAD").returncode == 0
    assert run(repo, None, "--history").returncode == 1  # ... but history is not


def test_pre_commit_hook_uses_staged_mode():
    hook = SCRIPT.parents[1] / ".githooks" / "pre-commit"
    assert "--staged" in hook.read_text()
    import os

    assert os.access(hook, os.X_OK)

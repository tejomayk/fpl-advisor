#!/usr/bin/env python3
"""Fail if something git would commit contains an API key. Standard library only.

What is scanned depends on the mode:
- default: every tracked or untracked-but-not-ignored file in the working tree
  (`git ls-files -co --exclude-standard`);
- `--staged`: the staged version of every staged file (`git show :path`), as the pre-commit
  hook needs, since the working tree may differ from what is about to be committed;
- `--range A..B`: lines added by the commits in that range (CI, on push and pull request);
- `--history`: lines added by every commit on every branch.

In each mode it looks for:
- key shapes: OpenRouter `sk-or-v1-...`, Google `AIza` + 35 characters, and a 32-character hex
  string on a line that mentions apiKey or ODDS_API (The Odds API's key format);
- the literal values of the secret variables currently set in the environment or in the
  repo-root `.env` (read in-process, never printed);
- a file named `.env` or `.env.<anything>` (other than .env.example/.sample/.template)
  being tracked, staged or added.

Reports only file:line and which variable or pattern matched, never the value. Exit codes:
0 clean, 1 a key was found, 2 the scan could not run. A line containing
`check-secrets: allow` is skipped (for documented fake examples only).

    python3 scripts/check_secrets.py                  # working tree (CI)
    python3 scripts/check_secrets.py --staged         # .githooks/pre-commit
    python3 scripts/check_secrets.py --range A..B     # CI: the pushed commits
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Keep in step with fpl_advisor.keys.SECRET_ENV_VARS (a test checks this).
SECRET_ENV_VARS = (
    "ODDS_API_KEY",
    "OPENROUTER_API_KEY",
    "YOUTUBE_API_KEY",
    "NTFY_TOKEN",
)
# Not an API key, but it works like a password and must never be committed either.
ALSO_PRIVATE = ("NTFY_TOPIC",)
MIN_LEN = 8
MAX_BYTES = 5_000_000
ALLOW = "check-secrets: allow"

SHAPES = (
    ("an OpenRouter key", re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}")),
    ("a Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    (
        "a Vercel Blob token",
        re.compile(r"vercel_blob_rw_[A-Za-z0-9]+_[A-Za-z0-9]{16,}"),
    ),
)
HEX32 = re.compile(r"(?<![0-9A-Fa-f])[0-9a-fA-F]{32}(?![0-9A-Fa-f])")
# Odds event ids are 32 hex too, so a plain "odds" nearby is not enough.
ODDS_HINT = re.compile(r"(?i)api[_-]?key|odds[_-]?api")


ENV_FILE = re.compile(r"(^|/)\.env(\.[^/]*)?$")
ENV_FILE_OK = re.compile(r"(^|/)\.env\.(example|sample|template)$")


def git(root: Path | None, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=True
    ).stdout


def repo_root() -> Path:
    return Path(git(None, "rev-parse", "--show-toplevel").decode().strip())


def is_env_file(name: str) -> bool:
    return bool(ENV_FILE.search(name)) and not ENV_FILE_OK.search(name)


def secret_values(root: Path) -> dict[str, str]:
    """{value: variable name} from the environment and `.env`. Values never leave here."""
    names = SECRET_ENV_VARS + ALSO_PRIVATE
    found: dict[str, str] = {}
    for k in names:
        v = (os.environ.get(k) or "").strip()
        if len(v) >= MIN_LEN:
            found[v] = k
    try:
        lines = (root / ".env").read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        k, sep, v = line.strip().removeprefix("export ").partition("=")
        k, v = k.strip(), v.strip().strip("'\"")
        if sep and k in names and len(v) >= MIN_LEN:
            found.setdefault(v, k)
    return found


def scan_line(line: str, values: dict[str, str]) -> list[str]:
    if ALLOW in line:
        return []
    hits = [f"the value of {var}" for value, var in values.items() if value in line]
    hits += [
        f"something shaped like {label}" for label, pat in SHAPES if pat.search(line)
    ]
    if ODDS_HINT.search(line) and HEX32.search(line):
        hits.append("a 32-character hex string next to apiKey/ODDS_API (Odds API key?)")
    return hits


def scan_blob(data: bytes, values: dict[str, str]) -> list[tuple[int, str]]:
    if len(data) > MAX_BYTES or b"\0" in data[:8192]:
        return []  # huge or binary
    text = data.decode("utf-8", "replace")
    return [
        (n, w)
        for n, line in enumerate(text.splitlines(), 1)
        for w in scan_line(line, values)
    ]


def scan_file(path: Path, values: dict[str, str]) -> list[tuple[int, str]]:
    try:
        if path.is_symlink() or not path.is_file():
            return []
        return scan_blob(path.read_bytes(), values)
    except OSError:
        return []


def _names(raw: bytes) -> list[str]:
    return [n for n in raw.decode("utf-8", "replace").split("\0") if n]


def scan_tree(root: Path, values: dict[str, str]) -> tuple[list[str], int]:
    names = _names(git(root, "ls-files", "-co", "--exclude-standard", "-z"))
    hits = [
        f"{n}: a .env file is tracked or not ignored" for n in names if is_env_file(n)
    ]
    for n in names:
        hits += [f"{n}:{line}: {what}" for line, what in scan_file(root / n, values)]
    return hits, len(names)


def scan_staged(root: Path, values: dict[str, str]) -> tuple[list[str], int]:
    names = _names(
        git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    )
    hits = [f"{n}: a .env file is staged" for n in names if is_env_file(n)]
    for n in names:
        try:
            blob = git(root, "show", f":{n}")
        except subprocess.CalledProcessError:
            continue  # e.g. a submodule
        hits += [f"{n}:{line}: {what}" for line, what in scan_blob(blob, values)]
    return hits, len(names)


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def scan_log(root: Path, values: dict[str, str], *spec: str) -> tuple[list[str], int]:
    """Lines added by the commits `git log` selects with `spec`."""
    out = git(
        root, "log", "-p", "--no-color", "--no-ext-diff", "--format=commit %H", *spec
    )
    hits: list[str] = []
    commits = 0
    sha = path = ""
    line_no = 0
    for raw in out.decode("utf-8", "replace").splitlines():
        if raw.startswith("commit ") and len(raw) == 47:
            sha, commits, path = raw[7:14], commits + 1, ""
        elif raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else ""
            if path and is_env_file(path):
                hits.append(f"commit {sha} {path}: a .env file was committed")
        elif m := HUNK.match(raw):
            line_no = int(m.group(1))
        elif raw.startswith("+") and path:
            hits += [
                f"commit {sha} {path}:{line_no}: {w}"
                for w in scan_line(raw[1:], values)
            ]
            line_no += 1
        elif raw.startswith(" ") and path:
            line_no += 1
    return hits, commits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fail if something git would commit holds a key."
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--staged", action="store_true", help="scan staged blobs (pre-commit)"
    )
    mode.add_argument(
        "--range", metavar="A..B", help="scan lines added by these commits"
    )
    mode.add_argument("--history", action="store_true", help="scan every commit")
    args = ap.parse_args(argv)
    try:
        root = repo_root()
        values = secret_values(root)
        if args.staged:
            hits, n = scan_staged(root, values)
            what = f"{n} staged file(s)"
        elif args.range:
            hits, n = scan_log(root, values, args.range)
            what = f"{n} commit(s) in {args.range}"
        elif args.history:
            hits, n = scan_log(root, values, "--all")
            what = f"{n} commit(s) in the whole history"
        else:
            hits, n = scan_tree(root, values)
            what = f"{n} file(s)"
    except (OSError, subprocess.CalledProcessError):
        print(
            "check_secrets: git failed (not a repository, or a bad range?)",
            file=sys.stderr,
        )
        return 2
    for h in hits:
        print(h)
    if hits:
        print(
            f"\ncheck_secrets: {len(hits)} possible key(s) in {what}. Remove them before "
            "committing. A key that has been pushed anywhere must be revoked at the provider "
            "and replaced, even after it is removed from the files.",
            file=sys.stderr,
        )
        return 1
    print(
        f"check_secrets: {what} clean ({len(values)} loaded secret value(s) checked, none printed)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

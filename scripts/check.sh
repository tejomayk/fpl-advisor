#!/usr/bin/env bash
# Every check a change must pass before it merges. CI runs this same script on each PR.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== secrets";       python3 scripts/check_secrets.py
echo "== ruff";          uv run ruff check .
echo "== ruff format";   uv run ruff format --check .
echo "== pytest";        uv run pytest -q
echo "== front end";     (cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run typecheck)
echo "All checks passed."

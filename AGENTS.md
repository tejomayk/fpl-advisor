# Project: fpl-advisor — open-source Fantasy Premier League advisor (any manager, any league)

## Commands
- Test: `uv run pytest -q`
- Lint/format: `uv run ruff check --fix . && uv run ruff format .`
- Run: `uv run fpl-advisor advise --team <id> --league <id>` (see README for config)

## Never
- Hard-code any one manager's team ID, league ID, name or ntfy topic in code, tests or docs. Personal config lives in `fpl-advisor.toml` (gitignored) or env vars.
- Call FPL endpoints that need login. Public endpoints only.
- Add dependencies without a clear reason noted in the commit.
- Weaken or delete tests to make them pass.
- Commit `.cache/`, `state/`, or anything under `fpl-advisor.toml`.

## Done means
- `pytest` and `ruff check` pass; show the output.
- Advice changes: run `advise` against live data and show the output. Model changes: show the backtest numbers before and after (`uv run python scripts/backtest.py`).
- Model or decision changes: show `season_sim` season totals before and after (`uv run python scripts/season_sim.py`).

## Working style
- Model and scoring rules live in `src/fpl_advisor/rules.py`; update there when FPL changes rules.
- Explain every recommendation in plain words (users are learning the game).
- End each run with: Blocked on me / Changed / Found.

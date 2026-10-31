"""Synthetic, network-free worlds for unit tests."""

from __future__ import annotations

import os
import urllib.request
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest

from fpl_advisor import keys, usage
from fpl_advisor import rules as R
from fpl_advisor.world import Event, Fixture, Player, Team, World

N_TEAMS = 20
# players per position, and base price per position
POS_COUNTS = {R.GKP: 8, R.DEF: 20, R.MID: 20, R.FWD: 10}
BASE_PRICE = {R.GKP: 4.5, R.DEF: 4.5, R.MID: 5.0, R.FWD: 6.0}
FINISHED_GWS = (1, 2, 3)
ALL_GWS = tuple(range(1, 9))


def history_row(gw: int, fixture: int, home: bool, minutes: int = 90, **kw) -> dict:
    row = {
        "round": gw,
        "fixture": fixture,
        "was_home": home,
        "minutes": minutes,
        "starts": 1 if minutes >= 60 else 0,
        "expected_goals": "0.20",
        "expected_assists": "0.10",
        "saves": 0,
        "bonus": 0,
        "yellow_cards": 0,
        "defensive_contribution": 4,
    }
    row.update(kw)
    return row


def make_world(
    *, gws=ALL_GWS, finished=FINISHED_GWS, drop_fixtures=None, extra_fixtures=()
) -> World:
    teams = {t: Team(t, f"Team {t}", f"T{t:02d}") for t in range(1, N_TEAMS + 1)}
    players: dict[int, Player] = {}
    pid = 0
    for pos, n in POS_COUNTS.items():
        for i in range(n):
            pid += 1
            team = (pid * 3) % N_TEAMS + 1  # spreads players across clubs
            price = round(BASE_PRICE[pos] + 0.1 * (i % 5), 1)
            players[pid] = Player(
                id=pid,
                name=f"P{pid}",
                full_name=f"Player {pid}",
                team=team,
                pos=pos,
                price=price,
                price_start=price,
            )
    fixtures: list[Fixture] = []
    fid = 0
    for gw in gws:
        for i in range(10):
            fid += 1
            home, away = i + 1, ((i + gw) % 10) + 11
            done = gw in finished
            fixtures.append(
                Fixture(
                    fid,
                    gw,
                    home,
                    away,
                    None,
                    done,
                    (1 + i % 3) if done else None,
                    (i % 2) if done else None,
                )
            )
    fixtures.extend(extra_fixtures)
    drop_fixtures = drop_fixtures or set()
    fixtures = [f for f in fixtures if f.id not in drop_fixtures]

    history: dict[int, list[dict]] = {}
    for p in players.values():
        rows = []
        for f in fixtures:
            if f.finished and p.team in (f.home, f.away):
                rows.append(history_row(f.event, f.id, f.home == p.team))
        if rows:
            history[p.id] = rows
    now = datetime(2025, 8, 1, tzinfo=UTC)
    events = [
        Event(
            g,
            now + timedelta(days=7 * g),
            g in finished,
            g == max(finished),
            g == max(finished) + 1,
        )
        for g in gws
    ]
    return World(
        players=players,
        teams=teams,
        fixtures=fixtures,
        events=events,
        history=history,
        past={},
        chips=[],
    )


PAID_HOSTS = ("api.the-odds-api.com", "openrouter.ai", "www.googleapis.com")
_real_urlopen = urllib.request.urlopen


@pytest.fixture(autouse=True)
def _no_real_keys_or_paid_calls(monkeypatch, tmp_path):
    """Every test: never read the real `.env`, keep the usage ledger in tmp_path, send no
    spend alerts, and fail loudly on any attempt to reach a paid API for real."""
    monkeypatch.setattr(keys, "DOTENV_PATH", tmp_path / "no-such.env")
    monkeypatch.setenv("FPL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FPL_USAGE_PATH", str(tmp_path / "usage" / "usage.json"))
    for k in ("FPL_USAGE_MIRROR", "FPL_EXPECT_VOLUME", "OPENROUTER_MODEL_PRICE"):
        monkeypatch.delenv(k, raising=False)
    for k in (*keys.SECRET_ENV_VARS, "NTFY_TOPIC"):
        monkeypatch.delenv(k, raising=False)
    # The .env loader writes os.environ directly; make sure whatever a test loads is undone.
    for k in keys.DOTENV_VARS:
        if k not in os.environ:
            monkeypatch.setenv(k, "")
            monkeypatch.delenv(k)
    monkeypatch.setattr(usage, "owner_alert", lambda title, body: None)

    def guarded(req, *a, **k):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        if urlsplit(url).hostname in PAID_HOSTS:
            raise RuntimeError("a test tried to call a paid API for real")
        return _real_urlopen(req, *a, **k)

    monkeypatch.setattr(urllib.request, "urlopen", guarded)


@pytest.fixture
def world_factory():
    return make_world


@pytest.fixture
def world() -> World:
    return make_world()


def fixture_for(world: World, team: int, gw: int) -> Fixture:
    return next(f for f in world.fixtures_in(gw) if team in (f.home, f.away))


def pick_squad(world: World, prices_by_pos=None) -> list[int]:
    """Cheapest legal 15-man squad (<=3 per club)."""
    squad: list[int] = []
    per_club: dict[int, int] = {}
    for pos, n in R.SQUAD_COUNT.items():
        cands = sorted(
            (p for p in world.players.values() if p.pos == pos),
            key=lambda p: (p.price, p.id),
        )
        got = 0
        for p in cands:
            if got == n:
                break
            if per_club.get(p.team, 0) >= R.MAX_PER_CLUB:
                continue
            squad.append(p.id)
            per_club[p.team] = per_club.get(p.team, 0) + 1
            got += 1
    return squad

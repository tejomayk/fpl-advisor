"""What-if transfers: applying a draft to the squad, its errors, and scoring it."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from conftest import make_world, pick_squad
from fastapi.testclient import TestClient

from fpl_advisor import advise, web, whatif
from fpl_advisor import rules as R
from fpl_advisor.config import Config
from fpl_advisor.serialize import advice_to_dict, whatif_to_dict
from fpl_advisor.squad import Squad
from fpl_advisor.whatif import (
    DraftError,
    apply_transfers,
    build_whatif,
    parse_moves,
)

NOW = datetime(2020, 1, 1, tzinfo=UTC)


def make_squad(world, ft=1, bank=1.0) -> Squad:
    players = pick_squad(world)
    # Bought cheaper than today, so selling prices sit below current prices.
    purchase = {p: round(world.players[p].price - 0.2, 1) for p in players}
    sell = {p: round(world.players[p].price - 0.1, 1) for p in players}
    return Squad(
        1,
        "Example Manager",
        "Example FC",
        players,
        players[:11],
        players[2],
        sell,
        purchase,
        bank,
        ft,
        [],
        vice=players[3],
    )


def club_count(world, players, team) -> int:
    return sum(world.players[p].team == team for p in players)


def replacement(world, sq: Squad, out: int, taken=(), cheap=True) -> int:
    """A legal signing for `out`: same position, not owned, room at his club."""
    pos = world.players[out].pos
    left = [p for p in sq.players if p != out]
    cands = [
        p
        for p in world.players.values()
        if p.pos == pos
        and p.id not in sq.players
        and p.id not in taken
        and club_count(world, left, p.team) < R.MAX_PER_CLUB
    ]
    cands.sort(key=lambda p: (p.price if cheap else -p.price, p.id))
    return cands[0].id


@pytest.fixture
def world():
    return make_world()


# ------------------------------------------------------------------ applying a draft


def test_one_free_transfer_updates_bank_prices_and_free_transfers(world):
    sq = make_squad(world, ft=1, bank=1.0)
    out = sq.players[5]
    inn = replacement(world, sq, out)
    edited, hits = apply_transfers(sq, world, [(out, inn)])
    price = world.players[inn].price
    assert hits == 0
    assert out not in edited.players and inn in edited.players
    assert edited.players.index(inn) == sq.players.index(out)  # same slot
    assert edited.bank == round(1.0 + sq.sell_price[out] - price, 1)
    # A signing costs his current price, which is also what he'd sell for today.
    assert edited.sell_price[inn] == price and edited.purchase_price[inn] == price
    assert out not in edited.sell_price and out not in edited.purchase_price
    assert edited.free_transfers == 0
    # The original squad is untouched.
    assert out in sq.players and sq.bank == 1.0 and sq.free_transfers == 1


def test_extra_moves_take_a_hit_each(world):
    sq = make_squad(world, ft=1, bank=5.0)
    o1, o2, o3 = sq.players[5], sq.players[10], sq.players[14]
    i1 = replacement(world, sq, o1)
    i2 = replacement(world, sq, o2, taken={i1})
    i3 = replacement(world, sq, o3, taken={i1, i2})
    edited, hits = apply_transfers(sq, world, [(o1, i1), (o2, i2), (o3, i3)])
    assert hits == 2 and edited.free_transfers == 0
    edited2, hits2 = apply_transfers(
        replace(sq, free_transfers=2), world, [(o1, i1), (o2, i2)]
    )
    assert hits2 == 0 and edited2.free_transfers == 0
    edited3, hits3 = apply_transfers(replace(sq, free_transfers=3), world, [(o1, i1)])
    assert hits3 == 0 and edited3.free_transfers == 2


def test_selling_the_captain_clears_the_armband(world):
    sq = make_squad(world)
    cap = sq.captain
    edited, _ = apply_transfers(sq, world, [(cap, replacement(world, sq, cap))])
    assert edited.captain is None and edited.vice == sq.vice


def test_empty_draft_changes_nothing(world):
    sq = make_squad(world)
    edited, hits = apply_transfers(sq, world, [])
    assert hits == 0 and edited.players == sq.players and edited.bank == sq.bank
    assert edited.free_transfers == sq.free_transfers


# ------------------------------------------------------------------ errors in plain words


def test_unknown_player(world):
    sq = make_squad(world)
    with pytest.raises(DraftError, match="don't know a player with ID 99999"):
        apply_transfers(sq, world, [(sq.players[0], 99999)])


def test_selling_someone_you_dont_own(world):
    sq = make_squad(world)
    stranger = next(p for p in world.players if p not in sq.players)
    with pytest.raises(DraftError, match="isn't in your squad"):
        apply_transfers(sq, world, [(stranger, sq.players[0])])


def test_duplicates(world):
    sq = make_squad(world, bank=10)
    out = sq.players[5]
    i1 = replacement(world, sq, out)
    i2 = replacement(world, sq, out, taken={i1})
    with pytest.raises(DraftError, match="selling .* twice"):
        apply_transfers(sq, world, [(out, i1), (out, i2)])
    other = sq.players[6]
    with pytest.raises(DraftError, match="buying .* twice"):
        apply_transfers(sq, world, [(out, i1), (other, i1)])
    with pytest.raises(DraftError, match="buying him back"):
        apply_transfers(sq, world, [(out, i1), (other, out)])
    with pytest.raises(DraftError, match="already in your squad"):
        apply_transfers(sq, world, [(out, other)])


def test_wrong_positions(world):
    sq = make_squad(world, bank=10)
    defender = next(p for p in sq.players if world.players[p].pos == R.DEF)
    mid = next(
        p.id
        for p in world.players.values()
        if p.pos == R.MID and p.id not in sq.players
    )
    with pytest.raises(DraftError) as e:
        apply_transfers(sq, world, [(defender, mid)])
    msg = str(e.value)
    assert "4 defenders and 6 midfielders" in msg
    assert "exactly 2 goalkeepers, 5 defenders, 5 midfielders and 3 forwards" in msg


def test_more_than_three_from_one_club(world):
    sq = make_squad(world, bank=50)
    club = world.players[sq.players[0]].team
    have = club_count(world, sq.players, club)
    # Sell players from other clubs for new signings from `club` until it would have 4.
    others = [p for p in sq.players if world.players[p].team != club]
    moves = []
    for k, out in enumerate(others[: R.MAX_PER_CLUB + 1 - have]):
        new_id = 1000 + k
        world.players[new_id] = replace(world.players[out], id=new_id, team=club)
        moves.append((out, new_id))
    with pytest.raises(DraftError, match="FPL allows at most 3 from one club"):
        apply_transfers(sq, world, moves)


def test_over_budget(world):
    sq = make_squad(world, bank=0.0)
    out = sq.players[5]
    inn = replacement(world, sq, out, cheap=False)
    gap = round(world.players[inn].price - sq.sell_price[out], 1)
    assert gap > 0
    with pytest.raises(DraftError, match=f"cost £{gap:.1f}m more than you have"):
        apply_transfers(sq, world, [(out, inn)])


def test_parse_moves():
    assert parse_moves("") == [] and parse_moves(None) == []
    assert parse_moves("301-55,7-200") == [(301, 55), (7, 200)]
    for bad in ("301", "a-b", "1-2-3", "1-2,", "-5-6"):
        with pytest.raises(DraftError, match="expected format"):
            parse_moves(bad)
    with pytest.raises(DraftError, match="at most 15"):
        parse_moves(",".join(f"{i}-{i + 100}" for i in range(1, 17)))


# ------------------------------------------------------------------ scoring a draft


@pytest.fixture
def advice(monkeypatch, world):
    sq = make_squad(world, ft=1, bank=2.0)
    monkeypatch.setattr(advise, "load_squad", lambda *a, **k: sq)
    client = SimpleNamespace(element_summaries=lambda ids: {})
    return advise.build_advice(Config(team_id=1), client, now=NOW, world=world)


def test_empty_draft_gives_the_same_numbers_as_plain_advice(advice):
    before = advice_to_dict(advice)
    w = build_whatif(advice, [])
    d = whatif_to_dict(w)
    assert advice_to_dict(advice) == before  # the advice itself is untouched
    assert w.yours is w.hold and w.p_beats_hold is None
    plan = advice.plan
    g = advice.event.id
    later_hits = sum(wk.hits for wk in plan.weeks)
    assert d["compare"]["advisor"]["points"] == round(
        sum(plan.gw_points[x] for x in advice.horizon) - R.HIT_COST * later_hits, 1
    )
    assert [(m["out"], m["in"]) for m in d["compare"]["advisor"]["transfers"]] == [
        (m["out"]["id"], m["in"]["id"]) for m in before["transfers"]["moves"]
    ]
    assert d["compare"]["yours"] == d["compare"]["hold"]
    assert d["hits"] == 0 and d["bank_after"] == before["team"]["bank"]
    if not plan.buys:  # the advice holds: your (empty) version is the advice
        assert d["lineup"]["xi"] == before["lineup"]["xi"]
        assert d["lineup"]["captain_id"] == before["lineup"]["captain_id"]
        assert d["compare"]["yours"]["points"] == d["compare"]["advisor"]["points"]
    assert g == d["gameweek"]["id"]


def test_a_draft_is_scored_against_holding(advice, world):
    sq = advice.squad
    out = sq.players[12]
    inn = replacement(world, sq, out)
    w = build_whatif(advice, [(out, inn)])
    d = whatif_to_dict(w)
    g = advice.event.id
    ids = {p["id"] for p in d["lineup"]["xi"] + d["lineup"]["bench"]}
    assert inn in ids and out not in ids  # the edited squad plays
    assert w.yours.plan.buys == []  # no more moves this week
    assert 0 <= d["p_beats_hold"] <= 1
    assert d["confidence"] in ("strong", "likely", "close call")
    assert d["hits"] == 0 and d["free_transfers_left"] == 0
    assert d["moves"][0]["out"]["sell_price"] == sq.sell_price[out]
    assert d["summary"].startswith("Your transfers score")
    assert d["captain"]["id"] == w.yours.plan.captain[g]
    assert d["win_chance"] is None  # no league


def test_hits_come_off_your_points(advice, world):
    sq = advice.squad
    o1, o2 = sq.players[12], sq.players[13]
    i1 = replacement(world, sq, o1)
    i2 = replacement(world, sq, o2, taken={i1})
    w = build_whatif(advice, [(o1, i1), (o2, i2)])
    assert w.hits == 1
    plan = w.yours.plan
    raw = sum(plan.gw_points[x] for x in advice.horizon)
    later = sum(wk.hits for wk in plan.weeks)
    assert w.yours.points == round(raw - R.HIT_COST * (1 + later), 1)
    assert "after the -4 hit" in whatif_to_dict(w)["summary"]


def test_league_odds_charge_the_draft_hits(advice, world, monkeypatch):
    seen = {}

    def fake_score(view, w, dists, gws, plans, *a, **k):
        seen.update(plans)
        p = {n: 0.1 * (i + 1) for i, n in enumerate(plans)}
        return SimpleNamespace(p_first=p), []

    monkeypatch.setattr(whatif, "score_plans", fake_score)
    advice.league = SimpleNamespace(rivals=[object()])
    advice.league_choice = object()
    sq = advice.squad
    o1, o2 = sq.players[12], sq.players[13]
    i1 = replacement(world, sq, o1)
    i2 = replacement(world, sq, o2, taken={i1})
    w = build_whatif(advice, [(o1, i1), (o2, i2)])
    assert list(seen) == ["advisor", "hold", "yours"]
    assert seen["yours"].weeks[0].hits == w.yours.plan.weeks[0].hits + 1
    assert w.win == {"advisor": 0.1, "hold": 0.2, "yours": 0.3}
    assert build_whatif(advice, []).win["yours"] == 0.2  # an empty draft is holding


def test_invalid_draft_is_rejected(advice):
    sq = advice.squad
    with pytest.raises(DraftError):
        build_whatif(advice, [(sq.players[0], sq.players[1])])


# ------------------------------------------------------------------ the web endpoint


@pytest.fixture
def api(monkeypatch, tmp_path, world):
    sq = make_squad(world, ft=1, bank=2.0)
    monkeypatch.setattr(advise, "load_squad", lambda *a, **k: sq)
    monkeypatch.setattr(web, "load_world", lambda client: world)
    monkeypatch.setenv("FPL_DATA_DIR", str(tmp_path))
    client = SimpleNamespace(element_summaries=lambda ids: {}, cache_dir=None)
    app = web.create_app(client=client, start_background=False, serverless=False)
    return TestClient(app), sq


def test_whatif_endpoint(api, world):
    c, sq = api
    plain = c.get("/api/advice", params={"team": 1}).json()
    assert plain["pending_note"].startswith("FPL only shows your squad")
    assert plain["pending_note"] not in plain["notes"]
    assert {p["id"] for p in plain["squad"]} == set(sq.players)
    assert all(p["sell_price"] == sq.sell_price[p["id"]] for p in plain["squad"])

    empty = c.get("/api/whatif", params={"team": 1}).json()
    assert empty["compare"]["yours"] == empty["compare"]["hold"]
    assert [(m["out"], m["in"]) for m in empty["compare"]["advisor"]["transfers"]] == [
        (m["out"]["id"], m["in"]["id"]) for m in plain["transfers"]["moves"]
    ]

    out = sq.players[12]
    inn = replacement(world, sq, out)
    r = c.get("/api/whatif", params={"team": 1, "moves": f"{out}-{inn}"})
    assert r.status_code == 200, r.text
    assert r.json()["moves"][0]["in"]["id"] == inn

    bad = c.get("/api/whatif", params={"team": 1, "moves": f"{out}-{sq.players[0]}"})
    assert bad.status_code == 400
    assert "already in your squad" in bad.json()["error"]
    junk = c.get("/api/whatif", params={"team": 1, "moves": "nonsense"})
    assert junk.status_code == 400 and "expected format" in junk.json()["error"]


def test_players_endpoint(api, world):
    c, _ = api
    d = c.get("/api/players").json()
    assert len(d["players"]) == len(world.players)
    p = d["players"][0]
    assert set(p) >= {"id", "name", "team", "pos", "price"}
    assert p["pos"] in ("GKP", "DEF", "MID", "FWD")

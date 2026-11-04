"""Solving the plan, holding and no-hit side by side gives the same decision as one at a time."""

import threading
import time

import pytest
from conftest import make_world, pick_squad
from test_robust import GWS, dists_with, outsiders, squad_obj

from fpl_advisor import advise, chips, optimise, robust, whatif
from fpl_advisor import rules as R


def decide_both(monkeypatch, world, squad, dists, chips_held=(), ft=1):
    monkeypatch.setattr(advise, "project_dist", lambda w, gws, **kw: dists)
    out = []
    for workers in (1, 3):
        sq = squad_obj(world, squad, ft)
        sq.chips_available = list(chips_held)
        out.append(advise.decide(world, sq, initial=False, max_hits=2, workers=workers))
    return out


def same_decision(a, b):
    for name in (
        "window",
        "horizon",
        "xp",
        "base",
        "plan",
        "chips",
        "chip_now",
        "hold",
        "alternatives",
        "value_of_moves",
        "p_beats_hold",
        "confidence",
        "move_confidence",
        "robust_note",
        "p_hit",
    ):
        assert getattr(a, name) == getattr(b, name), name


@pytest.fixture
def base():
    world = make_world()
    return world, pick_squad(world)


def test_free_move(monkeypatch, base):
    world, squad = base
    (a,) = outsiders(world, squad, R.MID, 1)
    seq, par = decide_both(
        monkeypatch,
        world,
        squad,
        dists_with(world, squad, {a: {g: (8.0, 0.2) for g in GWS}}),
    )
    assert a in seq.plan.buys and seq.plan.hits == 0 and seq.hold is not None
    same_decision(seq, par)


def test_no_moves(monkeypatch, base):
    world, squad = base
    seq, par = decide_both(monkeypatch, world, squad, dists_with(world, squad, {}))
    assert not seq.plan.buys and seq.hold is None  # holding was never compared
    same_decision(seq, par)


def test_hit_kept(monkeypatch, base):
    world, squad = base
    a, b = outsiders(world, squad, R.MID, 2)
    special = {a: {g: (12.0, 0.1) for g in GWS}, b: {g: (12.0, 0.1) for g in GWS}}
    monkeypatch.setattr(robust, "HIT_CONFIDENCE", 0.7)
    seq, par = decide_both(monkeypatch, world, squad, dists_with(world, squad, special))
    assert seq.plan.hits == 1 and "no_hit" in seq.alternatives
    same_decision(seq, par)


def test_hit_turned_down(monkeypatch, base):
    world, squad = base
    a, b = outsiders(world, squad, R.MID, 2)
    special = {
        a: {g: (8.0, 0.2) for g in GWS},
        b: {g: ((7.6 if g == GWS[0] else 3.0), 1.5) for g in GWS},
    }
    monkeypatch.setattr(robust, "HIT_CONFIDENCE", 0.7)
    seq, par = decide_both(monkeypatch, world, squad, dists_with(world, squad, special))
    assert seq.alternatives["planner"].hits == 1 and seq.plan.hits == 0
    same_decision(seq, par)


@pytest.mark.parametrize("chip", ["3xc", "bboost"])
def test_chip_this_week_with_a_hit(monkeypatch, base, chip):
    """Hold and no-hit are solved again with the chip the plan plays now."""
    world, squad = base
    a, b = outsiders(world, squad, R.MID, 2)
    # Two big signings, one costing a hit, both huge this week.
    special = {
        a: {g: ((30.0 if g == GWS[0] else 9.0), 0.1) for g in GWS},
        b: {g: ((14.0 if g == GWS[0] else 9.0), 0.1) for g in GWS},
    }
    monkeypatch.setitem(
        chips.KEEP_VALUE, chip, 0.0
    )  # worth playing as soon as it gains
    monkeypatch.setattr(robust, "HIT_CONFIDENCE", 0.7)
    seq, par = decide_both(
        monkeypatch, world, squad, dists_with(world, squad, special), chips_held=[chip]
    )
    assert seq.chip_now is not None and seq.chip_now.chip == chip
    assert seq.plan.chip_now == chip and seq.hold.chip_now == chip
    assert seq.plan.hits == 1 and seq.alternatives["no_hit"].chip_now == chip
    same_decision(seq, par)


def test_solves_run_side_by_side(monkeypatch, base):
    """With workers, the plan, holding and no-hit are solving at the same time."""
    world, squad = base
    a, b = outsiders(world, squad, R.MID, 2)
    special = {a: {g: (12.0, 0.1) for g in GWS}, b: {g: (12.0, 0.1) for g in GWS}}
    monkeypatch.setattr(robust, "HIT_CONFIDENCE", 0.7)
    running, most = [0], [0]
    lock = threading.Lock()
    real = advise.solve

    def slow(*a, **k):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.2)
        try:
            return real(*a, **k)
        finally:
            with lock:
                running[0] -= 1

    monkeypatch.setattr(advise, "solve", slow)
    seq, par = decide_both(monkeypatch, world, squad, dists_with(world, squad, special))
    assert most[0] == 3
    same_decision(seq, par)


def test_whatif_uses_the_hold_solved_alongside(monkeypatch, base):
    world, squad = base
    (a,) = outsiders(world, squad, R.MID, 1)
    dists = dists_with(world, squad, {a: {g: (8.0, 0.2) for g in GWS}})
    _, par = decide_both(monkeypatch, world, squad, dists)
    assert par.no_chip_hold() == par.hold

    class A:  # just what _hold_plan reads
        decision = par
        squad = None

    par.hold = None  # as when the plan plays a Wildcard or Free Hit
    monkeypatch.setattr(advise, "solve", None)  # no new solve allowed
    assert whatif._hold_plan(A()) == par.no_chip_hold()


def test_solve_workers(monkeypatch):
    monkeypatch.setenv("FPL_SOLVE_WORKERS", "1")
    assert optimise.solve_workers() == 1
    monkeypatch.delenv("FPL_SOLVE_WORKERS")
    monkeypatch.setattr(optimise, "usable_cpus", lambda: 1)
    assert optimise.solve_workers() == 1  # one CPU: one solve at a time, as before
    monkeypatch.setattr(optimise, "usable_cpus", lambda: 16)
    assert optimise.solve_workers() == optimise.MAX_SOLVE_WORKERS
    assert optimise.usable_cpus.__name__  # the real one is still importable

"""Advice as JSON-ready data for the web page. Text fields may contain **bold** markers."""

from __future__ import annotations

import copy

from . import rules as R
from .advise import (
    Advice,
    _why_out,
    captain_lines,
    captain_reason_league,
    evidence,
    league_win_odds,
    pencilled_weeks,
    plan_moves,
    signals_live,
    transfer_confidence,
    transfer_lines,
)
from .prices import outlook
from .robust import level
from .squad import chips_available


def _player(a: Advice, pid: int) -> dict:
    p = a.world.players[pid]
    g = a.event.id
    o = outlook(p)
    return {
        "id": pid,
        "name": p.name,
        "team": a.world.teams[p.team].short,
        "pos": R.POS_NAME[p.pos],
        "price": p.price,
        "xp_next": round(a.xp[pid].get(g, 0.0), 1),
        "xp_horizon": round(a.horizon_xp(pid), 1),
        "fixtures": a.fixtures(pid),
        "next_fixture": a.fixtures(pid, 1),
        "status": p.status,
        "chance": p.chance_next,
        "news": p.news,
        "price_move": None
        if not o or o.nights > 0
        else ("rise" if o.direction > 0 else "fall"),
        **_signal(a, pid),
    }


def _pct(v: float | None) -> float | None:
    return None if v is None else round(v, 3)


def _signal(a: Advice, pid: int) -> dict:
    """Outside signals for one player; keys are absent when no source covered them."""
    sig = a.signals.players.get(pid)
    if not sig:
        return {}
    live = signals_live(a)
    single = a.fixture_count(pid) == 1
    out = {
        "team_news": (sig.news or None) if live else None,
        "team_news_source": (sig.news_source or None) if live else None,
        "p_start": _pct(sig.p_start) if live else None,
        "p_goal": _pct(sig.p_goal) if live and single else None,
        "p_assist": _pct(sig.p_assist) if live and single else None,
        "elite_own": _pct(sig.elite_own),
        "elite_captain": _pct(sig.elite_captain),
        "elite_in": sig.elite_in,
        "elite_out": sig.elite_out,
    }
    return {k: v for k, v in out.items() if v is not None}


def _elite(a: Advice) -> dict | None:
    """What consistently good managers own and changed in the last gameweek."""
    e = a.signals.elite
    if not e or not e.managers:
        return None
    owned = set(a.squad.players)
    sigs = [(pid, s) for pid, s in a.signals.players.items() if pid in a.world.players]

    def row(pid: int) -> dict:
        return {**_player(a, pid), "in_your_team": pid in owned}

    by_own = sorted(
        (x for x in sigs if (x[1].elite_own or 0) > 0),
        key=lambda x: -(x[1].elite_own or 0),
    )
    net = sorted(
        (x for x in sigs if (x[1].elite_in or 0) or (x[1].elite_out or 0)),
        key=lambda x: (x[1].elite_in or 0) - (x[1].elite_out or 0),
    )
    return {
        "managers": e.managers,
        "gameweek": e.gameweek,
        "seasons": e.seasons,
        "median_rank": e.median_rank,
        "most_owned": [row(pid) for pid, _ in by_own[:10]],
        "missing": [row(pid) for pid, s in by_own if pid not in owned][:5],
        "bought": [
            row(pid)
            for pid, s in reversed(net)
            if (s.elite_in or 0) > (s.elite_out or 0)
        ][:5],
        "sold": [row(pid) for pid, s in net if (s.elite_out or 0) > (s.elite_in or 0)][
            :5
        ],
    }


def _creators(a: Advice) -> dict | None:
    """The Creator room (what big FPL YouTubers plan), only for this gameweek. Display only."""
    room = a.signals.creators
    if not room or room.get("gameweek") != a.event.id or not room.get("creators"):
        return None
    owned = set(a.squad.players)

    def mark(ref):
        if isinstance(ref, dict) and "id" in ref:
            ref["in_your_team"] = ref["id"] in owned

    out = copy.deepcopy(room)
    for item in out.get("agree", []):
        mark(item.get("player"))
    for item in out.get("split", []):
        mark(item.get("player"))
        for o in item.get("options", []):
            mark(o.get("player"))
    return out


def _strip(md: str) -> str:
    return md.strip().lstrip("- ").strip()


def _transfers(a: Advice) -> dict:
    lines = transfer_lines(a)
    moves = []
    pm = plan_moves(a)
    mc = a.move_confidence if len(a.move_confidence) == len(pm) else []
    for k, m in enumerate(pm):
        out, inn = m.out, m.inn
        po, pi = _player(a, out), _player(a, inn)
        po["sell_price"] = m.sell_price
        why_in = f"{pi['xp_horizon']:.1f} expected points over {len(a.horizon)} weeks; fixtures {pi['fixtures']}"
        if a.league:
            why_in += f"; owned by {a.league.ownership.get(inn, 0):.0%} of your rivals"
        why_in += "".join(f"; {e}" for e in evidence(a, inn))
        moves.append(
            {
                "out": po,
                "in": pi,
                # The gain as the plan counts it: weighted weeks, the role the plan gives him.
                "gain": m.gain,
                # The plain difference in expected points over the horizon, for reference.
                "xp_difference": round(pi["xp_horizon"] - po["xp_horizon"], 1),
                "money": m.money,  # change to the bank: + frees money, - costs money
                "why_out": _why_out(a, out),
                "why_in": why_in + ".",
                # P(this swap gains more than its share of any hit), with forecasts nudged.
                "confidence": (
                    {"level": level(mc[k].p), "p_beats_hold": round(mc[k].p, 3)}
                    if mc
                    else None
                ),
            }
        )
    hit_note = next(
        (_strip(line) for line in lines if line.lstrip().startswith("About the hit")),
        None,
    )
    return {
        "headline": _strip(lines[0]),
        "initial": a.initial,
        "free_transfers": a.squad.free_transfers,
        "count": len(a.plan.buys),
        "hits": a.plan.hits,
        "hit_cost": R.HIT_COST * a.plan.hits,
        "hit_note": hit_note,
        "moves": moves,
        "squad": [_player(a, p) for p in a.plan.squad] if a.initial else [],
        "bank_after": a.plan.bank_after,
        "value_of_moves": a.value_of_moves,
        # How sure the transfer advice is: level of the advice given (move or hold) and
        # P(this week's best moves beat making none). None when there was nothing to test.
        "confidence": transfer_confidence(a),
        "pencilled": [] if a.initial else [_week(a, wk) for wk in pencilled_weeks(a)],
    }


def _week(a: Advice, wk) -> dict:
    """A later week of the plan: moves and chip pencilled in, not advice to act on yet."""
    return {
        "gameweek": wk.gw,
        "free_transfers": wk.free_transfers,
        "hits": wk.hits,
        "chip": R.CHIP_NAMES[wk.chip] if wk.chip else None,
        "chip_code": wk.chip,
        "moves": [
            {
                "out": {**_player(a, m.out), "sell_price": m.sell_price},
                "in": _player(a, m.inn),
                "gain": m.gain,
                "money": m.money,
            }
            for m in wk.moves
        ],
        "bank_after": wk.bank,
    }


def _captain(a: Advice) -> dict:
    g = a.event.id
    cap, vice = a.plan.captain[g], a.plan.vice[g]
    ranked = sorted(a.plan.xi[g], key=lambda p: -a.xp[p].get(g, 0))[:3]
    reason = captain_reason_league(a)
    lines = [ln for ln in captain_lines(a) if not reason or reason not in ln]
    return {
        "captain": _player(a, cap),
        "vice": _player(a, vice),
        "multiplier": 3 if (a.chip_now and a.chip_now.chip == "3xc") else 2,
        "options": [_player(a, p) for p in ranked],
        "note": lines[2] if len(lines) > 2 else None,
        # Set only when the captain was changed to raise the chance of winning the league.
        "reason_league": reason,
    }


def _lineup(a: Advice) -> dict:
    """The team sheet after our moves, plus `current`: the team exactly as the manager left it."""
    g = a.event.id
    return {
        "xi": [_player(a, p) for p in a.plan.xi[g]],
        "bench": [_player(a, p) for p in a.plan.bench[g]],
        "captain_id": a.plan.captain[g],
        "vice_id": a.plan.vice[g],
        "expected_points": round(a.plan.gw_points[g], 1),
        "bench_boost": bool(a.chip_now and a.chip_now.chip == "bboost"),
        "current": _current_lineup(a),
    }


def _current_lineup(a: Advice) -> dict | None:
    """The XI, bench and captain saved in FPL last gameweek, scored for this one.

    FPL keeps that line-up unless the manager changes it, so it's what scores if they do nothing.
    Changes made since the last deadline can't be seen (FPL hides them until the next one).
    """
    sq = a.squad
    if a.initial or len(sq.starting) != 11:
        return None
    g = a.event.id
    xi = list(sq.starting)
    bench = [p for p in sq.players if p not in xi]
    cap = sq.captain if sq.captain in xi else None
    vice = sq.vice if sq.vice in xi else None
    points = sum(a.xp[p].get(g, 0.0) for p in xi)
    if (
        cap is not None
    ):  # the armband doubles one score; the vice only counts if they miss out
        points += a.xp[cap].get(g, 0.0)
    return {
        "xi": [_player(a, p) for p in xi],
        "bench": [_player(a, p) for p in bench],
        "captain_id": cap,
        "vice_id": vice,
        "expected_points": round(points, 1),
        "bench_boost": False,
    }


def _league(a: Advice) -> dict | None:
    lg = a.league
    if not lg:
        return None
    g = a.event.id
    mine = set(a.plan.squad)
    everyone = sorted([lg.me, *lg.rivals], key=lambda m: m.rank)
    threats = sorted(
        (
            p
            for p, s in lg.ownership.items()
            if s >= 0.5 and p not in mine and a.xp[p].get(g, 0) >= 2.5
        ),
        key=lambda p: -lg.ownership[p],
    )[:6]
    diffs = sorted(
        (p for p in a.plan.xi[g] if lg.ownership.get(p, 0) <= 0.25),
        key=lambda p: lg.ownership.get(p, 0),
    )[:6]
    return {
        "name": lg.name,
        "rank": lg.me.rank,
        "total_managers": lg.total_managers,
        "tracked": len(everyone),
        "table": [
            {
                "rank": m.rank,
                "manager": m.manager,
                "team_name": m.team_name,
                "total": m.total,
                "event_total": m.event_total,
                "is_me": m is lg.me,
            }
            for m in everyone[:10] + ([lg.me] if lg.me not in everyone[:10] else [])
        ],
        "gap_to_leader": lg.leader.total - lg.me.total,
        "target": lg.target,
        "required_per_gw": round(lg.required_per_gw, 1),
        "my_average": round(lg.me.total / max(1, lg.me.gws_played), 1),
        "remaining_gws": lg.remaining_gws,
        "last_season_best": (
            {"manager": lg.last_season_best[0], "points": lg.last_season_best[1]}
            if lg.last_season_best
            else None
        ),
        "strategy": a.risk_reason,
        "risk_lambda": round(a.risk_lambda, 3),
        "threats": [
            {**_player(a, p), "ownership": round(lg.ownership[p], 2)} for p in threats
        ],
        "differentials": [
            {**_player(a, p), "ownership": round(lg.ownership.get(p, 0), 2)}
            for p in diffs
        ],
        "win_odds": league_win_odds(a),
        "rival_chips": [
            {
                "manager": m.manager,
                "chips": [
                    R.CHIP_NAMES[c]
                    for c in chips_available(a.world.chips, m.chips_used, g)
                ],
            }
            for m in lg.rivals[:5]
        ],
    }


def advice_to_dict(a: Advice) -> dict:
    s = a.squad
    return {
        "team": {
            "id": s.team_id,
            "name": s.team_name,
            "manager": s.manager,
            "total_points": s.total_points,
            "value": s.value,
            "bank": s.bank,
            "free_transfers": s.free_transfers,
        },
        "gameweek": {
            "id": a.event.id,
            "deadline": a.event.deadline.isoformat(),
            "hours_to_deadline": round(a.hours_to_deadline, 1),
        },
        "horizon": a.horizon,
        "initial": a.initial,
        "transfers": _transfers(a),
        "captain": _captain(a),
        "lineup": _lineup(a),
        "chips": [
            {
                "chip": c.chip,
                "name": R.CHIP_NAMES[c.chip],
                "play_now": c.play_now,
                "expires_gw": c.expires_gw,
                "reason": c.reason,
            }
            for c in a.chips
        ],
        "chip_now": R.CHIP_NAMES[a.chip_now.chip] if a.chip_now else None,
        "chip_now_code": a.chip_now.chip if a.chip_now else None,
        "timing": list(a.timing),
        "league": _league(a),
        "elite": _elite(a),
        "creators": _creators(a),
        "sources": dict(a.signals.updated),
        "notes": list(a.notes),
    }

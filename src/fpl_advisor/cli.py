"""Command line: `fpl-advisor advise` for the full report, `fpl-advisor check` for alerts."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import UTC, datetime

from . import rules as R
from . import usage
from .advise import Advice, build_advice, render_markdown, summary_text
from .api import FPLClient, FPLError
from .config import Config, ConfigError, load_config
from .keys import load_dotenv, redact
from .notify import Notification, NotifyError, get_notifier, run_url
from .prices import next_price_update, outlook
from .signals_service import SignalsHub
from .state import AlertState
from .world import load_world


def _write_step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")


def collect_alerts(
    a: Advice, cfg: Config, state: AlertState, click: str | None = None
) -> list[tuple[list[str], Notification]]:
    """Alerts that are due now and haven't been sent yet, with the state keys to mark once sent."""
    gw = a.event.id
    click = click or run_url()
    alerts: list[tuple[list[str], Notification]] = []
    price_night = next_price_update(datetime.now(UTC)).strftime("%Y-%m-%d")

    # 1. Deadline reminders: send only the tightest window that applies.
    hours = a.hours_to_deadline
    due = sorted(h for h in cfg.reminder_hours if 0 < hours <= h)
    if due:
        key = f"deadline:{gw}:{due[0]}"
        if not state.seen(key):
            when = f"{hours:.0f}h" if hours >= 1 else f"{hours * 60:.0f} min"
            # Marking the wider windows too means a late first run doesn't send a stale "24h" alert.
            # The due window's key goes first: it is the one checked against what was already sent.
            keys = [key] + [
                f"deadline:{gw}:{h}" for h in cfg.reminder_hours if h > due[0]
            ]
            alerts.append(
                (
                    keys,
                    Notification(
                        title=f"GW{gw} deadline in {when}",
                        body=summary_text(a),
                        priority=4 if due[0] <= 2 else 3,
                        tags=["alarm_clock"],
                        click=click,
                    ),
                )
            )

    # 2. Price changes that affect what you should do today.
    # Waiting for team news is normally better, so this only fires when waiting costs money.
    price_keys, price_lines = [], []
    if hours > 6:
        for pid in a.plan.buys:
            o = outlook(a.world.players[pid])
            key = f"price:{pid}:up:{price_night}"
            if o and o.direction > 0 and o.nights == 0 and not state.seen(key):
                price_keys.append(key)
                price_lines.append(f"Buy {a.name(pid)}: likely to rise £0.1m tonight.")
        for pid in a.plan.sells:
            o = outlook(a.world.players[pid])
            key = f"price:{pid}:down:{price_night}"
            if o and o.direction < 0 and o.nights == 0 and not state.seen(key):
                price_keys.append(key)
                price_lines.append(f"Sell {a.name(pid)}: likely to drop £0.1m tonight.")
    if price_keys:
        alerts.append(
            (
                price_keys,
                Notification(
                    title="Make your transfers before prices change tonight",
                    body="\n".join(price_lines)
                    + "\nPrices update at about 01:30 UK time.\n"
                    + summary_text(a),
                    tags=["chart_with_upwards_trend"],
                    click=click,
                ),
            )
        )

    # 3. Injury, suspension and other news about your players.
    # Only new or changed news counts, and it is bundled into one notification.
    news_keys, news_lines = [], []
    for pid in a.squad.players:
        p = a.world.players[pid]
        flagged = p.status != "a" or (p.chance_next is not None and p.chance_next < 100)
        if not flagged:
            continue
        digest = hashlib.sha1(
            f"{p.status}{p.chance_next}{p.news}".encode()
        ).hexdigest()[:10]
        key = f"news:{pid}:{digest}"
        if state.seen(key):
            continue
        if pid in a.plan.sells:
            action = "plan: sell"
        elif pid not in a.plan.xi[gw]:
            action = "plan: bench"
        else:
            action = "plan: still start"
        news_keys.append(key)
        news_lines.append(f"{p.name}: {p.news or 'doubtful'} ({action})")
    if news_keys:
        alerts.append(
            (
                news_keys,
                Notification(
                    title=f"Team news: {len(news_lines)} of your players",
                    body="\n".join(news_lines),
                    tags=["warning"],
                    click=click,
                ),
            )
        )

    # 4. Chips about to expire.
    for c in a.chips:
        if c.expires_gw and c.expires_gw - gw <= 2:
            alerts.append(
                (
                    [f"chip:{c.chip}:{c.expires_gw}"],
                    Notification(
                        title=f"{R.CHIP_NAMES[c.chip]} expires after GW{c.expires_gw}",
                        body=c.reason,
                        tags=["hourglass"],
                        click=click,
                    ),
                )
            )

    return [(keys, n) for keys, n in alerts if not state.seen(keys[0])]


def _advice(cfg: Config, client: FPLClient, **kw) -> Advice:
    """Advice with whatever outside signals are configured (odds, news, elite managers).

    Uses the same data folder as the web server (FPL_DATA_DIR, default ./data), so the odds
    credit ledger and the elite list are shared. The elite list is only built by the web server
    (it is a long scan); the CLI reuses it if present.
    """
    world = load_world(client)
    data = os.environ.get("FPL_DATA_DIR", "data")
    # A CLI run may make paid calls; each is capped by the shared ledger in FPL_DATA_DIR.
    signals = SignalsHub(client, data).refresh(world, discover=False)
    return build_advice(cfg, client, world=world, signals=signals, **kw)


def cmd_advise(cfg: Config, args) -> int:
    client = FPLClient(cfg.cache_dir)
    advice = _advice(cfg, client, horizon_len=args.horizon)
    md = render_markdown(advice)
    print(md)
    _write_step_summary(md)
    return 0


def cmd_check(cfg: Config, args) -> int:
    client = FPLClient(cfg.cache_dir)
    state = AlertState(cfg.state_path)
    advice = _advice(cfg, client)
    _write_step_summary(render_markdown(advice))
    notifier = get_notifier(cfg)
    alerts = collect_alerts(advice, cfg, state)
    for keys, n in alerts:
        if args.dry_run:
            print(f"[dry run] {n.title}\n  {n.body}\n")
            continue
        notifier.send(n)
        # Save after every send, so a later failure doesn't make this alert repeat next hour.
        for key in keys:
            state.mark(key)
        state.save()
    print(f"{len(alerts)} alert(s) {'would be ' if args.dry_run else ''}sent.")
    return 0


def cmd_usage(args) -> int:
    if args.reset_confirm:
        ledger = usage.default()
        ledger.reset()
        print(
            f"Usage ledger {ledger.path} reset to zero (old copies kept next to it). "
            "Spending caps now count from nothing, so check each provider's own dashboard."
        )
    print(usage.report())
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # also installs key redaction for every log record
    ap = argparse.ArgumentParser(
        prog="fpl-advisor", description="Fantasy Premier League advisor."
    )
    ap.add_argument("--config", help="path to a TOML config file")
    ap.add_argument("--team", type=int, help="your FPL team ID")
    ap.add_argument("--league", type=int, help="your mini-league ID")
    ap.add_argument("--risk", choices=["auto", "low", "medium", "high"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_adv = sub.add_parser("advise", help="print this gameweek's advice")
    p_adv.add_argument(
        "--horizon", type=int, default=5, help="gameweeks to plan over (default 5)"
    )
    p_chk = sub.add_parser(
        "check", help="send any alerts that are due (run this hourly)"
    )
    p_chk.add_argument(
        "--dry-run", action="store_true", help="print alerts instead of sending them"
    )
    p_use = sub.add_parser(
        "usage",
        help="show spending on the paid APIs today and this month, against the caps",
    )
    p_use.add_argument(
        "--reset-confirm",
        action="store_true",
        help="start the spend ledger again from zero (only after checking each provider)",
    )
    args = ap.parse_args(argv)
    if args.cmd == "usage":
        return cmd_usage(args)

    try:
        cfg = load_config(
            args.config, team_id=args.team, league_id=args.league, risk=args.risk
        )
        return {"advise": cmd_advise, "check": cmd_check}[args.cmd](cfg, args)
    except (ConfigError, FPLError, NotifyError, ValueError) as e:
        print(f"error: {redact(e)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

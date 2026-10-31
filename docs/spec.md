# fpl-advisor spec (v0.2)

## Problem
Casual FPL managers lose mini-leagues mainly through inconsistency: missed deadlines, sitting on injured
players, wasted free transfers, poor captain picks, and chips that expire unused. They also do not know
*why* a move is good, so they do not improve.

## Users
Any FPL manager. They supply a team ID (and optionally a mini-league ID). No FPL login is needed; the
tool uses only FPL's public API.

## Product shape
A hosted, single-page website. A visitor pastes their FPL team link (and optionally a mini-league
link) and gets this week's advice. On the same page they can turn on phone reminders (ntfy), with no
account or login. The server wraps the Python engine below; the command line and the GitHub Actions
workflow remain for self-hosters and developers.

## What it does
1. **Advice per gameweek** (`fpl-advisor advise`): transfers (with hit or no hit), captain and vice,
   starting XI and bench order, and whether to play a chip. Every item has a plain-language reason.
2. **League view**: standings, gap to leader, the season points target, rival ownership of your
   players (who you gain on, who you only "keep up" with) and rival chips still in hand.
3. **Strategy that adapts to the league**: when you are behind, it weights players few rivals own a bit
   more. When you lead, it weights them less. The weighting is capped so it is never reckless.
4. **Alerts** (`fpl-advisor check`, run hourly): deadline reminders (24 h and 2 h by default), price
   changes expected tonight for players you own or plan to buy, injury news on your players, and chips
   about to expire. Alerts go to ntfy (phone push) or stdout.
5. **Runs without your computer**: a GitHub Actions workflow that forks can enable with three settings.

## Model (summary; details in code)
- Expected points per player per fixture, built from FPL's per-90 stats (xG, xA, defensive
  contributions, saves, bonus). These are adjusted for opponent strength, home or away, and chance of
  playing. Scoring constants are in `rules.py`.
- Optimiser: an integer program over a 5-gameweek horizon, with later gameweeks weighted less. It
  respects the budget (at selling prices), the 3-per-club limit, positions and formation. It charges
  −4 per extra transfer and gives a small value for rolling a free transfer.

## Evidence required (definition of done for v0.1)
- Unit tests cover the squad rules, selling price, free-transfer counting, the optimiser's validity on
  a fixture squad, and alert windows.
- A backtest on finished gameweeks reports the correlation and mean absolute error of projected
  versus actual points. The projection must beat a "last-season PPG" baseline.
- `advise` runs end to end on live data for a real team and gives a legal squad within budget.
- The README is enough for a stranger to get phone alerts working in under 10 minutes.

## Hosting constraints
- One server instance (the alert scheduler runs in-process; subscriptions live in SQLite on a volume).
- One shared FPL snapshot for all visitors, refreshed in the background; per-team data is cached
  on disk and pruned daily.
- Visitor IPs come from the hosting proxy (FORWARDED_ALLOW_IPS); rate limits are per visitor.
- Alert subscriptions expire at season end (FPL reuses team IDs each season).

## Out of scope for v0.2
Head-to-head leagues, accounts/logins, and logged-in actions such as making transfers for the user.

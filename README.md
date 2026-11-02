# fpl-advisor

An open-source Fantasy Premier League (FPL) advisor. It is a one-page website that tells you what to do each gameweek and explains why in plain words.

## Use it

Go to the website: https://<your-deployment>

The public URL will be added at launch. Until then, you can [run your own copy](#run-your-own).

1. Paste your FPL team link (or just the team ID).
2. Optional: paste your mini-league link. This adds standings, a points target and what your rivals own.
3. Read your advice: transfers, captain and vice-captain, starting XI and bench order, and whether to play a chip. Every suggestion has a plain reason.

The team sheet has two views. **Your team now** is the XI, bench and captain you last saved in FPL, with its expected points: what scores if you change nothing. **After our moves** is the team with the suggested transfers, captain and chips. You decide which to play.

No login and no password. It reads FPL's public data only.

If you do not have IDs to hand, use `/?demo=1` on the site to see an example team.

### Where to find your IDs

**Team ID.** Open the Points page on fantasy.premierleague.com. The number after `/entry/` is your team ID.

```
https://fantasy.premierleague.com/entry/1234567/event/9
                                        ^^^^^^^
```

**League ID (optional).** Open your mini-league's standings. The number after `/leagues/` is the league ID, for example `76543`.

The numbers in this README are made up. Use your own.

## Phone reminders (off for now)

Phone reminders through the free ntfy app are built but switched off on the website while the
advice itself is finished: everything happens on the page for now. Self-hosters can turn them back
on with `FPL_ALERTS=1`. The setup, what you get and what the server stores are described in
[docs/alerts.md](docs/alerts.md).

## How it works

**Projection model.** For each player the model estimates per-90 rates for xG, xA, saves, bonus points and defensive contributions. It also estimates how often the player starts and how many minutes they play. This season's matches count more the more recent they are. Last season is used as a starting point (a prior) that fades as this season's data grows. Team attack and defence come from xG blended with actual goals. Home advantage is included. Availability comes from FPL's own injury and news flags. Expected points then follow from FPL's scoring rules, which live in `src/fpl_advisor/rules.py`.

**Optimiser.** An integer program picks the best squad over a 5-gameweek horizon. Later gameweeks count for less. It respects your budget at your selling prices, the 3-players-per-club limit, and legal formations. Each extra transfer costs 4 points. Rolling a free transfer to next week has a small value. The captain is chosen on raw expected points, not on the league-adjusted score.

**League strategy.** If you give a mini-league, the advisor adapts to your position. If you are chasing, it leans a little toward players your rivals do not own. If you are leading, it leans a little toward players they do. This is deliberately gentle, so it never gets reckless.

**Chips.** Each chip is compared with its best later use inside its window. As the deadline for a half-season chip gets close, the bar for playing it drops, so you do not lose it by waiting.

**Outside signals (optional).** Three extra sources sharpen the coming gameweek (a fourth, the Creator room below, is shown but not used by the model). Each one is optional: with nothing configured the advisor runs on FPL data alone.
- *Consistently good managers.* The advisor finds the 100 managers with the best median overall rank over the last three seasons. It scans this season's top 50,000 once a month. At a polite 8 requests a second that takes about 2 hours, in the background. Their teams are public only after each deadline, so it shows what they owned, captained, bought and sold last gameweek. This is shown alongside the advice; it does not change expected points or the captain pick (see Accuracy).
- *Bookmaker odds* from [The Odds API](https://the-odds-api.com) (free key, 500 credits a month; the advisor uses about 300). Match odds set each fixture's expected goals. Anytime-scorer and assist odds move a player's goal and assist expectation part-way towards the bookmakers' view.
- *Team news* read by an LLM through [OpenRouter](https://openrouter.ai) (GPT-6.1 Terra by default, about $1-3 a month; capped at $0.30 a day and $5 a month). It turns news items into "chance of playing" and "chance of starting" for the next gameweek. Article text is treated as untrusted. No news feed is on by default, because the big publishers' terms restrict AI processing of their content. Point `FPL_NEWS_FEEDS` at a source whose terms you are happy with.

**Creator room (optional, display only).** A card that collects what the big FPL YouTubers (FPL Harry, Let's Talk FPL, FPLtips, FPL Focal, FPL Raptor, FPL Fran by default) plan for the next gameweek: who each one is thinking of buying, selling and captaining, any chip, and a link to the video. It then shows where they agree ("4 of 6 are thinking of buying this player") and where they are split. Creators watch each other, so agreement is one shared opinion, not independent votes, and none of this feeds the projection model.
- Needs `YOUTUBE_API_KEY` (a free [YouTube Data API v3](https://developers.google.com/youtube/v3/getting-started) key). Without it the card is hidden. It uses only the official API: one `channels.list` call per creator a month and one `playlistItems.list` call per creator per refresh (every 6 hours far from the deadline, hourly in the last day). That is about 400 of the free 10,000 daily units per gameweek. Cached YouTube data is deleted after 30 days, as the API policy requires.
- With `OPENROUTER_API_KEY` too, the same small LLM as team news reads each plan video's title and description once (about 6 to 18 short calls a gameweek, well under $0.01). Without it you still get each creator's latest plan video, but no summary or consensus. Video text is treated as untrusted, and only players actually named in it are kept.
- After a deadline it can show what each creator actually did (transfers, captain, chip) from FPL's public data, if you give their FPL entry IDs. They change every season, so none are shipped. Set them, or add and remove channels, in `fpl-advisor.toml` under `[creators]` (see `fpl-advisor.example.toml`) or with `FPL_CREATORS`, e.g. `FPL_CREATORS="FPLHarry=<entry id>,-FPLRaptor,+SomeOtherHandle"`.

**Prices.** Price alerts use FPL's own price-change projections. FPL updates prices around 01:30 UK time each night. You only get an early-move alert when waiting would cost you money. Otherwise the advice is to wait for team news.

## Accuracy

We tested the projection model out of sample on gameweeks 3 to 8 of the 2026/27 season. Each gameweek was projected using only data from before it.

| Measure | Model | Points per game so far | Last season's points per game |
| --- | --- | --- | --- |
| Mean absolute error (raw, lower is better) | 1.85 | 2.00 | 2.26 |
| Correlation with actual points | 0.378 | 0.265 | 0.198 |
| Average actual points of the top-30 picks | 4.11 | 3.61 | 3.68 |

Calibration is close. Players the model rated 2 to 3 points scored 2.80 on average (predicted 2.47). Players rated 4 to 5 scored 4.21 (predicted 4.38).

Be realistic. Single-gameweek FPL points are very noisy. A good projection helps on average over many weeks, not every week. Treat all numbers as estimates.

Reproduce the test:

```bash
uv run python scripts/backtest.py
```

The elite-manager signal was tested the same way, using their picks from the gameweek before (`scripts/elite_backtest.py`). Adding 2 points per unit of elite ownership lifted the top-30 picks from 4.11 to 4.46 points, and correlation stayed flat (0.381). Plain FPL-wide popularity added nothing. The elite are drawn from this season's top 50,000, so part of that gain may come from how they were chosen, and the sample is 6 gameweeks. Ownership also largely repeats what xG already shows, and adding it to expected points pulled captain picks toward the template, so the advisor keeps it as a separate signal rather than adding it to expected points. Odds and team news have no free history to test against, so their weights are judgment calls and are only checked going forward.

## Limitations

- It uses the public API only. Transfers you have already made for the upcoming gameweek are not visible until the deadline passes.
- Transfers are planned one gameweek at a time. It does not give you a full multi-week transfer plan.
- Classic leagues only. Head-to-head leagues are not supported.
- It cannot make transfers for you.
- The hosted site runs a single server instance. The first load after a restart can take up to a minute.
- Not affiliated with the Premier League or Fantasy Premier League.

## Run your own

### Docker

```bash
docker build -t fpl-advisor .
docker run --rm -p 8000:8000 -v fpl-data:/data fpl-advisor
```

Then open http://localhost:8000. For a real deployment see [docs/deploy.md](docs/deploy.md): the free, recommended path is **Vercel + GitHub Actions** (the website on Vercel, the 30-minute data refresh on GitHub Actions, its state in the Actions cache and the website's data published as a GitHub release asset). For one always-on server (Fly.io, Render, Railway), run exactly one instance and keep `/data` on a persistent volume.

### Local development

Install [uv](https://docs.astral.sh/uv/), then:

```bash
git clone https://github.com/<your-username>/fpl-advisor.git
cd fpl-advisor
uv run fpl-advisor-web
```

Open http://localhost:8000. Add `/?demo=1` to see sample data without calling FPL.

### Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8000` | Port the server listens on |
| `FPL_DATA_DIR` | `data` (`/data` in the Docker image) | Folder for the FPL cache and `alerts.db` (subscriptions). Use a persistent volume |
| `PUBLIC_URL` | unset | Public address of your site. Alerts then link back to your advice page |
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy server that delivers the push messages |
| `MAX_SUBSCRIPTIONS` | `300` | Stop accepting alert sign-ups beyond this many active subscriptions |
| `ALERT_RUN_MINUTE` | `7` | Minute past each hour when the alert check runs |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Set to `*` behind a hosting proxy (Fly, Render, Railway) so rate limits see real visitor IPs |
| `NTFY_TOKEN` | unset | Access token for ntfy (needed beyond ntfy.sh free-tier limits) |
| `FPL_ALERTS` | unset (off) | Set to `1` to turn on phone-alert sign-up and the alert scheduler on the website |
| `DISABLE_ALERT_SCHEDULER` | unset | Set to any value to turn the scheduler off. Sign-up still works |
| `ODDS_API_KEY` | unset | The Odds API key. Turns on bookmaker odds |
| `ODDS_MONTHLY_BUDGET` | `400` | Most Odds API credits to spend in a calendar month (calls also stop when the account has under 50 left) |
| `ODDS_DAILY_BUDGET` | `40` | Most Odds API credits to spend in a day (UTC) |
| `OPENROUTER_API_KEY` | unset | OpenRouter key. Turns on the team-news reader (also needs `FPL_NEWS_FEEDS`) |
| `OPENROUTER_MODEL` | `openai/gpt-6.1-terra` | Model that reads team news and creator videos (low reasoning effort; picked by a bake-off on real inputs) |
| `OPENROUTER_MONTHLY_BUDGET_USD` | `5.00` | Most to spend on OpenRouter in a calendar month (US$) |
| `OPENROUTER_DAILY_BUDGET_USD` | `0.30` | Most to spend on OpenRouter in a day (UTC, US$) |
| `OPENROUTER_MAX_CALLS_PER_DAY` | `100` | Most OpenRouter calls in a day (a normal day makes about 20) |
| `FPL_NEWS_FEEDS` | unset | Comma-separated RSS/Atom URLs to read, or `bbc` for BBC Sport's feeds (check their terms first) |
| `YOUTUBE_API_KEY` | unset | YouTube Data API v3 key. Turns on the Creator room |
| `OPENROUTER_MODEL_PRICE` | unset | Only priced models are ever called (`openai/gpt-6.1-terra`, `google/gemini-2.5-flash-lite`, `openai/gpt-4o-mini`). To use another plain model, set this to its `<input>,<output>` US$ per million tokens. Routers such as `openrouter/auto` and `:online` variants are always refused |
| `FPL_USAGE_PATH` | `~/.fpl-advisor/usage/usage.json` (`/data/usage/usage.json` in Docker) | The spend ledger. Every process using the same keys (website, CLI, cron) must point at the same file |
| `FPL_USAGE_MIRROR` | a `usage.mirror` folder next to it | Second copy of the ledger, in another folder |
| `YOUTUBE_DAILY_UNITS` | `2000` | Most YouTube quota units to use in a day (the day ends at midnight Pacific time, like Google's quota) |
| `FPL_CREATORS` | unset | Change the creator list: `Handle=<FPL entry id>`, `+Handle` to add, `-Handle` to remove, comma-separated |
| `ELITE_POOL_SIZE` | `50000` | How deep in this season's overall standings to search for consistently good managers |
| `ELITE_MAX_RPS` | `8` | Most FPL requests per second during that search |
| `FPL_SNAPSHOT_URL` | unset | Website (serverless) only: where to download the snapshot the refresh job published. Unset on Vercel, it is built from Vercel's `VERCEL_GIT_REPO_OWNER` / `VERCEL_GIT_REPO_SLUG`: `https://github.com/<owner>/<repo>/releases/download/snapshot/snapshot.json.gz`. With neither, the site reads `FPL_DATA_DIR` or loads FPL live |
| `FPL_SERVERLESS` | unset (on automatically when Vercel sets `VERCEL=1`) | Website reads the snapshot the refresh job published, runs no background work and refuses to start if a paid API key is set |
| `FPL_CACHE_DIR` | `.cache` | FPL response cache for `fpl-advisor refresh` and `discover-elite` |

### Keeping keys safe and costs capped

The odds, team-news and Creator-room features use paid or quota-limited APIs. The app guards
both the keys and the bill:

- **Keys stay on the server.** They are read from environment variables (or a local `.env`,
  which is gitignored and never copied into the Docker image). On a hosted deploy (Fly, Render,
  Railway, ...) set them as the platform's secrets or environment variables; never bake them
  into an image or commit them. The website only ever says whether a source is on or off.
- **Keys never reach a log.** Every log line, error and traceback is scrubbed of key values and
  anything shaped like a key. The YouTube key travels in a request header and the OpenRouter key
  in the `Authorization` header; The Odds API only accepts its key in the URL, so those URLs
  are never logged and errors are reduced to their type or HTTP status.
- **Keep `.env` private.** The app warns at startup if other users on the machine can read it.
  Fix that with `chmod 600 .env`.
- **A commit check.** `python3 scripts/check_secrets.py` scans for key shapes, for the values
  of the keys you have loaded and for a tracked `.env` file (it prints `file:line`, never a
  value). The pre-commit hook runs it on exactly what is staged (`--staged`); turn the hook on
  once per clone with `git config core.hooksPath .githooks`. The `secrets` GitHub workflow runs
  it on every push and pull request, over the files and the new commits (`--range`); a manual
  run scans the whole history (`--history`). A key that was ever pushed must be revoked at the
  provider, even after it is removed.
- **Hard spending caps.** Every paid call first reserves its worst-case cost in one shared
  ledger (`FPL_USAGE_PATH`, safe when the website and the command line run at once). If a daily
  or monthly cap (see the table above) would be passed, the call is not made and the last cached
  data is shown instead. The ledger keeps a second copy in another folder; if both copies are
  damaged or vanish mid-month, every paid call stops until you run
  `uv run fpl-advisor usage --reset-confirm`. With `NTFY_TOPIC` set you get one push alert when
  a provider reaches 80% and 100% of a cap.
- **One ledger per set of keys.** The Docker image keeps it at `/data/usage/usage.json`, so
  mount a persistent volume at `/data` (the server logs a loud warning when `/data` is not a
  mounted volume: each redeploy would otherwise restart the caps from zero). If you also run
  the CLI on another machine or folder with the same keys, point `FPL_USAGE_PATH` at the same
  file, or give it its own, smaller caps.
- **Only the scheduled refresh spends money.** Visitors' advice requests never call a paid API;
  only the server's background refresh (every 30 minutes), the `refresh` GitHub Actions job
  (Vercel hosting; the Vercel function holds no paid keys at all) and command-line runs do.
- **See the spend.** `uv run fpl-advisor usage` prints each provider's spend today and this
  month against its caps, and when each resets. It is not shown on the website.

The app's caps protect against its own bugs; they cannot stop a leaked key used elsewhere, or a
ledger that was wiped along with its volume. **The limits you set at each provider are the real
backstop**, so set them:

- **OpenRouter:** give the key its own credit limit (Settings -> Keys -> edit key -> credit
  limit), for example US$1.
- **Google Cloud:** restrict the API key to "YouTube Data API v3" only, and lower the API's
  "Queries per day" quota (APIs & Services -> YouTube Data API v3 -> Quotas) to, say, 2,000.
- **The Odds API:** the free plan is hard-capped at 500 credits a month, so it cannot run up a
  bill. Stay on it unless you need more.

## Command line and GitHub Actions (advanced)

You do not need this to use the site. It is for developers and self-hosters who want reports in a terminal, or phone alerts without running a server.

### Command line

```bash
uv run fpl-advisor --team 1234567 --league 76543 advise
```

The `--team`, `--league` and `--risk` options go before the word `advise`. Leave out `--league` if you do not want the league view. Use `--horizon N` after `advise` to plan over a different number of gameweeks (default 5).

Instead of options, copy the example config. `fpl-advisor.toml` is gitignored, so your IDs stay private.

```bash
cp fpl-advisor.example.toml fpl-advisor.toml
uv run fpl-advisor advise
```

The tool also reads `~/.config/fpl-advisor/config.toml`, or any file you pass with `--config`.

These environment variables override the config file. Command-line options override both.

| Variable | Meaning |
| --- | --- |
| `FPL_TEAM_ID` | Your team ID |
| `FPL_LEAGUE_ID` | Your mini-league ID (optional) |
| `FPL_NOTIFY` | `stdout` (print) or `ntfy` (phone push) |
| `NTFY_TOPIC` | Your secret ntfy topic |
| `NTFY_SERVER` | ntfy server (default `https://ntfy.sh`) |
| `NTFY_TOKEN` | Token, only for servers with access control |
| `FPL_REMINDER_HOURS` | Deadline reminders, e.g. `24,2` |
| `FPL_RISK` | `auto`, `low`, `medium` or `high` |
| `FPL_MAX_HITS` | Most -4 point hits the advisor may suggest (default 2) |

Preview what alerts would be sent, without sending anything:

```bash
uv run fpl-advisor check --dry-run
```

To get real alerts from your own machine, set `FPL_NOTIFY=ntfy` and `NTFY_TOPIC`, then run `uv run fpl-advisor check` every hour (for example from cron).

### Alerts with GitHub Actions

This runs on GitHub's servers for free, with no server of your own. It takes about 10 minutes.

1. **Fork this repo.** You need a free GitHub account.
2. **Install the ntfy app** on your phone.
3. **Pick a secret topic name and subscribe to it** in the app. Topics on ntfy.sh are public by name. Anyone who knows the name can read your alerts. Make it long and unguessable, such as `fpl-` followed by 20 or more random letters and numbers (for example `fpl-k3x9q7mzr2w8vb4t6yh1`). Do not use your name or team name.
4. **Add your settings to the fork.** In your fork go to Settings, then Secrets and variables, then Actions.
   - Variables tab: add `FPL_TEAM_ID` (your team ID).
   - Variables tab (optional): add `FPL_LEAGUE_ID` (your league ID).
   - Secrets tab: add `NTFY_TOPIC` (the topic name from step 3).
5. **Turn on Actions.** Forks have workflows switched off by default. Open the Actions tab of your fork and click the button to enable them. Then open the `advisor` workflow and enable it if it shows as disabled. Scheduled runs need this too.
6. **Test it.** In the Actions tab, pick `advisor` and click Run workflow.
7. **Read the report.** Open the finished run. The full report is in the run's Summary. Tapping a notification on your phone opens that page.

The workflow runs every hour and sends each alert only once. A quiet test run is normal.

Good to know:

- GitHub may start scheduled runs a few minutes late.
- GitHub pauses scheduled workflows in a repo with no activity for 60 days. If alerts stop, open the Actions tab and re-enable the workflow.
- If you self-host ntfy, add a Variable `NTFY_SERVER`. The default is `https://ntfy.sh`.

## Contributing

Contributions are welcome. Please read `AGENTS.md` first. It sets the project conventions, such as never hard-coding anyone's IDs or topic.

```bash
uv run pytest -q
uv run ruff check .
```

If you change the model, run `uv run python scripts/backtest.py` before and after, and include both sets of numbers in your pull request. The design goals are in `docs/spec.md`.

## License

MIT. See `LICENSE`.

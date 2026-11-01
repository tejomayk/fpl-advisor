# Deploying the hosted app

There are two ways to host it:

- **Vercel + GitHub Actions (free; recommended)**: the website runs as a Vercel function and a
  scheduled GitHub Actions job does all the background work. See the next section.
- **One always-on server (Docker)**: Fly.io, Render, Railway or your own machine. Needed if you want
  the phone-alert sign-up (`FPL_ALERTS=1`), which has its own scheduler and SQLite file. See
  "Run locally with Docker" and the host sections further down.

## Vercel + GitHub Actions (free)

How it fits together:

| Part | Where | What it does |
|---|---|---|
| Website | Vercel (Hobby), `index.py` + `vercel.json` | Serves the page from Vercel's CDN and `/api/advice`, `/api/health` from one Python function in Washington, D.C. (`iad1`; change `regions` in `vercel.json` to move it). Holds **no paid API keys** (it refuses to start if one is set) and never makes a paid call. Reads the web snapshot over HTTPS; holds no token for anything. |
| Web snapshot | GitHub release `snapshot` (a prerelease), asset `snapshot.json.gz` | FPL data + signals, exactly what the site shows. Public, like the site. Written by `refresh.yml`, read by the website. |
| Job state | GitHub Actions cache (`fpl-state-*` entries), the `data/` folder | Signal caches, elite list, the **spend ledger** and the last snapshot. Private: caches are not downloadable by the public, even in a public repo. |
| Refresh | GitHub Actions `refresh.yml`, every 30 min | `fpl-advisor refresh`: loads FPL data, refreshes odds / news / elite picks / creator room with every spend cap enforced, writes the snapshot, publishes it when it changed. The **only** place paid calls happen. |
| Elite scan | GitHub Actions `elite.yml`, monthly | `fpl-advisor discover-elite`: the ~2 hour scan for consistently good managers. Free (public FPL data only). |

How the website finds the snapshot: `FPL_SNAPSHOT_URL` if you set it; otherwise
`https://github.com/<owner>/<repo>/releases/download/snapshot/snapshot.json.gz`, built from the
`VERCEL_GIT_REPO_OWNER` and `VERCEL_GIT_REPO_SLUG` variables Vercel sets for a project imported
from GitHub (available at build and run time while "Enable access to System Environment
Variables" is ticked under the project's Environment Variables settings, the default). With neither, it loads FPL data live on every cold start.

The website keeps each snapshot in memory for 5 minutes per warm instance. If the snapshot is
missing, unreachable, older than 6 hours, or from before the latest deadline, it loads FPL data
live instead (about 2-3 seconds), so it keeps working when the job is late or broken.

### Set it up

1. **Vercel account.** Sign up at vercel.com with your GitHub account (the Hobby plan is free).
2. **Import the repo.** Add New -> Project -> pick your fork. Vercel detects the FastAPI app in
   `index.py`; leave the build settings alone (`vercel.json` sets the build command, which copies
   `src/fpl_advisor/static/` into `public/`, the region and the function's 60 s limit). Deploy.
   No storage to create and no Vercel environment variables to add. Do **not** add
   `ODDS_API_KEY`, `OPENROUTER_API_KEY` or `YOUTUBE_API_KEY` to Vercel: they belong to the GitHub
   job only. Optional: `FPL_SNAPSHOT_URL` if the snapshot lives somewhere other than this repo's
   `snapshot` release (for example the project was deployed with the CLI rather than from Git).
3. **GitHub secrets** (repo -> Settings -> Secrets and variables -> Actions -> New repository
   secret), all optional; the jobs run without any of them on free FPL data alone:
   - `ODDS_API_KEY`, `OPENROUTER_API_KEY`, `YOUTUBE_API_KEY`: each turns one source on.
   - `NTFY_TOPIC`: push alerts at 80% and 100% of a spending cap.
   - Leave `FPL_NEWS_FEEDS` unset: BBC (and other feeds') terms rarely allow a public site to
     republish them, so team news stays off.
   - Optional variable (not secret) `ELITE_POOL_SIZE` (default 50000).
4. **Enable the workflows.** Forks start with Actions off: Actions tab -> "I understand my
   workflows, go ahead and enable them". Check `refresh` and `elite` are listed as enabled. The
   workflows ask for their own permissions (`refresh`'s publish job needs `contents: write`), so
   the repository's default `GITHUB_TOKEN` permission can stay read-only.
5. **First runs, by hand, from the default branch.** Actions -> `elite` -> Run workflow (tick
   "force"); it takes about 2 hours and holds the refresh queue while it runs. Then Actions ->
   `refresh` -> Run workflow (tick "force_snapshot"). The first refresh creates the `snapshot`
   prerelease. After that the schedule takes over.
6. **Rate-limit rule.** Project -> Firewall -> Configure -> New Rule: "If Request Path starts with
   `/api/advice`, Rate Limit 20 requests per 60 s per IP, then Deny". Hobby allows one rate-limit
   rule (and three custom rules); blocked requests are free. The app's own limiter (20 a minute
   per IP) only counts within one warm instance, so this rule is the real control.

Moving from an older setup that used Vercel Blob: nothing reads Blob any more. Delete the
`BLOB_READ_WRITE_TOKEN` GitHub secret, and the Blob store itself in the Vercel dashboard
(Storage -> the store -> Settings -> Delete) once the first `refresh` run has published a
snapshot. The old spend ledger lived in Blob, so the new one starts from zero (see "If the job
state is lost" below).

### Check it works

- `curl https://<your-app>.vercel.app/api/health` gives `{"ok":true,"world_loaded":...,"mode":"serverless"}`.
- `curl -I https://<your-app>.vercel.app/static/icon.svg` shows `content-security-policy` and
  `x-content-type-options: nosniff` (headers come from `vercel.json` and apply to static files too).
- `curl "https://<your-app>.vercel.app/api/advice?team=<your team id>"` returns JSON. Its
  `sources` field lists the signals in use once the refresh job has run.
- `curl -sIL https://github.com/<owner>/<repo>/releases/download/snapshot/snapshot.json.gz`
  ends in `200`.
- The `refresh` run's summary page says "Web snapshot written" (or "unchanged") and prints the
  spend against each cap.

### How the job state is kept

- Each run restores `data/` from the newest `fpl-state-*` cache entry and, at the end, saves it
  under a new key `fpl-state-<run id>-<attempt>`. It saves even when a step failed part-way (the
  ledger then holds whatever that run spent), but only when the folder changed and its spend
  ledger is readable and has not gone backwards (`scripts/job_state.py check`). A run that broke
  the ledger therefore never replaces the last good copy.
- If no entry is restored but one exists (a failed download), the run stops before any paid
  call; the next run tries again. Only when no entry exists at all does a run start from empty.
- The snapshot is uploaded only when it differs from the published asset (GitHub reports each
  asset's sha256), by a separate `publish` job: the only job with write access, which runs no
  project code and holds no API key.
- Both workflows run only on the default branch: a cache saved on another branch would be a
  second, separate ledger.

### If the job state is lost

GitHub deletes a cache entry not used for 7 days (the 30-minute refresh keeps it in use; pausing
the workflows for a week loses it) and when a repository goes over 10 GB of caches. You can also
delete entries by hand (Actions -> Caches). Then the next run starts from an empty folder: the
signal caches refill on their own and the elite list is rebuilt by the next `elite` run (run it
by hand to get it back sooner), but **the spend ledger restarts from zero**, so this month's and
today's spend already made is no longer counted against the caps. The provider-side limits in the
README (a monthly limit on the OpenRouter key, a quota on the YouTube key, The Odds API's plan
quota) are the backstop, which is why they matter. After a loss, check each provider's dashboard.

### Costs and limits

- **Cost: nothing**, inside the free allowances: Vercel Hobby's included function usage (ample
  for a personal or mini-league site; check the Usage tab), and GitHub Actions (minutes, cache
  and release storage are free for public repos; a private repo would need 1,500-3,000 of its
  2,000 free minutes a month at one to two minutes a run, so keep the repo public or refresh less
  often). Paid APIs are capped by the ledger as before.
- **Why not Vercel Cron?** Hobby cron jobs run at most once a day, and a 30-minute refresh plus
  a 2-hour scan don't fit a 300 s function anyway. GitHub Actions does the scheduled work.
- **Snapshot uploads.** The snapshot is rewritten only when something that matters for advice
  changed (prices, news, fixtures, results, signals) or every 3 hours, and uploaded only when it
  differs from the published one. During the few seconds of an upload (`--clobber` replaces the
  asset) a cold start may find no snapshot and load FPL live for that instance.
- **Non-commercial only.** Vercel Hobby is for personal, non-commercial use. Ads, paid tiers or
  running it for a business need the Pro plan.
- **Schedules are best effort.** GitHub may start a scheduled run 5-30 minutes late (more at the
  top of the hour) or skip one when busy; the website then serves the previous snapshot or
  loads FPL live. GitHub turns schedules off after 60 days without repository activity in a
  public repo; `refresh.yml` has a `keepalive` job that re-enables both workflows once a day
  through the API (no commits).
- **Cold starts.** A new function instance takes about 1.5 s to import plus one snapshot
  download; the first advice for a team then takes about 1-2 s (live FPL loads add 2-3 s).
- **Single writer.** `refresh` and `elite` share one concurrency group that never cancels a
  running job, so only one job works on the state at a time. While the monthly elite scan runs
  (about 2 hours), refreshes wait. A `fpl-advisor refresh` on your own machine uses your own
  local `data/` and ledger, not the workflow's.
- **Resetting the ledger.** Run `refresh` by hand with "usage_reset_confirm" ticked (only after
  checking each provider's dashboard).
- **Phone alerts** (`FPL_ALERTS`) are not available here: they need the always-on server.

### Try it locally the Vercel way

```sh
# 1. Write a snapshot (keys blank, so no paid calls)
ODDS_API_KEY= OPENROUTER_API_KEY= YOUTUBE_API_KEY= FPL_DATA_DIR=/tmp/fpl-data uv run fpl-advisor refresh
# 2a. Serve it in serverless mode from local files ...
FPL_SERVERLESS=1 FPL_DATA_DIR=/tmp/fpl-data uv run uvicorn --app-dir . index:app --port 8000
# 2b. ... or over HTTP, as on Vercel
python3 -m http.server 8001 --directory /tmp/fpl-data/web &
FPL_SERVERLESS=1 FPL_SNAPSHOT_URL=http://localhost:8001/snapshot.json.gz uv run uvicorn --app-dir . index:app --port 8000
curl "localhost:8000/api/advice?team=<team id>"
```

The web app (`fpl-advisor-web`) serves the page, the advice API and the alert sign-up API. It also runs the hourly alert scheduler inside the same process.

## Run locally with Docker

```sh
docker build -t fpl-advisor .
docker run --rm -p 8000:8000 -v fpl-data:/data fpl-advisor
curl http://localhost:8000/api/health
```

Open http://localhost:8000. Without Docker: `uv run fpl-advisor-web`.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | Port the server listens on. Most hosts set this for you. |
| `FPL_DATA_DIR` | `data` (`/data` in the image) | Holds the FPL cache and `alerts.db` (subscriptions). Must be on a persistent volume. |
| `PUBLIC_URL` | unset | Public address of your site, e.g. `https://fpl.example.com`. Alerts then link back to your advice page. |
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy server that delivers the push notifications. |
| `NTFY_TOKEN` | unset | Access token for your ntfy server (sent as a Bearer token on every notification). Needed for ntfy Pro or a self-hosted server with auth. |
| `FORWARDED_ALLOW_IPS` | unset | Which proxies uvicorn trusts for the visitor's real IP. See below. |
| `MAX_SUBSCRIPTIONS` | `300` | Stop accepting sign-ups beyond this many active subscriptions. Each team ID can have at most 3. |
| `ALERT_RUN_MINUTE` | `7` | The scheduler runs at this minute of every hour (wall clock), plus once about a minute after start. |
| `DISABLE_ALERT_SCHEDULER` | unset | Set to any value to turn the scheduler off (sign-up still works). |

API keys (`ODDS_API_KEY`, `OPENROUTER_API_KEY`, `YOUTUBE_API_KEY`) and their spending caps are
listed in the README. Set keys as your host's secrets (for example `fly secrets set
ODDS_API_KEY=...`), never in `fly.toml`, a Dockerfile or the image: `.env` is excluded from the
Docker build on purpose. See "Keeping keys safe and costs capped" in the README.

## Client IP and `FORWARDED_ALLOW_IPS`

Sign-ups are rate limited per visitor IP. The app never reads `X-Forwarded-For` itself; uvicorn replaces the client address only for proxies you list in `FORWARDED_ALLOW_IPS`.

- On Fly.io, Render and Railway your app sits behind the platform's own proxy. Set `FORWARDED_ALLOW_IPS=*`. Without it every visitor looks like the proxy, and they share one rate limit.
- If the app is exposed directly to the internet, leave it unset. Setting `*` there would let visitors fake their IP and skip the limit.
- Behind your own proxy (nginx, Caddy), set it to that proxy's address.

## ntfy limits

Notifications go through ntfy. The free `ntfy.sh` limits requests per sending IP (your server's IP), so all your users share one quota. That is fine for a few hundred users. Beyond that, use ntfy Pro (set `NTFY_TOKEN`) or self-host ntfy (set `NTFY_SERVER`, and `NTFY_TOKEN` if you enable auth).

If ntfy answers HTTP 429, the scheduler stops sending for the rest of that cycle and retries the same alerts in the next one. ntfy errors never count against a subscriber.

## How the scheduler behaves

- It runs at minute `:07` of every hour (change with `ALERT_RUN_MINUTE`) and logs the cycle duration and counts.
- It only does the full (slow) advice build for a subscriber when a reminder window is due and unsent, on their first check, or when the last full build was 6 or more hours ago (to catch price changes and news). Subscribers with the same team and league share one build.
- **Season end:** when FPL has no upcoming gameweek, all subscriptions are deactivated with reason `season_ended`, because FPL reassigns team IDs every season. Users sign up again next season. Inactive subscriptions are deleted after 30 days and do not count toward `MAX_SUBSCRIPTIONS`.
- A subscription is also deactivated after 48 hourly failures that are specific to it (for example, the team no longer exists).

## Run exactly one instance

The scheduler runs inside the web process and subscriptions live in a SQLite file. Two instances would each send every alert, and would fight over the file. So:

- Keep the instance count at 1. Do not enable autoscaling.
- Mount a persistent volume or disk at `/data`. Without it, subscriptions are lost on every deploy.
- Do not run several uvicorn workers.

## Volume permissions

Many hosts mount volumes owned by root. The image starts as root only to create `$FPL_DATA_DIR` and `chown` it to the app user (uid 10001), then drops privileges with `setpriv` (see `docker-entrypoint.sh`). If you run the image as a non-root user (for example `docker run --user`), the entrypoint does not touch the volume, so it must already be writable by that user. If the data directory is not writable, the app logs an error at startup saying so.

## Fly.io

1. `fly launch --no-deploy` (accept the Dockerfile; set the internal port to 8000).
2. `fly volumes create fpl_data --size 1` and add to `fly.toml`: `[mounts] source = "fpl_data"  destination = "/data"`.
3. Keep one machine: `fly scale count 1`. Turn off auto-stop (`auto_stop_machines = false`, `min_machines_running = 1`), or the scheduler sleeps.
4. `fly secrets set PUBLIC_URL=https://<your-app>.fly.dev` then `fly deploy`.
5. Set `FORWARDED_ALLOW_IPS = "*"` under `[env]` in `fly.toml`.
6. Volumes are root-owned on Fly. The entrypoint fixes this, so no extra step is needed.

## Render

1. New Web Service from your repo, runtime Docker. Choose a paid instance type (disks are not available on free instances, and free ones sleep).
2. Add a Disk with mount path `/data`.
3. Set `PUBLIC_URL`. Leave the instance count at 1.
4. Set the health check path to `/api/health`.
5. Set `FORWARDED_ALLOW_IPS=*`. Render disks are root-owned; the entrypoint fixes this.

## Railway

1. New project from your repo; Railway builds the Dockerfile.
2. Add a Volume and mount it at `/data`.
3. Set `PUBLIC_URL`, `FORWARDED_ALLOW_IPS=*`, and generate a public domain. Keep replicas at 1.
4. Railway volumes are root-owned by default. The entrypoint fixes this.

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
| Website | Vercel (Hobby), `index.py` + `vercel.json` | Serves the page from Vercel's CDN and `/api/advice`, `/api/health` from one Python function in Washington, D.C. (`iad1`; change `regions` in `vercel.json` to move it). Holds **no paid API keys** (it refuses to start if one is set) and never makes a paid call. |
| Data store | Vercel Blob (private store) | `web/snapshot.json.gz` (FPL data + signals for the website) and `job/state.tar.gz` (signal caches, elite list, spend ledger). |
| Refresh | GitHub Actions `refresh.yml`, every 30 min | `fpl-advisor refresh`: loads FPL data, refreshes odds / news / elite picks / creator room with every spend cap enforced, saves the snapshot. The **only** place paid calls happen. |
| Elite scan | GitHub Actions `elite.yml`, monthly | `fpl-advisor discover-elite`: the ~2 hour scan for consistently good managers. Free (public FPL data only). |

The website keeps each snapshot in memory for 5 minutes per warm instance. If the snapshot is
missing, older than 6 hours, or from before the latest deadline, it loads FPL data live instead
(about 2-3 seconds), so it keeps working when the job is late or broken.

### Set it up

1. **Vercel account.** Sign up at vercel.com with your GitHub account (the Hobby plan is free).
2. **Import the repo.** Add New -> Project -> pick your fork. Vercel detects the FastAPI app in
   `index.py`; leave the build settings alone (`vercel.json` sets the build command, which copies
   `src/fpl_advisor/static/` into `public/`, the region and the function's 60 s limit). Deploy.
3. **Create a Blob store.** Blob stores live at the account level, not inside the project:
   open your dashboard (vercel.com/dashboard), pick the **Storage** tab in the top bar ->
   Create -> Blob. Choose **Private** access and the same region as the function (`regions` in
   `vercel.json`, Washington D.C. / `iad1` by default); neither can be changed later. Then open
   the store's **Projects** tab -> Connect to Project -> pick this project, all environments.
   Vercel adds `BLOB_STORE_ID` (and `BLOB_WEBHOOK_PUBLIC_KEY`) to the project. That is all the
   website needs: it signs each Blob read with the short-lived OIDC token Vercel gives every
   request, so no long-lived token sits in Vercel. Redeploy once (Deployments -> ... ->
   Redeploy) so the function sees `BLOB_STORE_ID`. CLI alternative, from a linked checkout:
   `vercel blob create-store fpl-advisor --access private --region iad1 --yes`.
4. **Vercel environment variables.** Nothing else is needed. Do **not** add `ODDS_API_KEY`,
   `OPENROUTER_API_KEY` or `YOUTUBE_API_KEY` to Vercel: they belong to the GitHub job only.
5. **GitHub secrets** (repo -> Settings -> Secrets and variables -> Actions -> New repository
   secret):
   - `BLOB_READ_WRITE_TOKEN`: required; the jobs skip themselves until it is set. GitHub runs
     outside Vercel, so it needs the store's long-lived read-write token, which the dashboard no
     longer shows. Fetch it straight into GitHub with the script (it never prints the token):
     create a Vercel access token with a 1-day expiry (vercel.com/account/settings/tokens), then
     from this repo with `gh` signed in run
     `scripts/set_blob_secret.sh <store_id>` (the store id is on the store's Settings page),
     paste the access token when asked, and delete the access token afterwards.
   - `ODDS_API_KEY`, `OPENROUTER_API_KEY`, `YOUTUBE_API_KEY`: optional, each turns one source on.
   - `NTFY_TOPIC`: optional, push alerts at 80% and 100% of a spending cap.
   - Leave `FPL_NEWS_FEEDS` unset: BBC (and other feeds') terms rarely allow a public site to
     republish them, so team news stays off.
   - Optional variable (not secret) `ELITE_POOL_SIZE` (default 50000).
6. **Enable the workflows.** Forks start with Actions off: Actions tab -> "I understand my
   workflows, go ahead and enable them". Check `refresh` and `elite` are listed as enabled.
7. **First runs, by hand.** Actions -> `elite` -> Run workflow (tick "force"); it takes about 2
   hours and holds the refresh queue while it runs. Then Actions -> `refresh` -> Run workflow
   (tick "force_snapshot"). After that the schedule takes over.
8. **Rate-limit rule.** Project -> Firewall -> Configure -> New Rule: "If Request Path starts with
   `/api/advice`, Rate Limit 20 requests per 60 s per IP, then Deny". Hobby allows one rate-limit
   rule (and three custom rules); blocked requests are free. The app's own limiter (20 a minute
   per IP) only counts within one warm instance, so this rule is the real control.

### Check it works

- `curl https://<your-app>.vercel.app/api/health` gives `{"ok":true,"world_loaded":...,"mode":"serverless"}`.
- `curl -I https://<your-app>.vercel.app/static/app.js` shows `content-security-policy` and
  `x-content-type-options: nosniff` (headers come from `vercel.json` and apply to static files too).
- `curl "https://<your-app>.vercel.app/api/advice?team=<your team id>"` returns JSON. Its
  `sources` field lists the signals in use once the refresh job has run.
- The `refresh` run's summary page says "Web snapshot written" (or "unchanged") and prints the
  spend against each cap. `fpl-advisor usage` run with the Blob token set prints the same.

### Costs and limits

- **Cost: nothing**, inside the free allowances: Vercel Hobby's included function usage (ample
  for a personal or mini-league site; check the Usage tab), Vercel Blob Hobby (1 GB storage,
  10,000 reads and **2,000 uploads** a month, 10 GB transfer), and GitHub Actions (unlimited for
  public repos; a private repo would need 1,500-3,000 of its 2,000 free minutes a month at one
  to two minutes a run, so keep the repo public or refresh less often). Paid APIs are capped by
  the ledger as before.
- **Why not Vercel Cron?** Hobby cron jobs run at most once a day, and a 30-minute refresh plus
  a 2-hour scan don't fit a 300 s function anyway. GitHub Actions does the scheduled work.
- **Blob uploads are the tight one.** Exceeding a Hobby Blob limit blocks the store for the rest
  of the 30 days (no bill). So the job never lists blobs, uploads the job state only when a byte
  changed, and rewrites the snapshot only when something that matters for advice changed
  (prices, news, fixtures, results, signals) or every 3 hours. Expect roughly 300-900 uploads a
  month. Browsing the Blob dashboard also counts.
- **Non-commercial only.** Vercel Hobby is for personal, non-commercial use. Ads, paid tiers or
  running it for a business need the Pro plan.
- **Schedules are best effort.** GitHub may start a scheduled run 5-30 minutes late (more at the
  top of the hour) or skip one when busy; the website then serves the previous snapshot or
  loads FPL live. GitHub turns schedules off after 60 days without repository activity in a
  public repo; `refresh.yml` has a `keepalive` job that re-enables both workflows once a day
  through the API (no commits).
- **Cold starts.** A new function instance takes about 1.5 s to import plus one snapshot read;
  the first advice for a team then takes about 1-2 s (live FPL loads add 2-3 s).
- **Single writer.** Blob has no locks. `refresh` and `elite` share one concurrency group that
  never cancels a running job, so only one job writes at a time; the job state is also written
  with an ETag check, so an unexpected second writer fails loudly instead of overwriting the
  ledger. Don't run `fpl-advisor refresh` with the Blob token on your own machine while the
  workflows are on. While the monthly elite scan runs (about 2 hours), refreshes wait.
- **Ledger safety.** A run that cannot read the store makes no paid call. If uploading the ledger
  fails, the run fails and (with `NTFY_TOPIC`) alerts you; that run's spend could then be counted
  again from the older copy at most once, which is why the provider-side limits in the README
  still matter. To reset the ledger, run `refresh` by hand with "usage_reset_confirm" ticked
  (only after checking each provider's dashboard).
- **Phone alerts** (`FPL_ALERTS`) are not available here: they need the always-on server.

### Try it locally the Vercel way

```sh
# 1. Fill a local store (keys blank, so no paid calls; omit BLOB_READ_WRITE_TOKEN = local files)
ODDS_API_KEY= OPENROUTER_API_KEY= YOUTUBE_API_KEY= FPL_DATA_DIR=/tmp/fpl-data uv run fpl-advisor refresh
# 2. Serve it in serverless mode
FPL_SERVERLESS=1 FPL_DATA_DIR=/tmp/fpl-data uv run uvicorn --app-dir . index:app --port 8000
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

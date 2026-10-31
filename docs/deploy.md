# Deploying the hosted app

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

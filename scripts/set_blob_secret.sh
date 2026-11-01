#!/usr/bin/env bash
# Copy a Vercel Blob store's read-write token into this repo's GitHub Actions secrets,
# without ever printing it.
#
# Vercel no longer shows this token in the dashboard (connected projects use OIDC), but the
# store still has one; Vercel's own Terraform provider reads it from
# GET /v1/storage/stores/{id}/secrets. This script does the same with a Vercel access token you
# paste in (create one with a 1-day expiry at vercel.com/account/settings/tokens and delete it
# afterwards), then hands the token straight to `gh secret set`.
#
# Usage: scripts/set_blob_secret.sh <store_id> [team_id]
#   store_id  from the store's Settings page, e.g. store_AbC123 (not a secret)
#   team_id   only if Vercel answers 403/404 without it (Team Settings -> General -> Team ID)
set -euo pipefail

store_id="${1:?usage: $0 <store_id> [team_id]}"
url="https://api.vercel.com/v1/storage/stores/${store_id}/secrets"
[[ -n "${2:-}" ]] && url="${url}?teamId=$2"

command -v gh >/dev/null || { echo "Needs the GitHub CLI (gh), signed in." >&2; exit 1; }

read -rsp "Vercel access token (input hidden): " vercel_token
echo
[[ -n "$vercel_token" ]] || { echo "No token given." >&2; exit 1; }

# The access token goes to curl on stdin so it never shows in the process list.
body="$(printf 'Authorization: Bearer %s\n' "$vercel_token" |
  curl -fsS --max-time 30 -H @- "$url")" || {
  unset vercel_token
  echo "Vercel refused the request. Check the store id, the token, or pass your team id." >&2
  exit 1
}
unset vercel_token

rw="$(printf '%s' "$body" | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("rwToken",""))')"
unset body
[[ "$rw" == vercel_blob_rw_* ]] || { echo "Vercel's answer had no read-write token." >&2; exit 1; }

printf '%s' "$rw" | gh secret set BLOB_READ_WRITE_TOKEN
unset rw
echo "Saved BLOB_READ_WRITE_TOKEN as a GitHub Actions secret. You can now delete the Vercel access token."

#!/usr/bin/env bash
# Fresh-smoke and certify the currently active Guardian Beta release.
set -euo pipefail

BASE_DIR=/home/forge/echo-guardian-beta
CURRENT_LINK=$BASE_DIR/current
PROD_PORT=8462
TOKEN_FILE=/etc/echo/credentials/echo-guardian-beta/write-token

if [ "$(id -u)" -ne 0 ]; then
  echo "finalize_guardian_beta.sh must run as root" >&2
  exit 2
fi

exec 9>/run/lock/echo-guardian-beta-deploy.lock
if ! flock -n 9; then
  echo "another Guardian Beta deploy or finalization holds the release lock" >&2
  exit 2
fi

ACTIVE_RELEASE="$(readlink -f "$CURRENT_LINK")"
case "$ACTIVE_RELEASE" in
  "$BASE_DIR"/releases/*) ;;
  *)
    echo "active release is missing or outside the immutable release tree" >&2
    exit 3
    ;;
esac
if [ ! -f "$ACTIVE_RELEASE/app.py" ] || [ ! -s "$TOKEN_FILE" ]; then
  echo "active release or dedicated credential is unavailable" >&2
  exit 3
fi
if ! systemctl is-active --quiet echo-guardian-beta.service; then
  echo "Guardian Beta systemd unit is not active" >&2
  exit 3
fi

curl -fsS --max-time 5 "http://127.0.0.1:$PROD_PORT/health" >/dev/null
python3 "$ACTIVE_RELEASE/smoke_live.py" \
  --base "http://127.0.0.1:$PROD_PORT" \
  --token-file "$TOKEN_FILE"

sudo -u postgres psql -v ON_ERROR_STOP=1 -d echo \
  -v active_release="$ACTIVE_RELEASE" \
  < "$ACTIVE_RELEASE/finalize_migration.sql" >/dev/null

echo "Guardian Beta active-release attestation and migration finalization are green"

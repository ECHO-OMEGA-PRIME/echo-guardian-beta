#!/usr/bin/env bash
# Staging-first, atomic-release deploy gate for Echo Guardian Beta.
set -euo pipefail

SRC_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BASE_DIR=/home/forge/echo-guardian-beta
RELEASES_DIR="$BASE_DIR/releases"
CURRENT_LINK="$BASE_DIR/current"
UNIT=echo-guardian-beta.service
PROD_PORT=8462
STAGING_PORT=8463
RUN_USER=echo-guardian-beta
DB_ROLE=echo-guardian-beta
D1_SQLITE=/mnt/cf_d1/d1_databases/echo-guardian-beta/echo-guardian-beta.sqlite3
NORMALIZED_SOURCE=/home/forge/cf-migration-recovered-guardian-20260802/echo-guardian-beta/source/index.js
TOKEN_DIR=/etc/echo/credentials/echo-guardian-beta
TOKEN_FILE=$TOKEN_DIR/write-token
TEST_PYTHON="${GUARDIAN_TEST_PYTHON:-/home/forge/echo-worker-server/venv/bin/python}"
RELEASE_ID="$(date -u +%Y%m%dT%H%M%S%NZ)-$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo source)"
RELEASE_DIR="$RELEASES_DIR/$RELEASE_ID"
STAGING_PID=""
OLD_TARGET=""
UNIT_BACKUP_DIR="$BASE_DIR/unit-backups/$RELEASE_ID"
EXPECTED_RESCUED_SHA=""
EXPECTED_NORMALIZED_SHA=""
EXPECTED_REPOSITORY_SHA=""
UNIT_FILES=(
  echo-guardian-beta.service
  echo-guardian-beta-job@.service
  echo-guardian-beta-health.timer
  echo-guardian-beta-enhance.timer
  echo-guardian-beta-audit.timer
  echo-guardian-beta-report.timer
)

log() { printf '[guardian-deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
cleanup() {
  if [ -n "$STAGING_PID" ]; then
    kill "$STAGING_PID" 2>/dev/null || true
    wait "$STAGING_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

wait_for_health() {
  local port="$1"
  for _ in $(seq 1 30); do
    curl -fsS --max-time 3 "http://127.0.0.1:$port/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

record_receipt() {
  local event_name="$1"
  local active_release="${2:-$RELEASE_DIR}"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d echo \
    -v candidate_release="$RELEASE_DIR" \
    -v active_release="$active_release" \
    -v event_name="$event_name" \
    -v rescued_sha="$EXPECTED_RESCUED_SHA" \
    -v normalized_sha="$EXPECTED_NORMALIZED_SHA" \
    -v repository_sha="$EXPECTED_REPOSITORY_SHA" >/dev/null <<'SQL'
INSERT INTO cf_echo_guardian_beta.migration_receipts
    (candidate_release, active_release, event_name,
     rescued_source_sha256, normalized_source_sha256,
     repository_source_sha256,
     service_dir, unit_name, health_state)
VALUES (:'candidate_release', :'active_release', :'event_name',
        :'rescued_sha', :'normalized_sha', :'repository_sha',
        '/home/forge/echo-guardian-beta',
        'echo-guardian-beta.service',
        CASE WHEN :'event_name' = 'provenance_verified'
             THEN 'verified' ELSE 'healthy' END)
ON CONFLICT (candidate_release, event_name) DO NOTHING;
SQL
}

backup_units() {
  mkdir -p "$UNIT_BACKUP_DIR"
  local name
  for name in "${UNIT_FILES[@]}"; do
    if [ -f "/etc/systemd/system/$name" ]; then
      cp -a "/etc/systemd/system/$name" "$UNIT_BACKUP_DIR/$name"
    else
      : > "$UNIT_BACKUP_DIR/$name.absent"
    fi
  done
}

restore_units() {
  local name
  for name in "${UNIT_FILES[@]}"; do
    if [ -f "$UNIT_BACKUP_DIR/$name.absent" ]; then
      rm -f "/etc/systemd/system/$name"
    else
      install -m 0644 "$UNIT_BACKUP_DIR/$name" "/etc/systemd/system/$name"
    fi
  done
}

rollback_release() {
  log "restoring prior release and unit files"
  if [ -n "$OLD_TARGET" ]; then
    ln -s "$OLD_TARGET" "$BASE_DIR/.rollback.$RELEASE_ID" || return 1
    mv -Tf "$BASE_DIR/.rollback.$RELEASE_ID" "$CURRENT_LINK" || return 1
    ln -sfn current/app.py "$BASE_DIR/app.py" || return 1
    restore_units || return 1
    systemctl daemon-reload || return 1
    systemctl restart "$UNIT" || return 1
    wait_for_health "$PROD_PORT" || return 1
    python3 "$CURRENT_LINK/smoke_live.py" \
      --base "http://127.0.0.1:$PROD_PORT" \
      --token-file "$TOKEN_FILE" || return 1
    record_receipt rollback_smoke "$OLD_TARGET" || return 1
  else
    for timer in health enhance audit report; do
      systemctl disable --now "echo-guardian-beta-$timer.timer" >/dev/null 2>&1 || true
    done
    systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
    rm -f "$CURRENT_LINK" "$BASE_DIR/app.py"
    restore_units || return 1
    systemctl daemon-reload || return 1
  fi
}

if [ "$(id -u)" -ne 0 ]; then
  echo "deploy_guardian_beta.sh must run as root" >&2
  exit 2
fi
exec 9>/run/lock/echo-guardian-beta-deploy.lock
if ! flock -n 9; then
  echo "another Guardian Beta deploy or finalization holds the release lock" >&2
  exit 2
fi
if [ ! -f "$SRC_DIR/app.py" ] || [ ! -f "$SRC_DIR/schema.sql" ]; then
  echo "invalid source directory" >&2
  exit 2
fi
if [ ! -x "$TEST_PYTHON" ] || ! "$TEST_PYTHON" -c "import pytest" 2>/dev/null; then
  echo "pytest runner unavailable: set GUARDIAN_TEST_PYTHON to a verified local test environment" >&2
  exit 2
fi
if ss -ltnH "sport = :$STAGING_PORT" | grep -q .; then
  echo "staging port $STAGING_PORT is already in use" >&2
  exit 2
fi
if [ ! -L "$CURRENT_LINK" ] && ss -ltnH "sport = :$PROD_PORT" | grep -q .; then
  echo "production port $PROD_PORT is occupied without an active Guardian Beta release" >&2
  exit 2
fi
if [ ! -r "$D1_SQLITE" ] || [ ! -r "$NORMALIZED_SOURCE" ]; then
  echo "required rescued evidence is unavailable" >&2
  exit 2
fi

install -d -m 0755 "$BASE_DIR" "$RELEASES_DIR"
mkdir -m 0755 "$RELEASE_DIR"
rsync -a \
  --exclude=.git --exclude=node_modules --exclude=.pytest_cache \
  --exclude=__pycache__ --exclude='*.pyc' \
  --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
  "$SRC_DIR/" "$RELEASE_DIR/"
chmod 0755 "$RELEASE_DIR"
EXPECTED_RESCUED_SHA="$(python3 -c "import json; print(json.load(open('$RELEASE_DIR/migration_contract.json', encoding='utf-8'))['provenance']['rescued_deployed_javascript_sha256'])")"
EXPECTED_NORMALIZED_SHA="$(python3 -c "import json; print(json.load(open('$RELEASE_DIR/migration_contract.json', encoding='utf-8'))['provenance']['normalized_recovered_bundle_sha256'])")"
EXPECTED_REPOSITORY_SHA="$(python3 -c "import json; print(json.load(open('$RELEASE_DIR/migration_contract.json', encoding='utf-8'))['provenance']['repository_typescript_sha256'])")"
ACTUAL_REPOSITORY_SHA="$(sha256sum "$RELEASE_DIR/src/index.ts" | awk '{print $1}')"
ACTUAL_NORMALIZED_SHA="$(sha256sum "$NORMALIZED_SOURCE" | awk '{print $1}')"
INVENTORY_RESCUED_SHA="$(sudo -u postgres psql -d echo -Atc "SELECT btrim(source_sha256) FROM inventory.cf_migration_status WHERE lower(worker_name)=lower('echo-guardian-beta')")"
if [ "$EXPECTED_RESCUED_SHA" != "5f7afb16ed7daea81022ffb0e458e369f5d425a7f82c0636f06e653d19b15f3c" ] || \
   [ "$EXPECTED_NORMALIZED_SHA" != "134eabf49017cc742c5b13bfca339c271f846bc2319b704a191509c69339e3d8" ] || \
   [ "$EXPECTED_REPOSITORY_SHA" != "b7d50e2fa3983984b56eed5c13699663b4a8221e4dcf4b98b112c9cf24ffb7e7" ] || \
   [ "$INVENTORY_RESCUED_SHA" != "$EXPECTED_RESCUED_SHA" ] || \
   [ "$ACTUAL_NORMALIZED_SHA" != "$EXPECTED_NORMALIZED_SHA" ] || \
   [ "$ACTUAL_REPOSITORY_SHA" != "$EXPECTED_REPOSITORY_SHA" ]; then
  echo "source provenance verification failed" >&2
  exit 3
fi
python3 -c "import glob,py_compile; [py_compile.compile(path,doraise=True) for path in glob.glob('$RELEASE_DIR/*.py')]"
"$TEST_PYTHON" -m pytest -q --confcutdir="$RELEASE_DIR" "$RELEASE_DIR/tests"
python3 -m venv --system-site-packages "$RELEASE_DIR/.venv"
if ! getent passwd "$RUN_USER" >/dev/null; then
  useradd --system --home-dir /nonexistent --no-create-home \
    --shell /usr/sbin/nologin --user-group "$RUN_USER"
fi
if [ "$(id -Gn "$RUN_USER")" != "$RUN_USER" ]; then
  echo "dedicated service identity has unexpected supplemental groups" >&2
  exit 3
fi
sudo -u "$RUN_USER" "$RELEASE_DIR/.venv/bin/python" -c "import fastapi,psycopg2,uvicorn; assert fastapi.__version__ == '0.136.1'; assert uvicorn.__version__ == '0.46.0'; assert psycopg2.__version__.split()[0] == '2.9.12'"
systemd-analyze verify "$RELEASE_DIR/systemd/echo-guardian-beta.service" "$RELEASE_DIR/systemd/echo-guardian-beta-job@.service" "$RELEASE_DIR/systemd/echo-guardian-beta-health.timer" "$RELEASE_DIR/systemd/echo-guardian-beta-enhance.timer" "$RELEASE_DIR/systemd/echo-guardian-beta-audit.timer" "$RELEASE_DIR/systemd/echo-guardian-beta-report.timer"
systemd-analyze calendar '*:02/5' '*:15/30' '*-*-* 03/6:00:00 UTC' '*-*-* 09:00:00 UTC' >/dev/null
log "release staged, compiled, and unit-tested: $RELEASE_ID"

if ! sudo -u postgres psql -d echo -Atc "SELECT 1 FROM pg_roles WHERE rolname='$DB_ROLE'" | grep -q 1; then
  sudo -u postgres createuser --no-createdb --no-createrole --no-superuser "$DB_ROLE"
fi
db_role_safe="$(sudo -u postgres psql -d echo -Atc \
  "SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls FROM pg_roles WHERE rolname='$DB_ROLE'")"
if [ "$db_role_safe" != "t" ]; then
  echo "dedicated database role failed privilege validation" >&2
  exit 3
fi

sudo -u postgres psql --single-transaction -v ON_ERROR_STOP=1 -d echo \
  < "$RELEASE_DIR/schema.sql" >/dev/null
timestamp_parser_ok="$(sudo -u postgres psql -d echo -Atc \
  "SELECT cf_echo_guardian_beta.safe_timestamptz('2026-08-03 01:02:03') IS NOT NULL AND cf_echo_guardian_beta.safe_timestamptz('2026-08-03T01:02:03Z') IS NOT NULL AND cf_echo_guardian_beta.safe_timestamptz('invalid') IS NULL")"
if [ "$timestamp_parser_ok" != "t" ]; then
  echo "mixed-format timestamp parser verification failed" >&2
  exit 3
fi
sudo -u postgres /usr/bin/python3 "$RELEASE_DIR/import_d1.py" \
  --sqlite "$D1_SQLITE" \
  --contract "$RELEASE_DIR/migration_contract.json" \
  --dsn dbname=echo >/dev/null
verified_counts="$(sudo -u postgres psql -d echo -At -F '|' -c \
  "SELECT 'creations',count(*) FROM cf_echo_guardian_beta.creations UNION ALL SELECT 'enhancement_queue',count(*) FROM cf_echo_guardian_beta.enhancement_queue UNION ALL SELECT 'enhancements',count(*) FROM cf_echo_guardian_beta.enhancements UNION ALL SELECT 'guardian_state',count(*) FROM cf_echo_guardian_beta.guardian_state UNION ALL SELECT 'health_checks',count(*) FROM cf_echo_guardian_beta.health_checks UNION ALL SELECT 'incidents',count(*) FROM cf_echo_guardian_beta.incidents UNION ALL SELECT 'partner_health',count(*) FROM cf_echo_guardian_beta.partner_health ORDER BY 1")"
log "additive schema and D1 import gate verified all seven source tables: $verified_counts"
record_receipt provenance_verified

STAGING_TOKEN="guardian-beta-staging-smoke"
(
  cd "$RELEASE_DIR"
  exec sudo -u "$RUN_USER" env \
    "ECHO_GUARDIAN_DATABASE_DSN=dbname=echo user=$DB_ROLE" \
    "ECHO_GUARDIAN_TARGETS_FILE=$RELEASE_DIR/config/targets.json" \
    "ECHO_GUARDIAN_WRITE_TOKEN=$STAGING_TOKEN" \
    ECHO_GUARDIAN_MAX_FANOUT=8 \
    ECHO_GUARDIAN_PROBE_TIMEOUT=3 \
    "$RELEASE_DIR/.venv/bin/python" -m uvicorn app:app --host 127.0.0.1 --port "$STAGING_PORT" --log-level warning --no-access-log
) &
STAGING_PID=$!
wait_for_health "$STAGING_PORT" || {
  log "staging failed readiness; production remains untouched"
  exit 4
}

staging_args=(--base "http://127.0.0.1:$STAGING_PORT" --token "$STAGING_TOKEN")
if [ "${GUARDIAN_FORCE_STAGING_SMOKE_FAIL:-0}" = "1" ]; then
  staging_args+=(--force-fail)
fi
if ! python3 "$RELEASE_DIR/smoke_live.py" "${staging_args[@]}"; then
  log "STAGING SMOKE RED; production remains on its prior release"
  exit 4
fi
log "staging smoke GREEN"
record_receipt staging_smoke
kill "$STAGING_PID" 2>/dev/null || true
wait "$STAGING_PID" 2>/dev/null || true
STAGING_PID=""

if [ -L "$CURRENT_LINK" ]; then
  OLD_TARGET="$(readlink -f "$CURRENT_LINK")"
fi
backup_units

promote_release() {
  install -d -o root -g root -m 0700 "$TOKEN_DIR" || return 1
  if [ -L "$TOKEN_FILE" ]; then
    echo "dedicated credential must not be a symbolic link" >&2
    return 1
  fi
  if [ ! -s "$TOKEN_FILE" ]; then
    local token_tmp="$TOKEN_DIR/.echo-guardian-beta-write-token.$RELEASE_ID"
    umask 077
    /usr/bin/python3 -c 'import secrets; print(secrets.token_hex(32))' > "$token_tmp" || return 1
    install -o root -g root -m 0400 "$token_tmp" "$TOKEN_FILE" || return 1
    rm -f "$token_tmp" || return 1
  fi
  chown root:root "$TOKEN_FILE" || return 1
  chmod 0400 "$TOKEN_FILE" || return 1
  if [ "$(stat -c '%U:%G:%a' "$TOKEN_FILE")" != "root:root:400" ]; then
    echo "dedicated credential ownership or mode validation failed" >&2
    return 1
  fi
  install -m 0644 "$RELEASE_DIR/systemd/echo-guardian-beta.service" /etc/systemd/system/echo-guardian-beta.service || return 1
  install -m 0644 "$RELEASE_DIR/systemd/echo-guardian-beta-job@.service" /etc/systemd/system/echo-guardian-beta-job@.service || return 1
  local timer
  for timer in health enhance audit report; do
    install -m 0644 "$RELEASE_DIR/systemd/echo-guardian-beta-$timer.timer" "/etc/systemd/system/echo-guardian-beta-$timer.timer" || return 1
  done
  ln -s "$RELEASE_DIR" "$BASE_DIR/.current.$RELEASE_ID" || return 1
  mv -Tf "$BASE_DIR/.current.$RELEASE_ID" "$CURRENT_LINK" || return 1
  ln -sfn current/app.py "$BASE_DIR/app.py" || return 1
  systemctl daemon-reload || return 1
  systemctl enable "$UNIT" >/dev/null || return 1
  systemctl restart "$UNIT" || return 1
  wait_for_health "$PROD_PORT" || return 1
  record_receipt production_candidate_active "$RELEASE_DIR" || return 1

  local prod_args=(--base "http://127.0.0.1:$PROD_PORT" --token-file "$TOKEN_FILE")
  if [ "${GUARDIAN_FORCE_PROD_SMOKE_FAIL:-0}" = "1" ]; then
    prod_args+=(--force-fail)
  fi
  python3 "$CURRENT_LINK/smoke_live.py" "${prod_args[@]}" || return 1
  for timer in health enhance audit report; do
    systemctl enable --now "echo-guardian-beta-$timer.timer" >/dev/null || return 1
  done
  record_receipt production_smoke "$RELEASE_DIR" || return 1
}

if ! promote_release; then
  if rollback_release; then
    log "promotion failed; rollback smoke GREEN"
    exit 5
  fi
  log "promotion and rollback both failed"
  exit 6
fi
log "PROMOTED $RELEASE_ID; production smoke GREEN; timers active"

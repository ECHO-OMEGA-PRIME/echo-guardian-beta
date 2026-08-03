import json
from pathlib import Path

import pytest

import smoke_live


ROOT = Path(__file__).resolve().parents[1]


def test_migration_contract_has_independent_identity_and_state_evidence():
    contract = json.loads((ROOT / "migration_contract.json").read_text())
    provenance = contract["provenance"]
    assert contract["worker_name"] == "echo-guardian-beta"
    assert contract["replacement"]["port"] == 8462
    assert contract["replacement"]["staging_port"] == 8463
    assert len(
        {
            provenance["rescued_deployed_javascript_sha256"],
            provenance["normalized_recovered_bundle_sha256"],
            provenance["repository_typescript_sha256"],
            provenance["d1_sqlite_sha256"],
        }
    ) == 4
    assert provenance["repository_typescript_sha256"] == (
        "08e3d47f0234986de30873c8ad53cba761446cc54e93207ca19a9160b561ba06"
    )
    assert provenance["repository_windows_worktree_sha256"] == (
        "b7d50e2fa3983984b56eed5c13699663b4a8221e4dcf4b98b112c9cf24ffb7e7"
    )
    assert "canonical Git blob bytes" in provenance["relationship"]
    assert "non-authoritative evidence" in provenance["relationship"]
    coverage = contract["non_generic_route_coverage"]
    assert (coverage["source"], coverage["implemented"], coverage["percent"]) == (
        12,
        12,
        100,
    )
    assert coverage["evidence"]
    assert contract["d1_source_counts"] == {
        "creations": 0,
        "enhancement_queue": 38,
        "enhancements": 0,
        "guardian_state": 0,
        "health_checks": 461520,
        "incidents": 0,
        "partner_health": 3202,
    }
    assert contract["d1_affinity_contract"]["health_checks"] == [
        "INTEGER",
        "TEXT",
        "TEXT",
        "INTEGER",
        "INTEGER",
        "TEXT",
        "TEXT",
        "TEXT",
    ]
    assert "dedicated" in contract["replacement"]["service_identity"]
    assert "echo-guardian-alpha" in contract["bindings"]["SVC_PARTNER"]


def test_timer_contract_matches_rescued_crons():
    expected = {
        "health": "OnCalendar=*:02/5",
        "enhance": "OnCalendar=*:15/30",
        "audit": "OnCalendar=*-*-* 03/6:00:00 UTC",
        "report": "OnCalendar=*-*-* 09:00:00 UTC",
    }
    for name, calendar in expected.items():
        text = (ROOT / "systemd" / f"echo-guardian-beta-{name}.timer").read_text()
        assert calendar in text
        assert f"Unit=echo-guardian-beta-job@{name}.service" in text
        assert "Persistent=true" in text


def test_service_uses_peer_auth_and_systemd_credentials():
    text = (ROOT / "systemd" / "echo-guardian-beta.service").read_text()
    job_text = (ROOT / "systemd" / "echo-guardian-beta-job@.service").read_text()
    assert "User=echo-guardian-beta" in text
    assert "User=echo-guardian-beta" in job_text
    assert "user=echo-guardian-beta" in text
    assert "user=echo-guardian-beta" in job_text
    assert (
        "LoadCredential=guardian_write_token:"
        "/etc/echo/credentials/echo-guardian-beta/write-token" in text
    )
    assert ".echo_sovereign_key" not in text
    assert "current/.venv/bin/python" in text
    assert "--host 127.0.0.1 --port 8462" in text
    assert "NoNewPrivileges=true" in text
    assert "ProtectHome=tmpfs" in text and "BindReadOnlyPaths=" in text
    assert "ProtectHome=tmpfs" in job_text and "BindReadOnlyPaths=" in job_text
    expected_bind = (
        "BindReadOnlyPaths=/home/forge/echo-guardian-beta:"
        "/opt/echo-guardian-beta-runtime"
    )
    assert expected_bind in text and expected_bind in job_text
    assert "WorkingDirectory=/opt/echo-guardian-beta-runtime/current" in text
    assert "WorkingDirectory=/opt/echo-guardian-beta-runtime/current" in job_text
    assert "LoadCredential=" not in job_text


def test_runtime_dependency_contract_matches_forge_preflight():
    requirements = (ROOT / "requirements.txt").read_text()
    deploy = (ROOT / "deploy_guardian_beta.sh").read_text()
    for requirement in (
        "fastapi==0.136.1",
        "psycopg2-binary==2.9.12",
        "uvicorn==0.46.0",
    ):
        assert requirement in requirements
    assert "fastapi.__version__ == '0.136.1'" in deploy
    assert "psycopg2.__version__.split()[0] == '2.9.12'" in deploy
    assert "uvicorn.__version__ == '0.46.0'" in deploy


def test_deploy_gate_contains_staging_and_rollback():
    text = (ROOT / "deploy_guardian_beta.sh").read_text()
    assert "STAGING_PORT=8463" in text
    assert "staging smoke GREEN" in text
    assert "restoring prior release" in text
    assert "mv -Tf" in text
    assert "GUARDIAN_FORCE_STAGING_SMOKE_FAIL" in text
    assert "GUARDIAN_FORCE_PROD_SMOKE_FAIL" in text
    assert "GUARDIAN_TEST_PYTHON" in text
    assert "echo-guardian-beta-deploy.lock" in text
    assert "TOKEN_DIR=/etc/echo/credentials/echo-guardian-beta" in text
    assert 'useradd --system --home-dir /nonexistent' in text
    assert 'id -Gn "$RUN_USER"' in text
    assert "NOT rolsuper" in text and "NOT rolbypassrls" in text
    assert '[ -L "$TOKEN_FILE" ]' in text
    assert "root:root:400" in text
    assert '"$TEST_PYTHON" -m pytest' in text
    assert 'systemd-run --quiet --wait --pipe --unit="$PREFLIGHT_UNIT"' in text
    assert '/usr/bin/env "$STAGING_MOUNT/.venv/bin/python" -c' in text
    assert "--only-binary=:all:" in text
    assert '--requirement "$RELEASE_DIR/requirements.txt"' in text
    assert 'install -d -m 0755 "$BASE_DIR" "$RELEASES_DIR"' in text
    assert 'mkdir -m 0755 "$RELEASE_DIR"' in text
    assert 'psql --single-transaction -v ON_ERROR_STOP=1 -d echo' in text
    assert 'systemd-run --quiet --wait --pipe --unit="$IMPORT_UNIT"' in text
    assert '"$IMPORT_MOUNT/import_d1.py"' in text
    assert '--property="BindReadOnlyPaths=$RELEASE_DIR:$IMPORT_MOUNT"' in text
    assert "NORMALIZED_SOURCE=" in text
    assert "INVENTORY_RESCUED_SHA=" in text
    assert 'date -u +%Y%m%dT%H%M%S%NZ' in text
    assert "SELECT 'creations',count(*)" in text
    assert "SELECT 'partner_health',count(*)" in text
    assert "mixed-format timestamp parser verification failed" in text
    assert "-v normalized_sha=\"$EXPECTED_NORMALIZED_SHA\"" in text
    assert "-v repository_sha=\"$EXPECTED_REPOSITORY_SHA\" >/dev/null <<'SQL'" in text
    assert 'ln -sfn current/app.py "$BASE_DIR/app.py"' in text
    assert "backup_units" in text and "restore_units" in text
    assert '--property="BindReadOnlyPaths=$RELEASE_DIR:$STAGING_MOUNT"' in text
    assert 'STAGING_UNIT="echo-guardian-beta-staging-$RELEASE_ID"' in text
    assert 'python3 "$CURRENT_LINK/smoke_live.py"' in text
    assert 'record_receipt production_candidate_active "$RELEASE_DIR"' in text
    assert "systemd-analyze verify" in text


def test_preserved_fleet_manifest_is_complete_and_unique():
    manifest = json.loads((ROOT / "fleet_manifest.json").read_text())
    workers = [worker for tier in manifest["tiers"].values() for worker in tier]
    assert len(workers) == manifest["worker_count"] == 144
    assert (
        len(set(workers)) == 143
    )  # echo-speak-cloud intentionally appears in two source tiers


def test_migration_finalization_is_evidence_gated_and_exact():
    sql = (ROOT / "finalize_migration.sql").read_text()
    schema = (ROOT / "schema.sql").read_text()
    deploy = (ROOT / "deploy_guardian_beta.sh").read_text()
    assert (
        "CREATE TABLE IF NOT EXISTS cf_echo_guardian_beta.migration_receipts" in schema
    )
    assert "CREATE TABLE IF NOT EXISTS cf_echo_guardian_beta.d1_import_receipts" in schema
    for table in (
        "creations",
        "enhancement_queue",
        "enhancements",
        "guardian_state",
        "health_checks",
        "incidents",
        "partner_health",
    ):
        assert f"CREATE TABLE IF NOT EXISTS cf_echo_guardian_beta.{table}" in schema
    assert 'TO "echo-guardian-beta"' in schema
    assert 'REVOKE ALL ON cf_echo_guardian_beta.d1_import_receipts' in schema
    assert "CREATE TABLE IF NOT EXISTS cf_echo_guardian_beta.active_release_attestations" in schema
    assert "safe_timestamptz" in schema
    assert "DO $repair_rescue_tables$" in schema
    assert "ALTER COLUMN %I TYPE %s USING %I::%s" in schema
    assert "ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY" in schema
    assert "guardian_state_pkey PRIMARY KEY (key)" in schema
    for event_name in (
        "provenance_verified",
        "staging_smoke",
        "production_candidate_active",
        "production_smoke",
        "rollback_smoke",
    ):
        assert event_name in schema
        assert event_name in deploy
        assert event_name in sql
    assert "inventory.cf_migration_status" in sql
    assert "btrim(source_sha256)=rescued_sha" in sql
    assert "rollback.active_release=production.candidate_release" in sql
    assert "rollback_provenance.candidate_release<>production.candidate_release" in sql
    assert "production.candidate_release=active_release" in sql
    assert "active_attempt.recorded_at >= rollback_staging.recorded_at" in sql
    assert "attestation.recorded_at >= now() - interval '5 minutes'" in sql
    assert "RAISE EXCEPTION" in sql
    assert "GET DIAGNOSTICS catalog_rows = ROW_COUNT" in sql
    assert "IF catalog_rows <> 1" in sql
    assert "5f7afb16ed7daea81022ffb0e458e369f5d425a7f82c0636f06e653d19b15f3c" in sql
    assert "134eabf49017cc742c5b13bfca339c271f846bc2319b704a191509c69339e3d8" in sql
    assert "08e3d47f0234986de30873c8ad53cba761446cc54e93207ca19a9160b561ba06" in sql
    assert "b0e57c3be6a3a0de9f590f369f911ea5fadd7918d533492a285a12b1d73c5e51" in sql
    assert "source_counts=d1_counts" in sql
    assert "status='verified'" in sql
    assert "coalesce(metadata, '{}'::jsonb)" in sql
    assert "'echo-guardian-beta', 'worker', 'migrated'" in sql
    assert "'/home/forge/echo-guardian-beta'" in sql
    assert "ON CONFLICT (cf_service_name) DO UPDATE" in sql


def test_finalizer_freshly_smokes_and_binds_current_symlink():
    text = (ROOT / "finalize_guardian_beta.sh").read_text()
    assert 'ACTIVE_RELEASE="$(readlink -f "$CURRENT_LINK")"' in text
    assert "echo-guardian-beta-deploy.lock" in text
    assert "systemctl is-active --quiet echo-guardian-beta.service" in text
    assert 'python3 "$ACTIVE_RELEASE/smoke_live.py"' in text
    assert '-v active_release="$ACTIVE_RELEASE"' in text
    assert "/etc/echo/credentials/echo-guardian-beta/write-token" in text


def test_smoke_failure_diagnostics_never_include_payload_values():
    with pytest.raises(AssertionError) as failure:
        smoke_live.require(False, "redaction", 500, {"secret": "restricted-value"})
    rendered = str(failure.value)
    assert "restricted-value" not in rendered
    assert "secret" in rendered


def test_live_smoke_uses_imported_worker_with_history():
    text = (ROOT / "smoke_live.py").read_text()
    assert 'parser.add_argument("--uptime-worker", default="")' in text
    assert "bool(fleet_workers)" in text
    assert "urllib.parse.quote(history_worker, safe='')" in text
    assert "?hours=8760" in text
    assert "valid_history = (" in text
    assert 'int(payload.get("checks") or 0) > 0' in text
    assert 'require(valid_history, "imported uptime history"' in text

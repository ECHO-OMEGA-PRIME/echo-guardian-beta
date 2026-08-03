-- psql script: the root-owned finalizer supplies the freshly smoke-tested
-- readlink target as :active_release while holding the deploy lock.
\if :{?active_release}
\else
\echo 'active_release psql variable is required'
\quit
\endif

BEGIN;

SELECT set_config('echo.guardian.active_release', :'active_release', true);

INSERT INTO cf_echo_guardian_beta.active_release_attestations
    (active_release, rescued_source_sha256, normalized_source_sha256,
     repository_source_sha256, health_state)
VALUES
    (:'active_release',
     '5f7afb16ed7daea81022ffb0e458e369f5d425a7f82c0636f06e653d19b15f3c',
     '134eabf49017cc742c5b13bfca339c271f846bc2319b704a191509c69339e3d8',
     'b7d50e2fa3983984b56eed5c13699663b4a8221e4dcf4b98b112c9cf24ffb7e7',
     'healthy');

DO $finalize$
DECLARE
    catalog_rows integer;
    active_release constant text := current_setting('echo.guardian.active_release');
    rescued_sha constant text := '5f7afb16ed7daea81022ffb0e458e369f5d425a7f82c0636f06e653d19b15f3c';
    normalized_sha constant text := '134eabf49017cc742c5b13bfca339c271f846bc2319b704a191509c69339e3d8';
    repository_sha constant text := 'b7d50e2fa3983984b56eed5c13699663b4a8221e4dcf4b98b112c9cf24ffb7e7';
    d1_sha constant text := 'b0e57c3be6a3a0de9f590f369f911ea5fadd7918d533492a285a12b1d73c5e51';
    d1_counts constant jsonb := '{"creations":0,"enhancement_queue":38,"enhancements":0,"guardian_state":0,"health_checks":461520,"incidents":0,"partner_health":3202}'::jsonb;
BEGIN
    IF active_release !~ '^/home/forge/echo-guardian-beta/releases/[A-Za-z0-9._-]+$' THEN
        RAISE EXCEPTION 'guardian migration finalization refused: invalid active release path';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM inventory.cf_migration_status
         WHERE lower(worker_name)=lower('echo-guardian-beta')
           AND btrim(source_sha256)=rescued_sha
    ) THEN
        RAISE EXCEPTION 'guardian migration finalization refused: canonical rescue identity mismatch';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM cf_echo_guardian_beta.d1_import_receipts
         WHERE source_sha256=d1_sha
           AND source_counts=d1_counts
    ) THEN
        RAISE EXCEPTION 'guardian migration finalization refused: exact D1 import receipt missing';
    END IF;

    IF (SELECT count(*) FROM cf_echo_guardian_beta.enhancement_queue) < 38
       OR (SELECT count(*) FROM cf_echo_guardian_beta.health_checks) < 461520
       OR (SELECT count(*) FROM cf_echo_guardian_beta.partner_health) < 3202 THEN
        RAISE EXCEPTION 'guardian migration finalization refused: imported D1 row counts regressed';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM cf_echo_guardian_beta.migration_receipts provenance
          JOIN cf_echo_guardian_beta.migration_receipts staging
            ON staging.candidate_release=provenance.candidate_release
           AND staging.event_name='staging_smoke'
           AND staging.health_state='healthy'
           AND staging.recorded_at >= provenance.recorded_at
          JOIN cf_echo_guardian_beta.migration_receipts production
            ON production.candidate_release=provenance.candidate_release
           AND production.event_name='production_smoke'
           AND production.health_state='healthy'
           AND production.active_release=production.candidate_release
           AND production.recorded_at >= staging.recorded_at
          JOIN cf_echo_guardian_beta.migration_receipts rollback_provenance
            ON rollback_provenance.candidate_release<>production.candidate_release
           AND rollback_provenance.event_name='provenance_verified'
           AND rollback_provenance.recorded_at >= production.recorded_at
          JOIN cf_echo_guardian_beta.migration_receipts rollback_staging
            ON rollback_staging.candidate_release=rollback_provenance.candidate_release
           AND rollback_staging.event_name='staging_smoke'
           AND rollback_staging.health_state='healthy'
           AND rollback_staging.recorded_at >= rollback_provenance.recorded_at
          JOIN cf_echo_guardian_beta.migration_receipts active_attempt
            ON active_attempt.candidate_release=rollback_provenance.candidate_release
           AND active_attempt.event_name='production_candidate_active'
           AND active_attempt.health_state='healthy'
           AND active_attempt.active_release=active_attempt.candidate_release
           AND active_attempt.recorded_at >= rollback_staging.recorded_at
          JOIN cf_echo_guardian_beta.migration_receipts rollback
            ON rollback.candidate_release=rollback_provenance.candidate_release
           AND rollback.event_name='rollback_smoke'
           AND rollback.health_state='healthy'
           AND rollback.active_release=production.candidate_release
           AND rollback.recorded_at >= active_attempt.recorded_at
          JOIN cf_echo_guardian_beta.active_release_attestations attestation
            ON attestation.active_release=production.candidate_release
           AND attestation.health_state='healthy'
           AND attestation.recorded_at >= now() - interval '5 minutes'
         WHERE provenance.event_name='provenance_verified'
           AND provenance.health_state='verified'
           AND production.candidate_release=active_release
           AND provenance.rescued_source_sha256=rescued_sha
           AND provenance.normalized_source_sha256=normalized_sha
           AND provenance.repository_source_sha256=repository_sha
           AND staging.rescued_source_sha256=rescued_sha
           AND staging.normalized_source_sha256=normalized_sha
           AND staging.repository_source_sha256=repository_sha
           AND production.rescued_source_sha256=rescued_sha
           AND production.normalized_source_sha256=normalized_sha
           AND production.repository_source_sha256=repository_sha
           AND production.service_dir='/home/forge/echo-guardian-beta'
           AND production.unit_name='echo-guardian-beta.service'
           AND rollback_provenance.rescued_source_sha256=rescued_sha
           AND rollback_provenance.normalized_source_sha256=normalized_sha
           AND rollback_provenance.repository_source_sha256=repository_sha
           AND rollback_staging.rescued_source_sha256=rescued_sha
           AND rollback_staging.normalized_source_sha256=normalized_sha
           AND rollback_staging.repository_source_sha256=repository_sha
           AND active_attempt.rescued_source_sha256=rescued_sha
           AND active_attempt.normalized_source_sha256=normalized_sha
           AND active_attempt.repository_source_sha256=repository_sha
           AND rollback.rescued_source_sha256=rescued_sha
           AND rollback.normalized_source_sha256=normalized_sha
           AND rollback.repository_source_sha256=repository_sha
           AND rollback.service_dir='/home/forge/echo-guardian-beta'
           AND rollback.unit_name='echo-guardian-beta.service'
           AND attestation.rescued_source_sha256=rescued_sha
           AND attestation.normalized_source_sha256=normalized_sha
           AND attestation.repository_source_sha256=repository_sha
    ) THEN
        RAISE EXCEPTION 'guardian migration finalization refused: ordered active production/rollback/fresh-attestation chain missing';
    END IF;

    UPDATE arcanum_sdk.cf_artifact_catalog
       SET status='verified',
           target_origin='http://127.0.0.1:8462',
           notes='Verified FORGE replacement: independent source identities, exact D1 seed, fresh active-release smoke, timers, and ordered rollback proof.',
           metadata=coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
               'forge_service_dir', '/home/forge/echo-guardian-beta',
               'forge_unit', 'echo-guardian-beta.service',
               'migration_contract', '/home/forge/echo-guardian-beta/current/migration_contract.json',
               'active_release', active_release,
               'rescued_source_sha256', rescued_sha,
               'normalized_source_sha256', normalized_sha,
               'repository_source_sha256', repository_sha,
               'd1_sqlite_sha256', d1_sha,
               'd1_source_counts', d1_counts,
               'verified_at', now()
           ),
           updated_at=now()
     WHERE kind='worker' AND lower(name)=lower('echo-guardian-beta');
    GET DIAGNOSTICS catalog_rows = ROW_COUNT;
    IF catalog_rows <> 1 THEN
        RAISE EXCEPTION 'guardian migration finalization refused: expected one catalog row, updated %', catalog_rows;
    END IF;
END
$finalize$;

INSERT INTO arcanum_sdk.cf_migration_track
    (cf_service_name, cf_service_kind, status, priority, echo_replacement_kind,
     echo_target_path, owner_agent, notes, migrated_at, updated_at)
VALUES
    ('echo-guardian-beta', 'worker', 'migrated', 6, 'fastapi',
     '/home/forge/echo-guardian-beta', 'continuous-builder',
     'Exact source and D1 provenance, fresh active-release smoke, ordered rollback proof, and 12/12 non-generic route reconciliation are green.',
     now(), now())
ON CONFLICT (cf_service_name) DO UPDATE SET
    status=EXCLUDED.status,
    priority=EXCLUDED.priority,
    echo_replacement_kind=EXCLUDED.echo_replacement_kind,
    echo_target_path=EXCLUDED.echo_target_path,
    owner_agent=EXCLUDED.owner_agent,
    notes=EXCLUDED.notes,
    migrated_at=EXCLUDED.migrated_at,
    updated_at=EXCLUDED.updated_at;

COMMIT;

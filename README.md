# Echo Guardian Beta

Echo Guardian Beta is ECHO's private-cluster fleet health and remediation
control plane. This repository preserves the original Cloudflare Worker in
`src/index.ts` and ships its production replacement as a FastAPI service on
FORGE.

The replacement keeps the full HTTP and scheduled-job contract while moving
state from D1/KV to PostgreSQL, replacing the Worker scheduler with systemd
timers, and closing the original unauthenticated mutation surface. Automatic
code, deployment, repository, creation, and resurrection actions are converted
to approval-gated queue entries.

## Runtime

- Service: `echo-guardian-beta.service`
- Production: `127.0.0.1:8462`
- Staging: `127.0.0.1:8463`
- Code: `/home/forge/echo-guardian-beta/current`
- Data: PostgreSQL schema `cf_echo_guardian_beta`
- Identity: dedicated `echo-guardian-beta` OS and peer-authenticated,
  non-superuser PostgreSQL roles granted only the required schema operations
- Authentication: a dedicated value-only token generated locally by the deploy
  gate and delivered through systemd credentials; no global credential is
  reused, committed, or printed
- Filesystem isolation: `/home` is masked and only the immutable Guardian Beta
  release tree is rebound read-only inside the unit namespace

## API contract

`GET /health` is the only public route. All other routes require either
`Authorization: Bearer …` or `X-Echo-API-Key`.

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Runtime identity |
| GET | `/health` | Readiness and database health |
| GET | `/fleet` | Latest status for monitored workers |
| GET | `/incidents` | Incident history (`open`, `limit`) |
| GET | `/enhancements` | Applied enhancement history |
| GET | `/queue` | Approval-gated remediation queue |
| GET | `/creations` | Historical worker creation records |
| GET | `/partner` | Partner guardian state |
| GET | `/stats` | 24-hour aggregate metrics |
| GET | `/uptime/{worker}` | Per-worker uptime window |
| POST | `/trigger/health` | Bounded health sweep |
| POST | `/trigger/enhance` | Queue remediation candidates |
| POST | `/trigger/audit` | Health, partner, and remediation audit |
| POST | `/trigger/report` | Persist the daily summary |
| GET | `/diagnostics` | Private dependency/configuration self-test |

POST callers should send an `X-Idempotency-Key`. Replays return the original
run identity without repeating side effects. PostgreSQL advisory locks collapse
overlapping timer and HTTP executions.

## Scheduled jobs

The systemd timers preserve the Worker schedules in UTC:

- health: every five minutes, offset at minute 02
- enhancement scan: every thirty minutes, offset at minute 15
- deep audit: every six hours, beginning at 03:00
- report: 09:00 daily

Small randomized delays prevent synchronized fleet spikes. Probes use a fixed
allowlist, three-second deadline, and maximum fan-out of eight. The preserved
144-worker tier manifest is in `fleet_manifest.json`; the production target
allowlist is `config/targets.json`. Partner recovery is deliberately disabled
until an exact private partner endpoint and approved deploy gate are available.
Daily reports are persisted locally and registered through the scoped builder
lane rather than giving the runtime a privileged brain-write credential.

## Local verification

```powershell
python -m pytest -q --confcutdir=C:\ECHO_OMEGA_PRIME\SYSTEMS\echo_guardian_beta C:\ECHO_OMEGA_PRIME\SYSTEMS\echo_guardian_beta\tests
python -m py_compile app.py guardian_core.py guardian_cli.py import_d1.py smoke_live.py
```

The tests cover the route/method contract, authenticated writes, private reads,
constant-time token handling, explicit CORS, security headers on errors, rate
limits, worker-name validation, idempotency, advisory-lock behavior, bounded
fan-out, approval-only remediation, timer mappings, and rollback hooks.

## Deployment

Copy the repository to a candidate directory on FORGE, then run:

```bash
sudo bash deploy_guardian_beta.sh /path/to/candidate
```

The deploy gate performs syntax checks and unit tests, applies only additive
schema/index changes, imports the hash-pinned rescued D1 database through a
typed, count-gated, non-destructive importer, and boots the
exact release on the staging port, runs the live smoke suite, atomically flips
the `current` symlink, restarts production, and runs the same smoke against
production. It uses the existing local FORGE test environment (override with
`GUARDIAN_TEST_PYTHON`) and validates runtime imports as the same dedicated
`echo-guardian-beta` identity used by systemd, never a network install. A red
production smoke
restores the prior release automatically.

Rollback mechanics can be exercised safely after a healthy deployment:

```bash
sudo GUARDIAN_FORCE_STAGING_SMOKE_FAIL=1 bash deploy_guardian_beta.sh /path/to/candidate
sudo GUARDIAN_FORCE_PROD_SMOKE_FAIL=1 bash deploy_guardian_beta.sh /path/to/candidate
```

The first leaves production untouched. The second records that the candidate
became active and healthy, deliberately fails the post-promotion smoke, restores
the previous release, and verifies it. Nanosecond release IDs make each proof
append-only and non-replayable.

After both proofs are green, record the evidence and rerun the shared audit
without queue reconciliation:

```bash
sudo bash /home/forge/echo-guardian-beta/current/finalize_guardian_beta.sh
python3 /home/forge/cf-migration-audit/audit_rollup.py
```

The finalizer holds the deploy lock, resolves the live `current` symlink, runs a
fresh production smoke with the dedicated credential, and binds that attestation
to the ordered production/failed-candidate/rollback receipt chain. Completion
requires the resulting `inventory.cf_migration_status` row to be `migrated` with
the exact service directory and active unit. Finalization is separate from
deployment so a failed release can never self-certify.

## Provenance and migration evidence

`migration_contract.json` independently records the canonical rescued deployed
JavaScript hash, the normalized recovered bundle hash, the preserved TypeScript
repository hash, and the rescued D1 hash/count manifest. These related
artifacts are not claimed to be byte-identical. It also
records the 12/12 non-generic route matrix, binding replacements, schedule map,
exact service identity, and deliberate safety deltas.

The legacy Worker can still be type-checked with its original package scripts,
but it is not deployed by the private-cluster gate.

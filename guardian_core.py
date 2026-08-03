"""Private-cluster runtime for Echo Guardian Beta.

The rescued Worker remains in ``src/index.ts`` as immutable provenance.  This
module implements its operational contract against PostgreSQL and allowlisted
FORGE health endpoints without Cloudflare or GitHub mutation privileges.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


SCHEMA = "cf_echo_guardian_beta"
SERVICE_NAME = "echo-guardian-beta"
DEFAULT_TARGETS = {
    "echo-workers": "http://127.0.0.1:8000/health",
    "echo-sentinel-chat": "http://127.0.0.1:8160/health",
    "sovereign-web": "http://127.0.0.1:8766/health",
}
ALLOWED_TARGET_HOSTS = frozenset({"127.0.0.1", "localhost", "forge"})


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Treat redirects as probe results instead of following a new location."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler())


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utcnow().isoformat()


@dataclass(frozen=True)
class ProbeResult:
    worker_name: str
    status: str
    status_code: int | None
    latency_ms: int
    version: str | None = None
    error_class: str | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


class Database:
    """Small parameterized-query adapter for the imported Guardian schema."""

    _RECENT_COLUMNS: Mapping[str, tuple[str, ...]] = {
        "incidents": (
            "id",
            "worker_name",
            "type",
            "severity",
            "description",
            "auto_resolved",
            "resolved_at",
            "created_at",
        ),
        "enhancements": (
            "id",
            "worker_name",
            "type",
            "description",
            "files_changed",
            "github_url",
            "deployed",
            "reverted",
            "created_at",
        ),
        "enhancement_queue": (
            "id",
            "worker_name",
            "priority",
            "type",
            "analysis",
            "status",
            "claimed_by",
            "created_at",
            "completed_at",
        ),
        "creations": (
            "id",
            "worker_name",
            "reason",
            "description",
            "lines_of_code",
            "github_repo",
            "deployed",
            "created_at",
        ),
    }

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv(
            "ECHO_GUARDIAN_DATABASE_DSN",
            "dbname=echo user=echo-guardian-beta",
        )

    @contextlib.contextmanager
    def connect(self) -> Iterator[Any]:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(self.dsn, connect_timeout=5)
        try:
            yield conn
        finally:
            conn.close()

    def health(self) -> dict[str, Any]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"database": "ok"}

    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        import psycopg2.extras

        with (
            self.connect() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query_all(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            affected = cur.rowcount
            conn.commit()
            return affected

    @contextlib.contextmanager
    def job_lock(self, job_name: str) -> Iterator[bool]:
        """Use a session advisory lock so overlapping timers collapse to one run."""

        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s))",
                (f"{SERVICE_NAME}:{job_name}",),
            )
            acquired = bool(cur.fetchone()[0])
            try:
                yield acquired
            finally:
                if acquired:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))",
                        (f"{SERVICE_NAME}:{job_name}",),
                    )

    def begin_job(self, job_name: str, idempotency_key: str) -> tuple[str, bool]:
        run_id = str(uuid.uuid4())
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA}.job_runs
                    (run_id, job_name, idempotency_key, status, started_at, updated_at)
                VALUES (%s, %s, %s, 'running', now(), now())
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (run_id, job_name, idempotency_key),
            )
            inserted = cur.rowcount == 1
            if not inserted:
                cur.execute(
                    f"SELECT run_id FROM {SCHEMA}.job_runs WHERE idempotency_key=%s",
                    (idempotency_key,),
                )
                row = cur.fetchone()
                run_id = str(row[0]) if row else run_id
            conn.commit()
        return run_id, inserted

    def finish_job(self, run_id: str, status: str, summary: Mapping[str, Any]) -> None:
        safe_summary = json.dumps(summary, sort_keys=True, separators=(",", ":"))
        if len(safe_summary) > 8000:
            safe_summary = json.dumps(
                {
                    "truncated": True,
                    "job": str(summary.get("job", ""))[:80],
                    "status": str(summary.get("status", status))[:40],
                    "result_keys": sorted(str(key)[:80] for key in summary)[:100],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        self.execute(
            f"""
            UPDATE {SCHEMA}.job_runs
               SET status=%s, summary_json=%s::jsonb, completed_at=now(), updated_at=now()
             WHERE run_id=%s
            """,
            (status, safe_summary, run_id),
        )

    def record_check(self, result: ProbeResult) -> None:
        self.execute(
            f"""
            INSERT INTO {SCHEMA}.health_checks
                (worker_name, status, status_code, latency_ms, version, error, checked_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                result.worker_name,
                result.status,
                result.status_code,
                result.latency_ms,
                result.version or "",
                result.error_class or "",
                iso_now(),
            ),
        )

    def reconcile_incident(self, result: ProbeResult) -> None:
        if result.status == "healthy":
            self.execute(
                f"""
                UPDATE {SCHEMA}.incidents
                   SET auto_resolved=1, resolved_at=%s
                 WHERE worker_name=%s
                   AND (resolved_at IS NULL OR resolved_at='')
                """,
                (iso_now(), result.worker_name),
            )
            return

        existing = self.query_one(
            f"""
            SELECT id FROM {SCHEMA}.incidents
             WHERE worker_name=%s AND (resolved_at IS NULL OR resolved_at='')
             ORDER BY created_at DESC LIMIT 1
            """,
            (result.worker_name,),
        )
        if existing:
            return
        severity = "critical" if result.status_code is None else "high"
        self.execute(
            f"""
            INSERT INTO {SCHEMA}.incidents
                (worker_name, type, severity, description, auto_resolved, resolved_at, created_at)
            VALUES (%s,'health_failure',%s,%s,0,NULL,%s)
            """,
            (
                result.worker_name,
                severity,
                "Private-cluster health probe failed; inspect the service journal.",
                iso_now(),
            ),
        )

    def recent(self, table: str, limit: int) -> list[dict[str, Any]]:
        columns = self._RECENT_COLUMNS.get(table)
        if columns is None:
            raise ValueError("unsupported table")
        safe_limit = max(1, min(int(limit), 100))
        order_column = "created_at"
        return self.query_all(
            f"SELECT {','.join(columns)} FROM {SCHEMA}.{table} "
            f"ORDER BY {order_column} DESC LIMIT %s",
            (safe_limit,),
        )

    def open_incidents(self, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        columns = self._RECENT_COLUMNS["incidents"]
        return self.query_all(
            f"SELECT {','.join(columns)} FROM {SCHEMA}.incidents "
            "WHERE resolved_at IS NULL OR resolved_at='' "
            "ORDER BY created_at DESC LIMIT %s",
            (safe_limit,),
        )

    def fleet(self, limit: int = 250) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        return self.query_all(
            f"""
            SELECT worker_name,status,status_code,latency_ms,version,checked_at
              FROM (
                    SELECT DISTINCT ON (worker_name)
                           worker_name,status,status_code,latency_ms,version,checked_at
                      FROM {SCHEMA}.health_checks
                     ORDER BY worker_name,checked_at DESC
                   ) latest
             ORDER BY worker_name LIMIT %s
            """,
            (safe_limit,),
        )

    def uptime(self, worker_name: str, hours: int = 24) -> dict[str, Any]:
        since = (utcnow() - timedelta(hours=max(1, min(hours, 720)))).isoformat()
        row = self.query_one(
            f"""
            SELECT count(*)::int AS checks,
                   count(*) FILTER (WHERE status='healthy')::int AS healthy,
                   coalesce(round(avg(
                       latency_ms
                   ),2),0) AS avg_latency_ms,
                   max(checked_at) AS last_checked_at
              FROM {SCHEMA}.health_checks
             WHERE worker_name=%s
               AND {SCHEMA}.safe_timestamptz(checked_at) >= %s::timestamptz
            """,
            (worker_name, since),
        ) or {"checks": 0, "healthy": 0, "avg_latency_ms": 0, "last_checked_at": None}
        checks = int(row.get("checks") or 0)
        healthy = int(row.get("healthy") or 0)
        row["uptime_pct"] = round((healthy / checks) * 100, 2) if checks else None
        row["uptime24h"] = row["uptime_pct"] if hours == 24 else None
        row["worker_name"] = worker_name
        row["hours"] = hours
        row["endpoints"] = checks
        row["error"] = None if checks else "no health history"
        return row

    def stats(self) -> dict[str, Any]:
        since = (utcnow() - timedelta(hours=24)).isoformat()
        health = (
            self.query_one(
                f"""
            SELECT count(*)::int AS checks,
                   count(*) FILTER (WHERE status='healthy')::int AS healthy,
                   count(DISTINCT worker_name)::int AS workers
              FROM {SCHEMA}.health_checks
             WHERE {SCHEMA}.safe_timestamptz(checked_at) >= %s::timestamptz
            """,
                (since,),
            )
            or {}
        )
        open_incidents = self.query_one(
            f"SELECT count(*)::int AS count FROM {SCHEMA}.incidents "
            "WHERE resolved_at IS NULL OR resolved_at=''"
        ) or {"count": 0}
        queued = self.query_one(
            f"SELECT count(*)::int AS count FROM {SCHEMA}.enhancement_queue "
            "WHERE status IN ('pending','pending_approval')"
        ) or {"count": 0}
        totals = (
            self.query_one(
                f"""
            SELECT
              (SELECT count(*) FROM {SCHEMA}.enhancements)::int AS enhancements,
              (SELECT count(*) FROM {SCHEMA}.creations)::int AS creations,
              coalesce((SELECT round(avg(
                latency_ms
              ),2) FROM {SCHEMA}.health_checks
               WHERE {SCHEMA}.safe_timestamptz(checked_at) >= %s::timestamptz),0) AS avg_latency_ms
            """,
                (since,),
            )
            or {}
        )
        checks = int(health.get("checks") or 0)
        healthy = int(health.get("healthy") or 0)
        return {
            "window_hours": 24,
            "checks": checks,
            "healthy_checks": healthy,
            "uptime_pct": round((healthy / checks) * 100, 2) if checks else None,
            "workers": int(health.get("workers") or 0),
            "open_incidents": int(open_incidents.get("count") or 0),
            "pending_enhancements": int(queued.get("count") or 0),
            "total_enhancements": int(totals.get("enhancements") or 0),
            "total_creations": int(totals.get("creations") or 0),
            "average_latency_ms": float(totals.get("avg_latency_ms") or 0),
        }

    def partner(self) -> dict[str, Any] | None:
        return self.query_one(
            f"""
            SELECT partner_name,status,latency_ms,consecutive_failures,last_success,
                   resurrection_attempted,checked_at
              FROM {SCHEMA}.partner_health ORDER BY checked_at DESC LIMIT 1
            """
        )

    def record_partner(self, result: ProbeResult, consecutive_failures: int) -> None:
        self.execute(
            f"""
            INSERT INTO {SCHEMA}.partner_health
                (partner_name,status,latency_ms,consecutive_failures,last_success,
                 resurrection_attempted,checked_at)
            VALUES (%s,%s,%s,%s,%s,0,%s)
            """,
            (
                result.worker_name,
                result.status,
                result.latency_ms,
                consecutive_failures,
                iso_now() if result.status == "healthy" else "",
                iso_now(),
            ),
        )

    def enhancement_candidates(self) -> list[str]:
        since = (utcnow() - timedelta(hours=1)).isoformat()
        rows = self.query_all(
            f"""
            SELECT worker_name
              FROM {SCHEMA}.health_checks
             WHERE {SCHEMA}.safe_timestamptz(checked_at) >= %s::timestamptz
               AND status <> 'healthy'
             GROUP BY worker_name HAVING count(*) >= 3
             ORDER BY worker_name LIMIT 100
            """,
            (since,),
        )
        return [str(row["worker_name"]) for row in rows]

    def enqueue_enhancement(self, worker_name: str) -> bool:
        existing = self.query_one(
            f"""
            SELECT id FROM {SCHEMA}.enhancement_queue
             WHERE worker_name=%s AND status IN ('pending','pending_approval') LIMIT 1
            """,
            (worker_name,),
        )
        if existing:
            return False
        self.execute(
            f"""
            INSERT INTO {SCHEMA}.enhancement_queue
                (worker_name,priority,type,analysis,status,claimed_by,created_at,completed_at)
            VALUES (%s,1,'reliability',%s,'pending_approval','',%s,NULL)
            """,
            (
                worker_name,
                "Repeated private-cluster health failures require reviewed remediation.",
                iso_now(),
            ),
        )
        return True

    def write_state(self, key: str, value: Mapping[str, Any]) -> None:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"))[:16000]
        self.execute(
            f"""
            INSERT INTO {SCHEMA}.guardian_state(key,value,updated_at)
            VALUES (%s,%s,%s)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at
            """,
            (key, payload, iso_now()),
        )


class DryRunStore:
    """Exercise real job reads/probes while making every write a no-op."""

    def __init__(self, delegate: Database) -> None:
        self.delegate = delegate
        self.checks: list[ProbeResult] = []

    def begin_job(self, _job_name: str, _idempotency_key: str) -> tuple[str, bool]:
        return str(uuid.uuid4()), True

    @contextlib.contextmanager
    def job_lock(self, _job_name: str) -> Iterator[bool]:
        yield True

    def finish_job(
        self, _run_id: str, _status: str, _summary: Mapping[str, Any]
    ) -> None:
        return None

    def record_check(self, result: ProbeResult) -> None:
        self.checks.append(result)

    def reconcile_incident(self, _result: ProbeResult) -> None:
        return None

    def partner(self) -> dict[str, Any] | None:
        return self.delegate.partner()

    def record_partner(self, _result: ProbeResult, _consecutive_failures: int) -> None:
        return None

    def enhancement_candidates(self) -> list[str]:
        return self.delegate.enhancement_candidates()

    def enqueue_enhancement(self, _worker_name: str) -> bool:
        return True

    def stats(self) -> dict[str, Any]:
        return self.delegate.stats()

    def write_state(self, _key: str, _value: Mapping[str, Any]) -> None:
        return None


def load_targets(path: str | None = None) -> dict[str, str]:
    configured_path = path or os.getenv("ECHO_GUARDIAN_TARGETS_FILE", "")
    raw: Mapping[str, Any] = DEFAULT_TARGETS
    if configured_path:
        target_path = Path(configured_path)
        if target_path.is_file():
            parsed = json.loads(target_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("target file must contain a JSON object")
            raw = parsed

    extra_hosts = {
        host.strip().lower()
        for host in os.getenv("ECHO_GUARDIAN_ALLOWED_TARGET_HOSTS", "").split(",")
        if host.strip()
    }
    allowed_hosts = ALLOWED_TARGET_HOSTS | extra_hosts
    targets: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("target names and URLs must be strings")
        if not name or len(name) > 128:
            raise ValueError("invalid target name")
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"invalid target URL for {name}")
        if parsed.hostname.lower() not in allowed_hosts:
            raise ValueError(f"target host is not allowlisted for {name}")
        targets[name] = value
    return targets


def validate_private_url(url: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    extra_hosts = {
        host.strip().lower()
        for host in os.getenv("ECHO_GUARDIAN_ALLOWED_TARGET_HOSTS", "").split(",")
        if host.strip()
    }
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.hostname.lower() not in (ALLOWED_TARGET_HOSTS | extra_hosts)
    ):
        raise ValueError("probe target is not an allowlisted private URL")
    return parsed


def probe_target(worker_name: str, url: str, timeout_seconds: float) -> ProbeResult:
    validate_private_url(url)
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": f"{SERVICE_NAME}/2"},
        method="GET",
    )
    try:
        with NO_REDIRECT_OPENER.open(request, timeout=timeout_seconds) as response:
            body = response.read(65536)
            status_code = int(response.status)
            version = None
            if body and "json" in (response.headers.get("content-type") or "").lower():
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict) and parsed.get("version") is not None:
                        version = str(parsed["version"])[:80]
                except (ValueError, UnicodeDecodeError):
                    pass
            return ProbeResult(
                worker_name=worker_name,
                status="healthy" if 200 <= status_code < 400 else "degraded",
                status_code=status_code,
                latency_ms=round((time.monotonic() - started) * 1000),
                version=version,
            )
    except urllib.error.HTTPError as exc:
        return ProbeResult(
            worker_name=worker_name,
            status="degraded",
            status_code=int(exc.code),
            latency_ms=round((time.monotonic() - started) * 1000),
            error_class=type(exc).__name__,
        )
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return ProbeResult(
            worker_name=worker_name,
            status="down",
            status_code=None,
            latency_ms=round((time.monotonic() - started) * 1000),
            error_class=type(exc).__name__,
        )


class GuardianService:
    """Bounded, idempotent jobs shared by HTTP triggers and systemd timers."""

    def __init__(self, store: Database | None = None) -> None:
        self.store = store or Database()
        self.targets = load_targets()
        self.timeout_seconds = max(
            0.5, min(float(os.getenv("ECHO_GUARDIAN_PROBE_TIMEOUT", "3")), 15.0)
        )
        self.max_workers = max(
            1, min(int(os.getenv("ECHO_GUARDIAN_MAX_FANOUT", "8")), 16)
        )

    def _idempotency_key(self, job_name: str, supplied: str | None) -> str:
        if supplied:
            return f"{job_name}:{supplied[:160]}"
        bucket_seconds = {
            "health": 240,
            "enhance": 1500,
            "audit": 18000,
            "report": 72000,
        }.get(job_name, 300)
        return f"{job_name}:auto:{int(time.time()) // bucket_seconds}"

    def _run_job(
        self, job_name: str, supplied_key: str | None, operation: Any
    ) -> dict[str, Any]:
        idempotency_key = self._idempotency_key(job_name, supplied_key)
        run_id, fresh = self.store.begin_job(job_name, idempotency_key)
        if not fresh:
            return {"job": job_name, "run_id": run_id, "status": "replayed"}
        with self.store.job_lock(job_name) as acquired:
            if not acquired:
                summary = {
                    "job": job_name,
                    "run_id": run_id,
                    "status": "skipped_concurrent",
                }
                self.store.finish_job(run_id, "skipped", summary)
                return summary
            try:
                details = dict(operation())
                summary = {
                    "job": job_name,
                    "run_id": run_id,
                    "status": "completed",
                    **details,
                }
                self.store.finish_job(run_id, "completed", summary)
                return summary
            except Exception:
                self.store.finish_job(
                    run_id, "failed", {"job": job_name, "status": "failed"}
                )
                raise

    def health_sweep(self, idempotency_key: str | None = None) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            results: list[ProbeResult] = []
            with ThreadPoolExecutor(
                max_workers=self.max_workers, thread_name_prefix="guardian-probe"
            ) as pool:
                futures = {
                    pool.submit(probe_target, name, url, self.timeout_seconds): name
                    for name, url in self.targets.items()
                }
                for future in as_completed(futures):
                    result = future.result()
                    self.store.record_check(result)
                    self.store.reconcile_incident(result)
                    results.append(result)
            results.sort(key=lambda item: item.worker_name)
            partner = self.partner_check()
            return {
                "checked": len(results),
                "total": len(results),
                "healthy": sum(1 for result in results if result.status == "healthy"),
                "degraded": sum(1 for result in results if result.status == "degraded"),
                "down": sum(1 for result in results if result.status == "down"),
                "partnerAlive": partner.get("status") in {"healthy", "not_configured"},
                "partner": partner,
                "results": [result.public() for result in results],
            }

        return self._run_job("health", idempotency_key, operation)

    def enhancement_scan(self, idempotency_key: str | None = None) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            candidates = self.store.enhancement_candidates()
            queued = sum(
                1 for worker in candidates if self.store.enqueue_enhancement(worker)
            )
            return {
                "candidates": len(candidates),
                "queued_for_approval": queued,
                "mutations_applied": 0,
                "mode": "approval_required",
            }

        return self._run_job("enhance", idempotency_key, operation)

    def partner_check(self) -> dict[str, Any]:
        partner_url = os.getenv("ECHO_GUARDIAN_PARTNER_URL", "").strip()
        partner_name = os.getenv(
            "ECHO_GUARDIAN_PARTNER_NAME", "echo-guardian-alpha"
        ).strip()[:128]
        if not partner_url:
            return {"status": "not_configured", "resurrection_attempted": False}
        validate_private_url(partner_url)
        result = probe_target(partner_name, partner_url, self.timeout_seconds)
        previous = self.store.partner() or {}
        failures = (
            0
            if result.status == "healthy"
            else int(previous.get("consecutive_failures") or 0) + 1
        )
        self.store.record_partner(result, failures)
        return {
            "status": result.status,
            "latency_ms": result.latency_ms,
            "consecutive_failures": failures,
            "resurrection_attempted": False,
        }

    def deep_audit(self, idempotency_key: str | None = None) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            sweep = self.health_sweep(f"audit-{idempotency_key or int(time.time())}")
            partner = self.partner_check()
            enhancements = self.enhancement_scan(
                f"audit-{idempotency_key or int(time.time())}"
            )
            return {
                "health": sweep,
                "partner": partner,
                "enhancements": enhancements,
                "automatic_deployments": 0,
                "automatic_creations": 0,
            }

        return self._run_job("audit", idempotency_key, operation)

    def daily_report(self, idempotency_key: str | None = None) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            report = {
                "generated_at": iso_now(),
                "stats": self.store.stats(),
                "partner": self.store.partner(),
            }
            self.store.write_state("latest_daily_report", report)
            return report

        return self._run_job("report", idempotency_key, operation)

    def dry_run_job(
        self, job_name: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        if job_name not in {"health", "enhance", "audit", "report"}:
            raise ValueError("unsupported dry-run job")
        dry_service = GuardianService(store=DryRunStore(self.store))
        dry_service.targets = dict(self.targets)
        dry_service.timeout_seconds = self.timeout_seconds
        dry_service.max_workers = self.max_workers
        handlers = {
            "health": dry_service.health_sweep,
            "enhance": dry_service.enhancement_scan,
            "audit": dry_service.deep_audit,
            "report": dry_service.daily_report,
        }
        result = handlers[job_name](idempotency_key)
        result["dry_run"] = True
        result["side_effects"] = 0
        return result

"""FastAPI surface for the private-cluster Echo Guardian Beta runtime."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from guardian_core import GuardianService, SERVICE_NAME


VERSION = "2.0.0"
WORKER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
PUBLIC_PATHS = frozenset({"/health"})
WRITE_PATHS = frozenset(
    {
        "/trigger/health",
        "/trigger/enhance",
        "/trigger/audit",
        "/trigger/report",
    }
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Cache-Control": "no-store",
}

logger = logging.getLogger("echo.guardian.beta")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(os.getenv("ECHO_GUARDIAN_LOG_LEVEL", "INFO").upper())


class SlidingWindowLimiter:
    def __init__(self, max_keys: int = 4096) -> None:
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max(64, min(max_keys, 65_536))

    def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events.get(key)
            if events is None:
                while len(self._events) >= self._max_keys:
                    self._events.popitem(last=False)
                events = deque()
                self._events[key] = events
            else:
                self._events.move_to_end(key)
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0])))
                return False, retry_after
            events.append(now)
            return True, 0


limiter = SlidingWindowLimiter()


def _rate_path(path: str) -> str:
    if path.startswith("/uptime/"):
        return "/uptime/{worker}"
    known = (
        PUBLIC_PATHS
        | WRITE_PATHS
        | {
            "/",
            "/fleet",
            "/incidents",
            "/enhancements",
            "/queue",
            "/creations",
            "/partner",
            "/stats",
            "/diagnostics",
        }
    )
    return path if path in known else "/_other"


@lru_cache(maxsize=1)
def _auth_token() -> str:
    inline = os.getenv("ECHO_GUARDIAN_WRITE_TOKEN", "")
    if inline:
        return inline.strip()
    configured = os.getenv("ECHO_GUARDIAN_TOKEN_FILE", "")
    credential_dir = os.getenv("CREDENTIALS_DIRECTORY", "")
    token_path = configured or (
        str(Path(credential_dir) / "guardian_write_token") if credential_dir else ""
    )
    if not token_path:
        return ""
    try:
        return Path(token_path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _presented_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("x-echo-api-key", "").strip()


def _allowed_origins() -> frozenset[str]:
    return frozenset(
        origin.strip()
        for origin in os.getenv(
            "ECHO_GUARDIAN_CORS_ORIGINS",
            "https://throne.echo-op.com,https://fleet.echo-op.com",
        ).split(",")
        if origin.strip()
    )


def _normalized_path(request: Request) -> str:
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str):
        return template
    if request.url.path.startswith("/uptime/"):
        return "/uptime/{worker}"
    return request.url.path[:120]


def _client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return hashlib.sha256(host.encode("utf-8", "ignore")).hexdigest()[:16]


def _rate_method(method: str) -> str:
    normalized = method.upper()
    return normalized if normalized in {"GET", "POST", "OPTIONS"} else "_OTHER"


app = FastAPI(
    title="Echo Guardian Beta",
    version=VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def guardian_middleware(request: Request, call_next: Any) -> Response:
    started = time.monotonic()
    request_id = request.headers.get("x-request-id", "")[:80] or str(uuid.uuid4())
    origin = request.headers.get("origin", "")
    allowed_origins = _allowed_origins()
    response: Response

    try:
        if request.method == "OPTIONS":
            preflight_allowed, preflight_retry_after = limiter.allow(
                f"preflight:{_client_key(request)}:"
                f"{_rate_path(request.url.path)}:{_rate_method(request.method)}",
                max(
                    1,
                    min(
                        int(os.getenv("ECHO_GUARDIAN_PREFLIGHT_RATE_LIMIT", "60")),
                        1000,
                    ),
                ),
                60,
            )
            if not preflight_allowed:
                response = JSONResponse(
                    {"detail": "rate limit exceeded"}, status_code=429
                )
                response.headers["Retry-After"] = str(preflight_retry_after)
            elif not origin or origin not in allowed_origins:
                response = JSONResponse(
                    {"detail": "origin not allowed"}, status_code=403
                )
            else:
                response = Response(status_code=204)
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = (
                    "Authorization, Content-Type, X-Echo-API-Key, X-Echo-Smoke-Test, "
                    "X-Idempotency-Key, X-Request-ID"
                )
                response.headers["Access-Control-Max-Age"] = "600"
        else:
            is_write = request.url.path in WRITE_PATHS
            protected = request.url.path not in PUBLIC_PATHS
            authenticated = not protected
            if protected:
                expected = _auth_token()
                presented = _presented_token(request)
                auth_failure_status = 0
                auth_failure_detail = ""
                if not expected:
                    auth_failure_status = 503
                    auth_failure_detail = "authentication unavailable"
                elif not presented or not hmac.compare_digest(presented, expected):
                    auth_failure_status = 401
                    auth_failure_detail = "unauthorized"
                else:
                    authenticated = True
                if auth_failure_status:
                    auth_allowed, auth_retry_after = limiter.allow(
                        f"unauthenticated:{_client_key(request)}:"
                        f"{_rate_path(request.url.path)}:{_rate_method(request.method)}",
                        max(
                            1,
                            min(
                                int(
                                    os.getenv(
                                        "ECHO_GUARDIAN_AUTH_FAILURE_RATE_LIMIT", "30"
                                    )
                                ),
                                1000,
                            ),
                        ),
                        60,
                    )
                    if auth_allowed:
                        response = JSONResponse(
                            {"detail": auth_failure_detail},
                            status_code=auth_failure_status,
                        )
                    else:
                        response = JSONResponse(
                            {"detail": "rate limit exceeded"}, status_code=429
                        )
                        response.headers["Retry-After"] = str(auth_retry_after)
            if authenticated:
                limit = int(
                    os.getenv(
                        "ECHO_GUARDIAN_WRITE_RATE_LIMIT"
                        if is_write
                        else "ECHO_GUARDIAN_READ_RATE_LIMIT",
                        "10" if is_write else "120",
                    )
                )
                rate_subject = "authenticated" if protected else _client_key(request)
                allowed, retry_after = limiter.allow(
                    f"{rate_subject}:{_rate_path(request.url.path)}:"
                    f"{_rate_method(request.method)}",
                    max(1, min(limit, 1000)),
                    60,
                )
                if not allowed:
                    response = JSONResponse(
                        {"detail": "rate limit exceeded"}, status_code=429
                    )
                    response.headers["Retry-After"] = str(retry_after)
                else:
                    response = await call_next(request)
    except Exception:
        logger.error(
            json.dumps(
                {
                    "event": "request_error",
                    "request_id": request_id,
                    "method": request.method,
                    "path": _normalized_path(request),
                },
                sort_keys=True,
            )
        )
        response = JSONResponse(
            {"detail": "internal service error", "request_id": request_id},
            status_code=500,
        )

    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    response.headers["X-Request-ID"] = request_id
    response.headers.append("Vary", "Origin")
    if origin and origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    logger.info(
        json.dumps(
            {
                "event": "request",
                "request_id": request_id,
                "method": request.method,
                "path": _normalized_path(request),
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
            sort_keys=True,
        )
    )
    return response


_service: GuardianService | None = None


def get_service() -> GuardianService:
    global _service
    if _service is None:
        _service = GuardianService()
    return _service


def _idempotency_key(request: Request) -> str | None:
    value = request.headers.get("x-idempotency-key", "").strip()
    if value and not re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", value):
        raise HTTPException(status_code=400, detail="invalid idempotency key")
    return value or None


def _smoke_preview(request: Request, job_name: str) -> dict[str, Any] | None:
    if request.headers.get("x-echo-smoke-test") != "1":
        return None
    return get_service().dry_run_job(job_name, _idempotency_key(request))


@app.get("/health")
def health() -> JSONResponse:
    try:
        service = get_service()
        database = service.store.health()
        try:
            partner_status = (service.store.partner() or {}).get("status", "unknown")
        except Exception:
            partner_status = "unknown"
    except Exception:
        return JSONResponse(
            {
                "service": SERVICE_NAME,
                "status": "degraded",
                "version": VERSION,
                "database": "down",
            },
            status_code=503,
        )
    return JSONResponse(
        {
            "service": SERVICE_NAME,
            "name": "Echo Guardian Beta",
            "status": "healthy",
            "version": VERSION,
            "partner": partner_status,
            **database,
        }
    )


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "name": "Echo Guardian Beta",
        "status": "operational",
        "version": VERSION,
        "runtime": "private-cluster",
    }


@app.get("/fleet")
def fleet() -> dict[str, Any]:
    workers = get_service().store.fleet()
    return {"total": len(workers), "workers": workers}


@app.get("/incidents")
def incidents(
    open_only: bool = Query(False, alias="open"),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    rows = get_service().store.recent("incidents", limit)
    if open_only:
        store = get_service().store
        if hasattr(store, "open_incidents"):
            rows = store.open_incidents(limit)
        else:
            rows = [row for row in rows if not row.get("resolved_at")]
    return {"count": len(rows), "incidents": rows}


@app.get("/enhancements")
def enhancements(limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    rows = get_service().store.recent("enhancements", limit)
    return {"count": len(rows), "enhancements": rows}


@app.get("/queue")
def queue(limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    rows = get_service().store.recent("enhancement_queue", limit)
    return {"count": len(rows), "queue": rows}


@app.get("/creations")
def creations(limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    rows = get_service().store.recent("creations", limit)
    return {"count": len(rows), "creations": rows}


@app.get("/partner")
def partner() -> dict[str, Any]:
    latest = get_service().store.partner()
    return {
        "partner": latest,
        "configured": bool(os.getenv("ECHO_GUARDIAN_PARTNER_URL", "")),
    }


@app.get("/stats")
def stats() -> dict[str, Any]:
    data = get_service().store.stats()
    return {
        "guardian": SERVICE_NAME,
        "monitored_workers": data.get("workers", 0),
        **data,
    }


@app.get("/uptime/{worker}")
def uptime(worker: str, hours: int = Query(24, ge=1, le=720)) -> dict[str, Any]:
    if not WORKER_RE.fullmatch(worker):
        raise HTTPException(status_code=400, detail="invalid worker name")
    data = get_service().store.uptime(worker, hours)
    if not data.get("checks"):
        raise HTTPException(status_code=404, detail="worker has no health history")
    return data


@app.get("/diagnostics")
def diagnostics() -> dict[str, Any]:
    service = get_service()
    return {
        "service": SERVICE_NAME,
        "version": VERSION,
        "database": service.store.health(),
        "target_count": len(service.targets),
        "max_fanout": service.max_workers,
        "probe_timeout_seconds": service.timeout_seconds,
        "automatic_mutations": False,
    }


@app.post("/trigger/health")
def trigger_health(request: Request) -> dict[str, Any]:
    return _smoke_preview(request, "health") or get_service().health_sweep(
        _idempotency_key(request)
    )


@app.post("/trigger/enhance")
def trigger_enhance(request: Request) -> dict[str, Any]:
    return _smoke_preview(request, "enhance") or get_service().enhancement_scan(
        _idempotency_key(request)
    )


@app.post("/trigger/audit")
def trigger_audit(request: Request) -> dict[str, Any]:
    return _smoke_preview(request, "audit") or get_service().deep_audit(
        _idempotency_key(request)
    )


@app.post("/trigger/report")
def trigger_report(request: Request) -> dict[str, Any]:
    return _smoke_preview(request, "report") or get_service().daily_report(
        _idempotency_key(request)
    )

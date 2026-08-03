"""Live HTTP smoke suite for staging and production Guardian Beta releases."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


REQUIRED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "strict-transport-security": "max-age=63072000; includeSubDomains; preload",
}


def request_json(
    base: str,
    path: str,
    *,
    method: str = "GET",
    token: str = "",
    origin: str | None = None,
    idempotency_key: str | None = None,
    smoke_test: bool = False,
) -> tuple[int, dict[str, str], Any]:
    headers = {"Accept": "application/json", "User-Agent": "guardian-beta-smoke/2"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if origin:
        headers["Origin"] = origin
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    if smoke_test:
        headers["X-Echo-Smoke-Test"] = "1"
    req = urllib.request.Request(
        base.rstrip("/") + path, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read(1_000_000)
            status = response.status
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(1_000_000)
        status = exc.code
        response_headers = {key.lower(): value for key, value in exc.headers.items()}
    try:
        payload: Any = json.loads(raw) if raw else None
    except (UnicodeDecodeError, ValueError):
        payload = raw.decode("utf-8", "replace")[:1000]
    return status, response_headers, payload


def require_headers(headers: dict[str, str], label: str) -> None:
    for name, expected in REQUIRED_HEADERS.items():
        actual = headers.get(name)
        if actual != expected:
            raise AssertionError(f"{label}: missing or invalid {name}")


def payload_shape(payload: Any) -> dict[str, Any]:
    """Return value-free diagnostics safe for deploy logs."""
    if isinstance(payload, dict):
        return {
            "type": "object",
            "keys": sorted(str(key) for key in payload)[:32],
            "key_count": len(payload),
        }
    if isinstance(payload, list):
        return {"type": "array", "item_count": len(payload)}
    return {"type": type(payload).__name__}


def require(condition: bool, label: str, status: int, payload: Any = None) -> None:
    if not condition:
        raise AssertionError(
            {"check": label, "status": status, "payload": payload_shape(payload)}
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--token")
    parser.add_argument("--token-file")
    parser.add_argument("--uptime-worker", default="")
    parser.add_argument("--force-fail", action="store_true")
    args = parser.parse_args()

    token = args.token or ""
    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("an authentication token or token file is required")

    checks = 0

    status, headers, payload = request_json(args.base, "/health")
    require(
        status == 200
        and isinstance(payload, dict)
        and payload.get("status") == "healthy",
        "health",
        status,
        payload,
    )
    require_headers(headers, "health")
    checks += 1

    status, headers, _ = request_json(args.base, "/trigger/health", method="POST")
    assert status == 401, status
    require_headers(headers, "anonymous trigger")
    checks += 1

    status, headers, _ = request_json(
        args.base,
        "/trigger/health",
        method="POST",
        token="definitely-invalid",
    )
    assert status == 401, status
    require_headers(headers, "invalid trigger")
    checks += 1

    run_prefix = f"smoke-{uuid.uuid4()}"
    for trigger in ("health", "enhance", "audit", "report"):
        status, headers, payload = request_json(
            args.base,
            f"/trigger/{trigger}",
            method="POST",
            token=token,
            idempotency_key=f"{run_prefix}-{trigger}",
            smoke_test=True,
        )
        require(
            status == 200
            and isinstance(payload, dict)
            and payload.get("status") in {"completed", "replayed"},
            f"trigger {trigger}",
            status,
            payload,
        )
        require(
            isinstance(payload, dict)
            and payload.get("dry_run") is True
            and payload.get("side_effects") == 0,
            f"trigger {trigger} dry-run",
            status,
            payload,
        )
        require_headers(headers, f"trigger {trigger}")
        checks += 1

    reads = (
        ("/", "service"),
        ("/fleet", "workers"),
        ("/incidents?open=true&limit=10", "incidents"),
        ("/enhancements?limit=10", "enhancements"),
        ("/queue?limit=10", "queue"),
        ("/creations?limit=10", "creations"),
        ("/partner", "partner"),
        ("/stats", "guardian"),
        ("/diagnostics", "database"),
    )
    fleet_workers: list[dict[str, Any]] = []
    for path, key in reads:
        status, headers, payload = request_json(args.base, path, token=token)
        require(
            status == 200 and isinstance(payload, dict) and key in payload,
            path,
            status,
            payload,
        )
        if path == "/fleet" and isinstance(payload.get(key), list):
            fleet_workers = payload[key]
        require_headers(headers, path)
        checks += 1

    require(bool(fleet_workers), "imported fleet history", 200, {"workers": fleet_workers})
    history_worker = args.uptime_worker.strip() or str(
        fleet_workers[0].get("worker_name", "")
    )
    require(bool(history_worker), "imported fleet worker identity", 200)
    uptime_path = f"/uptime/{urllib.parse.quote(history_worker, safe='')}"
    status, headers, payload = request_json(args.base, uptime_path, token=token)
    valid_history = (
        status == 200
        and isinstance(payload, dict)
        and payload.get("worker_name") == history_worker
        and int(payload.get("checks") or 0) > 0
    )
    require(valid_history, "imported uptime history", status, payload)
    require_headers(headers, "imported uptime history")
    checks += 1

    status, headers, _ = request_json(args.base, "/uptime/not%20valid", token=token)
    assert status in {400, 404}, status
    require_headers(headers, "invalid worker")
    checks += 1

    status, headers, _ = request_json(args.base, "/incidents?limit=1000", token=token)
    assert status == 422, status
    require_headers(headers, "invalid limit")
    checks += 1

    status, headers, _ = request_json(args.base, "/does-not-exist", token=token)
    assert status == 404, status
    require_headers(headers, "404")
    checks += 1

    status, headers, _ = request_json(
        args.base,
        "/stats",
        token=token,
        origin="https://untrusted.invalid",
    )
    require(
        status == 200 and "access-control-allow-origin" not in headers,
        "untrusted origin",
        status,
    )
    checks += 1

    status, headers, _ = request_json(
        args.base,
        "/stats",
        method="OPTIONS",
        origin="https://throne.echo-op.com",
    )
    assert status == 204, status
    require(
        headers.get("access-control-allow-origin") == "https://throne.echo-op.com",
        "trusted preflight",
        status,
    )
    require_headers(headers, "preflight")
    checks += 1

    if args.force_fail:
        print(
            json.dumps(
                {"ok": False, "checks": checks, "forced_failure": True}, sort_keys=True
            )
        )
        return 9
    print(json.dumps({"ok": True, "checks": checks}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module


class FakeStore:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.uptime_calls: list[str] = []

    def health(self):
        if not self.healthy:
            raise RuntimeError("database unavailable")
        return {"database": "ok"}

    def partner(self):
        return {"status": "healthy"}

    def fleet(self):
        return [{"worker_name": "echo-workers", "status": "healthy"}]

    def recent(self, table, limit):
        return []

    def stats(self):
        return {
            "workers": 1,
            "checks": 1,
            "healthy_checks": 1,
            "uptime_pct": 100.0,
            "open_incidents": 0,
            "pending_enhancements": 0,
        }

    def uptime(self, worker, hours):
        self.uptime_calls.append(worker)
        return {
            "worker_name": worker,
            "hours": hours,
            "checks": 1,
            "healthy": 1,
            "uptime_pct": 100.0,
        }


class FakeService:
    def __init__(self, store=None) -> None:
        self.store = store or FakeStore()
        self.targets = {"echo-workers": "http://127.0.0.1/health"}
        self.max_workers = 2
        self.timeout_seconds = 1.0
        self.calls: list[str] = []

    def _result(self, name):
        self.calls.append(name)
        return {"job": name, "run_id": "run", "status": "completed"}

    def health_sweep(self, _key=None):
        return self._result("health")

    def enhancement_scan(self, _key=None):
        return self._result("enhance")

    def deep_audit(self, _key=None):
        return self._result("audit")

    def daily_report(self, _key=None):
        return self._result("report")

    def dry_run_job(self, name, _key=None):
        return {
            "job": name,
            "run_id": "dry-run",
            "status": "completed",
            "dry_run": True,
            "side_effects": 0,
        }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ECHO_GUARDIAN_WRITE_TOKEN", "test-token")
    monkeypatch.setenv("ECHO_GUARDIAN_WRITE_RATE_LIMIT", "10")
    monkeypatch.setenv("ECHO_GUARDIAN_READ_RATE_LIMIT", "120")
    app_module._auth_token.cache_clear()
    app_module.limiter = app_module.SlidingWindowLimiter()
    service = FakeService()
    app_module._service = service
    with TestClient(app_module.app) as test_client:
        yield test_client, service
    app_module._service = None
    app_module._auth_token.cache_clear()


def auth_headers(**extra):
    return {"Authorization": "Bearer test-token", **extra}


def assert_security_headers(response):
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_route_contract_and_methods():
    expected = {
        ("/", "GET"),
        ("/health", "GET"),
        ("/fleet", "GET"),
        ("/incidents", "GET"),
        ("/enhancements", "GET"),
        ("/queue", "GET"),
        ("/creations", "GET"),
        ("/partner", "GET"),
        ("/stats", "GET"),
        ("/uptime/{worker}", "GET"),
        ("/trigger/health", "POST"),
        ("/trigger/enhance", "POST"),
        ("/trigger/audit", "POST"),
        ("/trigger/report", "POST"),
    }
    actual = {
        (route.path, method)
        for route in app_module.app.routes
        if hasattr(route, "methods")
        for method in route.methods
        if method in {"GET", "POST"}
    }
    assert expected <= actual


def test_public_health_and_private_reads(client):
    test_client, _ = client
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert_security_headers(response)

    denied = test_client.get("/stats")
    assert denied.status_code == 401
    assert_security_headers(denied)

    allowed = test_client.get("/stats", headers=auth_headers())
    assert allowed.status_code == 200
    assert allowed.json()["guardian"] == "echo-guardian-beta"


@pytest.mark.parametrize("trigger", ["health", "enhance", "audit", "report"])
def test_mutating_triggers_require_auth_and_accept_idempotency(client, trigger):
    test_client, service = client
    denied = test_client.post(f"/trigger/{trigger}")
    assert denied.status_code == 401
    assert service.calls == []

    invalid = test_client.post(
        f"/trigger/{trigger}",
        headers=auth_headers(**{"X-Idempotency-Key": "bad key"}),
    )
    assert invalid.status_code == 400
    assert service.calls == []

    accepted = test_client.post(
        f"/trigger/{trigger}",
        headers=auth_headers(**{"X-Idempotency-Key": f"test-{trigger}-123"}),
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "completed"
    assert service.calls == [trigger]


def test_cors_is_explicit_and_errors_keep_security_headers(client):
    test_client, _ = client
    allowed = test_client.options(
        "/stats",
        headers={"Origin": "https://throne.echo-op.com"},
    )
    assert allowed.status_code == 204
    assert (
        allowed.headers["access-control-allow-origin"] == "https://throne.echo-op.com"
    )
    assert allowed.headers["vary"] == "Origin"
    assert_security_headers(allowed)

    denied = test_client.options(
        "/stats",
        headers={"Origin": "https://untrusted.invalid"},
    )
    assert denied.status_code == 403
    assert "access-control-allow-origin" not in denied.headers
    assert_security_headers(denied)

    missing = test_client.get("/missing", headers=auth_headers())
    assert missing.status_code == 404
    assert_security_headers(missing)


@pytest.mark.parametrize("trigger", ["health", "enhance", "audit", "report"])
def test_authenticated_smoke_preview_has_zero_side_effects(client, trigger):
    test_client, service = client
    response = test_client.post(
        f"/trigger/{trigger}",
        headers=auth_headers(**{"X-Echo-Smoke-Test": "1"}),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["side_effects"] == 0
    assert service.calls == []


def test_rate_limit_returns_retry_after(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setenv("ECHO_GUARDIAN_WRITE_RATE_LIMIT", "1")
    app_module.limiter = app_module.SlidingWindowLimiter()
    first = test_client.post("/trigger/health", headers=auth_headers())
    second = test_client.post("/trigger/health", headers=auth_headers())
    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) >= 1
    assert_security_headers(second)


def test_invalid_auth_does_not_consume_authenticated_rate_bucket(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setenv("ECHO_GUARDIAN_WRITE_RATE_LIMIT", "1")
    monkeypatch.setenv("ECHO_GUARDIAN_AUTH_FAILURE_RATE_LIMIT", "1")
    app_module.limiter = app_module.SlidingWindowLimiter()
    first_denied = test_client.post(
        "/trigger/health", headers={"Authorization": "Bearer invalid"}
    )
    limited_denied = test_client.post(
        "/trigger/health", headers={"Authorization": "Bearer invalid"}
    )
    allowed = test_client.post("/trigger/health", headers=auth_headers())
    assert first_denied.status_code == 401
    assert limited_denied.status_code == 429
    assert allowed.status_code == 200


def test_preflight_has_its_own_bounded_bucket(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setenv("ECHO_GUARDIAN_PREFLIGHT_RATE_LIMIT", "1")
    app_module.limiter = app_module.SlidingWindowLimiter()
    headers = {"Origin": "https://throne.echo-op.com"}
    first = test_client.options("/stats", headers=headers)
    second = test_client.options("/stats", headers=headers)
    assert first.status_code == 204
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) >= 1


def test_rate_limiter_keyspace_is_bounded():
    bounded = app_module.SlidingWindowLimiter(max_keys=64)
    for index in range(1000):
        assert bounded.allow(f"key-{index}", 1, 60)[0]
    assert len(bounded._events) == 64
    assert app_module._rate_path("/arbitrary/unique/path") == "/_other"
    assert app_module._rate_path("/uptime/worker-name") == "/uptime/{worker}"
    assert app_module._rate_method("GET") == "GET"
    assert {app_module._rate_method(f"X-ATTACK-{index}") for index in range(100)} == {
        "_OTHER"
    }


def test_worker_identifier_is_validated_before_store(client):
    test_client, service = client
    response = test_client.get("/uptime/%24%7Bbad%7D", headers=auth_headers())
    assert response.status_code == 400
    assert service.store.uptime_calls == []


def test_uptime_window_covers_rescued_annual_history(client):
    test_client, _ = client
    response = test_client.get(
        "/uptime/echo-workers?hours=8760", headers=auth_headers()
    )
    assert response.status_code == 200
    assert response.json()["hours"] == 8760
    too_wide = test_client.get(
        "/uptime/echo-workers?hours=8761", headers=auth_headers()
    )
    assert too_wide.status_code == 422


def test_degraded_health_is_503(client):
    test_client, service = client
    service.store.healthy = False
    response = test_client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert_security_headers(response)


def test_no_auth_configuration_fails_closed(client, monkeypatch):
    test_client, _ = client
    monkeypatch.delenv("ECHO_GUARDIAN_WRITE_TOKEN")
    monkeypatch.delenv("ECHO_GUARDIAN_TOKEN_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    app_module._auth_token.cache_clear()
    response = test_client.get("/stats", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503
    assert_security_headers(response)

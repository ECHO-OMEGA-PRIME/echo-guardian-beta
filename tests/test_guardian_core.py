from __future__ import annotations

import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import guardian_core


class FakeJobStore:
    def __init__(self, *, lock=True):
        self.lock = lock
        self.keys = {}
        self.finished = []
        self.checks = []
        self.incidents = []
        self.queued = []

    def begin_job(self, job_name, key):
        if key in self.keys:
            return self.keys[key], False
        run_id = f"run-{len(self.keys) + 1}"
        self.keys[key] = run_id
        return run_id, True

    @contextlib.contextmanager
    def job_lock(self, _job_name):
        yield self.lock

    def finish_job(self, run_id, status, summary):
        self.finished.append((run_id, status, summary))

    def record_check(self, result):
        self.checks.append(result)

    def reconcile_incident(self, result):
        self.incidents.append(result)

    def enhancement_candidates(self):
        return ["worker-a", "worker-b"]

    def enqueue_enhancement(self, worker):
        if worker == "worker-b":
            return False
        self.queued.append(worker)
        return True

    def stats(self):
        return {"checks": 1, "workers": 1}

    def partner(self):
        return None

    def write_state(self, _key, _value):
        return None


def build_service(store, monkeypatch):
    monkeypatch.delenv("ECHO_GUARDIAN_TARGETS_FILE", raising=False)
    monkeypatch.delenv("ECHO_GUARDIAN_PARTNER_URL", raising=False)
    monkeypatch.setenv("ECHO_GUARDIAN_MAX_FANOUT", "2")
    monkeypatch.setenv("ECHO_GUARDIAN_PROBE_TIMEOUT", "1")
    service = guardian_core.GuardianService(store=store)
    service.targets = {
        "one": "http://127.0.0.1/one",
        "two": "http://127.0.0.1/two",
        "three": "http://127.0.0.1/three",
        "four": "http://127.0.0.1/four",
    }
    return service


def test_target_loader_rejects_non_allowlisted_hosts(tmp_path):
    target_file = tmp_path / "targets.json"
    target_file.write_text(
        json.dumps({"bad": "https://example.com/health"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not allowlisted"):
        guardian_core.load_targets(str(target_file))


def test_probe_rejects_direct_external_target():
    with pytest.raises(ValueError, match="not an allowlisted private URL"):
        guardian_core.probe_target("bad", "https://example.com/health", 1)


def test_probe_does_not_follow_redirect_outside_allowlist():
    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "https://example.com/health")
            self.end_headers()

        def log_message(self, _format, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = guardian_core.probe_target(
            "redirect", f"http://127.0.0.1:{server.server_port}/health", 1
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.status == "degraded"
    assert result.status_code == 302


def test_health_sweep_bounds_concurrency_and_records_results(monkeypatch):
    store = FakeJobStore()
    service = build_service(store, monkeypatch)
    active = 0
    maximum = 0
    guard = threading.Lock()

    def probe(name, _url, _timeout):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with guard:
            active -= 1
        return guardian_core.ProbeResult(name, "healthy", 200, 20)

    monkeypatch.setattr(guardian_core, "probe_target", probe)
    result = service.health_sweep("bounded-test")
    assert result["status"] == "completed"
    assert result["checked"] == 4
    assert result["healthy"] == 4
    assert maximum <= 2
    assert len(store.checks) == 4
    assert len(store.incidents) == 4


def test_idempotency_replays_without_side_effects(monkeypatch):
    store = FakeJobStore()
    service = build_service(store, monkeypatch)
    monkeypatch.setattr(
        guardian_core,
        "probe_target",
        lambda name, _url, _timeout: guardian_core.ProbeResult(name, "healthy", 200, 1),
    )
    first = service.health_sweep("same-request")
    checks_after_first = len(store.checks)
    second = service.health_sweep("same-request")
    assert first["status"] == "completed"
    assert second["status"] == "replayed"
    assert len(store.checks) == checks_after_first


def test_concurrent_job_skips_safely(monkeypatch):
    store = FakeJobStore(lock=False)
    service = build_service(store, monkeypatch)
    result = service.health_sweep("lock-test")
    assert result["status"] == "skipped_concurrent"
    assert store.checks == []


def test_enhancement_scan_is_approval_only(monkeypatch):
    store = FakeJobStore()
    service = build_service(store, monkeypatch)
    result = service.enhancement_scan("enhance-test")
    assert result["status"] == "completed"
    assert result["mode"] == "approval_required"
    assert result["mutations_applied"] == 0
    assert result["queued_for_approval"] == 1
    assert store.queued == ["worker-a"]


def test_dry_run_exercises_job_without_persistent_writes(monkeypatch):
    store = FakeJobStore()
    service = build_service(store, monkeypatch)
    monkeypatch.setattr(
        guardian_core,
        "probe_target",
        lambda name, _url, _timeout: guardian_core.ProbeResult(name, "healthy", 200, 1),
    )
    health = service.dry_run_job("health", "dry-health")
    enhance = service.dry_run_job("enhance", "dry-enhance")
    assert health["status"] == "completed" and health["checked"] == 4
    assert enhance["status"] == "completed" and enhance["queued_for_approval"] == 2
    assert health["dry_run"] is True and health["side_effects"] == 0
    assert store.checks == []
    assert store.incidents == []
    assert store.queued == []
    assert store.finished == []


def test_large_job_summary_remains_valid_json(monkeypatch):
    database = guardian_core.Database("unused")
    captured = {}

    def capture(_sql, params):
        captured["summary"] = params[1]
        return 1

    monkeypatch.setattr(database, "execute", capture)
    database.finish_job("run", "completed", {"job": "health", "payload": "x" * 20_000})
    parsed = json.loads(captured["summary"])
    assert parsed["truncated"] is True
    assert parsed["job"] == "health"

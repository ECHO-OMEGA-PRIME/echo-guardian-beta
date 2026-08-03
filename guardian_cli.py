"""Systemd timer entry point for Guardian Beta jobs."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from guardian_core import GuardianService


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Echo Guardian Beta job")
    parser.add_argument("job", choices=("health", "enhance", "audit", "report"))
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    service = GuardianService()
    handlers = {
        "health": service.health_sweep,
        "enhance": service.enhancement_scan,
        "audit": service.deep_audit,
        "report": service.daily_report,
    }
    result = handlers[args.job](args.idempotency_key)
    # Job results contain only bounded aggregate/status metadata.
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("status") != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())

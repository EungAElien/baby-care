from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inject a container smoke failure and prove owned resources are cleaned."
    )
    parser.add_argument("--image", required=True)
    args = parser.parse_args()

    prefix = f"baby-care-b13-cleanup-{uuid4().hex[:10]}"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verification" / "container_smoke.py"),
            "--mode",
            "unconfigured",
            "--image",
            args.image,
            "--resource-prefix",
            prefix,
            "--inject-failure-after-start",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = {}
    expected_failure = (
        completed.returncode != 0
        and result.get("started_containers", 0) >= 1
        and result.get("cleanup") is True
        and result.get("reason") == "synthetic failure injected after container start"
    )
    summary = {
        "status": "passed" if expected_failure else "failed",
        "probe": "post-start-failure-cleanup",
        "failure_was_detected": completed.returncode != 0,
        "container_was_started": result.get("started_containers", 0) >= 1,
        "owned_resources_absent": result.get("cleanup") is True,
        "note": "Injected resources use a unique prefix; sensitive values are never printed.",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if expected_failure else 1


if __name__ == "__main__":
    raise SystemExit(main())

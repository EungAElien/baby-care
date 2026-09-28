from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

REQUIRED_JOBS = ("test", "container", "local-supabase", "failure-detection")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail the stable final CI check when any required job failed, was cancelled, or did not run."
    )
    parser.add_argument("--results", help="JSON object; defaults to CI_NEEDS_JSON")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    raw = args.results or os.environ.get("CI_NEEDS_JSON")
    if not raw:
        raise SystemExit("CI result JSON is required")
    payload: dict[str, Any] = json.loads(raw)
    observed: dict[str, str] = {}
    for job in REQUIRED_JOBS:
        value = payload.get(job)
        if isinstance(value, dict):
            observed[job] = str(value.get("result", "missing"))
        elif value is None:
            observed[job] = "missing"
        else:
            observed[job] = str(value)
    failed = {name: result for name, result in observed.items() if result != "success"}
    summary = {
        "status": "failed" if failed else "passed",
        "required_jobs": observed,
        "non_success_jobs": failed,
        "rule": "Every required job must report success; skipped, cancelled, missing, and failure are failures.",
    }
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

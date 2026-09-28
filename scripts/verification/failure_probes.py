from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def run(
    command: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def result(name: str, detected: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "passed" if detected else "failed",
        "detail": detail,
    }


def generated_mismatch(python: str, temporary: Path) -> dict[str, Any]:
    expected = temporary / "expected"
    shutil.copytree(ROOT / "contracts", expected / "contracts")
    for relative in (
        Path("apps/web/src/lib/api/generated.d.ts"),
        Path("apps/web/src/lib/mock/fixtures.json"),
    ):
        target = expected / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    fixture = expected / "apps/web/src/lib/mock/fixtures.json"
    fixture.write_text(fixture.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    completed = run(
        [
            python,
            str(ROOT / "scripts/verification/check_generated.py"),
            "--python",
            python,
            "--expected-root",
            str(expected),
            "--tool-root",
            str(ROOT),
        ],
        cwd=ROOT,
    )
    return result(
        "generated-artifact-mismatch",
        completed.returncode != 0,
        "regeneration comparison rejected a modified committed fixture copy",
    )


def missing_integration_environment(python: str) -> dict[str, Any]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("BABY_CARE_TEST_")
    }
    environment["BABY_CARE_REQUIRE_INTEGRATION"] = "1"
    completed = run(
        [
            python,
            "-m",
            "pytest",
            "-q",
            "--no-cov",
            (
                "tests/integration/test_postgres_authorization.py::"
                "test_current_membership_authorization_and_connection_context_reset"
            ),
        ],
        cwd=ROOT / "apps/api",
        env=environment,
    )
    detected = (
        completed.returncode != 0
        and "required when integration tests are mandatory" in completed.stdout
    )
    return result(
        "missing-required-integration-environment",
        detected,
        "required integration mode rejected missing database configuration instead of succeeding with skips",
    )


def downstream_failure() -> dict[str, Any]:
    payload = {
        "test": {"result": "success"},
        "container": {"result": "failure"},
        "local-supabase": {"result": "success"},
        "failure-detection": {"result": "success"},
    }
    completed = run(
        [
            sys.executable,
            str(ROOT / "scripts/verification/evaluate_ci_results.py"),
            "--results",
            json.dumps(payload),
        ],
        cwd=ROOT,
    )
    return result(
        "downstream-failure-propagation",
        completed.returncode != 0,
        "the stable final gate rejected a failed required child job",
    )


def sensitive_marker(temporary: Path) -> dict[str, Any]:
    marker = "BABY_CARE_SECRET_CANARY_failure_probe_7f91c2"
    artifact = temporary / "artifact.log"
    artifact.write_text(f"normal line\n{marker}\n", encoding="utf-8")
    completed = run(
        [
            sys.executable,
            str(ROOT / "scripts/verification/scan_sensitive.py"),
            str(artifact),
        ],
        cwd=ROOT,
    )
    detected = completed.returncode != 0 and marker not in completed.stdout
    return result(
        "sensitive-artifact-marker",
        detected,
        "the artifact scanner failed without echoing the detected marker value",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prove that mandatory verification failures are detected."
    )
    parser.add_argument("--python", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="baby-care-failure-probes-") as directory:
        temporary = Path(directory)
        probes = [
            generated_mismatch(args.python, temporary),
            missing_integration_environment(args.python),
            downstream_failure(),
            sensitive_marker(temporary),
        ]
    failed = [probe for probe in probes if probe["status"] != "passed"]
    summary = {
        "status": "failed" if failed else "passed",
        "probes": probes,
        "note": "Injected mismatches are temporary and are not retained in the repository.",
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

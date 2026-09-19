from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from common import ROOT, VerificationReport, default_artifact_dir
from isolated_supabase import IsolatedSupabase


def api_python() -> str:
    candidates = [
        os.environ.get("API_PYTHON"),
        str(ROOT / "apps" / "api" / ".venv" / "bin" / "python"),
        shutil.which("python3.12"),
        sys.executable if sys.version_info[:2] == (3, 12) else None,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        executable = (
            str(Path(candidate).absolute())
            if os.path.dirname(candidate)
            else shutil.which(candidate)
        )
        if executable and Path(executable).is_file() and os.access(executable, os.X_OK):
            # Keep a virtualenv's interpreter path intact. Resolving its symlink would
            # invoke the base interpreter and silently drop the installed environment.
            return executable
    raise SystemExit(
        "Python 3.12 API environment is required. Set API_PYTHON or create apps/api/.venv."
    )


def image_tag(suite: str, artifact_dir: Path) -> str:
    suffix = re.sub(r"[^a-z0-9_.-]+", "-", artifact_dir.name.lower()).strip("-.")
    return f"baby-care-api:b13-{suite}-{suffix or os.getpid()}"


def run_quick(artifact_dir: Path) -> int:
    report = VerificationReport("quick", artifact_dir)
    try:
        python = api_python()
    except SystemExit as exc:
        report.record(
            "api-python-preflight",
            status="failed",
            reason=str(exc),
            verification_level="tooling-preflight",
        )
        return report.finish()

    checks = [
        (
            "contract-validation",
            [
                python,
                str(ROOT / "contracts" / "validate_contract.py"),
                "--output",
                str(artifact_dir / "contract-validation.json"),
            ],
            ROOT,
            "contract-structure-and-examples",
            (
                "OpenAPI structure",
                "references",
                "request/response examples",
                "negative examples",
            ),
            None,
        ),
        (
            "generated-artifacts",
            [
                python,
                str(ROOT / "scripts" / "verification" / "check_generated.py"),
                "--python",
                python,
                "--output",
                str(artifact_dir / "generated-consistency.json"),
            ],
            ROOT,
            "temporary-regeneration",
            (
                "canonical OpenAPI",
                "synthetic fixtures",
                "web generated types",
                "web fixtures",
            ),
            None,
        ),
        (
            "api-format",
            [python, "-m", "ruff", "format", "--check", "src", "tests"],
            ROOT / "apps" / "api",
            "static-analysis",
            (),
            None,
        ),
        (
            "api-lint",
            [python, "-m", "ruff", "check", "src", "tests"],
            ROOT / "apps" / "api",
            "static-analysis",
            (),
            None,
        ),
        (
            "api-mypy",
            [python, "-m", "mypy", "src"],
            ROOT / "apps" / "api",
            "static-analysis",
            (),
            None,
        ),
        (
            "api-unit",
            [
                python,
                "-m",
                "pytest",
                "--ignore=tests/integration",
                "--no-cov",
                f"--junitxml={artifact_dir / 'api-unit-junit.xml'}",
            ],
            ROOT / "apps" / "api",
            "unit-with-synthetic-data",
            ("SEC01", "SEC02", "SEC06", "contract models"),
            artifact_dir / "api-unit-junit.xml",
        ),
        (
            "api-operation-contract",
            [
                python,
                str(ROOT / "scripts" / "verification" / "check_api_contract.py"),
                "--output",
                str(artifact_dir / "api-operation-coverage.json"),
            ],
            ROOT,
            "runtime-openapi-structure",
            ("implemented operationIds", "future unimplemented operationIds"),
            None,
        ),
        (
            "web-typecheck",
            ["npm", "run", "typecheck"],
            ROOT / "apps" / "web",
            "web-static-analysis",
            (),
            None,
        ),
        (
            "web-vitest",
            [
                "npm",
                "test",
                "--",
                "--reporter=junit",
                f"--outputFile={artifact_dir / 'web-vitest-junit.xml'}",
            ],
            ROOT / "apps" / "web",
            "web-unit-with-synthetic-data",
            ("API client", "mock fixtures", "private scope"),
            artifact_dir / "web-vitest-junit.xml",
        ),
        (
            "web-lint",
            ["npm", "run", "lint"],
            ROOT / "apps" / "web",
            "web-static-analysis",
            (),
            None,
        ),
        (
            "web-production-build",
            ["npm", "run", "build"],
            ROOT / "apps" / "web",
            "web-production-build",
            (),
            None,
        ),
    ]

    for name, command, cwd, level, evidence, junit in checks:
        report.run(
            name,
            command,
            cwd=cwd,
            verification_level=level,
            evidence=evidence,
            junit_path=junit,
        )

    report.write()
    report.run(
        "artifact-sensitive-scan",
        [
            sys.executable,
            str(ROOT / "scripts" / "verification" / "scan_sensitive.py"),
            str(artifact_dir),
            "--output",
            str(artifact_dir / "sensitive-scan.json"),
        ],
        verification_level="artifact-secret-shape-scan",
        evidence=("SEC39", "SEC57", "SEC58"),
    )
    return report.finish()


def run_failure_detection(artifact_dir: Path) -> int:
    report = VerificationReport("failure-detection", artifact_dir)
    try:
        python = api_python()
    except SystemExit as exc:
        report.record(
            "api-python-preflight",
            status="failed",
            reason=str(exc),
            verification_level="tooling-preflight",
        )
        return report.finish()
    report.run(
        "expected-failure-probes",
        [
            python,
            str(ROOT / "scripts" / "verification" / "failure_probes.py"),
            "--python",
            python,
            "--output",
            str(artifact_dir / "failure-probes.json"),
        ],
        verification_level="negative-control",
        evidence=(
            "generated mismatch rejection",
            "required integration environment rejection",
            "child job failure propagation",
            "sensitive marker rejection",
        ),
    )
    report.write()
    report.run(
        "artifact-sensitive-scan",
        [
            sys.executable,
            str(ROOT / "scripts" / "verification" / "scan_sensitive.py"),
            str(artifact_dir),
            "--output",
            str(artifact_dir / "sensitive-scan.json"),
        ],
        verification_level="artifact-secret-shape-scan",
        evidence=("SEC39", "SEC57", "SEC58"),
    )
    return report.finish()


def run_container(artifact_dir: Path) -> int:
    report = VerificationReport("container", artifact_dir)
    image = image_tag("container", artifact_dir)
    built = report.run(
        "container-image-build",
        ["docker", "build", "--tag", image, str(ROOT / "apps" / "api")],
        verification_level="container-image-build",
        evidence=(
            "pinned Python base image",
            "locked API dependencies",
            "non-root runtime",
        ),
    )
    if built:
        report.run(
            "container-unconfigured-http-smoke",
            [
                sys.executable,
                str(ROOT / "scripts" / "verification" / "container_smoke.py"),
                "--mode",
                "unconfigured",
                "--image",
                image,
            ],
            verification_level="container-runtime-fail-closed",
            evidence=("liveness 200", "readiness 503", "representative API 503"),
        )
        report.run(
            "container-post-failure-cleanup-probe",
            [
                sys.executable,
                str(ROOT / "scripts" / "verification" / "check_container_cleanup.py"),
                "--image",
                image,
            ],
            verification_level="negative-control-owned-resource-cleanup",
            evidence=(
                "post-start injected failure",
                "container removal",
                "network removal",
            ),
        )
        report.run(
            "container-test-image-cleanup",
            ["docker", "image", "rm", "--force", image],
            verification_level="owned-resource-cleanup",
            evidence=("unique test image tag",),
        )
    else:
        for name, level in (
            ("container-unconfigured-http-smoke", "container-runtime-fail-closed"),
            (
                "container-post-failure-cleanup-probe",
                "negative-control-owned-resource-cleanup",
            ),
            ("container-test-image-cleanup", "owned-resource-cleanup"),
        ):
            report.record(
                name,
                status="not_run",
                reason="container image build failed",
                verification_level=level,
            )
    report.write()
    report.run(
        "artifact-sensitive-scan",
        [
            sys.executable,
            str(ROOT / "scripts" / "verification" / "scan_sensitive.py"),
            str(artifact_dir),
            "--output",
            str(artifact_dir / "sensitive-scan.json"),
        ],
        verification_level="artifact-secret-shape-scan",
        evidence=("SEC39", "SEC57", "SEC58"),
    )
    return report.finish()


def run_integration(artifact_dir: Path) -> int:
    report = VerificationReport("integration", artifact_dir)
    try:
        python = api_python()
        stack = IsolatedSupabase(ROOT)
    except (SystemExit, RuntimeError, OSError) as exc:
        report.record(
            "isolated-supabase-preflight",
            status="failed",
            reason=str(exc),
            verification_level="tooling-and-resource-preflight",
        )
        return report.finish()

    report.report["isolated_resources"] = {
        "project_id": stack.project_id,
        "ports": stack.ports,
        "preexisting_default_project_was_not_targeted": True,
    }
    environment = {
        **stack.environment(),
        "API_PYTHON": python,
        "BABY_CARE_VERIFICATION_ARTIFACT_DIR": str(artifact_dir),
    }
    started = False
    reset = False
    integration_image = image_tag("integration", artifact_dir)
    image_built = False
    try:
        started = report.run(
            "supabase-isolated-start",
            stack.cli("start"),
            env=environment,
            verification_level="isolated-local-infrastructure",
            evidence=(
                "unique project id",
                "unique ports",
                "local synthetic services only",
            ),
        )
        if started:
            reset = report.run(
                "supabase-empty-reset-migrations-seed",
                stack.cli("db", "reset", "--local"),
                env=environment,
                verification_level="local-database-migration",
                evidence=("migrations", "synthetic seed", "dedicated empty database"),
            )
        else:
            report.record(
                "supabase-empty-reset-migrations-seed",
                status="not_run",
                reason="isolated Supabase start failed",
                verification_level="local-database-migration",
            )

        dependent_checks = [
            (
                "supabase-pgtap",
                stack.cli("test", "db"),
                "local-database-pgtap",
                ("schema constraints", "RLS allow/deny", "SEC03~SEC12", "SEC18~SEC24"),
            ),
            (
                "supabase-connection-context",
                ["bash", str(ROOT / "scripts" / "test-supabase-context.sh")],
                "local-database-session-context",
                (
                    "SEC10",
                    "commit/rollback context reset",
                    "connection reuse isolation",
                ),
            ),
            (
                "api-auth-database-integration",
                ["bash", str(ROOT / "scripts" / "test-api-postgres-authorization.sh")],
                "local-auth-api-database-integration",
                (
                    "AC01",
                    "AC03",
                    "AC13~AC14",
                    "AC21~AC22",
                    "AC24",
                    "AC28",
                    "AC39~AC44",
                    "SEC01~SEC21 applicable subconditions",
                    "SEC48",
                    "SEC62",
                ),
            ),
            (
                "supabase-auth-data-storage-http",
                ["node", str(ROOT / "scripts" / "test-supabase-http.mjs")],
                "local-auth-data-api-storage-integration",
                ("SEC09", "SEC18~SEC23", "SEC27"),
            ),
        ]
        for name, command, level, evidence in dependent_checks:
            if reset:
                report.run(
                    name,
                    command,
                    env=environment,
                    verification_level=level,
                    evidence=evidence,
                    junit_path=(
                        artifact_dir / "api-integration-junit.xml"
                        if name == "api-auth-database-integration"
                        else None
                    ),
                )
            else:
                report.record(
                    name,
                    status="not_run",
                    reason="isolated migration/seed reset did not pass",
                    verification_level=level,
                    evidence=evidence,
                )
        if reset:
            image_built = report.run(
                "container-configured-image-build",
                [
                    "docker",
                    "build",
                    "--tag",
                    integration_image,
                    str(ROOT / "apps" / "api"),
                ],
                verification_level="container-image-build",
                evidence=(
                    "pinned Python base image",
                    "locked API dependencies",
                    "non-root runtime",
                ),
            )
            if image_built:
                report.run(
                    "container-configured-http-smoke",
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "verification" / "container_smoke.py"),
                        "--mode",
                        "configured",
                        "--image",
                        integration_image,
                        "--supabase-workdir",
                        str(stack.workdir),
                        "--db-container",
                        stack.db_container,
                    ],
                    env=environment,
                    verification_level="configured-container-auth-database-http",
                    evidence=(
                        "readiness 200",
                        "real local Auth JWT",
                        "authenticated list/create",
                        "broken JWKS fail-closed",
                        "synthetic secret marker not echoed or logged",
                    ),
                )
            else:
                report.record(
                    "container-configured-http-smoke",
                    status="not_run",
                    reason="configured container image build failed",
                    verification_level="configured-container-auth-database-http",
                )
        else:
            report.record(
                "container-configured-image-build",
                status="not_run",
                reason="isolated migration/seed reset did not pass",
                verification_level="container-image-build",
            )
            report.record(
                "container-configured-http-smoke",
                status="not_run",
                reason="isolated migration/seed reset did not pass",
                verification_level="configured-container-auth-database-http",
            )
    finally:
        if image_built:
            report.run(
                "container-configured-image-cleanup",
                ["docker", "image", "rm", "--force", integration_image],
                verification_level="owned-resource-cleanup",
                evidence=("unique configured test image tag",),
            )
        else:
            report.record(
                "container-configured-image-cleanup",
                status="not_run",
                reason="configured container image was not built",
                verification_level="owned-resource-cleanup",
            )
        cleanup_ok = report.run(
            "supabase-isolated-stop",
            stack.cli("stop", "--project-id", stack.project_id, "--no-backup"),
            env=environment,
            verification_level="owned-resource-cleanup",
            evidence=("containers", "network", "volumes"),
        )
        if cleanup_ok:
            report.run(
                "supabase-cleanup-confirmation",
                [
                    sys.executable,
                    str(ROOT / "scripts" / "verification" / "check_resource_absent.py"),
                    "--project-id",
                    stack.project_id,
                ],
                verification_level="owned-resource-cleanup",
                evidence=(
                    "no matching container",
                    "no matching network",
                    "no matching volume",
                ),
            )
        else:
            report.record(
                "supabase-cleanup-confirmation",
                status="failed",
                reason="Supabase stop failed; manual inspection is required",
                verification_level="owned-resource-cleanup",
            )
        stack.close()

    report.write()
    report.run(
        "artifact-sensitive-scan",
        [
            sys.executable,
            str(ROOT / "scripts" / "verification" / "scan_sensitive.py"),
            str(artifact_dir),
            "--output",
            str(artifact_dir / "sensitive-scan.json"),
        ],
        verification_level="artifact-secret-shape-scan",
        evidence=("SEC39", "SEC57", "SEC58"),
    )
    return report.finish()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run reproducible Baby Care verification suites."
    )
    parser.add_argument(
        "suite", choices=("quick", "container", "integration", "failure-detection")
    )
    parser.add_argument("--artifact-dir", type=Path)
    args = parser.parse_args()
    artifact_dir = (args.artifact_dir or default_artifact_dir(args.suite)).resolve()
    if args.suite == "quick":
        return run_quick(artifact_dir)
    if args.suite == "failure-detection":
        return run_failure_detection(artifact_dir)
    if args.suite == "container":
        return run_container(artifact_dir)
    if args.suite == "integration":
        return run_integration(artifact_dir)
    raise AssertionError(args.suite)


if __name__ == "__main__":
    raise SystemExit(main())

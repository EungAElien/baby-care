from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from common import npx_command

ROOT = Path(__file__).resolve().parents[2]
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
SENSITIVE_PATTERNS = {
    "canary": re.compile(r"BABY_CARE_SECRET_CANARY_[A-Za-z0-9_-]+"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    "supabase-secret": re.compile(r"\bsb_secret_[A-Za-z0-9_-]{8,}\b"),
    "database-password": re.compile(r"postgres(?:ql)?://[^:\s/]+:[^@\s]+@"),
    "labeled-secret": re.compile(
        r"(?i)[\"']?(?:jwt_secret|secret_key|service_role_key|database_password|password|access_token|refresh_token)[\"']?\s*[:=]\s*[\"']?[^\"',\s}]{8,}"
    ),
}


class SmokeError(RuntimeError):
    pass


def run(
    command: list[str],
    *,
    check: bool = True,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError(f"{command[0]} did not complete") from exc
    if check and completed.returncode != 0:
        label = " ".join(command[:2])
        raise SmokeError(f"{label} failed with exit code {completed.returncode}")
    return completed


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 10,
) -> tuple[int, dict[str, Any], str]:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = Request(
        url,
        method=method,
        headers=request_headers,
        data=None if body is None else json.dumps(body).encode(),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw else {}, raw
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        return exc.code, payload, raw
    except (OSError, TimeoutError, URLError) as exc:
        raise SmokeError("HTTP dependency was unreachable") from exc


def wait_for_liveness(base_url: str, *, timeout: float = 45) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, payload, _ = http_json(f"{base_url}/health/live", timeout=2)
        except SmokeError:
            time.sleep(0.25)
            continue
        if status == 200 and payload.get("status") == "ok":
            return payload
        time.sleep(0.25)
    raise SmokeError("container liveness did not become ready before the deadline")


def error_code(payload: dict[str, Any]) -> str | None:
    direct = payload.get("code")
    if direct is not None:
        return str(direct)
    error = payload.get("error")
    if isinstance(error, dict):
        value = error.get("code")
        return str(value) if value is not None else None
    return None


def expect_status(
    result: tuple[int, dict[str, Any], str],
    expected: int,
    label: str,
    *,
    expected_code: str | None = None,
) -> dict[str, Any]:
    status, payload, _ = result
    if status != expected:
        raise SmokeError(f"{label} returned {status}; expected {expected}")
    actual_code = error_code(payload)
    if expected_code is not None and actual_code != expected_code:
        raise SmokeError(f"{label} returned an unexpected error code")
    return payload


def sensitive_findings(value: str) -> list[str]:
    return sorted(name for name, pattern in SENSITIVE_PATTERNS.items() if pattern.search(value))


class DockerResources:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.network = prefix
        self.containers: list[str] = []

    def prepare(self) -> None:
        if run(["docker", "network", "inspect", self.network], check=False).returncode == 0:
            raise SmokeError("refusing to reuse an existing smoke network")
        run(["docker", "network", "create", self.network])

    def start(self, suffix: str, image: str, environment: dict[str, str]) -> tuple[str, str]:
        name = f"{self.prefix}-{suffix}"
        if run(["docker", "inspect", name], check=False).returncode == 0:
            raise SmokeError("refusing to reuse an existing smoke container")
        port = free_port()
        command = [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            self.network,
            "--add-host",
            "host.docker.internal:host-gateway",
            "--publish",
            f"127.0.0.1:{port}:8080",
        ]
        for key, value in environment.items():
            command.extend(["--env", f"{key}={value}"])
        command.append(image)
        run(command)
        self.containers.append(name)
        return name, f"http://127.0.0.1:{port}"

    def remove_container(self, name: str) -> None:
        run(["docker", "rm", "--force", name], check=False)
        if name in self.containers:
            self.containers.remove(name)

    def logs(self, name: str) -> str:
        return run(["docker", "logs", name], check=False, timeout=30).stdout

    def cleanup(self) -> bool:
        for name in reversed(self.containers):
            run(["docker", "rm", "--force", name], check=False)
        self.containers.clear()
        run(["docker", "network", "rm", self.network], check=False)
        return self.is_absent()

    def is_absent(self) -> bool:
        network_absent = (
            run(["docker", "network", "inspect", self.network], check=False).returncode != 0
        )
        container_absent = all(
            run(["docker", "inspect", f"{self.prefix}-{suffix}"], check=False).returncode != 0
            for suffix in ("unconfigured", "bad-jwks", "configured")
        )
        return network_absent and container_absent


def verify_image(image: str, canary: str) -> dict[str, Any]:
    dockerfile = (ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
    from_instructions = [
        line.strip() for line in dockerfile.splitlines() if line.strip().upper().startswith("FROM ")
    ]
    if len(from_instructions) != 2 or not re.fullmatch(
        r"FROM mwader/static-ffmpeg:7\.1\.1@sha256:[0-9a-f]{64} AS ffmpeg",
        from_instructions[0],
    ):
        raise SmokeError("the API FFmpeg stage is not pinned to the expected digest form")
    if not re.fullmatch(
        r"FROM python:3\.12\.12-slim-bookworm@sha256:[0-9a-f]{64}",
        from_instructions[1],
    ):
        raise SmokeError("the API base image is not pinned to the expected digest form")
    inspected = run(["docker", "image", "inspect", image, "--format", "{{json .Config}}"])
    config = json.loads(inspected.stdout)
    if not isinstance(config, dict) or config.get("User") != "app":
        raise SmokeError("the runtime image is not configured for the non-root app user")
    history = run(["docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", image]).stdout
    findings = sensitive_findings(f"{inspected.stdout}\n{history}")
    if canary in inspected.stdout or canary in history or findings:
        raise SmokeError("the image metadata contains a sensitive-looking value")
    return {
        "base_image_digest_pinned": True,
        "ffmpeg_image_digest_pinned": True,
        "runtime_user": "app",
        "metadata_scan": "passed",
    }


def supabase_status(workdir: Path) -> dict[str, str]:
    completed = run([npx_command(), "supabase", "--workdir", str(workdir), "status", "-o", "json"])
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeError("Supabase status was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SmokeError("Supabase status was not an object")
    return {str(key): str(value) for key, value in payload.items()}


def required(status: dict[str, str], key: str) -> str:
    value = status.get(key)
    if not value:
        raise SmokeError(f"Supabase status omitted {key}")
    return value


def create_auth_session(status: dict[str, str]) -> str:
    api_url = required(status, "API_URL").rstrip("/")
    anon_key = required(status, "ANON_KEY")
    service_key = required(status, "SERVICE_ROLE_KEY")
    email = f"b13-container-{uuid4()}@example.test"
    password = f"Synthetic-{uuid4()}-Aa1!"
    admin_headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    expect_status(
        http_json(
            f"{api_url}/auth/v1/admin/users",
            method="POST",
            headers=admin_headers,
            body={"email": email, "password": password, "email_confirm": True},
        ),
        200,
        "local synthetic Auth user creation",
    )
    payload = expect_status(
        http_json(
            f"{api_url}/auth/v1/token?grant_type=password",
            method="POST",
            headers={"apikey": anon_key},
            body={"email": email, "password": password},
        ),
        200,
        "local synthetic Auth token exchange",
    )
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise SmokeError("local Auth token exchange omitted an access token")
    return access_token


def container_environment(status: dict[str, str], *, jwks_url: str) -> dict[str, str]:
    api_url = required(status, "API_URL").rstrip("/")
    docker_api_url = api_url.replace("127.0.0.1", "host.docker.internal")
    docker_database_url = required(status, "DB_URL").replace("127.0.0.1", "host.docker.internal")
    return {
        "BABY_CARE_ENVIRONMENT": "test",
        "BABY_CARE_DATABASE_URL": docker_database_url,
        "BABY_CARE_SUPABASE_JWT_ISSUER": f"{api_url}/auth/v1",
        "BABY_CARE_SUPABASE_JWT_AUDIENCE": "authenticated",
        "BABY_CARE_SUPABASE_JWKS_URL": jwks_url,
        "BABY_CARE_SUPABASE_URL": docker_api_url,
        "BABY_CARE_SUPABASE_PUBLISHABLE_KEY": required(status, "ANON_KEY"),
        "BABY_CARE_REAUTHENTICATION_PROOF_SECRET": (
            "local-container-smoke-proof-secret-at-least-32-bytes"
        ),
        "BABY_CARE_CHILD_DATA_PRODUCTION_ENABLED": "false",
    }


def check_logs(resources: DockerResources, name: str, canary: str) -> None:
    logs = resources.logs(name)
    findings = sensitive_findings(logs)
    if canary in logs or findings:
        raise SmokeError(
            "container logs contained sensitive-looking data (values intentionally omitted)"
        )


def run_unconfigured(
    resources: DockerResources,
    image: str,
    canary: str,
    *,
    inject_failure_after_start: bool,
) -> dict[str, Any]:
    name, base_url = resources.start("unconfigured", image, {})
    live = wait_for_liveness(base_url)
    if inject_failure_after_start:
        raise SmokeError("synthetic failure injected after container start")
    ready = expect_status(http_json(f"{base_url}/health/ready"), 503, "unconfigured readiness")
    unavailable = expect_status(
        http_json(f"{base_url}/v1/babies"),
        503,
        "unconfigured representative API",
        expected_code="SERVICE_UNAVAILABLE",
    )
    check_logs(resources, name, canary)
    return {
        "liveness": live.get("status"),
        "readiness": ready.get("status"),
        "representative_api_error": error_code(unavailable),
    }


def run_configured(
    resources: DockerResources,
    image: str,
    canary: str,
    workdir: Path,
    db_container: str,
) -> dict[str, Any]:
    status = supabase_status(workdir)
    token = create_auth_session(status)
    api_url = required(status, "API_URL").rstrip("/")
    docker_api_url = api_url.replace("127.0.0.1", "host.docker.internal")
    run(
        [
            "docker",
            "exec",
            db_container,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "grant baby_app to postgres with set true",
        ]
    )
    try:
        bad_environment = container_environment(
            status, jwks_url="http://host.docker.internal:1/unavailable-jwks"
        )
        bad_name, bad_url = resources.start("bad-jwks", image, bad_environment)
        wait_for_liveness(bad_url)
        bad_ready = expect_status(
            http_json(f"{bad_url}/health/ready", timeout=12),
            503,
            "broken-JWKS readiness",
        )
        bad_api = expect_status(
            http_json(
                f"{bad_url}/v1/babies",
                headers={"Authorization": f"Bearer {token}"},
                timeout=12,
            ),
            503,
            "broken-JWKS representative API",
            expected_code="SERVICE_UNAVAILABLE",
        )
        check_logs(resources, bad_name, canary)
        resources.remove_container(bad_name)

        configured_environment = container_environment(
            status,
            jwks_url=f"{docker_api_url}/auth/v1/.well-known/jwks.json",
        )
        configured_name, configured_url = resources.start(
            "configured", image, configured_environment
        )
        wait_for_liveness(configured_url)
        ready = expect_status(
            http_json(f"{configured_url}/health/ready", timeout=15),
            200,
            "configured readiness",
        )
        list_payload = expect_status(
            http_json(
                f"{configured_url}/v1/babies",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-B13-Synthetic-Marker": canary,
                },
            ),
            200,
            "configured authenticated list",
        )
        if list_payload.get("items") != []:
            raise SmokeError("new synthetic user did not start with an empty baby list")
        request_id = str(uuid4())
        create_payload = expect_status(
            http_json(
                f"{configured_url}/v1/babies",
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": request_id,
                    "X-B13-Synthetic-Marker": canary,
                },
                body={
                    "client_request_id": request_id,
                    "alias": "B-13 container smoke",
                    "birth_date": "2026-01-01",
                    "feeding_mode": "MIXED",
                    "timezone": "Asia/Seoul",
                },
            ),
            201,
            "configured authenticated create",
        )
        if not isinstance(create_payload.get("baby"), dict):
            raise SmokeError("configured create response omitted the baby object")
        invalid_request_id = str(uuid4())
        invalid_payload = expect_status(
            http_json(
                f"{configured_url}/v1/babies",
                method="POST",
                headers={
                    "Authorization": "Bearer invalid-synthetic-token",
                    "Idempotency-Key": invalid_request_id,
                    "X-B13-Synthetic-Marker": canary,
                },
                body={
                    "client_request_id": invalid_request_id,
                    "alias": "B-13 invalid authentication",
                    "birth_date": "2026-01-01",
                    "feeding_mode": "MIXED",
                    "timezone": "Asia/Seoul",
                },
            ),
            401,
            "invalid authentication boundary",
            expected_code="INVALID_TOKEN",
        )
        if canary in json.dumps(invalid_payload):
            raise SmokeError("an error response echoed the synthetic sensitive marker")
        check_logs(resources, configured_name, canary)
        return {
            "broken_dependency_readiness": bad_ready.get("status"),
            "broken_dependency_api_error": error_code(bad_api),
            "configured_readiness": ready.get("status"),
            "authenticated_list": "passed",
            "authenticated_create": "passed",
            "invalid_request": "rejected-without-echo",
            "log_scan": "passed",
        }
    finally:
        run(
            [
                "docker",
                "exec",
                db_container,
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "grant baby_app to postgres with set false",
            ],
            check=False,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fail-closed API container HTTP smoke checks.")
    parser.add_argument("--mode", choices=("unconfigured", "configured"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--resource-prefix")
    parser.add_argument("--supabase-workdir", type=Path)
    parser.add_argument("--db-container")
    parser.add_argument("--inject-failure-after-start", action="store_true")
    args = parser.parse_args()

    prefix = args.resource_prefix or f"baby-care-b13-smoke-{uuid4().hex[:10]}"
    if not NAME_PATTERN.fullmatch(prefix):
        print(json.dumps({"status": "failed", "reason": "invalid resource prefix"}))
        return 2
    if args.mode == "configured" and (args.supabase_workdir is None or not args.db_container):
        print(json.dumps({"status": "failed", "reason": "configured inputs are required"}))
        return 2

    canary = f"BABY_CARE_SECRET_CANARY_{uuid4().hex}"
    resources = DockerResources(prefix)
    summary: dict[str, Any] = {
        "status": "failed",
        "mode": args.mode,
        "resource_prefix": prefix,
        "started_containers": 0,
        "cleanup": False,
    }
    exit_code = 1
    try:
        image_checks = verify_image(args.image, canary)
        resources.prepare()
        if args.mode == "unconfigured":
            checks = run_unconfigured(
                resources,
                args.image,
                canary,
                inject_failure_after_start=args.inject_failure_after_start,
            )
        else:
            assert args.supabase_workdir is not None
            assert args.db_container is not None
            checks = run_configured(
                resources,
                args.image,
                canary,
                args.supabase_workdir.resolve(),
                args.db_container,
            )
        summary.update({"status": "passed", "image": image_checks, "checks": checks})
        exit_code = 0
    except (SmokeError, AssertionError, json.JSONDecodeError) as exc:
        summary["reason"] = str(exc)
    finally:
        summary["started_containers"] = len(resources.containers)
        summary["cleanup"] = resources.cleanup()
        if not summary["cleanup"]:
            summary["status"] = "failed"
            summary["reason"] = "owned Docker resources were not fully removed"
            exit_code = 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

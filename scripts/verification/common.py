from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]


def npx_command() -> str:
    """Return the platform-specific npx launcher used by verification commands."""

    candidates = ("npx.cmd", "npx") if os.name == "nt" else ("npx",)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    # Keep the command visible in the verification report when the launcher is
    # unavailable; subprocess.run will record the actionable failure.
    return candidates[0]

FOLLOW_UP_ACCEPTANCE = [
    "A의 실제 브라우저·기기·두 화면 공동 인수",
    "Realtime과 SEC30·SEC31",
    "TUS·음원 처리·실제 모델·외부 LLM",
    "물리 삭제 실행기·백업 복원·키 회수 사고 리허설",
    "운영 IAM·Secret Manager·전역 할당량·부하 측정·Cloud Run 배포",
    "B-09 변경 이력 정리의 운영 스케줄 등록",
]


def _capture(command: list[str], *, cwd: Path = ROOT) -> str | None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return None
    return completed.stdout.strip()


def _git_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "commit_sha": _capture(["git", "rev-parse", "HEAD"]),
        "branch": _capture(["git", "branch", "--show-current"]),
        "github_event": os.environ.get("GITHUB_EVENT_NAME"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_sha": os.environ.get("GITHUB_SHA"),
        "pr_head_sha": None,
        "pr_base_sha": None,
        "test_merge_sha": None,
    }
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return metadata
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        metadata["event_metadata_error"] = "GitHub event payload was unreadable"
        return metadata
    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict):
        metadata["pr_head_sha"] = pull_request.get("head", {}).get("sha")
        metadata["pr_base_sha"] = pull_request.get("base", {}).get("sha")
        metadata["test_merge_sha"] = pull_request.get("merge_commit_sha")
    return metadata


def collect_metadata(suite: str) -> dict[str, Any]:
    contract_path = ROOT / "contracts" / "openapi계약.json"
    contract_version = None
    try:
        contract_version = json.loads(contract_path.read_text(encoding="utf-8"))[
            "info"
        ]["version"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        pass
    migrations = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
    return {
        "suite": suite,
        "execution_environment": "github-actions"
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else "local",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "host": platform.platform(),
        "runtimes": {
            "report_python": platform.python_version(),
            "node": _capture(["node", "--version"]),
            "npm": _capture(["npm", "--version"]),
            "docker": _capture(["docker", "--version"]),
            "supabase": _capture([npx_command(), "supabase", "--version"]),
        },
        "contract_version": contract_version,
        "latest_migration": migrations[-1].name if migrations else None,
        "source_control": _git_metadata(),
    }


class Redactor:
    _rules: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        (
            re.compile(
                r"(?i)([\"']?(?:jwt_secret|secret_key|service_role_key|database_password|password|access_token|refresh_token)[\"']?\s*[:=]\s*[\"']?)[^\"',\s}]+"
            ),
            r"\1[REDACTED]",
        ),
        (
            re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~-]+"),
            r"\1[REDACTED]",
        ),
        (
            re.compile(
                r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
            ),
            "[REDACTED_JWT]",
        ),
        (re.compile(r"\bsb_secret_[A-Za-z0-9_-]{8,}\b"), "[REDACTED_SUPABASE_SECRET]"),
        (
            re.compile(r"(postgres(?:ql)?://[^:\s/]+:)[^@\s]+(@)"),
            r"\1[REDACTED]\2",
        ),
        (
            re.compile(r"BABY_CARE_SECRET_CANARY_[A-Za-z0-9_-]+"),
            "[REDACTED_CANARY]",
        ),
    ]

    def __init__(self) -> None:
        values: list[str] = []
        sensitive_words = (
            "SECRET",
            "TOKEN",
            "PASSWORD",
            "SERVICE_ROLE_KEY",
            "DATABASE_URL",
        )
        for name, value in os.environ.items():
            if (
                any(word in name.upper() for word in sensitive_words)
                and len(value) >= 8
            ):
                values.append(value)
        self._environment_values = sorted(set(values), key=len, reverse=True)

    def __call__(self, value: str) -> str:
        sanitized = value
        for secret in self._environment_values:
            sanitized = sanitized.replace(secret, "[REDACTED_ENV]")
        for pattern, replacement in self._rules:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized


def _junit_counts(path: Path) -> dict[str, int] | None:
    if not path.exists():
        return None
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return None
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.attrib.get(key, "0"))
    return totals


def _evidence_list(evidence: Iterable[str]) -> list[str]:
    """Keep a single evidence label intact if a caller passes a bare string."""
    if isinstance(evidence, str):
        return [evidence]
    return list(evidence)


class VerificationReport:
    def __init__(self, suite: str, artifact_dir: Path) -> None:
        self.suite = suite
        self.artifact_dir = artifact_dir.resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.redact = Redactor()
        self.report: dict[str, Any] = {
            "schema_version": "1.0.0",
            "status": "running",
            "metadata": collect_metadata(suite),
            "checks": [],
            "scope_boundary": (
                "B-13 전체 완료가 아니라 현재 구현 범위의 자동 검사 기반을 판정한다. "
                "정적 계약·fixture 성공을 실제 API·RLS·모델·브라우저 인수로 집계하지 않는다."
            ),
            "follow_up_acceptance": FOLLOW_UP_ACCEPTANCE,
        }

    def run(
        self,
        name: str,
        command: list[str],
        *,
        cwd: Path = ROOT,
        env: dict[str, str] | None = None,
        evidence: Iterable[str] = (),
        verification_level: str,
        junit_path: Path | None = None,
    ) -> bool:
        log_path = (
            self.artifact_dir / f"{len(self.report['checks']) + 1:02d}-{name}.log"
        )
        started = time.monotonic()
        record: dict[str, Any] = {
            "name": name,
            "command": shlex.join(command),
            "working_directory": str(cwd.relative_to(ROOT)) if cwd != ROOT else ".",
            "required": True,
            "verification_level": verification_level,
            "evidence": _evidence_list(evidence),
            "status": "failed",
            "exit_code": None,
            "duration_seconds": None,
            "log": log_path.name,
        }
        print(f"\n==> {name}: {record['command']}", flush=True)
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=merged_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert process.stdout is not None
            with log_path.open("w", encoding="utf-8", newline="\n") as log_file:
                for line in process.stdout:
                    sanitized = self.redact(line)
                    sys.stdout.write(sanitized)
                    log_file.write(sanitized)
            exit_code = process.wait()
        except FileNotFoundError:
            message = self.redact(f"Required executable was not found: {command[0]}\n")
            print(message, end="", file=sys.stderr)
            log_path.write_text(message, encoding="utf-8")
            exit_code = 127
        record["exit_code"] = exit_code
        record["duration_seconds"] = round(time.monotonic() - started, 3)
        record["status"] = "passed" if exit_code == 0 else "failed"
        if junit_path is not None:
            record["test_counts"] = _junit_counts(junit_path)
        self.report["checks"].append(record)
        self.write()
        return exit_code == 0

    def record(
        self,
        name: str,
        *,
        status: str,
        reason: str,
        evidence: Iterable[str] = (),
        verification_level: str,
    ) -> None:
        self.report["checks"].append(
            {
                "name": name,
                "command": None,
                "required": True,
                "verification_level": verification_level,
                "evidence": _evidence_list(evidence),
                "status": status,
                "exit_code": None,
                "duration_seconds": 0,
                "reason": reason,
                "log": None,
            }
        )
        self.write()

    def write(self) -> None:
        checks = self.report["checks"]
        metadata = self.report["metadata"]
        source_control = metadata["source_control"]
        runtimes = metadata["runtimes"]
        counts = {
            "executed": sum(
                check["status"] in {"passed", "failed"} for check in checks
            ),
            "passed": sum(check["status"] == "passed" for check in checks),
            "failed": sum(check["status"] == "failed" for check in checks),
            "not_run": sum(check["status"] == "not_run" for check in checks),
        }
        self.report["counts"] = counts
        self.report["status"] = (
            "passed"
            if checks and counts["failed"] == 0 and counts["not_run"] == 0
            else "failed"
        )
        self.report["updated_at"] = datetime.now(timezone.utc).isoformat()
        result_path = self.artifact_dir / "result.json"
        result_path.write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        lines = [
            f"# {self.suite} 자동 검사 결과",
            "",
            f"- 결과: **{self.report['status']}**",
            f"- 실행 환경/시각: `{metadata['execution_environment']}` / `{metadata['started_at']}`",
            f"- checkout 커밋: `{source_control['commit_sha']}`",
            (
                f"- GitHub SHA / PR head / test merge: "
                f"`{source_control['github_sha'] or '-'}` / "
                f"`{source_control['pr_head_sha'] or '-'}` / "
                f"`{source_control['test_merge_sha'] or '-'}`"
            ),
            (
                f"- 계약 / 최신 migration: `{metadata['contract_version']}` / "
                f"`{metadata['latest_migration'] or '-'}`"
            ),
            (
                f"- 런타임: Python `{runtimes['report_python']}`, "
                f"Node `{runtimes['node'] or '-'}`, Docker `{runtimes['docker'] or '-'}`, "
                f"Supabase `{runtimes['supabase'] or '-'}`"
            ),
            f"- 실행/통과/실패/미실행: {counts['executed']}/{counts['passed']}/{counts['failed']}/{counts['not_run']}",
            "",
            "| 검사 | 결과 | 검증 수준 | AC·SEC/근거 | 증거 위치 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for check in checks:
            evidence = ", ".join(check.get("evidence", [])) or "-"
            location = check.get("log") or "result.json"
            lines.append(
                f"| {check['name']} | {check['status']} | {check['verification_level']} | "
                f"{evidence} | {location} |"
            )
        lines.extend(
            [
                "",
                "## 판정 경계",
                "",
                self.report["scope_boundary"],
                "",
                "## 후속 인수",
                "",
                *[f"- {item}" for item in FOLLOW_UP_ACCEPTANCE],
                "",
            ]
        )
        (self.artifact_dir / "summary.md").write_text(
            "\n".join(lines), encoding="utf-8", newline="\n"
        )

    def finish(self) -> int:
        self.write()
        print(f"\nVerification artifacts: {self.artifact_dir}")
        return 0 if self.report["status"] == "passed" else 1


def default_artifact_dir(suite: str) -> Path:
    configured = os.environ.get("BABY_CARE_VERIFICATION_DIR")
    base = (
        Path(configured).resolve()
        if configured
        else ROOT / ".artifacts" / "verification"
    )
    run_id = os.environ.get("GITHUB_RUN_ID")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    if run_id:
        suffix = f"{run_id}-{attempt or '1'}"
    else:
        suffix = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
        )
    return base / f"{suite}-{suffix}"

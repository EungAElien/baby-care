from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from common import ROOT
from dotenv import dotenv_values
from isolated_supabase import IsolatedSupabase

API_SOURCE = ROOT / "apps" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

import psycopg
import uvicorn
from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.main import create_app
from baby_care_api.models.normalization import (
    MODEL_ID,
    NormalizedContent,
)
from baby_care_api.services.normalization_validation import validate_normalized_content
from baby_care_api.services.normalizer import (
    NormalizerRequest,
    NormalizerResult,
    OpenAINormalizerAdapter,
)
from baby_care_api.services.provider_call_audit import (
    PersistentProviderCallAudit,
)
from pydantic import SecretStr

CASES_PATH = ROOT / "apps" / "api" / "evals" / "b07_product_smoke" / "cases.json"
DEFAULT_ARTIFACT_DIR = ROOT / ".artifacts" / "b07-terra-product-smoke"
DEFAULT_SERVER_ENV = ROOT / "apps" / "api" / ".env"
MAX_CASES = 3
MAX_PROVIDER_REQUESTS = 6
COMMON_PROVIDER_FAILURES = {
    "NORMALIZATION_CREDENTIALS_MISSING",
    "NORMALIZATION_DEPENDENCY_MISSING",
    "NORMALIZATION_TIMEOUT",
    "NORMALIZATION_INCOMPLETE",
    "NORMALIZATION_SCHEMA_INVALID",
    "NORMALIZATION_PROVIDER_ERROR",
}


class SmokeError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: dict[str, Any]


@dataclass(frozen=True)
class AuthSession:
    user_id: UUID
    session_id: UUID
    access_token: str = field(repr=False)


class DiagnosticNormalizerAdapter:
    """Keep content-free semantic issue codes around the production adapter."""

    def __init__(self, delegate: OpenAINormalizerAdapter) -> None:
        self._delegate = delegate
        self.semantic_issue_shapes: list[list[dict[str, str]]] = []

    async def normalize(self, request: NormalizerRequest) -> NormalizerResult:
        result = await self._delegate.normalize(request)
        issues = (
            []
            if result.content is None
            else validate_normalized_content(
                result.content,
                raw_text=request.raw_text,
                choices=request.choices,
                allow_user_correction=False,
                confirmation=False,
                eventless=True,
            )
        )
        self.semantic_issue_shapes.append(
            [{"field": issue.field, "code": issue.code} for issue in issues]
        )
        return result

    async def close(self) -> None:
        await self._delegate.close()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise SmokeError("local Auth returned a malformed access token")
    return json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 35,
) -> HttpResult:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = Request(
        url,
        method=method,
        headers=request_headers,
        data=None
        if body is None
        else json.dumps(body, ensure_ascii=False).encode("utf-8"),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            payload = {} if not raw else json.loads(raw)
            return HttpResult(response.status, payload)
    except HTTPError as exc:
        raw = exc.read()
        payload = {} if not raw else json.loads(raw)
        return HttpResult(exc.code, payload)
    except URLError as exc:
        raise SmokeError("HTTP request could not reach the isolated service") from exc


def _expect(result: HttpResult, status: int, purpose: str) -> dict[str, Any]:
    if result.status != status:
        code = result.body.get("code") if isinstance(result.body, dict) else None
        raise SmokeError(
            f"{purpose} returned HTTP {result.status} ({code or 'no-code'})"
        )
    return result.body


def _api_headers(
    session: AuthSession, request_id: UUID | None = None
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {session.access_token}"}
    if request_id is not None:
        headers["Idempotency-Key"] = str(request_id)
    return headers


def _required(status: dict[str, str], name: str) -> str:
    value = status.get(name)
    if not value:
        raise SmokeError(f"isolated Supabase status omitted {name}")
    return value


def _run_quiet(command: list[str], *, cwd: Path = ROOT) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise SmokeError(f"required local command failed: {command[0]} {command[-1]}")


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if (
        payload.get("frozen_before_provider_calls") is not True
        or payload.get("synthetic_only") is not True
        or not isinstance(cases, list)
        or len(cases) != MAX_CASES
    ):
        raise SmokeError(
            "live smoke fixtures must contain exactly three frozen synthetic cases"
        )
    for case in cases:
        NormalizedContent.model_validate(case["confirmed_content"])
        if "blocking_content" in case:
            NormalizedContent.model_validate(case["blocking_content"])
    return cases


def _load_key(
    server_env: Path,
) -> tuple[str | None, str | None, str | None, str | None]:
    process_key = os.environ.get("BABY_CARE_OPENAI_API_KEY")
    if process_key and process_key.strip():
        return (
            process_key,
            "BABY_CARE_OPENAI_API_KEY_PROCESS",
            os.environ.get("BABY_CARE_OPENAI_ORGANIZATION"),
            os.environ.get("BABY_CARE_OPENAI_PROJECT"),
        )
    compatibility_key = os.environ.get("OPENAI_API_KEY")
    if compatibility_key and compatibility_key.strip():
        return (
            compatibility_key,
            "OPENAI_API_KEY_PROCESS",
            os.environ.get("OPENAI_ORG_ID"),
            os.environ.get("OPENAI_PROJECT"),
        )
    if not server_env.is_file():
        return None, None, None, None
    values = dotenv_values(server_env)
    file_key = values.get("BABY_CARE_OPENAI_API_KEY")
    if isinstance(file_key, str) and file_key.strip():
        organization = values.get("BABY_CARE_OPENAI_ORGANIZATION")
        project = values.get("BABY_CARE_OPENAI_PROJECT")
        return (
            file_key,
            "BABY_CARE_OPENAI_API_KEY_SERVER_ENV_FILE",
            organization if isinstance(organization, str) else None,
            project if isinstance(project, str) else None,
        )
    raw_value = server_env.read_text(encoding="utf-8").strip()
    if raw_value and "=" not in raw_value and "\n" not in raw_value:
        return raw_value, "RAW_SINGLE_LINE_SERVER_SECRET_FILE", None, None
    return None, None, None, None


def _create_auth_session(status: dict[str, str], label: str) -> AuthSession:
    api_url = _required(status, "API_URL").rstrip("/")
    anon_key = _required(status, "ANON_KEY")
    service_key = _required(status, "SERVICE_ROLE_KEY")
    email = f"b07-live-{label}-{uuid4()}@example.test"
    password = f"Synthetic-{uuid4()}-Aa1!"
    _expect(
        _http_json(
            f"{api_url}/auth/v1/admin/users",
            method="POST",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            body={"email": email, "password": password, "email_confirm": True},
        ),
        200,
        f"create {label} local Auth user",
    )
    token = _expect(
        _http_json(
            f"{api_url}/auth/v1/token?grant_type=password",
            method="POST",
            headers={"apikey": anon_key},
            body={"email": email, "password": password},
        ),
        200,
        f"sign in {label} local Auth user",
    )
    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise SmokeError("local Auth token response omitted access_token")
    claims = _jwt_claims(access_token)
    return AuthSession(
        user_id=UUID(claims["sub"]),
        session_id=UUID(claims["session_id"]),
        access_token=access_token,
    )


class ServerHandle:
    def __init__(self, app: Any, port: int) -> None:
        self.base_url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                access_log=False,
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                result = _http_json(f"{self.base_url}/health/live", timeout=2)
                if result.status == 200:
                    return
            if not self.thread.is_alive():
                break
            time.sleep(0.1)
        raise SmokeError("isolated FastAPI HTTP server did not become live")

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise SmokeError("isolated FastAPI HTTP server did not stop")


def _create_baby(base_url: str, owner: AuthSession) -> str:
    request_id = uuid4()
    body = _expect(
        _http_json(
            f"{base_url}/v1/babies",
            method="POST",
            headers=_api_headers(owner, request_id),
            body={
                "client_request_id": str(request_id),
                "alias": "B07 Terra synthetic baby",
                "birth_date": "2026-01-01",
                "feeding_mode": "MIXED",
                "timezone": "Asia/Seoul",
            },
        ),
        201,
        "create synthetic baby",
    )
    return str(body["baby"]["baby_id"])


def _add_caregiver(database_url: str, baby_id: str, caregiver: AuthSession) -> str:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            insert into baby_data.baby_memberships (
                baby_id, user_id, role, relationship, display_name
            ) values (%s, %s, 'CAREGIVER', 'OTHER', 'B07 Terra synthetic caregiver')
            returning membership_id
            """,
            (baby_id, caregiver.user_id),
        ).fetchone()
    if row is None:
        raise SmokeError("synthetic caregiver membership was not created")
    return str(row[0])


def _create_entry(
    base_url: str,
    caregiver: AuthSession,
    baby_id: str,
    entry_fixture: dict[str, Any],
) -> dict[str, Any]:
    request_id = uuid4()
    return _expect(
        _http_json(
            f"{base_url}/v1/babies/{baby_id}/care-entries",
            method="POST",
            headers=_api_headers(caregiver, request_id),
            body={
                "client_request_id": str(request_id),
                "episode_id": None,
                **entry_fixture,
                "supersedes_entry_id": None,
                "base_record_versions": [],
            },
        ),
        201,
        "create synthetic care entry",
    )


def _normalization_projection(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "actions": [
            {
                "action_code": item["action_code"],
                "assertion": item["assertion"],
                "amount": item["amount"],
                "unit": item["unit"],
                "feeding_mode": item["feeding_mode"],
                "occurred_at_present": item["occurred_at"] is not None,
            }
            for item in result["actions"]
        ],
        "states": [item["state_codes"] for item in result["states"]],
        "outcome_count": len(result["outcomes"]),
        "unresolved_codes": [item["code"] for item in result["unresolved"]],
    }


def _model_checks(case_id: str, content: dict[str, Any] | None) -> list[dict[str, Any]]:
    if content is None:
        return [{"name": "complete_result", "passed": False}]
    actions = content["actions"]
    states = content["states"]
    unresolved = content["unresolved"]

    def action(code: str, assertion: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in actions
                if item["action_code"] == code and item["assertion"] == assertion
            ),
            None,
        )

    if case_id == "B07-LIVE-001":
        feeding = action("FEEDING", "PERFORMED")
        return [
            {
                "name": "performed_feeding_80_ml_formula",
                "passed": feeding is not None
                and feeding["amount"] == 80
                and feeding["unit"] == "ML"
                and feeding["feeding_mode"] == "FORMULA",
            },
            {
                "name": "explicit_awake_state",
                "passed": any("AWAKE" in item["state_codes"] for item in states),
            },
            {
                "name": "no_invented_feeding_actor_or_time",
                "passed": feeding is not None
                and feeding["performed_by_user_id"] is None
                and feeding["occurred_at"] is None,
            },
        ]
    if case_id == "B07-LIVE-002":
        expected = {
            ("DIAPER_CHANGE", "PERFORMED"),
            ("FEEDING", "NEGATED"),
            ("HOLDING", "PLANNED"),
            ("BURPING", "UNCERTAIN"),
        }
        actual = {(item["action_code"], item["assertion"]) for item in actions}
        return [
            {"name": "assertion_classes_preserved", "passed": expected <= actual},
            {
                "name": "only_diaper_is_performed",
                "passed": {
                    item["action_code"]
                    for item in actions
                    if item["assertion"] == "PERFORMED"
                }
                == {"DIAPER_CHANGE"},
            },
            {"name": "no_state_invented", "passed": states == []},
        ]
    if case_id == "B07-LIVE-003":
        feeding = action("FEEDING", "PERFORMED")
        return [
            {
                "name": "mixed_conflict_preserved",
                "passed": any(item["code"] == "CONFLICT" for item in unresolved),
            },
            {
                "name": "unknown_amount_not_zero",
                "passed": feeding is not None and feeding["amount"] is None,
            },
            {
                "name": "unknown_time_not_filled",
                "passed": feeding is not None and feeding["occurred_at"] is None,
            },
        ]
    raise SmokeError(f"unknown case id: {case_id}")


def _entry_counts(database_url: str, entry_id: str) -> dict[str, Any]:
    with psycopg.connect(database_url) as connection:
        care_events = connection.execute(
            "select count(*), array_agg(distinct data_origin::text) "
            "from baby_data.care_events where source_entry_id = %s",
            (entry_id,),
        ).fetchone()
        states = connection.execute(
            "select count(*), array_agg(distinct data_origin::text) "
            "from baby_data.state_observations where source_entry_id = %s",
            (entry_id,),
        ).fetchone()
        labels = connection.execute(
            "select count(*), array_agg(distinct source::text) "
            "from baby_data.label_annotations where entry_id = %s",
            (entry_id,),
        ).fetchone()
    return {
        "care_event_count": int(care_events[0]),
        "care_event_data_origins": care_events[1] or [],
        "state_observation_count": int(states[0]),
        "state_observation_data_origins": states[1] or [],
        "label_count": int(labels[0]),
        "label_sources": labels[1] or [],
    }


def _confirm(
    base_url: str,
    caregiver: AuthSession,
    entry_id: str,
    *,
    content: dict[str, Any],
    mode: str,
    run_id: str | None,
    request_id: UUID,
) -> HttpResult:
    return _http_json(
        f"{base_url}/v1/care-entries/{entry_id}/confirm",
        method="POST",
        headers=_api_headers(caregiver, request_id),
        body={
            "client_request_id": str(request_id),
            "input_revision": 1,
            "run_id": run_id,
            "normalization_mode": mode,
            "content": content,
            "base_record_versions": [],
        },
    )


def _verify_refetch(
    base_url: str,
    resources: dict[str, Any],
    owner: AuthSession,
    outsider: AuthSession,
) -> dict[str, Any]:
    checks: list[bool] = []
    origins: list[str] = []
    for resource_id in resources["care_event_ids"]:
        owner_result = _http_json(
            f"{base_url}/v1/care-events/{resource_id}", headers=_api_headers(owner)
        )
        checks.append(owner_result.status == 200)
        if owner_result.status == 200:
            origins.append(str(owner_result.body["data_origin"]))
        checks.append(
            _http_json(
                f"{base_url}/v1/care-events/{resource_id}",
                headers=_api_headers(outsider),
            ).status
            == 404
        )
    for resource_id in resources["state_observation_ids"]:
        owner_result = _http_json(
            f"{base_url}/v1/state-observations/{resource_id}",
            headers=_api_headers(owner),
        )
        checks.append(owner_result.status == 200)
        if owner_result.status == 200:
            origins.append(str(owner_result.body["data_origin"]))
        checks.append(
            _http_json(
                f"{base_url}/v1/state-observations/{resource_id}",
                headers=_api_headers(outsider),
            ).status
            == 404
        )
    return {
        "active_member_refetch_passed": all(checks[::2]),
        "nonmember_blocked": all(checks[1::2]),
        "saved_data_origins": sorted(set(origins)),
    }


def _run_case(
    *,
    base_url: str,
    database_url: str,
    baby_id: str,
    case: dict[str, Any],
    caregiver: AuthSession,
    owner: AuthSession,
    outsider: AuthSession,
) -> tuple[dict[str, Any], bool]:
    entry = _create_entry(base_url, caregiver, baby_id, case["entry"])
    entry_id = str(entry["entry_id"])
    private_before_confirmation = (
        _http_json(
            f"{base_url}/v1/care-entries/{entry_id}", headers=_api_headers(owner)
        ).status
        == 404
        and _http_json(
            f"{base_url}/v1/care-entries/{entry_id}", headers=_api_headers(outsider)
        ).status
        == 404
        and _entry_counts(database_url, entry_id)["care_event_count"] == 0
        and _entry_counts(database_url, entry_id)["state_observation_count"] == 0
    )

    normalization_id = uuid4()
    run_id = uuid4()
    post = _http_json(
        f"{base_url}/v1/care-entries/{entry_id}/normalizations",
        method="POST",
        headers=_api_headers(caregiver, normalization_id),
        body={
            "client_request_id": str(normalization_id),
            "run_id": str(run_id),
            "input_revision": 1,
        },
    )
    if post.status != 200:
        raise SmokeError(
            f"{case['case_id']} createNormalization returned HTTP {post.status}"
        )

    # Deliberately ignore the POST body and recover the same logical run by run_id.
    recovered = _expect(
        _http_json(
            f"{base_url}/v1/normalizations/{run_id}",
            headers=_api_headers(caregiver),
        ),
        200,
        f"recover {case['case_id']} normalization",
    )
    run_private = (
        _http_json(
            f"{base_url}/v1/normalizations/{run_id}", headers=_api_headers(owner)
        ).status
        == 404
        and _http_json(
            f"{base_url}/v1/normalizations/{run_id}", headers=_api_headers(outsider)
        ).status
        == 404
    )
    model_content = (
        recovered.get("result") if recovered.get("status") == "COMPLETE" else None
    )
    model_checks = _model_checks(case["case_id"], model_content)
    model_semantic_pass = bool(model_checks) and all(
        item["passed"] for item in model_checks
    )

    blocking_rejected: bool | None = None
    if "blocking_content" in case:
        blocking_id = uuid4()
        blocking_mode = "LLM" if recovered.get("status") == "COMPLETE" else "MANUAL"
        blocking = _confirm(
            base_url,
            caregiver,
            entry_id,
            content=case["blocking_content"],
            mode=blocking_mode,
            run_id=str(run_id) if blocking_mode == "LLM" else None,
            request_id=blocking_id,
        )
        blocking_rejected = (
            blocking.status == 422
            and blocking.body.get("code") == "VALIDATION_ERROR"
            and any(
                item.get("code") == "UNRESOLVED_BLOCKING"
                for item in blocking.body.get("field_errors", [])
            )
        )
        if not blocking_rejected:
            raise SmokeError("blocking mixed-input conflict was not rejected")

    use_model_original = model_semantic_pass and "blocking_content" not in case
    content = model_content if use_model_original else case["confirmed_content"]
    if content is None:
        raise SmokeError("confirmation content was unexpectedly absent")
    mode = "LLM" if recovered.get("status") == "COMPLETE" else "MANUAL"
    confirmation_source = (
        "MODEL_ORIGINAL"
        if use_model_original
        else "TEST_USER_EDIT"
        if mode == "LLM"
        else "MANUAL_AFTER_MODEL_FAILURE"
    )
    confirm_id = uuid4()
    confirmed_result = _confirm(
        base_url,
        caregiver,
        entry_id,
        content=content,
        mode=mode,
        run_id=str(run_id) if mode == "LLM" else None,
        request_id=confirm_id,
    )
    resources = _expect(confirmed_result, 200, f"confirm {case['case_id']}")
    counts_after = _entry_counts(database_url, entry_id)
    replay = _confirm(
        base_url,
        caregiver,
        entry_id,
        content=content,
        mode=mode,
        run_id=str(run_id) if mode == "LLM" else None,
        request_id=confirm_id,
    )
    counts_after_replay = _entry_counts(database_url, entry_id)
    refetch = _verify_refetch(base_url, resources, owner, outsider)
    persistence_pass = (
        counts_after["care_event_count"]
        == case["expected"]["persisted_care_event_count"]
        and counts_after["state_observation_count"]
        == case["expected"]["persisted_state_observation_count"]
        and counts_after == counts_after_replay
        and replay.status == 200
        and replay.body == resources
        and set(counts_after["care_event_data_origins"])
        | set(counts_after["state_observation_data_origins"])
        <= {"USER"}
        and refetch["active_member_refetch_passed"]
        and refetch["nonmember_blocked"]
    )

    failure_code = None
    if isinstance(recovered.get("failure"), dict):
        failure_code = recovered["failure"].get("code")
    stop_for_common_failure = recovered.get("status") == "FAILED" and failure_code in (
        COMMON_PROVIDER_FAILURES
    )
    return (
        {
            "case_id": case["case_id"],
            "synthetic_test": True,
            "entry_data_origin": entry["data_origin"],
            "post_response_discarded_before_get": True,
            "normalization": {
                "status": recovered.get("status"),
                "provider_call_executed": recovered.get("provider_call_executed"),
                "provider": recovered.get("provider"),
                "model": recovered.get("model"),
                "prompt_version": recovered.get("prompt_version"),
                "schema_version": recovered.get("schema_version"),
                "failure_code": failure_code,
                "projection": _normalization_projection(model_content),
                "model_checks": model_checks,
                "model_semantic_pass": model_semantic_pass,
            },
            "privacy_before_confirmation": private_before_confirmation and run_private,
            "blocking_conflict_rejected": blocking_rejected,
            "confirmation_source": confirmation_source,
            "confirmation_storage": {
                "passed": persistence_pass,
                "resource_counts": counts_after,
                "same_key_replay_returned_same_ids": replay.body == resources,
                **refetch,
            },
        },
        stop_for_common_failure,
    )


def _eventless_outcome_probe(
    base_url: str,
    database_url: str,
    baby_id: str,
    caregiver: AuthSession,
) -> dict[str, Any]:
    entry = _create_entry(
        base_url,
        caregiver,
        baby_id,
        {
            "input_mode": "TEXT",
            "raw_text": "안아 주니 진정됐어요.",
            "choices": [],
            "occurred_at": None,
            "time_precision": "UNKNOWN",
        },
    )
    entry_id = str(entry["entry_id"])
    evidence = [
        {
            "source": "USER_CORRECTION",
            "choice_id": None,
            "span_start": None,
            "span_end": None,
            "quote": "합성 사용자가 사건 없는 결과를 제출",
        }
    ]
    content = {
        "actions": [
            {
                "action_ref": "a1",
                "action_code": "HOLDING",
                "assertion": "PERFORMED",
                "performed_by_user_id": None,
                "occurred_at": None,
                "relative_time": None,
                "time_precision": "UNKNOWN",
                "sequence": 1,
                "amount": None,
                "unit": None,
                "feeding_mode": None,
                "evidence": evidence,
            }
        ],
        "states": [],
        "outcomes": [
            {
                "response_code": "CALMED",
                "observed_at": None,
                "time_precision": "UNKNOWN",
                "linked_action_refs": ["a1"],
                "attribution": "SINGLE",
                "evidence": evidence,
            }
        ],
        "caregiver_interpretations": [],
        "unresolved": [],
    }
    request_id = uuid4()
    result = _confirm(
        base_url,
        caregiver,
        entry_id,
        content=content,
        mode="MANUAL",
        run_id=None,
        request_id=request_id,
    )
    counts = _entry_counts(database_url, entry_id)
    return {
        "external_provider_requests": 0,
        "http_status": result.status,
        "error_code": result.body.get("code"),
        "event_required_reported": any(
            item.get("code") == "EVENT_REQUIRED"
            for item in result.body.get("field_errors", [])
        ),
        "no_resources_persisted": counts["care_event_count"] == 0
        and counts["state_observation_count"] == 0
        and counts["label_count"] == 0,
    }


def _revoke_and_check(
    base_url: str,
    database_url: str,
    baby_id: str,
    membership_id: str,
    caregiver: AuthSession,
    confirmed_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            update baby_data.baby_memberships
               set status = 'REVOKED', version = version + 1
             where membership_id = %s and baby_id = %s
            """,
            (membership_id, baby_id),
        )
    statuses = [
        _http_json(
            f"{base_url}/v1/babies/{baby_id}/care-entries",
            headers=_api_headers(caregiver),
        ).status
    ]
    for case in confirmed_cases:
        for resource_id in case["resources"]["care_event_ids"]:
            statuses.append(
                _http_json(
                    f"{base_url}/v1/care-events/{resource_id}",
                    headers=_api_headers(caregiver),
                ).status
            )
        for resource_id in case["resources"]["state_observation_ids"]:
            statuses.append(
                _http_json(
                    f"{base_url}/v1/state-observations/{resource_id}",
                    headers=_api_headers(caregiver),
                ).status
            )
    return {
        "statuses": statuses,
        "all_blocked_as_not_found": all(item == 404 for item in statuses),
    }


def _write_report(report: dict[str, Any], artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result_path = artifact_dir / "result.json"
    result_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result_path.chmod(0o600)
    verdicts = report.get("verdicts", {})
    lines = [
        "# B-07 actual Terra product API smoke",
        "",
        f"- Decision: `{report.get('decision')}`",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Provider requests used: `{report.get('provider_requests_used', 0)}` / `{MAX_PROVIDER_REQUESTS}`",
        f"- Connection: `{verdicts.get('connection', 'NOT_RUN')}`",
        f"- Model semantics: `{verdicts.get('model_semantics', 'NOT_RUN')}`",
        f"- Confirmation, authorization, and recovery: `{verdicts.get('confirmation_authorization_recovery', 'NOT_RUN')}`",
        "",
        "Synthetic fixtures only. No real caregiver, baby, audio, or production database data was used.",
        "The API key, JWTs, authorization headers, raw provider response, and full prompts are not stored.",
    ]
    if report.get("decision") == "NOT_RUN_MISSING_API_KEY":
        lines.extend(
            [
                "",
                "Required server setting: `BABY_CARE_OPENAI_API_KEY` in `apps/api/.env` or the process environment.",
            ]
        )
    summary_path = artifact_dir / "summary.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary_path.chmod(0o600)


def _base_report(key_source: str | None) -> dict[str, Any]:
    return {
        "schema_version": "baby-care.b07-terra-product-smoke.v1",
        "generated_at": _timestamp(),
        "synthetic_only": True,
        "requested_model": MODEL_ID,
        "key_source": key_source,
        "limits": {
            "max_cases": MAX_CASES,
            "max_provider_requests_including_retries": MAX_PROVIDER_REQUESTS,
            "product_adapter_max_attempts_per_case": 2,
        },
        "cases": [],
        "provider_requests_used": 0,
        "verdicts": {
            "connection": "NOT_RUN",
            "model_semantics": "NOT_RUN",
            "confirmation_authorization_recovery": "NOT_RUN",
            "substitute_failure_paths": "SEPARATE_REGRESSION_REQUIRED",
        },
    }


def _load_resumable_cases(
    artifact_dir: Path, fixture_ids: set[str]
) -> list[dict[str, Any]]:
    result_path = artifact_dir / "result.json"
    if not result_path.is_file():
        return []
    try:
        prior = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if (
        prior.get("schema_version") != "baby-care.b07-terra-product-smoke.v1"
        or prior.get("requested_model") != MODEL_ID
    ):
        return []
    resumable: list[dict[str, Any]] = []
    for case in prior.get("cases", []):
        if (
            isinstance(case, dict)
            and case.get("case_id") in fixture_ids
            and case.get("normalization", {}).get("model_semantic_pass") is True
            and case.get("confirmation_storage", {}).get("passed") is True
        ):
            resumable.append(case)
    return resumable


def run(args: argparse.Namespace) -> int:
    cases = _load_cases()
    key, key_source, organization, project = _load_key(args.server_env)
    report = _base_report(key_source)
    if (
        not args.allow_provider_calls
        or os.environ.get("BABY_CARE_B07_LIVE_SMOKE") != "1"
    ):
        report["decision"] = "NOT_RUN_EXPLICIT_OPT_IN_REQUIRED"
        _write_report(report, args.artifact_dir)
        return 2
    if key is None:
        report["decision"] = "NOT_RUN_MISSING_API_KEY"
        report["required_setting"] = "BABY_CARE_OPENAI_API_KEY"
        report["checked_server_env"] = str(args.server_env.relative_to(ROOT))
        _write_report(report, args.artifact_dir)
        return 2

    budget = PersistentProviderCallAudit(
        args.artifact_dir / "provider-call-budget.json",
        max_requests=MAX_PROVIDER_REQUESTS,
    )
    before_budget = budget.snapshot()["provider_request_count"]
    if before_budget >= MAX_PROVIDER_REQUESTS:
        report["decision"] = "NOT_RUN_PROVIDER_REQUEST_BUDGET_EXHAUSTED"
        report["provider_requests_used"] = before_budget
        _write_report(report, args.artifact_dir)
        return 2

    if args.resume_successful_cases:
        resumed_cases = _load_resumable_cases(
            args.artifact_dir, {case["case_id"] for case in cases}
        )
        report["cases"].extend(resumed_cases)
        report["resumed_case_ids"] = [case["case_id"] for case in resumed_cases]
    resumed_case_ids = {case["case_id"] for case in report["cases"]}

    stack = IsolatedSupabase(ROOT)
    server: ServerHandle | None = None
    grant_changed = False
    cleanup = {
        "isolated_server_stopped": False,
        "isolated_supabase_stopped": False,
        "owned_resources_only": True,
    }
    confirmed_cases: list[dict[str, Any]] = []
    try:
        _run_quiet(stack.cli("start"))
        _run_quiet(stack.cli("db", "reset", "--local"))
        status = stack.status()
        database_url = _required(status, "DB_URL")
        api_url = _required(status, "API_URL").rstrip("/")
        _run_quiet(
            [
                "docker",
                "exec",
                stack.db_container,
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
        grant_changed = True

        owner = _create_auth_session(status, "owner")
        caregiver = _create_auth_session(status, "caregiver")
        outsider = _create_auth_session(status, "outsider")
        settings = Settings(
            environment=RuntimeEnvironment.TEST,
            log_level="WARNING",
            host="127.0.0.1",
            port=_free_port(),
            database_url=SecretStr(database_url),
            supabase_jwt_issuer=f"{api_url}/auth/v1",
            supabase_jwt_audience="authenticated",
            supabase_jwt_algorithms="ES256,RS256",
            supabase_jwks_url=f"{api_url}/auth/v1/.well-known/jwks.json",
            supabase_url=api_url,
            supabase_publishable_key=SecretStr(_required(status, "ANON_KEY")),
            reauthentication_proof_secret=SecretStr(
                "b07-live-smoke-proof-secret-at-least-32-bytes"
            ),
            external_normalization_enabled=True,
            openai_api_key=SecretStr(key),
            openai_organization=organization,
            openai_project=project,
            normalization_timeout_seconds=20,
            normalization_lease_seconds=30,
        )
        adapter = DiagnosticNormalizerAdapter(
            OpenAINormalizerAdapter(
                api_key=key,
                organization=organization,
                project=project,
                timeout_seconds=20,
                request_observer=budget,
            )
        )
        app = create_app(settings=settings, normalizer_adapter=adapter)
        server = ServerHandle(app, settings.port)
        server.start()
        capabilities = _expect(
            _http_json(f"{server.base_url}/v1/capabilities"),
            200,
            "getCapabilities",
        )
        if not capabilities.get("normalizer_available"):
            raise SmokeError(
                "normalizer capability was unavailable in the isolated live process"
            )

        baby_id = _create_baby(server.base_url, owner)
        membership_id = _add_caregiver(database_url, baby_id, caregiver)
        initial_changes = _expect(
            _http_json(
                f"{server.base_url}/v1/babies/{baby_id}/changes?since_revision=0",
                headers=_api_headers(owner),
            ),
            200,
            "initial change boundary",
        )
        initial_revision = int(initial_changes["current_revision"])

        for case in cases:
            if case["case_id"] in resumed_case_ids:
                continue
            result, stop = _run_case(
                base_url=server.base_url,
                database_url=database_url,
                baby_id=baby_id,
                case=case,
                caregiver=caregiver,
                owner=owner,
                outsider=outsider,
            )
            result["normalization"]["pre_finalize_semantic_issues"] = (
                adapter.semantic_issue_shapes[-1]
            )
            report["cases"].append(result)
            if stop:
                report["stopped_after_common_provider_failure"] = case["case_id"]
                break

        # Rebuild resource IDs from DB-owned confirmed entry payloads without exposing content.
        with psycopg.connect(database_url) as connection:
            rows = connection.execute(
                """
                select confirmed_resources
                  from baby_data.raw_care_entries
                 where baby_id = %s and status = 'CONFIRMED'
                 order by recorded_at
                """,
                (baby_id,),
            ).fetchall()
        confirmed_cases = [
            {"resources": row[0]}
            for row in rows
            if isinstance(row[0], dict)
            and (row[0].get("care_event_ids") or row[0].get("state_observation_ids"))
        ]
        outcome_probe = _eventless_outcome_probe(
            server.base_url, database_url, baby_id, caregiver
        )
        changes = _expect(
            _http_json(
                f"{server.base_url}/v1/babies/{baby_id}/changes?since_revision={initial_revision}",
                headers=_api_headers(owner),
            ),
            200,
            "confirmed shared changes",
        )
        feed_serialized = json.dumps(changes, ensure_ascii=False)
        raw_text_absent = all(
            case["entry"]["raw_text"] not in feed_serialized for case in cases
        )
        change_types = sorted({item["resource_type"] for item in changes["changes"]})
        revocation = _revoke_and_check(
            server.base_url,
            database_url,
            baby_id,
            membership_id,
            caregiver,
            confirmed_cases,
        )

        session_events = budget.session_events()
        report["provider_call_metadata"] = session_events
        report["provider_requests_used"] = budget.snapshot()["provider_request_count"]
        report["actual_provider_requests_this_execution"] = len(session_events)
        report["eventless_outcome_negative_probe"] = outcome_probe
        report["shared_change_recovery"] = {
            "change_types": change_types,
            "raw_text_absent": raw_text_absent,
            "normalized_content_absent": "actions" not in feed_serialized,
            "resync_required": changes["resync_required"],
        }
        report["membership_revocation"] = revocation
        connection_pass = (
            len(report["cases"]) > 0
            and all(
                case["normalization"]["provider_call_executed"]
                for case in report["cases"]
            )
            and all(
                event["record"] is not None
                and event["record"]["response_model"] in {None, MODEL_ID}
                for event in session_events
            )
        )
        model_semantics = (
            "PASS"
            if len(report["cases"]) == MAX_CASES
            and all(
                case["normalization"]["model_semantic_pass"] for case in report["cases"]
            )
            else "FAIL"
        )
        product_flow_pass = (
            len(report["cases"]) == MAX_CASES
            and all(case["privacy_before_confirmation"] for case in report["cases"])
            and all(case["confirmation_storage"]["passed"] for case in report["cases"])
            and outcome_probe["event_required_reported"]
            and outcome_probe["no_resources_persisted"]
            and raw_text_absent
            and revocation["all_blocked_as_not_found"]
        )
        report["verdicts"] = {
            "connection": "PASS" if connection_pass else "FAIL",
            "model_semantics": model_semantics,
            "confirmation_authorization_recovery": "PASS"
            if product_flow_pass
            else "FAIL",
            "substitute_failure_paths": "SEPARATE_REGRESSION_REQUIRED",
        }
        report["decision"] = (
            "PRODUCT_FLOW_PASS_MODEL_REVIEW_REQUIRED"
            if connection_pass and product_flow_pass and model_semantics != "PASS"
            else "PASS"
            if connection_pass and product_flow_pass and model_semantics == "PASS"
            else "NOT_PASSED"
        )
    except Exception as exc:  # noqa: BLE001 - persist a redacted report for any harness failure.
        report["decision"] = "NOT_PASSED"
        report["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        if "budget" in locals():
            report["provider_requests_used"] = budget.snapshot()[
                "provider_request_count"
            ]
            report["actual_provider_requests_this_execution"] = len(
                budget.session_events()
            )
            report["provider_call_metadata"] = budget.session_events()
    finally:
        if server is not None:
            try:
                server.close()
                cleanup["isolated_server_stopped"] = True
            except SmokeError:
                pass
        if grant_changed:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    stack.db_container,
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "-c",
                    "grant baby_app to postgres with set false",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        stopped = subprocess.run(
            stack.cli("stop", "--project-id", stack.project_id, "--no-backup"),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cleanup["isolated_supabase_stopped"] = stopped.returncode == 0
        stack.close()
        report["cleanup"] = cleanup
        report["generated_at"] = _timestamp()
        _write_report(report, args.artifact_dir)
    return (
        0
        if report.get("decision") in {"PASS", "PRODUCT_FLOW_PASS_MODEL_REVIEW_REQUIRED"}
        else 1
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the opt-in B-07 FastAPI to real Terra product smoke."
    )
    parser.add_argument("--allow-provider-calls", action="store_true")
    parser.add_argument("--resume-successful-cases", action="store_true")
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--server-env", type=Path, default=DEFAULT_SERVER_ENV)
    args = parser.parse_args()
    args.artifact_dir = args.artifact_dir.resolve()
    args.server_env = args.server_env.resolve()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

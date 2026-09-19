from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from baby_care_api.core.errors import ERROR_STATUS
from baby_care_api.models.b04 import (
    CreateAction,
    CreateBaby,
    CreateCareEntry,
    CreateInvite,
    PatchBaby,
)
from baby_care_api.models.care_events import CareEvent, CreateCareEvent
from baby_care_api.models.errors import ApiError, ErrorCode

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "openapi계약.json"
FIXTURE_PATH = REPOSITORY_ROOT / "contracts" / "목 응답과 시험 사용자 배치.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


CONTRACT = _load_json(CONTRACT_PATH)
FIXTURES = _load_json(FIXTURE_PATH)
SCHEMA_REGISTRY = Registry().with_resource(
    "urn:baby-care:openapi",
    Resource(contents=CONTRACT, specification=DRAFT202012),
)


def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"urn:baby-care:openapi#/components/schemas/{schema_name}"},
        registry=SCHEMA_REGISTRY,
        format_checker=FormatChecker(),
    )


def _scenario(name: str) -> dict[str, Any]:
    return next(item for item in FIXTURES["scenarios"] if item["name"] == name)


def test_first_shared_record_fixture_round_trips_through_contract_models() -> None:
    scenario = _scenario("care_event_saved")

    request = CreateCareEvent.model_validate(scenario["request"]["body"])
    response = CareEvent.model_validate(scenario["response"]["body"])

    request_json = request.model_dump(mode="json")
    response_json = response.model_dump(mode="json")
    _validator("CreateCareEvent").validate(request_json)
    _validator("CareEvent").validate(response_json)
    assert request_json == scenario["request"]["body"]
    assert response_json == scenario["response"]["body"]


@pytest.mark.parametrize(
    ("mutation", "expected_field"),
    [
        (lambda body: body.update({"created_by_user_id": str(UUID(int=1))}), None),
        (lambda body: body.pop("client_request_id"), "client_request_id"),
        (lambda body: body["event"].update({"type": "UNKNOWN"}), "event"),
        (lambda body: body.update({"client_request_id": None}), "client_request_id"),
    ],
)
def test_create_care_event_rejects_extra_missing_invalid_enum_and_non_nullable_values(
    mutation: Any,
    expected_field: str | None,
) -> None:
    body = deepcopy(_scenario("care_event_saved")["request"]["body"])
    mutation(body)

    with pytest.raises(ValidationError) as caught:
        CreateCareEvent.model_validate(body)

    if expected_field is not None:
        assert expected_field in str(caught.value)


def test_nullable_fields_remain_distinct_from_missing_required_fields() -> None:
    body = deepcopy(_scenario("care_event_saved")["request"]["body"])
    body["event"]["occurred_at"] = None
    body["event"]["payload"]["amount_ml"] = None
    accepted = CreateCareEvent.model_validate(body)
    assert accepted.event.occurred_at is None

    del body["event"]["occurred_at"]
    with pytest.raises(ValidationError):
        CreateCareEvent.model_validate(body)


def test_sleep_requires_non_null_time_and_exact_precision() -> None:
    body = deepcopy(_scenario("care_event_saved")["request"]["body"])
    body["event"] = {
        "type": "SLEEP",
        "occurred_at": None,
        "ended_at": None,
        "time_precision": "UNKNOWN",
        "payload": {},
    }

    with pytest.raises(ValidationError):
        CreateCareEvent.model_validate(body)


def test_error_enum_and_null_shape_match_canonical_contract() -> None:
    assert {code.value for code in ErrorCode} == set(
        CONTRACT["components"]["schemas"]["ErrorCode"]["enum"]
    )
    payload = ApiError(
        code=ErrorCode.SERVICE_UNAVAILABLE,
        message="The required service is not available.",
        retryable=True,
        request_id=UUID("10000000-0000-4000-8000-000000000900"),
        field_errors=[],
        details={
            "current_version": None,
            "current_resource": None,
            "resource_type": None,
            "existing_analysis_id": None,
            "existing_run_id": None,
            "existing_session_id": None,
            "deletion_job_id": None,
            "status_url": None,
            "retry_after_seconds": None,
        },
    ).model_dump(mode="json")

    _validator("ApiError").validate(payload)
    assert all(value is None for value in payload["details"].values())


def test_error_status_mapping_matches_canonical_contract() -> None:
    assert {code.value: int(status) for code, status in ERROR_STATUS.items()} == CONTRACT[
        "x-error-status"
    ]


def test_version_conflict_accepts_only_typed_care_event_resource() -> None:
    payload = _scenario("edit_conflict")["response"]["body"]

    error = ApiError.model_validate(payload)

    assert error.code is ErrorCode.VERSION_CONFLICT
    assert isinstance(error.details.current_resource, CareEvent)
    _validator("ApiError").validate(error.model_dump(mode="json"))


def test_contract_prefix_is_v1() -> None:
    assert CONTRACT["servers"][0]["url"] == "/v1"


def test_baby_inputs_reject_future_birth_dates_invalid_timezones_and_empty_patches() -> None:
    common = {
        "client_request_id": "10000000-0000-4000-8000-000000000807",
        "alias": "Synthetic baby",
        "birth_date": date.today() + timedelta(days=1),
        "feeding_mode": "MIXED",
        "timezone": "Asia/Seoul",
    }
    with pytest.raises(ValidationError, match="birth_date cannot be in the future"):
        CreateBaby.model_validate(common)
    with pytest.raises(ValidationError, match="timezone must be an IANA time zone"):
        CreateBaby.model_validate(
            {**common, "birth_date": date.today(), "timezone": "Not/A-Timezone"}
        )
    with pytest.raises(ValidationError, match="at least one baby field"):
        PatchBaby.model_validate({"client_request_id": common["client_request_id"], "version": 1})
    with pytest.raises(ValidationError, match="birth_date cannot be in the future"):
        PatchBaby.model_validate(
            {
                "client_request_id": common["client_request_id"],
                "version": 1,
                "birth_date": date.today() + timedelta(days=1),
            }
        )
    with pytest.raises(ValidationError, match="timezone must be an IANA time zone"):
        PatchBaby.model_validate(
            {
                "client_request_id": common["client_request_id"],
                "version": 1,
                "timezone": "Not/A-Timezone",
            }
        )


def test_invite_email_is_normalized_and_invalid_shape_is_rejected() -> None:
    request_id = "10000000-0000-4000-8000-000000000807"
    assert (
        CreateInvite.model_validate(
            {"client_request_id": request_id, "email": " Caregiver@Example.Test "}
        ).email
        == "caregiver@example.test"
    )

    with pytest.raises(ValidationError, match="email must be valid"):
        CreateInvite.model_validate({"client_request_id": request_id, "email": "invalid"})


@pytest.mark.parametrize(
    ("input_mode", "raw_text", "choices", "message"),
    [
        ("CHOICE", "unexpected text", [], "CHOICE requires choices"),
        ("TEXT", " ", [], "TEXT requires raw_text"),
        ("MIXED", "mixed text", [], "MIXED requires raw_text and choices"),
    ],
)
def test_care_entry_input_modes_enforce_text_and_choice_shape(
    input_mode: str,
    raw_text: str | None,
    choices: list[dict[str, str | None]],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        CreateCareEntry.model_validate(
            {
                "client_request_id": "10000000-0000-4000-8000-000000000807",
                "episode_id": None,
                "supersedes_entry_id": None,
                "input_mode": input_mode,
                "raw_text": raw_text,
                "choices": choices,
                "occurred_at": None,
                "time_precision": "UNKNOWN",
                "base_record_versions": [],
            }
        )


def test_action_requires_exactly_one_event_source() -> None:
    base = {
        "client_request_id": "10000000-0000-4000-8000-000000000807",
        "recommendation_id": None,
        "performed_by_user_id": None,
        "sequence": 1,
    }
    with pytest.raises(ValidationError, match="exactly one"):
        CreateAction.model_validate({**base, "care_event_id": None, "new_care_event": None})
    with pytest.raises(ValidationError, match="exactly one"):
        CreateAction.model_validate(
            {
                **base,
                "care_event_id": "10000000-0000-4000-8000-000000000101",
                "new_care_event": {
                    "type": "SOOTHE",
                    "occurred_at": None,
                    "ended_at": None,
                    "time_precision": "UNKNOWN",
                    "payload": {"action_kind": "HOLDING"},
                },
            }
        )


@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "FEEDING",
            "occurred_at": (datetime.now(UTC) + timedelta(minutes=6)).isoformat(),
            "ended_at": None,
            "time_precision": "EXACT",
            "payload": {"mode": "FORMULA", "amount_ml": 10, "duration_minutes": None},
        },
        {
            "type": "FEEDING",
            "occurred_at": datetime.now(UTC).isoformat(),
            "ended_at": (datetime.now(UTC) + timedelta(minutes=6)).isoformat(),
            "time_precision": "EXACT",
            "payload": {"mode": "FORMULA", "amount_ml": 10, "duration_minutes": None},
        },
        {
            "type": "FEEDING",
            "occurred_at": datetime.now(UTC).isoformat(),
            "ended_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "time_precision": "EXACT",
            "payload": {"mode": "FORMULA", "amount_ml": 10, "duration_minutes": None},
        },
    ],
)
def test_care_event_times_reject_future_and_reverse_ranges(event: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CreateCareEvent.model_validate(
            {
                "client_request_id": "10000000-0000-4000-8000-000000000807",
                "event": event,
            }
        )

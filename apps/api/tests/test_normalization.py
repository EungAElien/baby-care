from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

import baby_care_api.main as main_module
import baby_care_api.services.normalizer as normalizer_module
from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.main import create_app
from baby_care_api.models.b04 import Choice
from baby_care_api.models.normalization import (
    ConfirmCareEntry,
    NormalizedContent,
)
from baby_care_api.services.normalization_validation import (
    validate_normalized_content,
    visual_state_code,
)
from baby_care_api.services.normalizer import (
    MODEL_ID,
    NormalizerRequest,
    OpenAINormalizerAdapter,
)
from baby_care_api.services.provider_errors import classify_provider_error


def _content(raw_text: str = "🍼 분유 80mL 먹였어요") -> NormalizedContent:
    return NormalizedContent.model_validate(
        {
            "actions": [
                {
                    "action_ref": "a1",
                    "action_code": "FEEDING",
                    "assertion": "PERFORMED",
                    "performed_by_user_id": None,
                    "occurred_at": None,
                    "relative_time": None,
                    "time_precision": "UNKNOWN",
                    "sequence": 1,
                    "amount": 80,
                    "unit": "ML",
                    "feeding_mode": "FORMULA",
                    "evidence": [
                        {
                            "source": "TEXT",
                            "choice_id": None,
                            "span_start": 0,
                            "span_end": len(raw_text),
                            "quote": raw_text,
                        }
                    ],
                }
            ],
            "states": [],
            "outcomes": [],
            "caregiver_interpretations": [],
            "unresolved": [
                {
                    "field": "actions.a1.occurred_at",
                    "code": "UNKNOWN_TIME",
                    "message": "시각을 모름으로 확인해 주세요.",
                }
            ],
        }
    )


def test_unicode_evidence_and_unknown_time_are_confirmable() -> None:
    raw = "🍼 분유 80mL 먹였어요"
    issues = validate_normalized_content(
        _content(raw),
        raw_text=raw,
        choices=[],
        allow_user_correction=True,
        confirmation=True,
        eventless=True,
    )

    assert issues == []


def test_unicode_offsets_are_code_points_not_utf16_units() -> None:
    raw = "🍼수유"
    candidate = _content(raw).model_dump(mode="json")
    candidate["actions"][0]["evidence"][0]["span_start"] = 1
    candidate["actions"][0]["evidence"][0]["quote"] = raw

    issues = validate_normalized_content(
        NormalizedContent.model_validate(candidate),
        raw_text=raw,
        choices=[],
        allow_user_correction=False,
        confirmation=False,
        eventless=True,
    )

    assert {issue.code for issue in issues} == {"EVIDENCE_TEXT_MISMATCH"}


def test_choice_semantics_and_rule_evidence_are_grounded() -> None:
    choice = Choice(
        choice_id="feeding",
        kind="ACTION",
        code="FEEDING",
        assertion="PLANNED",
    )
    payload = _content().model_dump(mode="json")
    payload["actions"][0].update(
        {
            "assertion": "NEGATED",
            "amount": None,
            "unit": None,
            "feeding_mode": None,
            "evidence": [
                {
                    "source": "CHOICE",
                    "choice_id": "feeding",
                    "span_start": None,
                    "span_end": None,
                    "quote": None,
                }
            ],
        }
    )

    issues = validate_normalized_content(
        NormalizedContent.model_validate(payload),
        raw_text=None,
        choices=[choice],
        allow_user_correction=False,
        confirmation=True,
        eventless=True,
    )

    assert {issue.code for issue in issues} == {"CHOICE_SEMANTIC_MISMATCH"}


def test_eventless_outcome_and_blocking_unresolved_prevent_confirmation() -> None:
    payload = _content().model_dump(mode="json")
    payload["outcomes"] = [
        {
            "response_code": "CALMED",
            "observed_at": None,
            "time_precision": "UNKNOWN",
            "linked_action_refs": ["a1"],
            "attribution": "SINGLE",
            "evidence": payload["actions"][0]["evidence"],
        }
    ]
    payload["unresolved"].append(
        {
            "field": "outcomes.0",
            "code": "UNSUPPORTED_CODE",
            "message": "사건 연결이 필요합니다.",
        }
    )

    issues = validate_normalized_content(
        NormalizedContent.model_validate(payload),
        raw_text="🍼 분유 80mL 먹였어요",
        choices=[],
        allow_user_correction=True,
        confirmation=True,
        eventless=True,
    )

    assert {issue.code for issue in issues} == {"EVENT_REQUIRED", "UNRESOLVED_BLOCKING"}


def test_conflict_and_missing_evidence_cannot_be_silently_confirmed() -> None:
    raw = "아기가 울다가 차분해졌어요"
    payload = _content(raw).model_dump(mode="json")
    payload["actions"] = []
    payload["states"] = [
        {
            "state_codes": ["CRYING", "CALM"],
            "phase": "UNRELATED",
            "observed_at": None,
            "time_precision": "UNKNOWN",
            "linked_action_refs": [],
            "evidence": [
                {
                    "source": "TEXT",
                    "choice_id": None,
                    "span_start": 0,
                    "span_end": len(raw),
                    "quote": raw,
                }
            ],
        }
    ]
    payload["unresolved"] = [
        {
            "field": "states.0.state_codes",
            "code": "CONFLICT",
            "message": "상충하는 상태를 보호자가 선택해야 합니다.",
        }
    ]

    issues = validate_normalized_content(
        NormalizedContent.model_validate(payload),
        raw_text=raw,
        choices=[],
        allow_user_correction=True,
        confirmation=True,
        eventless=True,
    )
    assert {issue.code for issue in issues} == {"UNRESOLVED_BLOCKING"}
    assert visual_state_code(["CRYING", "CALM"]) == "NEUTRAL"

    payload["states"][0]["evidence"] = []
    with pytest.raises(ValidationError):
        NormalizedContent.model_validate(payload)


def test_semantic_validator_reports_quantity_time_reference_and_state_errors() -> None:
    now = datetime.now(UTC)
    correction_evidence = [
        {
            "source": "USER_CORRECTION",
            "choice_id": None,
            "span_start": None,
            "span_end": None,
            "quote": "보호자 수정",
        }
    ]
    action = _content().model_dump(mode="json")["actions"][0]
    content = NormalizedContent.model_validate(
        {
            "actions": [
                {
                    **action,
                    "performed_by_user_id": str(uuid4()),
                    "occurred_at": None,
                    "time_precision": "EXACT",
                    "amount": 80,
                    "unit": None,
                    "evidence": correction_evidence,
                },
                {
                    **action,
                    "action_ref": "a1",
                    "sequence": 1,
                    "occurred_at": now.isoformat(),
                    "time_precision": "UNKNOWN",
                    "amount": 80,
                    "unit": "ML",
                    "feeding_mode": "BREAST",
                    "evidence": correction_evidence,
                },
                {
                    **action,
                    "action_ref": "a3",
                    "action_code": "HOLDING",
                    "sequence": 3,
                    "time_precision": "RELATIVE",
                    "relative_time": " ",
                    "amount": 1,
                    "unit": "MINUTES",
                    "feeding_mode": None,
                    "evidence": correction_evidence,
                },
                {
                    **action,
                    "action_ref": "a4",
                    "action_code": "BURPING",
                    "sequence": 4,
                    "relative_time": "방금",
                    "amount": None,
                    "unit": None,
                    "feeding_mode": None,
                    "evidence": correction_evidence,
                },
            ],
            "states": [
                {
                    "state_codes": ["CRYING", "CALM", "CALM"],
                    "phase": "AFTER",
                    "observed_at": None,
                    "time_precision": "EXACT",
                    "linked_action_refs": ["missing"],
                    "evidence": [
                        {
                            "source": "CHOICE",
                            "choice_id": "duplicate",
                            "span_start": None,
                            "span_end": None,
                            "quote": None,
                        }
                    ],
                }
            ],
            "outcomes": [
                {
                    "response_code": "CALMED",
                    "observed_at": None,
                    "time_precision": "EXACT",
                    "linked_action_refs": ["missing"],
                    "attribution": "UNKNOWN",
                    "evidence": [
                        {
                            "source": "CHOICE",
                            "choice_id": "duplicate",
                            "span_start": None,
                            "span_end": None,
                            "quote": None,
                        }
                    ],
                }
            ],
            "caregiver_interpretations": [
                {
                    "text": "배가 고팠던 것 같아요",
                    "certainty": "CAREGIVER_REPORTED",
                    "evidence": [
                        {
                            "source": "USER_CORRECTION",
                            "choice_id": None,
                            "span_start": None,
                            "span_end": None,
                            "quote": " ",
                        }
                    ],
                }
            ],
            "unresolved": [],
        }
    )
    duplicate_choice = Choice(choice_id="duplicate", kind="STATE", code="ASLEEP", assertion=None)

    issues = validate_normalized_content(
        content,
        raw_text=None,
        choices=[duplicate_choice, duplicate_choice],
        allow_user_correction=True,
        confirmation=False,
        eventless=False,
    )

    assert {
        "DUPLICATE_CHOICE_ID",
        "DUPLICATE_ACTION_REF",
        "DUPLICATE_ACTION_SEQUENCE",
        "ACTOR_NOT_SUPPORTED",
        "TIME_REQUIRED",
        "UNKNOWN_TIME_HAS_VALUE",
        "RELATIVE_TIME_REQUIRED",
        "RELATIVE_TIME_UNEXPECTED",
        "QUANTITY_PAIR_REQUIRED",
        "QUANTITY_CONFLICT",
        "QUANTITY_NOT_ALLOWED",
        "DUPLICATE_STATE",
        "UNKNOWN_ACTION_REF",
        "CHOICE_SEMANTIC_MISMATCH",
        "STATE_CONFLICT",
        "EVIDENCE_CORRECTION_INVALID",
    } <= {issue.code for issue in issues}


@pytest.mark.parametrize(
    ("evidence", "raw_text", "allow_correction", "expected"),
    [
        (
            {
                "source": "TEXT",
                "choice_id": None,
                "span_start": 0,
                "span_end": 1,
                "quote": "x",
            },
            None,
            False,
            "EVIDENCE_TEXT_MISMATCH",
        ),
        (
            {
                "source": "CHOICE",
                "choice_id": "missing",
                "span_start": 0,
                "span_end": None,
                "quote": None,
            },
            None,
            False,
            "EVIDENCE_CHOICE_MISMATCH",
        ),
        (
            {
                "source": "USER_CORRECTION",
                "choice_id": None,
                "span_start": None,
                "span_end": None,
                "quote": "보호자 수정",
            },
            None,
            False,
            "EVIDENCE_CORRECTION_INVALID",
        ),
    ],
)
def test_invalid_evidence_shapes_are_classified(
    evidence: dict[str, Any],
    raw_text: str | None,
    allow_correction: bool,
    expected: str,
) -> None:
    payload = _content().model_dump(mode="json")
    payload["actions"][0]["evidence"] = [evidence]

    issues = validate_normalized_content(
        NormalizedContent.model_validate(payload),
        raw_text=raw_text,
        choices=[],
        allow_user_correction=allow_correction,
        confirmation=False,
        eventless=True,
    )

    assert expected in {issue.code for issue in issues}
    assert visual_state_code([]) == "NEUTRAL"


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["UNKNOWN"], "NEUTRAL"),
        (["CRYING", "CALM"], "NEUTRAL"),
        (["ASLEEP", "AWAKE"], "NEUTRAL"),
        (["FUSSING", "AWAKE"], "FUSSING"),
        (["ASLEEP"], "ASLEEP"),
    ],
)
def test_confirmed_visual_mapping(states: list[str], expected: str) -> None:
    assert visual_state_code(states) == expected


def test_confirmation_mode_requires_matching_run_shape() -> None:
    common = {
        "client_request_id": uuid4(),
        "input_revision": 1,
        "normalization_mode": "LLM",
        "content": _content(),
        "base_record_versions": [],
    }

    with pytest.raises(ValidationError):
        ConfirmCareEntry(run_id=None, **common)
    manual = ConfirmCareEntry(
        run_id=None,
        **{**common, "normalization_mode": "MANUAL"},
    )
    assert manual.normalization_mode == "MANUAL"


class _FakeResponses:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.responses = _FakeResponses(outcomes)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _request() -> NormalizerRequest:
    return NormalizerRequest(
        raw_text="🍼 분유 80mL 먹였어요",
        choices=[],
        occurred_at=None,
        time_precision="UNKNOWN",
        current_time=datetime.now(UTC),
        timezone="Asia/Seoul",
    )


def _response(**overrides: Any) -> Any:
    values = {
        "model": MODEL_ID,
        "status": "completed",
        "output": [],
        "output_text": _content().model_dump_json(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _adapter(outcomes: list[Any]) -> tuple[OpenAINormalizerAdapter, _FakeClient]:
    adapter = OpenAINormalizerAdapter(api_key="synthetic-not-a-live-key")
    client = _FakeClient(outcomes)
    adapter._client = client
    return adapter, client


def test_product_adapter_uses_exact_model_schema_and_no_tools() -> None:
    adapter, client = _adapter([_response()])

    result = asyncio.run(adapter.normalize(_request()))
    asyncio.run(adapter.close())

    assert result.content == _content()
    assert result.provider_call_executed is True
    assert client.closed is True
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6-terra"
    assert call["store"] is False
    assert call["reasoning"] == {"effort": "low"}
    assert call["text"]["format"]["strict"] is True
    assert "tools" not in call
    assert "synthetic_data" not in json.loads(call["input"][0]["content"])


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (_response(status="incomplete"), "NORMALIZATION_INCOMPLETE"),
        (_response(output_text="{}"), "NORMALIZATION_SCHEMA_INVALID"),
        (
            _response(output=[SimpleNamespace(content=[SimpleNamespace(type="refusal")])]),
            "NORMALIZATION_REFUSED",
        ),
        (_response(model="another-model"), "NORMALIZATION_PROVIDER_ERROR"),
    ],
)
def test_product_adapter_preserves_distinct_failures(response: Any, code: str) -> None:
    adapter, _ = _adapter([response])

    result = asyncio.run(adapter.normalize(_request()))

    assert result.content is None
    assert result.failure_code == code
    assert result.provider_call_executed is True


def test_product_adapter_retries_once_without_exceeding_request_budget() -> None:
    timeout_error = type("APITimeoutError", (Exception,), {})()
    adapter, client = _adapter([timeout_error, _response()])

    result = asyncio.run(adapter.normalize(_request()))

    assert result.content is not None
    assert len(client.responses.calls) == 2


@pytest.mark.parametrize("timeout_error", [TimeoutError, type("APITimeoutError", (Exception,), {})])
def test_product_adapter_reports_timeout_after_single_bounded_retry(
    timeout_error: type[Exception],
) -> None:
    adapter, client = _adapter([timeout_error(), timeout_error()])

    result = asyncio.run(adapter.normalize(_request()))

    assert result.content is None
    assert result.failure_code == "NORMALIZATION_TIMEOUT"
    assert result.retryable is True
    assert len(client.responses.calls) == 2


def test_product_adapter_rejects_blank_output_and_non_retryable_provider_error() -> None:
    blank_adapter, _ = _adapter([_response(output_text=" ")])
    blank = asyncio.run(blank_adapter.normalize(_request()))
    assert blank.failure_code == "NORMALIZATION_INCOMPLETE"

    provider_error = type("BadRequestError", (Exception,), {})()
    error_adapter, client = _adapter([provider_error])
    failed = asyncio.run(error_adapter.normalize(_request()))
    assert failed.failure_code == "NORMALIZATION_PROVIDER_ERROR"
    assert failed.retryable is False
    assert len(client.responses.calls) == 1


def test_product_adapter_stops_before_call_when_deadline_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, client = _adapter([_response()])
    ticks = iter([0.0, 21.0])
    monkeypatch.setattr(normalizer_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    result = asyncio.run(adapter.normalize(_request()))

    assert result.failure_code == "NORMALIZATION_TIMEOUT"
    assert client.responses.calls == []


def _provider_exception(
    name: str,
    *,
    status_code: int | None = None,
    code: str | None = None,
    body: dict[str, Any] | None = None,
) -> Exception:
    error = type(name, (Exception,), {})()
    if status_code is not None:
        error.status_code = status_code  # type: ignore[attr-defined]
    if code is not None:
        error.code = code  # type: ignore[attr-defined]
    if body is not None:
        error.body = body  # type: ignore[attr-defined]
    return error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_provider_exception("AuthenticationError"), ("AUTHENTICATION_FAILED", False)),
        (_provider_exception("PermissionDeniedError"), ("MODEL_ACCESS_UNAVAILABLE", False)),
        (
            _provider_exception("ProviderError", body={"code": "model_not_found"}),
            ("MODEL_ACCESS_UNAVAILABLE", False),
        ),
        (
            _provider_exception("ProviderError", body={"error": {"code": "unsupported_model"}}),
            ("MODEL_ACCESS_UNAVAILABLE", False),
        ),
        (_provider_exception("RateLimitError"), ("LIMIT_OR_QUOTA_EXCEEDED", False)),
        (_provider_exception("APITimeoutError"), ("TIMEOUT", True)),
        (_provider_exception("APIConnectionError"), ("CONNECTION_ERROR", True)),
        (_provider_exception("ProviderError", status_code=503), ("PROVIDER_SERVER_ERROR", True)),
        (_provider_exception("BadRequestError"), ("INVALID_PROVIDER_REQUEST", False)),
        (_provider_exception("ProviderError", code="other"), ("PROVIDER_ERROR", False)),
    ],
)
def test_provider_error_classification_is_stable_and_message_free(
    error: Exception, expected: tuple[str, bool]
) -> None:
    assert classify_provider_error(error) == expected


class _ExplodingAdapter:
    calls = 0

    async def normalize(self, request: NormalizerRequest) -> Any:
        self.calls += 1
        raise AssertionError(request)

    async def close(self) -> None:
        return None


def test_external_gate_cannot_be_bypassed_by_configured_key_or_injected_adapter() -> None:
    adapter = _ExplodingAdapter()
    app = create_app(
        settings=Settings(
            environment=RuntimeEnvironment.TEST,
            database_url="postgresql://example.invalid/test",
            reauthentication_proof_secret="synthetic-secret-long-enough-for-tests",
            external_normalization_enabled=False,
            openai_api_key="synthetic-not-a-live-key",
        ),
        normalizer_adapter=adapter,
    )

    assert app.state.normalizer_unavailable_reason == "DISABLED"
    assert app.state.b07_service.available is False
    assert adapter.calls == 0


def test_normalizer_availability_checks_credentials_dependency_and_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    common = {
        "environment": RuntimeEnvironment.TEST,
        "database_url": "postgresql://example.invalid/test",
        "reauthentication_proof_secret": "synthetic-secret-long-enough-for-tests",
        "external_normalization_enabled": True,
    }
    missing = create_app(settings=Settings(**common))
    assert missing.state.normalizer_unavailable_reason == "CREDENTIALS_MISSING"
    assert missing.state.b07_service.available is False

    monkeypatch.setattr(main_module, "openai_dependency_available", lambda: False)
    dependency = create_app(settings=Settings(**common, openai_api_key="synthetic-not-a-live-key"))
    assert dependency.state.normalizer_unavailable_reason == "DEPENDENCY_MISSING"

    constructed = _ExplodingAdapter()
    monkeypatch.setattr(main_module, "openai_dependency_available", lambda: True)
    monkeypatch.setattr(
        main_module,
        "OpenAINormalizerAdapter",
        lambda **_: constructed,
    )
    available = create_app(settings=Settings(**common, openai_api_key="synthetic-not-a-live-key"))
    assert available.state.normalizer_unavailable_reason is None
    assert available.state.b07_service.available is True

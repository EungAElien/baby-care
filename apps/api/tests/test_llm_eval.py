from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

import baby_care_api.llm_eval.runner as runner_module
from baby_care_api.llm_eval import __main__ as llm_eval_cli
from baby_care_api.llm_eval.datasets import (
    API_ROOT,
    EVAL_ROOT,
    load_cases,
    load_counseling_cases,
    load_normalization_cases,
)
from baby_care_api.llm_eval.evaluation import (
    evaluate_counseling,
    evaluate_normalization,
)
from baby_care_api.llm_eval.models import CounselingCase, NormalizationCase
from baby_care_api.llm_eval.provider import (
    MODEL_ID,
    OpenAIResponsesAdapter,
    ProviderBudget,
    ProviderBudgetExceeded,
    ProviderCaseResult,
    ProviderRequestRecord,
    classify_provider_error,
    estimate_cost_usd,
)
from baby_care_api.llm_eval.runner import run_live_smoke, run_offline
from baby_care_api.llm_eval.tools import (
    ToolExecutionError,
    execute_synthetic_tool,
    tools_for_case,
)

REPOSITORY_ROOT = API_ROOT.parents[1]
CONTRACT = json.loads(
    (REPOSITORY_ROOT / "contracts" / "openapi계약.json").read_text(encoding="utf-8")
)
REGISTRY = Registry().with_resource(
    "urn:baby-care:openapi",
    Resource(contents=CONTRACT, specification=DRAFT202012),
)


def _case(case_id: str) -> NormalizationCase | CounselingCase:
    return next(case for case in load_cases() if case.case_id == case_id)


def _offline_trace(case: CounselingCase) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for call in case.expected.required_tool_calls:
        fixture = next(
            item
            for item in case.tool_fixtures
            if item.tool_name == call.tool_name and item.arguments == call.arguments
        )
        values.append(
            {
                "tool_name": call.tool_name,
                "arguments": call.arguments,
                "status": fixture.status,
                "evidence_ids": fixture.evidence_ids,
            }
        )
    return values


def test_datasets_have_stable_minimum_counts_and_distinct_splits() -> None:
    normalization = load_normalization_cases()
    counseling = load_counseling_cases()

    assert len(normalization) == 20
    assert len(counseling) == 23
    assert len({case.case_id for case in normalization + counseling}) == 43
    assert {case.split for case in normalization} == {"PROMPT_TUNING", "FINAL_CONFIRMATION"}
    assert {case.split for case in counseling} == {"PROMPT_TUNING", "FINAL_CONFIRMATION"}
    assert all(case.synthetic_data is True for case in normalization + counseling)
    assert all(case.human_review and case.automatic_checks for case in normalization + counseling)


def test_committed_datasets_are_reproducible() -> None:
    result = subprocess.run(
        [sys.executable, str(EVAL_ROOT / "build_datasets.py"), "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_normalization_gold_matches_canonical_openapi_schema() -> None:
    validator = Draft202012Validator(
        {"$ref": "urn:baby-care:openapi#/components/schemas/NormalizedContent"},
        registry=REGISTRY,
        format_checker=FormatChecker(),
    )
    for case in load_normalization_cases():
        validator.validate(case.expected.output.model_dump(mode="json"))
        assert case.allowed_tools == []
        assert case.tool_fixtures == []


def test_unicode_offsets_are_code_points_not_utf16_units() -> None:
    case = _case("NORM-HOLD-002")
    assert isinstance(case, NormalizationCase)
    output = case.expected.output

    for item in [*output.actions, *output.states, *output.outcomes]:
        for evidence in item.evidence:
            assert evidence.span_start is not None
            assert evidence.span_end is not None
            assert case.input.raw_text[evidence.span_start : evidence.span_end] == evidence.quote

    crying = output.states[0].evidence[0]
    utf16_start = len(case.input.raw_text[: crying.span_start].encode("utf-16-le")) // 2
    assert utf16_start != crying.span_start or "😢" in (crying.quote or "")


def test_numeric_gold_is_derived_from_synthetic_source_records() -> None:
    feeding = _case("COUNSEL-DEV-004")
    sleep = _case("COUNSEL-DEV-005")
    unknown = _case("COUNSEL-DEV-007")
    zero = _case("COUNSEL-DEV-008")
    assert all(isinstance(item, CounselingCase) for item in (feeding, sleep, unknown, zero))

    assert (
        sum(
            record["amount_ml"]
            for record in feeding.source_records
            if record["amount_ml"] is not None
        )
        == 180
    )
    assert sum(record["amount_ml"] is None for record in feeding.source_records) == 1
    assert (
        next(
            claim.numeric_value
            for claim in feeding.expected.claims
            if claim.fact_key == "feeding_total_known_ml"
        )
        == 180
    )
    assert (
        next(
            claim.numeric_value
            for claim in sleep.expected.claims
            if claim.fact_key == "sleep_minutes"
        )
        == 90
    )
    assert sum(record["amount_ml"] is None for record in unknown.source_records) == 1
    assert zero.source_records[0]["amount_ml"] == 0


def _numeric_values(value: object) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [float(value)]
    if isinstance(value, dict):
        return [number for item in value.values() for number in _numeric_values(item)]
    if isinstance(value, list):
        return [number for item in value for number in _numeric_values(item)]
    return []


def test_every_counseling_number_matches_prefixed_gold_and_tool_source() -> None:
    for case in load_counseling_cases():
        numeric_claims = {
            claim.fact_key: (claim.numeric_value, claim.unit)
            for claim in case.expected.claims
            if claim.numeric_value is not None
        }
        declared_numbers = {
            str(item["fact_key"]): (item["value"], item["unit"]) for item in case.expected.numbers
        }
        assert declared_numbers == numeric_claims
        for claim in case.expected.claims:
            if claim.numeric_value is None:
                continue
            source_numbers = [
                number
                for fixture in case.tool_fixtures
                if set(claim.evidence_ids) & set(fixture.evidence_ids)
                for number in _numeric_values(fixture.payload)
            ]
            assert float(claim.numeric_value) in source_numbers


def test_offline_run_has_zero_provider_calls_and_keeps_human_review_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_constructed(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline mode constructed a provider adapter")

    monkeypatch.setattr(OpenAIResponsesAdapter, "__init__", fail_if_constructed)
    report = run_offline()

    assert report["provider_request_count"] == 0
    assert report["actual_model_executed"] is False
    assert report["summary"]["automated_pass_count"] == 43
    assert report["summary"]["negative_probe_count"] >= 8
    assert report["summary"]["negative_probe_fail_count"] == 0
    assert report["summary"]["human_review_pending_count"] == 43
    assert report["summary"]["split_counts"] == {
        "PROMPT_TUNING": 31,
        "FINAL_CONFIRMATION": 12,
    }
    assert report["summary"]["check_summary"]["normalization.schema"] == {
        "pass": 20,
        "fail": 0,
    }
    assert report["summary"]["check_summary"]["counseling.schema"] == {
        "pass": 23,
        "fail": 0,
    }
    assert report["summary"]["decision"] == "AUTOMATED_PASS_HUMAN_REVIEW_PENDING"


def test_schema_valid_but_semantically_wrong_normalization_fails() -> None:
    case = _case("NORM-HOLD-001")
    assert isinstance(case, NormalizationCase)
    probe = case.adversarial_candidates[0]

    evaluation = evaluate_normalization(case, probe.candidate)

    assert "normalization.semantics" in evaluation.failure_codes
    assert "normalization.schema" not in evaluation.failure_codes


def test_normalization_semantics_and_valid_evidence_wording_are_scored_separately() -> None:
    first = _case("NORM-DEV-001")
    sequence = _case("NORM-DEV-006")
    assert isinstance(first, NormalizationCase)
    assert isinstance(sequence, NormalizationCase)

    broad_evidence = first.expected.output.model_dump(mode="json")
    broad_evidence["actions"][0]["evidence"] = [
        {
            "source": "TEXT",
            "choice_id": None,
            "span_start": 0,
            "span_end": len(first.input.raw_text),
            "quote": first.input.raw_text,
        }
    ]
    first_evaluation = evaluate_normalization(first, broad_evidence)
    assert first_evaluation.passed

    shorter_evidence = sequence.expected.output.model_dump(mode="json")
    shorter_evidence["actions"][2]["evidence"] = [
        {
            "source": "TEXT",
            "choice_id": None,
            "span_start": 20,
            "span_end": 23,
            "quote": "안아줬",
        }
    ]
    sequence_evaluation = evaluate_normalization(sequence, shorter_evidence)
    assert sequence_evaluation.passed


def test_normalization_relative_time_gold_matches_prompt_contract() -> None:
    case = _case("NORM-DEV-001")
    assert isinstance(case, NormalizationCase)
    assert case.expected.output.actions[0].relative_time == "오늘 오전 9시"


def test_numeric_hallucination_and_deleted_disclosure_fail() -> None:
    for case_id in ("COUNSEL-DEV-004", "COUNSEL-HOLD-001"):
        case = _case(case_id)
        assert isinstance(case, CounselingCase)
        probe = case.adversarial_candidates[0]
        evaluation = evaluate_counseling(
            case,
            probe.candidate,
            tool_trace=_offline_trace(case),
        )
        assert not evaluation.passed
        assert set(probe.expected_failure_codes) <= evaluation.failure_codes


def test_counseling_claim_aliases_and_localized_units_do_not_fail_numeric_scoring() -> None:
    case = _case("COUNSEL-DEV-004")
    assert isinstance(case, CounselingCase)
    candidate = case.offline_candidate.model_dump(mode="json")
    aliases = [
        ("feeding_record_count_yesterday", "회"),
        ("feeding_total_known_ml_yesterday", "mL"),
        ("feeding_unknown_amount_yesterday", "건"),
    ]
    for claim, (fact_key, unit) in zip(candidate["claims"], aliases, strict=True):
        claim["fact_key"] = fact_key
        claim["unit"] = unit
    candidate["claims"][2]["kind"] = "LIMITATION"

    evaluation = evaluate_counseling(
        case,
        candidate,
        tool_trace=_offline_trace(case),
    )

    assert evaluation.passed


def test_scoped_tool_rejects_identity_expansion_and_unknown_calls() -> None:
    case = _case("COUNSEL-DEV-004")
    assert isinstance(case, CounselingCase)
    arguments = dict(case.expected.required_tool_calls[0].arguments)
    arguments["baby_id"] = "other-baby"

    with pytest.raises(ToolExecutionError, match="INVALID_TOOL_ARGUMENTS"):
        execute_synthetic_tool(case, "get_server_aggregates", arguments)
    with pytest.raises(ToolExecutionError, match="UNAUTHORIZED_TOOL"):
        execute_synthetic_tool(case, "run_sql", {})


def test_scoped_tool_accepts_narrower_safe_limit_and_rejects_broader_limit() -> None:
    case = _case("COUNSEL-DEV-017")
    assert isinstance(case, CounselingCase)
    expected_call = case.expected.required_tool_calls[0]
    arguments = dict(expected_call.arguments)
    arguments["limit"] = 1

    execution = execute_synthetic_tool(case, expected_call.tool_name, arguments)

    assert execution.arguments["limit"] == 1
    schema = tools_for_case(case)[0]["parameters"]
    assert schema["properties"]["limit"]["maximum"] == 10

    arguments["limit"] = 11
    with pytest.raises(ToolExecutionError, match="FIXTURE_NOT_FOUND"):
        execute_synthetic_tool(case, expected_call.tool_name, arguments)


def test_failure_fixtures_expose_retryability_without_model_guessing() -> None:
    for case_id in (
        "COUNSEL-DEV-010",
        "COUNSEL-DEV-011",
        "COUNSEL-DEV-012",
        "COUNSEL-HOLD-001",
        "COUNSEL-HOLD-002",
    ):
        case = _case(case_id)
        assert isinstance(case, CounselingCase)
        assert len(case.expected.tool_failures) == 1
        expected_failure = case.expected.tool_failures[0]
        assert len(case.tool_fixtures) == 1
        assert case.tool_fixtures[0].payload["retryable"] is expected_failure.retryable


def test_provider_budget_hard_stops_after_twelve_requests() -> None:
    budget = ProviderBudget(max_requests=12, max_elapsed_seconds=360)
    assert [budget.reserve_request() for _ in range(12)] == list(range(1, 13))
    with pytest.raises(ProviderBudgetExceeded, match="PROVIDER_REQUEST_LIMIT"):
        budget.reserve_request()

    elapsed = ProviderBudget(max_requests=12, max_elapsed_seconds=-1)
    with pytest.raises(ProviderBudgetExceeded, match="PROVIDER_WALL_TIME_LIMIT"):
        elapsed.reserve_request()


def test_snapshot_model_suffix_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case("NORM-DEV-001")
    assert isinstance(case, NormalizationCase)
    responses = _FakeResponses(
        [
            _FakeResponse(
                output_text=case.expected.output.model_dump_json(),
                model=f"{MODEL_ID}-2026-09-20",
            )
        ]
    )
    adapter = _adapter_with_fake_openai(monkeypatch, responses)

    result = adapter.run_normalization(case, instructions="synthetic test")

    assert result.failure_type is None


def test_unrelated_model_prefix_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case("NORM-DEV-001")
    assert isinstance(case, NormalizationCase)
    responses = _FakeResponses([_FakeResponse(model=f"{MODEL_ID}lookalike")])
    adapter = _adapter_with_fake_openai(monkeypatch, responses)

    result = adapter.run_normalization(case, instructions="synthetic test")

    assert result.failure_type == "MODEL_MISMATCH"


def test_live_mode_requires_explicit_opt_in_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BABY_CARE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    not_allowed = run_live_smoke(allow_provider_calls=False)
    missing_key = run_live_smoke(allow_provider_calls=True)

    assert not_allowed["provider_request_count"] == 0
    assert not_allowed["summary"]["decision"] == "NOT_RUN_EXPLICIT_OPT_IN_REQUIRED"
    assert missing_key["provider_request_count"] == 0
    assert missing_key["summary"]["decision"] == "NOT_RUN_MISSING_API_KEY"


def test_cli_returns_success_only_for_a_passed_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    offline_report = tmp_path / "offline.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-eval", "--mode", "offline", "--report", str(offline_report)],
    )
    assert llm_eval_cli.main() == 0
    assert json.loads(offline_report.read_text(encoding="utf-8"))["provider_request_count"] == 0

    live_report = tmp_path / "live.json"
    monkeypatch.delenv("BABY_CARE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-eval",
            "--mode",
            "live-smoke",
            "--allow-provider-calls",
            "--report",
            str(live_report),
        ],
    )
    assert llm_eval_cli.main() == 1
    assert json.loads(live_report.read_text(encoding="utf-8"))["summary"]["decision"] == (
        "NOT_RUN_MISSING_API_KEY"
    )


def test_live_smoke_runner_is_serial_bounded_and_keeps_human_review_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        def __init__(self, *, api_key: str, budget: ProviderBudget, **kwargs: object) -> None:
            assert api_key == "synthetic-test-key"
            self.budget = budget

        def request_record(self) -> ProviderRequestRecord:
            return ProviderRequestRecord(
                sequence=self.budget.reserve_request(),
                requested_model=MODEL_ID,
                response_model=MODEL_ID,
                status="COMPLETED",
                latency_ms=1,
                input_tokens=100,
                cached_input_tokens=0,
                output_tokens=20,
                total_tokens=120,
                failure_type=None,
            )

        def run_normalization(
            self, case: NormalizationCase, *, instructions: str
        ) -> ProviderCaseResult:
            assert instructions
            return ProviderCaseResult(
                candidate=case.expected.output.model_dump(mode="json"),
                tool_trace=(),
                request_records=(self.request_record(),),
                actual_model_executed=True,
                failure_type=None,
            )

        def run_counseling(self, case: CounselingCase, *, instructions: str) -> ProviderCaseResult:
            assert instructions
            records = (self.request_record(), self.request_record())
            return ProviderCaseResult(
                candidate=case.offline_candidate.model_dump(mode="json"),
                tool_trace=tuple(_offline_trace(case)),
                request_records=records,
                actual_model_executed=True,
                failure_type=None,
            )

    monkeypatch.setenv("BABY_CARE_OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(runner_module, "OpenAIResponsesAdapter", FakeAdapter)

    report = run_live_smoke(allow_provider_calls=True)

    assert report["provider_request_count"] == 9
    assert len(report["cases"]) == 6
    assert report["actual_model_executed"] is True
    assert report["summary"]["decision"] == "SMOKE_AUTOMATED_PASS_HUMAN_REVIEW_PENDING"
    assert report["summary"]["human_review_pending_count"] == 6
    assert report["pricing"]["estimate_status"] == "ESTIMATED_FROM_REPORTED_TOKENS"


class _FakeUsage:
    input_tokens = 100
    output_tokens = 20
    total_tokens = 120
    input_tokens_details = SimpleNamespace(cached_tokens=40)


class _FakeResponse:
    def __init__(
        self,
        *,
        output_text: str = "",
        output: list[object] | None = None,
        model: str = MODEL_ID,
        status: str = "completed",
    ) -> None:
        self.output_text = output_text
        self.output = output or []
        self.model = model
        self.status = status
        self.usage = _FakeUsage()


class _FakeCall:
    type = "function_call"

    def __init__(self, name: str, arguments: dict[str, Any], call_id: str = "call-1") -> None:
        self.name = name
        self.arguments = json.dumps(arguments)
        self.call_id = call_id


class _FakeResponses:
    def __init__(self, items: list[object]) -> None:
        self.items = list(items)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, _FakeResponse)
        return item


def _adapter_with_fake_openai(
    monkeypatch: pytest.MonkeyPatch, responses: _FakeResponses, *, max_requests: int = 12
) -> OpenAIResponsesAdapter:
    fake_module = ModuleType("openai")

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["api_key"] == "synthetic-key"
            self.responses = responses

    fake_module.OpenAI = FakeOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return OpenAIResponsesAdapter(
        api_key="synthetic-key",
        budget=ProviderBudget(max_requests=max_requests),
        max_retries_per_request=1,
    )


def test_live_adapter_normalization_records_model_usage_without_exposing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("NORM-DEV-001")
    assert isinstance(case, NormalizationCase)
    responses = _FakeResponses(
        [_FakeResponse(output_text=json.dumps(case.expected.output.model_dump(mode="json")))]
    )
    adapter = _adapter_with_fake_openai(monkeypatch, responses)

    result = adapter.run_normalization(case, instructions="synthetic prompt")

    assert result.failure_type is None
    assert result.actual_model_executed is True
    assert result.candidate is not None
    assert result.request_records[0].response_model == MODEL_ID
    assert result.request_records[0].total_tokens == 120
    assert responses.calls[0]["store"] is False
    assert responses.calls[0]["max_output_tokens"] == 1200
    assert "synthetic-key" not in json.dumps(responses.calls[0], default=str)


def test_live_adapter_retries_one_timeout_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("NORM-DEV-001")
    assert isinstance(case, NormalizationCase)
    timeout_type = type("APITimeoutError", (Exception,), {})
    responses = _FakeResponses(
        [
            timeout_type(),
            _FakeResponse(output_text=case.expected.output.model_dump_json()),
        ]
    )
    adapter = _adapter_with_fake_openai(monkeypatch, responses)

    result = adapter.run_normalization(case, instructions="synthetic prompt")

    assert result.failure_type is None
    assert len(result.request_records) == 2
    assert result.request_records[0].failure_type == "TIMEOUT"


def test_incomplete_provider_response_is_not_parsed_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("NORM-DEV-001")
    assert isinstance(case, NormalizationCase)
    responses = _FakeResponses([_FakeResponse(status="incomplete")])
    adapter = _adapter_with_fake_openai(monkeypatch, responses)

    result = adapter.run_normalization(case, instructions="synthetic prompt")

    assert result.failure_type == "INCOMPLETE_RESPONSE"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            SimpleNamespace(output=[SimpleNamespace(content=[SimpleNamespace(type="refusal")])]),
            "REFUSAL",
        ),
        (SimpleNamespace(output=[], output_text=""), "INCOMPLETE_RESPONSE"),
        (SimpleNamespace(output=[], output_text="{"), "SCHEMA_VIOLATION"),
        (SimpleNamespace(output=[], output_text="[]"), "SCHEMA_VIOLATION"),
    ],
)
def test_refusal_incomplete_and_schema_failures_are_distinct(
    response: object, expected: str
) -> None:
    candidate, failure = OpenAIResponsesAdapter._parse_candidate(response)
    assert candidate is None
    assert failure == expected


def test_live_adapter_executes_one_synthetic_tool_round_trip_serially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("COUNSEL-DEV-004")
    assert isinstance(case, CounselingCase)
    expected_call = case.expected.required_tool_calls[0]
    responses = _FakeResponses(
        [
            _FakeResponse(output=[_FakeCall(expected_call.tool_name, expected_call.arguments)]),
            _FakeResponse(output_text=json.dumps(case.offline_candidate.model_dump(mode="json"))),
        ]
    )
    adapter = _adapter_with_fake_openai(monkeypatch, responses)

    result = adapter.run_counseling(case, instructions="synthetic prompt")

    assert result.failure_type is None
    assert len(result.tool_trace) == 1
    assert len(result.request_records) == 2
    assert responses.calls[0]["parallel_tool_calls"] is False
    assert responses.calls[1]["tool_choice"] == "none"


def test_live_adapter_records_rejected_fixture_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("COUNSEL-DEV-017")
    assert isinstance(case, CounselingCase)
    expected_call = case.expected.required_tool_calls[0]
    broad_arguments = dict(expected_call.arguments)
    broad_arguments["limit"] = 11
    responses = _FakeResponses(
        [_FakeResponse(output=[_FakeCall(expected_call.tool_name, broad_arguments)])]
    )
    adapter = _adapter_with_fake_openai(monkeypatch, responses)

    result = adapter.run_counseling(case, instructions="synthetic prompt")

    assert result.failure_type == "FIXTURE_NOT_FOUND"
    assert result.tool_trace == (
        {
            "tool_name": expected_call.tool_name,
            "arguments": broad_arguments,
            "status": "REJECTED",
            "evidence_ids": [],
            "failure_type": "FIXTURE_NOT_FOUND",
        },
    )


def test_live_adapter_rejects_unauthorized_tool_and_model_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("COUNSEL-DEV-004")
    assert isinstance(case, CounselingCase)
    unauthorized = _FakeResponses([_FakeResponse(output=[_FakeCall("run_sql", {})])])
    adapter = _adapter_with_fake_openai(monkeypatch, unauthorized)
    result = adapter.run_counseling(case, instructions="synthetic prompt")
    assert result.failure_type == "UNAUTHORIZED_TOOL"

    normalization = _case("NORM-DEV-001")
    assert isinstance(normalization, NormalizationCase)
    mismatch = _FakeResponses(
        [
            _FakeResponse(
                output_text=json.dumps(normalization.expected.output.model_dump(mode="json")),
                model="different-model",
            )
        ]
    )
    mismatch_adapter = _adapter_with_fake_openai(monkeypatch, mismatch)
    mismatch_result = mismatch_adapter.run_normalization(
        normalization, instructions="synthetic prompt"
    )
    assert mismatch_result.failure_type == "MODEL_MISMATCH"


@pytest.mark.parametrize(
    ("exception_name", "status_code", "expected", "retryable"),
    [
        ("AuthenticationError", 401, "AUTHENTICATION_FAILED", False),
        ("PermissionDeniedError", 403, "MODEL_ACCESS_UNAVAILABLE", False),
        ("RateLimitError", 429, "LIMIT_OR_QUOTA_EXCEEDED", False),
        ("APITimeoutError", None, "TIMEOUT", True),
        ("APIConnectionError", None, "CONNECTION_ERROR", True),
        ("InternalServerError", 500, "PROVIDER_SERVER_ERROR", True),
        ("BadRequestError", 400, "INVALID_PROVIDER_REQUEST", False),
    ],
)
def test_provider_failures_are_distinguished(
    exception_name: str,
    status_code: int | None,
    expected: str,
    retryable: bool,
) -> None:
    exception_type = type(exception_name, (Exception,), {})
    exc = exception_type()
    exc.status_code = status_code  # type: ignore[attr-defined]
    assert classify_provider_error(exc) == (expected, retryable)


def test_cost_estimate_uses_verified_token_rates() -> None:
    record = ProviderRequestRecord(
        sequence=1,
        requested_model=MODEL_ID,
        response_model=MODEL_ID,
        status="COMPLETED",
        latency_ms=1,
        input_tokens=100,
        cached_input_tokens=40,
        output_tokens=20,
        total_tokens=120,
        failure_type=None,
    )
    expected = (60 * 2.0 + 40 * 0.2 + 20 * 12.0) / 1_000_000
    assert estimate_cost_usd([record]) == round(expected, 8)
    assert estimate_cost_usd([deepcopy(record), deepcopy(record)]) == round(expected * 2, 8)
    missing_usage = deepcopy(record)
    object.__setattr__(missing_usage, "input_tokens", None)
    assert estimate_cost_usd([missing_usage]) is None

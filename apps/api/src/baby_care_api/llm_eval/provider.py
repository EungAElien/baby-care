from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from baby_care_api.llm_eval.models import (
    MODEL_ID,
    CounselingCase,
    CounselingOutput,
    NormalizationCase,
    NormalizedContent,
)
from baby_care_api.llm_eval.tools import (
    ToolExecutionError,
    execute_synthetic_tool,
    tools_for_case,
)

INPUT_PRICE_USD_PER_MILLION = 2.0
CACHED_INPUT_PRICE_USD_PER_MILLION = 0.2
OUTPUT_PRICE_USD_PER_MILLION = 12.0
PRICING_VERIFIED_ON = "2026-09-20"
PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.6-terra"


class ProviderBudgetExceeded(RuntimeError):
    pass


@dataclass
class ProviderBudget:
    max_requests: int = 12
    max_elapsed_seconds: float = 360.0
    request_count: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def reserve_request(self) -> int:
        if self.request_count >= self.max_requests:
            raise ProviderBudgetExceeded("PROVIDER_REQUEST_LIMIT")
        if time.monotonic() - self.started_at >= self.max_elapsed_seconds:
            raise ProviderBudgetExceeded("PROVIDER_WALL_TIME_LIMIT")
        self.request_count += 1
        return self.request_count


@dataclass(frozen=True)
class ProviderRequestRecord:
    sequence: int
    requested_model: str
    response_model: str | None
    status: str
    latency_ms: int
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    failure_type: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "requested_model": self.requested_model,
            "response_model": self.response_model,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "usage": {
                "input_tokens": self.input_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
            },
            "failure_type": self.failure_type,
        }


@dataclass(frozen=True)
class ProviderCaseResult:
    candidate: dict[str, Any] | None
    tool_trace: tuple[dict[str, Any], ...]
    request_records: tuple[ProviderRequestRecord, ...]
    actual_model_executed: bool
    failure_type: str | None


def _usage(response: Any) -> tuple[int | None, int | None, int | None, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None, None, None
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = getattr(details, "cached_tokens", None) if details is not None else None
    return input_tokens, cached_tokens, output_tokens, total_tokens


def _error_code(exc: Exception) -> str | None:
    direct = getattr(exc, "code", None)
    if isinstance(direct, str):
        return direct
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        value = body.get("code")
        if isinstance(value, str):
            return value
        nested = body.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("code"), str):
            return str(nested["code"])
    return None


def classify_provider_error(exc: Exception) -> tuple[str, bool]:
    name = type(exc).__name__
    code = _error_code(exc)
    status_code = getattr(exc, "status_code", None)
    if name == "AuthenticationError" or status_code == 401:
        return "AUTHENTICATION_FAILED", False
    if name in {"PermissionDeniedError", "NotFoundError"} or status_code in {403, 404}:
        return "MODEL_ACCESS_UNAVAILABLE", False
    if code in {"model_not_found", "model_not_available", "unsupported_model"}:
        return "MODEL_ACCESS_UNAVAILABLE", False
    if name == "RateLimitError" or status_code == 429:
        return "LIMIT_OR_QUOTA_EXCEEDED", False
    if name == "APITimeoutError":
        return "TIMEOUT", True
    if name == "APIConnectionError":
        return "CONNECTION_ERROR", True
    if status_code in {500, 502, 503, 504} or name == "InternalServerError":
        return "PROVIDER_SERVER_ERROR", True
    if name == "BadRequestError" or status_code == 400:
        return "INVALID_PROVIDER_REQUEST", False
    return "PROVIDER_ERROR", False


def _has_refusal(response: Any) -> bool:
    for item in getattr(response, "output", ()):
        for content in getattr(item, "content", ()):
            if getattr(content, "type", None) == "refusal":
                return True
    return False


class OpenAIResponsesAdapter:
    """Bounded live adapter. Construction alone performs no network request."""

    def __init__(
        self,
        *,
        api_key: str,
        budget: ProviderBudget,
        timeout_seconds: float = 45.0,
        max_retries_per_request: int = 1,
        organization: str | None = None,
        project: str | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "The live evaluator requires the locked development dependencies."
            ) from exc
        self._client: Any = OpenAI(
            api_key=api_key,
            organization=organization,
            project=project,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self._budget = budget
        self._max_retries_per_request = max_retries_per_request

    def _request(self, **kwargs: Any) -> tuple[Any | None, list[ProviderRequestRecord], str | None]:
        records: list[ProviderRequestRecord] = []
        for attempt in range(self._max_retries_per_request + 1):
            try:
                sequence = self._budget.reserve_request()
            except ProviderBudgetExceeded as exc:
                return None, records, str(exc)
            started = time.monotonic()
            try:
                response = self._client.responses.create(**kwargs)
            except Exception as exc:  # OpenAI exception subclasses are classified without messages.
                error_failure_type, retryable = classify_provider_error(exc)
                records.append(
                    ProviderRequestRecord(
                        sequence=sequence,
                        requested_model=MODEL_ID,
                        response_model=None,
                        status="ERROR",
                        latency_ms=round((time.monotonic() - started) * 1000),
                        input_tokens=None,
                        cached_input_tokens=None,
                        output_tokens=None,
                        total_tokens=None,
                        failure_type=error_failure_type,
                    )
                )
                if retryable and attempt < self._max_retries_per_request:
                    continue
                return None, records, error_failure_type
            input_tokens, cached_tokens, output_tokens, total_tokens = _usage(response)
            response_model = getattr(response, "model", None)
            status = str(getattr(response, "status", "unknown"))
            failure_type: str | None = None
            if not isinstance(response_model, str) or not (
                response_model == MODEL_ID or response_model.startswith(f"{MODEL_ID}-")
            ):
                failure_type = "MODEL_MISMATCH"
            elif status != "completed":
                failure_type = "INCOMPLETE_RESPONSE"
            records.append(
                ProviderRequestRecord(
                    sequence=sequence,
                    requested_model=MODEL_ID,
                    response_model=response_model if isinstance(response_model, str) else None,
                    status=status.upper(),
                    latency_ms=round((time.monotonic() - started) * 1000),
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    failure_type=failure_type,
                )
            )
            if failure_type is not None:
                return response, records, failure_type
            return response, records, None
        return None, records, "PROVIDER_ERROR"

    @staticmethod
    def _parse_candidate(response: Any) -> tuple[dict[str, Any] | None, str | None]:
        if _has_refusal(response):
            return None, "REFUSAL"
        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            return None, "INCOMPLETE_RESPONSE"
        try:
            candidate = json.loads(output_text)
        except json.JSONDecodeError:
            return None, "SCHEMA_VIOLATION"
        if not isinstance(candidate, dict):
            return None, "SCHEMA_VIOLATION"
        return candidate, None

    def run_normalization(
        self, case: NormalizationCase, *, instructions: str
    ) -> ProviderCaseResult:
        payload = {
            "synthetic_data": True,
            "current_time": case.current_time.isoformat(),
            "timezone": case.timezone,
            "input": case.input.model_dump(mode="json"),
        }
        response, records, failure = self._request(
            model=MODEL_ID,
            instructions=instructions,
            input=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "normalization_result",
                    "strict": True,
                    "schema": NormalizedContent.model_json_schema(),
                }
            },
            reasoning={"effort": "low"},
            max_output_tokens=1200,
            store=False,
        )
        if response is None or failure is not None:
            return ProviderCaseResult(
                candidate=None,
                tool_trace=(),
                request_records=tuple(records),
                actual_model_executed=response is not None,
                failure_type=failure,
            )
        candidate, parse_failure = self._parse_candidate(response)
        return ProviderCaseResult(
            candidate=candidate,
            tool_trace=(),
            request_records=tuple(records),
            actual_model_executed=True,
            failure_type=parse_failure,
        )

    def run_counseling(self, case: CounselingCase, *, instructions: str) -> ProviderCaseResult:
        payload = {
            "synthetic_data": True,
            "scope": {
                "current_user": "server_bound_current_user",
                "selected_baby": "server_bound_selected_baby",
            },
            "current_time": case.current_time.isoformat(),
            "timezone": case.timezone,
            "conversation": [turn.model_dump(mode="json") for turn in case.conversation],
            "input": case.input,
        }
        input_items: list[Any] = [
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ]
        tools = tools_for_case(case)
        common: dict[str, Any] = {
            "model": MODEL_ID,
            "instructions": instructions,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "counseling_evaluation_result",
                    "strict": True,
                    "schema": CounselingOutput.model_json_schema(),
                }
            },
            "reasoning": {"effort": "low"},
            "max_output_tokens": 1400,
            "store": False,
        }
        if tools:
            common.update(
                {
                    "tools": tools,
                    "max_tool_calls": 4,
                    "parallel_tool_calls": False,
                    "tool_choice": ("required" if case.expected.required_tool_calls else "auto"),
                }
            )
        response, records, failure = self._request(input=input_items, **common)
        all_records = list(records)
        if response is None or failure is not None:
            return ProviderCaseResult(
                candidate=None,
                tool_trace=(),
                request_records=tuple(all_records),
                actual_model_executed=response is not None,
                failure_type=failure,
            )

        tool_calls = [
            item
            for item in getattr(response, "output", ())
            if getattr(item, "type", None) == "function_call"
        ]
        if not tool_calls:
            candidate, parse_failure = self._parse_candidate(response)
            return ProviderCaseResult(
                candidate=candidate,
                tool_trace=(),
                request_records=tuple(all_records),
                actual_model_executed=True,
                failure_type=parse_failure,
            )
        if len(tool_calls) > 4:
            return ProviderCaseResult(
                candidate=None,
                tool_trace=(),
                request_records=tuple(all_records),
                actual_model_executed=True,
                failure_type="TOOL_CALL_LIMIT",
            )

        input_items.extend(getattr(response, "output", ()))
        tool_trace: list[dict[str, Any]] = []
        for call in tool_calls:
            try:
                arguments = json.loads(call.arguments)
                if not isinstance(arguments, dict):
                    raise ToolExecutionError("INVALID_TOOL_ARGUMENTS")
                execution = execute_synthetic_tool(case, call.name, arguments)
            except (json.JSONDecodeError, ToolExecutionError) as exc:
                code = exc.code if isinstance(exc, ToolExecutionError) else "INVALID_TOOL_ARGUMENTS"
                return ProviderCaseResult(
                    candidate=None,
                    tool_trace=tuple(tool_trace),
                    request_records=tuple(all_records),
                    actual_model_executed=True,
                    failure_type=code,
                )
            tool_trace.append(execution.audit_dict())
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": execution.output,
                }
            )

        final_common = dict(common)
        if tools:
            final_common["tool_choice"] = "none"
        final_response, final_records, failure = self._request(
            input=input_items,
            **final_common,
        )
        all_records.extend(final_records)
        if final_response is None or failure is not None:
            return ProviderCaseResult(
                candidate=None,
                tool_trace=tuple(tool_trace),
                request_records=tuple(all_records),
                actual_model_executed=True,
                failure_type=failure,
            )
        if any(
            getattr(item, "type", None) == "function_call"
            for item in getattr(final_response, "output", ())
        ):
            return ProviderCaseResult(
                candidate=None,
                tool_trace=tuple(tool_trace),
                request_records=tuple(all_records),
                actual_model_executed=True,
                failure_type="TOOL_ROUND_LIMIT",
            )
        candidate, parse_failure = self._parse_candidate(final_response)
        return ProviderCaseResult(
            candidate=candidate,
            tool_trace=tuple(tool_trace),
            request_records=tuple(all_records),
            actual_model_executed=True,
            failure_type=parse_failure,
        )


def estimate_cost_usd(records: list[ProviderRequestRecord]) -> float | None:
    if any(record.input_tokens is None or record.output_tokens is None for record in records):
        return None
    total = 0.0
    for record in records:
        input_tokens = record.input_tokens or 0
        cached_tokens = record.cached_input_tokens or 0
        uncached_tokens = max(0, input_tokens - cached_tokens)
        output_tokens = record.output_tokens or 0
        total += uncached_tokens * INPUT_PRICE_USD_PER_MILLION / 1_000_000
        total += cached_tokens * CACHED_INPUT_PRICE_USD_PER_MILLION / 1_000_000
        total += output_tokens * OUTPUT_PRICE_USD_PER_MILLION / 1_000_000
    return round(total, 8)

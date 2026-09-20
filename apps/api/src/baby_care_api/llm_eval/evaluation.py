from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from baby_care_api.llm_eval.models import (
    CounselingCase,
    CounselingOutput,
    Evidence,
    NormalizationCase,
    NormalizedContent,
)


@dataclass(frozen=True)
class CheckResult:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class CandidateEvaluation:
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failure_codes(self) -> set[str]:
        return {check.code for check in self.checks if not check.passed}


def _check(code: str, condition: bool, detail: str) -> CheckResult:
    return CheckResult(code=code, passed=condition, detail=detail)


def _normalization_semantics(output: NormalizedContent) -> dict[str, Any]:
    payload = output.model_dump(mode="json")
    for collection_name in ("actions", "states", "outcomes", "caregiver_interpretations"):
        for item in payload[collection_name]:
            item.pop("evidence", None)
    for unresolved in payload["unresolved"]:
        unresolved["message"] = "<non-empty>" if unresolved["message"].strip() else ""
    return payload


def _all_evidence(output: NormalizedContent) -> Iterable[Evidence]:
    for action in output.actions:
        yield from action.evidence
    for state in output.states:
        yield from state.evidence
    for outcome in output.outcomes:
        yield from outcome.evidence
    for interpretation in output.caregiver_interpretations:
        yield from interpretation.evidence


def _evidence_matches_source(case: NormalizationCase, evidence: Evidence) -> bool:
    if evidence.source == "TEXT":
        if (
            evidence.span_start is None
            or evidence.span_end is None
            or evidence.quote is None
            or evidence.choice_id is not None
        ):
            return False
        if evidence.span_start > evidence.span_end:
            return False
        return case.input.raw_text[evidence.span_start : evidence.span_end] == evidence.quote
    if evidence.source == "CHOICE":
        choice_ids = {str(choice.get("choice_id")) for choice in case.input.choices}
        return (
            evidence.choice_id in choice_ids
            and evidence.span_start is None
            and evidence.span_end is None
            and evidence.quote is None
        )
    return evidence.quote is not None


def evaluate_normalization(
    case: NormalizationCase, candidate: dict[str, Any] | NormalizedContent
) -> CandidateEvaluation:
    try:
        parsed = (
            candidate
            if isinstance(candidate, NormalizedContent)
            else NormalizedContent.model_validate(candidate)
        )
    except ValidationError:
        return CandidateEvaluation(
            checks=(_check("normalization.schema", False, "candidate violates NormalizedContent"),)
        )

    expected = case.expected.output
    candidate_semantics = _normalization_semantics(parsed)
    expected_semantics = _normalization_semantics(expected)
    serialized = json.dumps(candidate_semantics, ensure_ascii=False).casefold()
    checks = [
        _check("normalization.schema", True, "candidate matches the strict schema"),
        _check(
            "normalization.semantics",
            candidate_semantics == expected_semantics,
            "actions, assertions, times, quantities, states, outcomes, interpretations, "
            "and unresolved fields match the independently defined gold data",
        ),
        _check(
            "normalization.evidence_spans",
            all(_evidence_matches_source(case, evidence) for evidence in _all_evidence(parsed)),
            "all evidence refers to the original text by Unicode code-point offsets or to "
            "an input choice",
        ),
        _check(
            "normalization.forbidden_output",
            not any(token.casefold() in serialized for token in case.expected.forbidden_outputs),
            "forbidden fabricated or injected values are absent",
        ),
    ]
    return CandidateEvaluation(checks=tuple(checks))


def _canonical_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    normalized = unit.strip().casefold()
    aliases = {
        "count": "COUNT",
        "회": "COUNT",
        "건": "COUNT",
        "번": "COUNT",
        "ml": "ML",
        "밀리리터": "ML",
        "minutes": "MINUTES",
        "minute": "MINUTES",
        "분": "MINUTES",
    }
    return aliases.get(normalized, unit.strip().upper())


def _claim_signature(
    *,
    kind: str,
    numeric_value: float | None,
    value_text: str | None,
    unit: str | None,
    evidence_ids: list[str],
) -> tuple[str, float | None, str | None, str | None, tuple[str, ...]]:
    is_numeric = numeric_value is not None
    scored_kind = "NUMBER" if is_numeric else kind
    return (
        scored_kind,
        numeric_value,
        None if is_numeric else value_text,
        _canonical_unit(unit),
        tuple(sorted(evidence_ids)),
    )


def _claim_projection(output: CounselingOutput) -> Counter[tuple[Any, ...]]:
    return Counter(
        _claim_signature(
            kind=claim.kind,
            numeric_value=claim.numeric_value,
            value_text=claim.value_text,
            unit=claim.unit,
            evidence_ids=claim.evidence_ids,
        )
        for claim in output.claims
    )


def _expected_claim_projection(case: CounselingCase) -> Counter[tuple[Any, ...]]:
    return Counter(
        _claim_signature(
            kind=claim.kind,
            numeric_value=claim.numeric_value,
            value_text=claim.value_text,
            unit=claim.unit,
            evidence_ids=claim.evidence_ids,
        )
        for claim in case.expected.claims
    )


def _arguments_within_expected_scope(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    if set(actual) != set(expected):
        return False
    for key, expected_value in expected.items():
        actual_value = actual[key]
        if key == "limit":
            if (
                not isinstance(actual_value, int)
                or isinstance(actual_value, bool)
                or actual_value < 1
                or actual_value > expected_value
            ):
                return False
        elif actual_value != expected_value:
            return False
    return True


def _tool_trace_matches(case: CounselingCase, tool_trace: list[dict[str, Any]]) -> bool:
    expected = [call.model_dump(mode="json") for call in case.expected.required_tool_calls]
    if len(tool_trace) != len(expected):
        return False
    return all(
        actual.get("tool_name") == expected_call["tool_name"]
        and isinstance(actual.get("arguments"), dict)
        and _arguments_within_expected_scope(actual["arguments"], expected_call["arguments"])
        for actual, expected_call in zip(tool_trace, expected, strict=True)
    )


def evaluate_counseling(
    case: CounselingCase,
    candidate: dict[str, Any] | CounselingOutput,
    *,
    tool_trace: list[dict[str, Any]],
) -> CandidateEvaluation:
    try:
        parsed = (
            candidate
            if isinstance(candidate, CounselingOutput)
            else CounselingOutput.model_validate(candidate)
        )
    except ValidationError:
        return CandidateEvaluation(
            checks=(_check("counseling.schema", False, "candidate violates the evaluation schema"),)
        )

    expected_claims = _expected_claim_projection(case)
    actual_claims = _claim_projection(parsed)
    allowed_evidence = {
        evidence_id for fixture in case.tool_fixtures for evidence_id in fixture.evidence_ids
    }
    allowed_evidence.update(case.expected.evidence_ids)
    cited_evidence = {evidence_id for claim in parsed.claims for evidence_id in claim.evidence_ids}
    required_markers = {
        evidence_id for claim in case.expected.claims for evidence_id in claim.evidence_ids
    }
    combined_text = "\n".join(
        [parsed.answer, *(claim.text for claim in parsed.claims), *parsed.limitations]
    ).casefold()
    term_groups_ok = all(
        any(term.casefold() in combined_text for term in group)
        for group in case.expected.required_answer_term_groups
    )

    checks = [
        _check("counseling.schema", True, "candidate matches the internal evaluation schema"),
        _check(
            "counseling.answer_mode",
            parsed.answer_mode == case.expected.answer_mode
            and parsed.personalization_status == case.expected.personalization_status,
            "answer and personalization modes preserve no-record, partial, unavailable, "
            "and access-denied meanings",
        ),
        _check(
            "counseling.claims",
            actual_claims == expected_claims,
            "verifiable claim kinds, numbers, normalized units, statuses, and evidence match "
            "the precomputed source of truth; evaluation-only fact_key wording is not scored",
        ),
        _check(
            "counseling.evidence",
            cited_evidence <= allowed_evidence
            and not (cited_evidence & set(case.expected.forbidden_evidence_ids))
            and all(f"[{item}]" in parsed.answer for item in required_markers),
            "claim evidence is allowed, current, and visibly cited",
        ),
        _check(
            "counseling.tool_trace",
            _tool_trace_matches(case, tool_trace),
            "only the expected scoped read tools were used with the expected arguments",
        ),
        _check(
            "counseling.record_candidates",
            [item.model_dump(mode="json") for item in parsed.record_candidates]
            == [item.model_dump(mode="json") for item in case.expected.record_candidates],
            "performed, planned, negated, and uncertain statements are not conflated",
        ),
        _check(
            "counseling.tool_failures",
            [item.model_dump(mode="json") for item in parsed.tool_failures]
            == [item.model_dump(mode="json") for item in case.expected.tool_failures],
            "lookup failure is not converted into no records or zero",
        ),
        _check(
            "counseling.required_terms",
            term_groups_ok,
            "required limitation or reviewed safety wording is present",
        ),
        _check(
            "counseling.forbidden_output",
            not any(token.casefold() in combined_text for token in case.expected.forbidden_outputs),
            "forbidden disclosure, certainty, or success wording is absent",
        ),
        _check(
            "counseling.no_write",
            parsed.writes_executed is False,
            "evaluation never reports a record or memory write",
        ),
    ]
    return CandidateEvaluation(checks=tuple(checks))

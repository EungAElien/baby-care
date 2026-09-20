from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from baby_care_api.llm_eval.datasets import PROMPT_ROOT, load_cases
from baby_care_api.llm_eval.evaluation import (
    CandidateEvaluation,
    evaluate_counseling,
    evaluate_normalization,
)
from baby_care_api.llm_eval.models import (
    COUNSELING_PROMPT_VERSION,
    COUNSELING_SCHEMA_VERSION,
    MODEL_ID,
    NORMALIZATION_PROMPT_VERSION,
    NORMALIZATION_SCHEMA_VERSION,
    CounselingCase,
    NormalizationCase,
)
from baby_care_api.llm_eval.provider import (
    PRICING_SOURCE,
    PRICING_VERIFIED_ON,
    OpenAIResponsesAdapter,
    ProviderBudget,
    ProviderRequestRecord,
    estimate_cost_usd,
)

DEFAULT_LIVE_NORMALIZATION_CASES = ("NORM-DEV-001", "NORM-DEV-006", "NORM-DEV-013")
DEFAULT_LIVE_COUNSELING_CASES = ("COUNSEL-DEV-004", "COUNSEL-DEV-011", "COUNSEL-DEV-017")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prompt_metadata() -> dict[str, Any]:
    normalization = (PROMPT_ROOT / "normalization.v2.md").read_text(encoding="utf-8")
    counseling = (PROMPT_ROOT / "counseling.v2.md").read_text(encoding="utf-8")
    return {
        "normalization": {
            "version": NORMALIZATION_PROMPT_VERSION,
            "schema_version": NORMALIZATION_SCHEMA_VERSION,
            "sha256": _sha256_text(normalization),
        },
        "counseling": {
            "version": COUNSELING_PROMPT_VERSION,
            "schema_version": COUNSELING_SCHEMA_VERSION,
            "sha256": _sha256_text(counseling),
        },
    }


def _check_dicts(evaluation: CandidateEvaluation) -> list[dict[str, Any]]:
    return [
        {"code": check.code, "passed": check.passed, "detail": check.detail}
        for check in evaluation.checks
    ]


def _aggregate_checks(case_results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for case_result in case_results:
        for check in case_result["checks"]:
            counts = summary.setdefault(check["code"], {"pass": 0, "fail": 0})
            counts["pass" if check["passed"] else "fail"] += 1
    return dict(sorted(summary.items()))


def _split_counts(case_results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        split: sum(str(item["split"]) == split for item in case_results)
        for split in ("PROMPT_TUNING", "FINAL_CONFIRMATION")
    }


def _offline_tool_trace(case: CounselingCase) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for expected_call in case.expected.required_tool_calls:
        fixture = next(
            fixture
            for fixture in case.tool_fixtures
            if fixture.tool_name == expected_call.tool_name
            and fixture.arguments == expected_call.arguments
        )
        trace.append(
            {
                "tool_name": expected_call.tool_name,
                "arguments": expected_call.arguments,
                "status": fixture.status,
                "evidence_ids": fixture.evidence_ids,
            }
        )
    return trace


def _negative_probe_results(
    case: NormalizationCase | CounselingCase,
    *,
    tool_trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for probe in case.adversarial_candidates:
        if isinstance(case, NormalizationCase):
            evaluation = evaluate_normalization(case, probe.candidate)
        else:
            evaluation = evaluate_counseling(
                case,
                probe.candidate,
                tool_trace=tool_trace,
            )
        detected_codes = sorted(evaluation.failure_codes)
        expected_codes = set(probe.expected_failure_codes)
        results.append(
            {
                "candidate_id": probe.candidate_id,
                "purpose": probe.purpose,
                "expected_failure_codes": sorted(expected_codes),
                "detected_failure_codes": detected_codes,
                "passed": not evaluation.passed and expected_codes <= evaluation.failure_codes,
            }
        )
    return results


def _base_report(mode: str) -> dict[str, Any]:
    return {
        "report_version": "baby-care.llm-eval.report.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "data_origin": "SYNTHETIC",
        "requested_model": MODEL_ID,
        "prompt_and_schema": _prompt_metadata(),
        "pricing": {
            "source": PRICING_SOURCE,
            "verified_on": PRICING_VERIFIED_ON,
            "estimate_status": "NOT_APPLICABLE_OFFLINE" if mode == "offline" else "PENDING_USAGE",
        },
        "limits": {
            "normalization_cases": 3 if mode == "live-smoke" else None,
            "counseling_cases": 3 if mode == "live-smoke" else None,
            "max_provider_requests": 12 if mode == "live-smoke" else 0,
            "concurrency": 1,
            "per_request_timeout_seconds": 45 if mode == "live-smoke" else None,
            "total_wall_time_seconds": 360 if mode == "live-smoke" else None,
            "normalization_max_output_tokens": 1200 if mode == "live-smoke" else None,
            "counseling_max_output_tokens": 1400 if mode == "live-smoke" else None,
            "max_tool_calls_per_case": 4 if mode == "live-smoke" else 0,
            "max_tool_rounds_per_case": 1 if mode == "live-smoke" else 0,
            "max_retries_per_request": 1 if mode == "live-smoke" else 0,
        },
        "actual_model_executed": False,
        "provider_request_count": 0,
        "cases": [],
    }


def run_offline() -> dict[str, Any]:
    report = _base_report("offline")
    case_results: list[dict[str, Any]] = []
    for case in load_cases():
        if isinstance(case, NormalizationCase):
            evaluation = evaluate_normalization(case, case.expected.output)
            tool_trace: list[dict[str, Any]] = []
        else:
            tool_trace = _offline_tool_trace(case)
            evaluation = evaluate_counseling(
                case,
                case.offline_candidate,
                tool_trace=tool_trace,
            )
        negative_probes = _negative_probe_results(case, tool_trace=tool_trace)
        case_results.append(
            {
                "case_id": case.case_id,
                "suite": case.suite,
                "split": case.split,
                "synthetic_data": True,
                "candidate_source": "SYNTHETIC_GOLD_REPLAY",
                "actual_model_executed": False,
                "automated_pass": evaluation.passed,
                "checks": _check_dicts(evaluation),
                "negative_probes": negative_probes,
                "negative_probes_passed": all(item["passed"] for item in negative_probes),
                "human_review_status": "PENDING",
                "human_review_items": case.human_review,
            }
        )
    report["cases"] = case_results
    automated_pass = all(
        item["automated_pass"] and item["negative_probes_passed"] for item in case_results
    )
    report["summary"] = {
        "normalization_case_count": sum(item["suite"] == "normalization" for item in case_results),
        "counseling_case_count": sum(item["suite"] == "counseling" for item in case_results),
        "automated_pass_count": sum(item["automated_pass"] for item in case_results),
        "automated_fail_count": sum(not item["automated_pass"] for item in case_results),
        "negative_probe_count": sum(len(item["negative_probes"]) for item in case_results),
        "negative_probe_fail_count": sum(
            not probe["passed"] for item in case_results for probe in item["negative_probes"]
        ),
        "split_counts": _split_counts(case_results),
        "check_summary": _aggregate_checks(case_results),
        "human_review_pending_count": len(case_results),
        "decision": (
            "AUTOMATED_PASS_HUMAN_REVIEW_PENDING"
            if automated_pass
            else "AUTOMATED_VALIDATION_FAILED"
        ),
        "scope_note": (
            "This verifies synthetic dataset and evaluator behavior only. It is not a provider, "
            "product API, database authorization, deletion, or production-quality result."
        ),
    }
    return report


def _read_api_key() -> str | None:
    return os.getenv("BABY_CARE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")


def run_live_smoke(*, allow_provider_calls: bool) -> dict[str, Any]:
    report = _base_report("live-smoke")
    if not allow_provider_calls:
        report["pricing"]["estimate_status"] = "NOT_RUN"
        report["summary"] = {
            "decision": "NOT_RUN_EXPLICIT_OPT_IN_REQUIRED",
            "failure_type": "PROVIDER_CALLS_NOT_ALLOWED",
        }
        return report
    api_key = _read_api_key()
    if api_key is None:
        report["pricing"]["estimate_status"] = "NOT_RUN"
        report["summary"] = {
            "decision": "NOT_RUN_MISSING_API_KEY",
            "failure_type": "MISSING_API_KEY",
        }
        return report

    cases = load_cases()
    selected_ids = set(DEFAULT_LIVE_NORMALIZATION_CASES + DEFAULT_LIVE_COUNSELING_CASES)
    selected = [case for case in cases if case.case_id in selected_ids]
    if len(selected) != 6:
        raise RuntimeError("live smoke selection must contain exactly three cases per suite")
    budget = ProviderBudget(max_requests=12, max_elapsed_seconds=360)
    adapter = OpenAIResponsesAdapter(
        api_key=api_key,
        budget=budget,
        timeout_seconds=45,
        max_retries_per_request=1,
        organization=os.getenv("OPENAI_ORG_ID"),
        project=os.getenv("OPENAI_PROJECT"),
    )
    normalization_prompt = (PROMPT_ROOT / "normalization.v2.md").read_text(encoding="utf-8")
    counseling_prompt = (PROMPT_ROOT / "counseling.v2.md").read_text(encoding="utf-8")
    case_results: list[dict[str, Any]] = []
    all_request_records: list[ProviderRequestRecord] = []
    for case in selected:  # Intentionally serial: concurrency is fixed at one.
        if isinstance(case, NormalizationCase):
            provider_result = adapter.run_normalization(case, instructions=normalization_prompt)
            evaluation = (
                evaluate_normalization(case, provider_result.candidate)
                if provider_result.candidate is not None
                else None
            )
        else:
            provider_result = adapter.run_counseling(case, instructions=counseling_prompt)
            evaluation = (
                evaluate_counseling(
                    case,
                    provider_result.candidate,
                    tool_trace=list(provider_result.tool_trace),
                )
                if provider_result.candidate is not None
                else None
            )
        all_request_records.extend(provider_result.request_records)
        case_results.append(
            {
                "case_id": case.case_id,
                "suite": case.suite,
                "split": case.split,
                "synthetic_data": True,
                "candidate_source": "OPENAI_RESPONSES_API",
                "actual_model_executed": provider_result.actual_model_executed,
                "failure_type": provider_result.failure_type,
                "automated_pass": evaluation.passed if evaluation is not None else False,
                "checks": _check_dicts(evaluation) if evaluation is not None else [],
                "tool_trace": list(provider_result.tool_trace),
                "request_records": [item.as_dict() for item in provider_result.request_records],
                "candidate": provider_result.candidate,
                "human_review_status": "PENDING",
                "human_review_items": case.human_review,
            }
        )
        if provider_result.failure_type in {
            "PROVIDER_REQUEST_LIMIT",
            "PROVIDER_WALL_TIME_LIMIT",
            "AUTHENTICATION_FAILED",
            "MODEL_ACCESS_UNAVAILABLE",
            "LIMIT_OR_QUOTA_EXCEEDED",
        }:
            break

    report["actual_model_executed"] = any(item["actual_model_executed"] for item in case_results)
    report["provider_request_count"] = budget.request_count
    report["cases"] = case_results
    cost = estimate_cost_usd(all_request_records)
    report["pricing"]["estimate_status"] = (
        "ESTIMATED_FROM_REPORTED_TOKENS" if cost is not None else "USAGE_UNAVAILABLE"
    )
    report["pricing"]["estimated_cost_usd"] = cost
    all_six_completed = len(case_results) == 6
    all_automated = all(item["automated_pass"] for item in case_results)
    report["summary"] = {
        "completed_case_count": len(case_results),
        "automated_pass_count": sum(item["automated_pass"] for item in case_results),
        "automated_fail_count": sum(not item["automated_pass"] for item in case_results),
        "split_counts": _split_counts(case_results),
        "check_summary": _aggregate_checks(case_results),
        "human_review_pending_count": len(case_results),
        "decision": (
            "SMOKE_AUTOMATED_PASS_HUMAN_REVIEW_PENDING"
            if all_six_completed and all_automated
            else "SMOKE_NOT_PASSED"
        ),
        "scope_note": (
            "A limited synthetic smoke is connection evidence only. It does not establish full "
            "evaluation quality, production performance, product authorization, or child-data "
            "approval."
        ),
    }
    return report


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

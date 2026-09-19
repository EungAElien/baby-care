from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from baby_care_api.llm_eval.models import CounselingCase, EvaluationCase, NormalizationCase

API_ROOT = Path(__file__).resolve().parents[3]
EVAL_ROOT = API_ROOT / "evals" / "llm_prevalidation"
DATASET_ROOT = EVAL_ROOT / "datasets"
PROMPT_ROOT = EVAL_ROOT / "prompts"
NORMALIZATION_DATASET = DATASET_ROOT / "normalization.v1.jsonl"
COUNSELING_DATASET = DATASET_ROOT / "counseling.v1.jsonl"

_CASE_ADAPTER: TypeAdapter[EvaluationCase] = TypeAdapter(EvaluationCase)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: case must be an object")
        rows.append(value)
    return rows


def load_cases() -> list[NormalizationCase | CounselingCase]:
    parsed = [
        _CASE_ADAPTER.validate_python(row)
        for path in (NORMALIZATION_DATASET, COUNSELING_DATASET)
        for row in _load_jsonl(path)
    ]
    identifiers = [case.case_id for case in parsed]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("case_id values must be globally unique")
    return parsed


def load_normalization_cases() -> list[NormalizationCase]:
    return [case for case in load_cases() if isinstance(case, NormalizationCase)]


def load_counseling_cases() -> list[CounselingCase]:
    return [case for case in load_cases() if isinstance(case, CounselingCase)]

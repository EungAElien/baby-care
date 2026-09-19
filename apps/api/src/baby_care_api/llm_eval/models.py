from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_ID = "gpt-5.6-terra"
CASE_FORMAT_VERSION = "baby-care.llm-eval.case.v1"
NORMALIZATION_PROMPT_VERSION = "normalization.2026-09-20.v1"
NORMALIZATION_SCHEMA_VERSION = "openapi-1.1.1.NormalizedContent"
COUNSELING_PROMPT_VERSION = "counseling.2026-09-20.v1"
COUNSELING_SCHEMA_VERSION = "counseling-eval-output.v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationSplit(StrEnum):
    PROMPT_TUNING = "PROMPT_TUNING"
    FINAL_CONFIRMATION = "FINAL_CONFIRMATION"


class EvidenceSource(StrEnum):
    CHOICE = "CHOICE"
    TEXT = "TEXT"
    USER_CORRECTION = "USER_CORRECTION"


class ActionCode(StrEnum):
    FEEDING = "FEEDING"
    DIAPER_CHECK = "DIAPER_CHECK"
    DIAPER_CHANGE = "DIAPER_CHANGE"
    HOLDING = "HOLDING"
    BURPING = "BURPING"
    SLEEP_PREPARATION = "SLEEP_PREPARATION"
    ENVIRONMENT_ADJUSTMENT = "ENVIRONMENT_ADJUSTMENT"
    OTHER = "OTHER"


class Assertion(StrEnum):
    PERFORMED = "PERFORMED"
    PLANNED = "PLANNED"
    NEGATED = "NEGATED"
    UNCERTAIN = "UNCERTAIN"


class TimePrecision(StrEnum):
    EXACT = "EXACT"
    RELATIVE = "RELATIVE"
    UNKNOWN = "UNKNOWN"


class Evidence(StrictModel):
    source: EvidenceSource
    choice_id: str | None
    span_start: Annotated[int, Field(ge=0)] | None
    span_end: Annotated[int, Field(ge=0)] | None
    quote: str | None


class DraftAction(StrictModel):
    action_ref: str
    action_code: ActionCode
    assertion: Assertion
    performed_by_user_id: UUID | None
    occurred_at: datetime | None
    relative_time: str | None
    time_precision: TimePrecision
    sequence: Annotated[int, Field(ge=1)]
    amount: Annotated[float, Field(ge=0)] | None
    unit: Literal["ML", "MINUTES"] | None
    feeding_mode: Literal["BREAST", "FORMULA", "MIXED", "UNSPECIFIED"] | None
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class DraftState(StrictModel):
    state_codes: Annotated[
        list[
            Literal[
                "CRYING",
                "FUSSING",
                "CALM",
                "SLEEPY_APPEARING",
                "ASLEEP",
                "AWAKE",
                "CHEERFUL_APPEARING",
                "UNKNOWN",
            ]
        ],
        Field(min_length=1),
    ]
    phase: Literal["BEFORE", "AFTER", "UNRELATED", "UNKNOWN"]
    observed_at: datetime | None
    time_precision: TimePrecision
    linked_action_refs: list[str]
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class DraftOutcome(StrictModel):
    response_code: Literal["CALMED", "PARTIALLY_CALMED", "NO_CHANGE", "CRYING_AGAIN", "UNKNOWN"]
    observed_at: datetime | None
    time_precision: TimePrecision
    linked_action_refs: list[str]
    attribution: Literal["SINGLE", "MULTI", "UNKNOWN"]
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class Interpretation(StrictModel):
    text: str
    certainty: Literal["CAREGIVER_REPORTED"]
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class Unresolved(StrictModel):
    field: str
    code: Literal[
        "CONFLICT", "UNKNOWN_VALUE", "UNKNOWN_TIME", "UNSUPPORTED_CODE", "MISSING_EVIDENCE"
    ]
    message: str


class NormalizedContent(StrictModel):
    """Mirrors OpenAPI 1.1.1 ``NormalizedContent`` for evaluation only."""

    actions: list[DraftAction]
    states: list[DraftState]
    outcomes: list[DraftOutcome]
    caregiver_interpretations: list[Interpretation]
    unresolved: list[Unresolved]


class ConversationTurn(StrictModel):
    role: Literal["user", "assistant"]
    content: str


class AdversarialCandidate(StrictModel):
    candidate_id: str
    purpose: str
    candidate: dict[str, Any]
    expected_failure_codes: Annotated[list[str], Field(min_length=1)]


class NormalizationInput(StrictModel):
    raw_text: str
    choices: list[dict[str, Any]]


class NormalizationExpected(StrictModel):
    facts: list[str]
    numbers: list[dict[str, Any]]
    evidence_spans: list[Evidence]
    allowed_actions: list[str]
    forbidden_outputs: list[str]
    output: NormalizedContent


class ToolFixture(StrictModel):
    tool_name: str
    arguments: dict[str, Any]
    status: Literal[
        "OK", "NO_RECORDS", "FAILED", "NOT_READY", "PARTIAL", "ACCESS_DENIED", "DELETED"
    ]
    payload: dict[str, Any]
    evidence_ids: list[str]


class ExpectedToolCall(StrictModel):
    tool_name: str
    arguments: dict[str, Any]


class CounselingClaim(StrictModel):
    fact_key: str
    kind: Literal["FACT", "NUMBER", "LIMITATION", "SAFETY"]
    text: str
    numeric_value: float | None
    value_text: str | None
    unit: str | None
    evidence_ids: list[str]


class RecordCandidate(StrictModel):
    source_text: str
    action_code: ActionCode | None
    assertion: Assertion
    requires_confirmation: Literal[True]
    evidence_ids: list[str]


class ToolFailure(StrictModel):
    tool_name: str
    code: str
    retryable: bool


class CounselingOutput(StrictModel):
    """Internal evaluation envelope; this is not the B-14 product API contract."""

    answer: str
    answer_mode: Literal[
        "GENERAL",
        "RECORD_BASED",
        "NEEDS_CLARIFICATION",
        "UNAVAILABLE",
        "ACCESS_DENIED",
        "SAFETY_GUIDANCE",
    ]
    personalization_status: Literal[
        "NOT_NEEDED",
        "VERIFIED_RECORDS",
        "NO_RECORDS",
        "PARTIAL",
        "UNAVAILABLE",
        "ACCESS_DENIED",
    ]
    claims: list[CounselingClaim]
    record_candidates: list[RecordCandidate]
    tool_failures: list[ToolFailure]
    limitations: list[str]
    follow_up_question: str | None
    writes_executed: Literal[False]


class ExpectedClaim(StrictModel):
    fact_key: str
    kind: Literal["FACT", "NUMBER", "LIMITATION", "SAFETY"]
    numeric_value: float | None
    value_text: str | None
    unit: str | None
    evidence_ids: list[str]


class CounselingExpected(StrictModel):
    facts: list[str]
    numbers: list[dict[str, Any]]
    evidence_ids: list[str]
    required_tool_calls: list[ExpectedToolCall]
    answer_mode: str
    personalization_status: str
    claims: list[ExpectedClaim]
    record_candidates: list[RecordCandidate]
    tool_failures: list[ToolFailure]
    allowed_actions: list[str]
    forbidden_outputs: list[str]
    forbidden_evidence_ids: list[str]
    required_answer_term_groups: list[list[str]]


class BaseCase(StrictModel):
    format_version: Literal["baby-care.llm-eval.case.v1"]
    case_id: str
    split: EvaluationSplit
    synthetic_data: Literal[True]
    purpose: str
    related_requirements: Annotated[list[str], Field(min_length=1)]
    current_time: datetime
    timezone: str
    conversation: Annotated[list[ConversationTurn], Field(min_length=1)]
    source_records: list[dict[str, Any]]
    allowed_tools: list[str]
    tool_fixtures: list[ToolFixture]
    automatic_checks: Annotated[list[str], Field(min_length=1)]
    human_review: Annotated[list[str], Field(min_length=1)]
    adversarial_candidates: list[AdversarialCandidate]


class NormalizationCase(BaseCase):
    suite: Literal["normalization"]
    input: NormalizationInput
    expected: NormalizationExpected

    @model_validator(mode="after")
    def normalization_has_no_tools(self) -> NormalizationCase:
        if self.allowed_tools or self.tool_fixtures:
            raise ValueError("normalization cases cannot expose tools")
        return self


class CounselingCase(BaseCase):
    suite: Literal["counseling"]
    input: dict[str, Any]
    expected: CounselingExpected
    offline_candidate: CounselingOutput


EvaluationCase = Annotated[
    NormalizationCase | CounselingCase,
    Field(discriminator="suite"),
]

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, PositiveInt, model_validator

from baby_care_api.models.base import ContractModel
from baby_care_api.models.care_events import DataOrigin, TimePrecision

MODEL_ID = "gpt-5.6-terra"
NORMALIZATION_PROMPT_VERSION = "normalization.2026-09-20.v1"
NORMALIZATION_SCHEMA_VERSION = "openapi-1.2.0.NormalizedContent"
NORMALIZATION_ONTOLOGY_VERSION = "care-v1"
VISUAL_MAPPING_VERSION = "care-visual-v1"


class StrictContractModel(ContractModel):
    model_config = ConfigDict(extra="forbid")


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


class Evidence(StrictContractModel):
    source: EvidenceSource
    choice_id: str | None
    span_start: Annotated[int, Field(ge=0)] | None
    span_end: Annotated[int, Field(ge=0)] | None
    quote: str | None


class DraftAction(StrictContractModel):
    action_ref: str = Field(min_length=1, max_length=80)
    action_code: ActionCode
    assertion: Assertion
    performed_by_user_id: UUID | None
    occurred_at: AwareDatetime | None
    relative_time: str | None
    time_precision: TimePrecision
    sequence: PositiveInt
    amount: Annotated[float, Field(ge=0)] | None
    unit: Literal["ML", "MINUTES"] | None
    feeding_mode: Literal["BREAST", "FORMULA", "MIXED", "UNSPECIFIED"] | None
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class DraftState(StrictContractModel):
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
    observed_at: AwareDatetime | None
    time_precision: TimePrecision
    linked_action_refs: list[str]
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class DraftOutcome(StrictContractModel):
    response_code: Literal["CALMED", "PARTIALLY_CALMED", "NO_CHANGE", "CRYING_AGAIN", "UNKNOWN"]
    observed_at: AwareDatetime | None
    time_precision: TimePrecision
    linked_action_refs: list[str]
    attribution: Literal["SINGLE", "MULTI", "UNKNOWN"]
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class Interpretation(StrictContractModel):
    text: str = Field(min_length=1, max_length=2000)
    certainty: Literal["CAREGIVER_REPORTED"]
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class Unresolved(StrictContractModel):
    field: str = Field(min_length=1, max_length=200)
    code: Literal[
        "CONFLICT", "UNKNOWN_VALUE", "UNKNOWN_TIME", "UNSUPPORTED_CODE", "MISSING_EVIDENCE"
    ]
    message: str = Field(min_length=1, max_length=500)


class NormalizedContent(StrictContractModel):
    actions: list[DraftAction]
    states: list[DraftState]
    outcomes: list[DraftOutcome]
    caregiver_interpretations: list[Interpretation]
    unresolved: list[Unresolved]


class CreateNormalization(StrictContractModel):
    client_request_id: UUID
    run_id: UUID
    input_revision: PositiveInt


class NormalizationFailure(StrictContractModel):
    code: Literal[
        "NORMALIZATION_DISABLED",
        "NORMALIZATION_CREDENTIALS_MISSING",
        "NORMALIZATION_DEPENDENCY_MISSING",
        "NORMALIZATION_TIMEOUT",
        "NORMALIZATION_LEASE_EXPIRED",
        "NORMALIZATION_REFUSED",
        "NORMALIZATION_INCOMPLETE",
        "NORMALIZATION_SCHEMA_INVALID",
        "NORMALIZATION_PROVIDER_ERROR",
        "SOURCE_DELETED",
        "ACCESS_REVOKED",
    ]
    message: str
    retryable: bool


class NormalizationRun(StrictContractModel):
    run_id: UUID
    entry_id: UUID
    input_revision: PositiveInt
    status: Literal["RUNNING", "COMPLETE", "FAILED", "STALE"]
    lease_expires_at: AwareDatetime | None
    execution_mode: Literal["REAL"]
    provider_call_executed: bool
    provider: Literal["openai"]
    model: Literal["gpt-5.6-terra"]
    prompt_version: str
    schema_version: str
    ontology_version: str
    result: NormalizedContent | None
    failure: NormalizationFailure | None
    recorded_at: AwareDatetime
    completed_at: AwareDatetime | None


class RecordVersionInput(StrictContractModel):
    resource_type: Literal["CARE_EVENT", "OUTCOME", "STATE_OBSERVATION", "ACTION"]
    resource_id: UUID
    version: PositiveInt


class ConfirmCareEntry(StrictContractModel):
    client_request_id: UUID
    input_revision: PositiveInt
    run_id: UUID | None
    normalization_mode: Literal["LLM", "RULE", "MANUAL"]
    content: NormalizedContent
    base_record_versions: list[RecordVersionInput]

    @model_validator(mode="after")
    def validate_run_mode(self) -> Self:
        if (self.normalization_mode == "LLM") != (self.run_id is not None):
            raise ValueError("LLM requires run_id; RULE and MANUAL require null run_id")
        return self


class ConfirmedResources(StrictContractModel):
    entry_id: UUID
    input_revision: PositiveInt
    care_event_ids: list[UUID]
    action_ids: list[UUID]
    action_group_ids: list[UUID]
    state_observation_ids: list[UUID]
    outcome_ids: list[UUID]
    label_annotation_ids: list[UUID]


class StateObservation(StrictContractModel):
    state_observation_id: UUID
    baby_id: UUID
    episode_id: UUID | None
    action_id: UUID | None
    source_entry_id: UUID | None
    phase: Literal["BEFORE", "AFTER", "UNRELATED", "UNKNOWN"]
    observed_at: AwareDatetime | None
    time_precision: TimePrecision
    state_codes: list[
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
    ]
    observation_source: Literal["SELF_REPORTED", "REPORTED_BY_OTHER"]
    confirmation_status: Literal["USER_CONFIRMED", "USER_CORRECTED"]
    visual_state_code: Literal[
        "CRYING",
        "FUSSING",
        "CALM",
        "SLEEPY_APPEARING",
        "ASLEEP",
        "AWAKE",
        "CHEERFUL_APPEARING",
        "NEUTRAL",
    ]
    visual_mapping_version: str
    created_by_user_id: UUID
    updated_by_user_id: UUID
    confirmed_by_user_id: UUID
    data_origin: DataOrigin
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, NonNegativeFloat, PositiveInt

from baby_care_api.models.base import ContractModel


class DataOrigin(StrEnum):
    USER = "USER"
    DEMO = "DEMO"


class LifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETING = "DELETING"
    DELETED = "DELETED"


class TimePrecision(StrEnum):
    EXACT = "EXACT"
    RELATIVE = "RELATIVE"
    UNKNOWN = "UNKNOWN"


class FeedingMode(StrEnum):
    BREAST = "BREAST"
    FORMULA = "FORMULA"
    MIXED = "MIXED"
    UNSPECIFIED = "UNSPECIFIED"


class DiaperOperation(StrEnum):
    CHECK = "CHECK"
    CHANGE = "CHANGE"


class DiaperCondition(StrEnum):
    WET = "WET"
    STOOL = "STOOL"
    BOTH = "BOTH"
    CLEAN = "CLEAN"
    UNKNOWN = "UNKNOWN"


class SootheActionKind(StrEnum):
    HOLDING = "HOLDING"
    BURPING = "BURPING"
    SLEEP_PREPARATION = "SLEEP_PREPARATION"
    ENVIRONMENT_ADJUSTMENT = "ENVIRONMENT_ADJUSTMENT"
    OTHER = "OTHER"


class FeedingPayload(ContractModel):
    mode: FeedingMode
    amount_ml: NonNegativeFloat | None
    duration_minutes: NonNegativeFloat | None


class SleepPayload(ContractModel):
    pass


class DiaperPayload(ContractModel):
    operation: DiaperOperation
    condition: DiaperCondition


class SoothePayload(ContractModel):
    action_kind: SootheActionKind


class FeedingEventValue(ContractModel):
    type: Literal["FEEDING"]
    occurred_at: AwareDatetime | None
    ended_at: AwareDatetime | None
    time_precision: TimePrecision
    payload: FeedingPayload


class SleepEventValue(ContractModel):
    type: Literal["SLEEP"]
    occurred_at: AwareDatetime
    ended_at: AwareDatetime | None
    time_precision: Literal["EXACT"]
    payload: SleepPayload


class DiaperEventValue(ContractModel):
    type: Literal["DIAPER"]
    occurred_at: AwareDatetime | None
    ended_at: AwareDatetime | None
    time_precision: TimePrecision
    payload: DiaperPayload


class SootheEventValue(ContractModel):
    type: Literal["SOOTHE"]
    occurred_at: AwareDatetime | None
    ended_at: AwareDatetime | None
    time_precision: TimePrecision
    payload: SoothePayload


type CareEventValue = Annotated[
    FeedingEventValue | SleepEventValue | DiaperEventValue | SootheEventValue,
    Field(discriminator="type"),
]


class CreateCareEvent(ContractModel):
    client_request_id: UUID
    event: CareEventValue


class PatchCareEvent(ContractModel):
    client_request_id: UUID
    version: PositiveInt
    event: CareEventValue


class CareEvent(ContractModel):
    care_event_id: UUID
    baby_id: UUID
    created_by_user_id: UUID
    updated_by_user_id: UUID
    source_entry_id: UUID | None
    status: LifecycleStatus
    event: CareEventValue
    data_origin: DataOrigin
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime

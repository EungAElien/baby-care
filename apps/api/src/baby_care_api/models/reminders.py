from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, NonNegativeFloat, NonNegativeInt, PositiveInt

from baby_care_api.models.audio import SourceRef
from baby_care_api.models.base import ContractModel

type ReminderKind = Literal["FEEDING", "SLEEP_PREPARATION", "DIAPER"]
type PatternHoldReason = Literal[
    "INSUFFICIENT_RECORDS", "HIGH_VARIABILITY", "UNCONFIRMED_DAYS", "MANUAL_ONLY"
]


class Pattern(ContractModel):
    kind: ReminderKind
    status: Literal["READY", "ON_HOLD"]
    reason: PatternHoldReason | None
    valid_days: NonNegativeInt
    interval_count: NonNegativeInt
    median_minutes: NonNegativeFloat | None
    p25_minutes: NonNegativeFloat | None
    p75_minutes: NonNegativeFloat | None
    anchor_event_id: UUID | None
    estimated_due_at: AwareDatetime | None
    policy_version: str


class Patterns(ContractModel):
    baby_id: UUID
    range_days: Literal[7] = 7
    as_of: AwareDatetime
    items: list[Pattern]


class Reminder(ContractModel):
    reminder_id: UUID
    baby_id: UUID
    recipient_user_id: UUID
    kind: ReminderKind
    anchor_event_id: UUID | None
    state: Literal["SCHEDULED", "DUE", "SNOOZED", "DISMISSED", "EXPIRED"]
    due_at: AwareDatetime
    snoozed_until: AwareDatetime | None
    seen_at: AwareDatetime | None
    evidence_refs: list[SourceRef]
    policy_version: str
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime


class ReminderPage(ContractModel):
    items: list[Reminder]
    next_cursor: str | None = None


class PatchReminder(ContractModel):
    client_request_id: UUID
    version: PositiveInt
    action: Literal["SNOOZE_10_MIN", "DISMISS_OCCURRENCE", "MARK_SEEN"]


class ReminderSetting(ContractModel):
    baby_id: UUID
    user_id: UUID
    kind: ReminderKind
    enabled: bool
    lead_minutes: Literal[10] = 10
    manual_interval_minutes: PositiveInt | None
    version: PositiveInt


class ReminderSettings(ContractModel):
    items: list[ReminderSetting]


class SetReminderSetting(ContractModel):
    client_request_id: UUID
    kind: ReminderKind
    enabled: bool
    manual_interval_minutes: PositiveInt | None
    version: NonNegativeInt


class SetRecordCoverage(ContractModel):
    client_request_id: UUID
    date: date
    type: Literal["FEEDING", "SLEEP", "DIAPER"]
    confirmed: bool
    version: NonNegativeInt


class PatternRecord(ContractModel):
    event_id: UUID
    kind: ReminderKind
    occurred_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    feeding_mode: Literal["BREAST", "FORMULA", "MIXED", "UNSPECIFIED"] | None = None
    version: PositiveInt


class PatternEvidence(ContractModel):
    pattern: Pattern
    source_refs: list[SourceRef] = Field(default_factory=list)

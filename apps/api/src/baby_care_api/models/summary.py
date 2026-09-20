from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, NonNegativeFloat, NonNegativeInt, PositiveInt

from baby_care_api.models.audio import StateObservation
from baby_care_api.models.base import ContractModel


class FeedingSummary(ContractModel):
    record_count: NonNegativeInt
    known_amount_count: NonNegativeInt
    unknown_amount_count: NonNegativeInt
    total_recorded_ml: NonNegativeFloat | None
    breastfeeding_minutes: NonNegativeFloat | None


class SleepSummary(ContractModel):
    record_count: NonNegativeInt
    recorded_minutes_in_day: NonNegativeFloat
    active_sleep_id: UUID | None
    has_unknown_duration: bool


class DiaperSummary(ContractModel):
    check_count: NonNegativeInt
    change_count: NonNegativeInt


class RecordCoverage(ContractModel):
    baby_id: UUID
    date: date
    type: Literal["FEEDING", "SLEEP", "DIAPER"]
    confirmed: bool
    updated_by_user_id: UUID
    version: PositiveInt


class DailySummary(ContractModel):
    baby_id: UUID
    date: date
    timezone: str
    as_of: AwareDatetime
    context_revision: NonNegativeInt
    has_records: bool
    feeding: FeedingSummary
    sleep: SleepSummary
    diaper: DiaperSummary
    record_coverage: list[RecordCoverage]
    latest_confirmed_state: StateObservation | None
    missing_fields: list[str]

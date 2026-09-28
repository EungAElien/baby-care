from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, NonNegativeInt, PositiveInt

from baby_care_api.models.base import ContractModel


class ChangeResourceType(StrEnum):
    BABY = "BABY"
    MEMBERSHIP = "MEMBERSHIP"
    CARE_EVENT = "CARE_EVENT"
    EPISODE = "EPISODE"
    ANALYSIS = "ANALYSIS"
    RECOMMENDATION = "RECOMMENDATION"
    STATE_OBSERVATION = "STATE_OBSERVATION"
    OUTCOME = "OUTCOME"
    DELETION = "DELETION"


class Change(ContractModel):
    resource_type: ChangeResourceType
    resource_id: UUID
    version: PositiveInt
    deleted: bool


class Changes(ContractModel):
    baby_id: UUID
    current_revision: NonNegativeInt
    changes: list[Change] = Field(max_length=500)
    resync_required: bool
    server_time: AwareDatetime

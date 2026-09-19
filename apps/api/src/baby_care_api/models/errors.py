from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import NonNegativeFloat, PositiveInt

from baby_care_api.models.base import ContractModel
from baby_care_api.models.care_events import CareEvent


class ErrorCode(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    INVALID_TOKEN = "INVALID_TOKEN"
    OWNER_ONLY = "OWNER_ONLY"
    AUTHOR_ONLY = "AUTHOR_ONLY"
    INVITE_EMAIL_MISMATCH = "INVITE_EMAIL_MISMATCH"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    SOURCE_REVISION_CHANGED = "SOURCE_REVISION_CHANGED"
    ANALYSIS_IN_PROGRESS = "ANALYSIS_IN_PROGRESS"
    NORMALIZATION_IN_PROGRESS = "NORMALIZATION_IN_PROGRESS"
    OPERATION_IN_PROGRESS = "OPERATION_IN_PROGRESS"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    OWNER_REQUIRED = "OWNER_REQUIRED"
    ACTIVE_SESSION_EXISTS = "ACTIVE_SESSION_EXISTS"
    SLEEP_ALREADY_ACTIVE = "SLEEP_ALREADY_ACTIVE"
    RESOURCE_DELETING = "RESOURCE_DELETING"
    ALREADY_MEMBER = "ALREADY_MEMBER"
    INVITE_ALREADY_USED = "INVITE_ALREADY_USED"
    OWNER_BABY_LIMIT = "OWNER_BABY_LIMIT"
    ALREADY_CONFIRMED = "ALREADY_CONFIRMED"
    INVALID_STATE = "INVALID_STATE"
    INVITE_EXPIRED = "INVITE_EXPIRED"
    INVITE_REVOKED = "INVITE_REVOKED"
    RESOURCE_DELETED = "RESOURCE_DELETED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_AUDIO = "INVALID_AUDIO"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    MODEL_NOT_READY = "MODEL_NOT_READY"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class FieldError(ContractModel):
    field: str
    code: str
    message: str


class ErrorDetails(ContractModel):
    """Implemented subset of the contract's conflict details.

    ``current_resource`` is intentionally limited to CareEvent for this first shared-record
    boundary. Add a typed resource to the union only when its endpoint is implemented.
    """

    current_version: PositiveInt | None
    current_resource: CareEvent | None
    resource_type: str | None
    existing_analysis_id: UUID | None
    existing_run_id: UUID | None
    existing_session_id: UUID | None
    deletion_job_id: UUID | None
    status_url: str | None
    retry_after_seconds: NonNegativeFloat | None

    @classmethod
    def empty(cls) -> Self:
        return cls(
            current_version=None,
            current_resource=None,
            resource_type=None,
            existing_analysis_id=None,
            existing_run_id=None,
            existing_session_id=None,
            deletion_job_id=None,
            status_url=None,
            retry_after_seconds=None,
        )


class ApiError(ContractModel):
    code: ErrorCode
    message: str
    retryable: bool
    request_id: UUID
    field_errors: list[FieldError]
    details: ErrorDetails

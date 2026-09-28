from __future__ import annotations

from typing import Protocol
from uuid import UUID

from baby_care_api.core.errors import ApiException
from baby_care_api.models.errors import ErrorCode, FieldError


class IdempotencyPort(Protocol):
    """Persistence boundary for the B-03 atomic idempotency implementation.

    The digest must represent a normalized body; raw text and full responses are not stored here.
    Authorization and deletion state must be rechecked before replaying a saved success.
    """

    async def reserve(
        self,
        *,
        user_id: UUID,
        method: str,
        target_path: str,
        key: UUID,
        request_sha256: str,
    ) -> None: ...


class UnconfiguredIdempotencyPort:
    """Fail closed instead of accepting a mutation without duplicate protection."""

    async def reserve(
        self,
        *,
        user_id: UUID,
        method: str,
        target_path: str,
        key: UUID,
        request_sha256: str,
    ) -> None:
        del user_id, method, target_path, key, request_sha256
        raise ApiException.service_unavailable()


def ensure_idempotency_key_matches(*, header_key: UUID, client_request_id: UUID) -> None:
    if header_key == client_request_id:
        return
    raise ApiException(
        ErrorCode.VALIDATION_ERROR,
        "The request did not match the API contract.",
        field_errors=[
            FieldError(
                field="Idempotency-Key",
                code="idempotency_key_mismatch",
                message="Idempotency-Key must match client_request_id.",
            )
        ],
    )

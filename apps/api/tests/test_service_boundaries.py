from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from baby_care_api.core.errors import ApiException
from baby_care_api.models.errors import ErrorCode
from baby_care_api.services.idempotency import (
    UnconfiguredIdempotencyPort,
    ensure_idempotency_key_matches,
)
from baby_care_api.services.security import (
    AuthenticatedPrincipal,
    UnconfiguredAuthenticationPort,
    UnconfiguredAuthorizationPort,
)


def test_unconfigured_authentication_fails_closed() -> None:
    with pytest.raises(ApiException) as caught:
        asyncio.run(UnconfiguredAuthenticationPort().authenticate("Bearer synthetic-token"))

    assert caught.value.code is ErrorCode.SERVICE_UNAVAILABLE


def test_unconfigured_authorization_fails_closed() -> None:
    principal = AuthenticatedPrincipal(
        user_id=UUID("10000000-0000-4000-8000-000000000001"),
        session_id=UUID("10000000-0000-4000-8000-000000000011"),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    with pytest.raises(ApiException) as caught:
        asyncio.run(
            UnconfiguredAuthorizationPort().require_active_baby_access(
                principal=principal,
                baby_id=UUID("10000000-0000-4000-8000-000000000101"),
            )
        )

    assert caught.value.code is ErrorCode.SERVICE_UNAVAILABLE


def test_unconfigured_idempotency_fails_closed() -> None:
    with pytest.raises(ApiException) as caught:
        asyncio.run(
            UnconfiguredIdempotencyPort().reserve(
                user_id=UUID("10000000-0000-4000-8000-000000000001"),
                method="POST",
                target_path="/v1/babies/{baby_id}/care-events",
                key=UUID("10000000-0000-4000-8000-000000000807"),
                request_sha256="0" * 64,
            )
        )

    assert caught.value.code is ErrorCode.SERVICE_UNAVAILABLE


def test_idempotency_header_and_body_id_must_match() -> None:
    matching = UUID("10000000-0000-4000-8000-000000000807")
    ensure_idempotency_key_matches(header_key=matching, client_request_id=matching)

    with pytest.raises(ApiException) as caught:
        ensure_idempotency_key_matches(
            header_key=matching,
            client_request_id=UUID("10000000-0000-4000-8000-000000000808"),
        )

    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert caught.value.field_errors[0].field == "Idempotency-Key"

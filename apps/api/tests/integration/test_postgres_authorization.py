from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.core.errors import ApiException
from baby_care_api.main import create_app
from baby_care_api.models.errors import ErrorCode
from baby_care_api.services.postgres import PostgresAuthorizationPort
from baby_care_api.services.security import AuthenticatedPrincipal, MembershipRole

pytestmark = pytest.mark.integration


def _database_url() -> str:
    value = os.environ.get("BABY_CARE_TEST_DATABASE_URL")
    if value is None:
        pytest.skip("BABY_CARE_TEST_DATABASE_URL is required for the local integration test")
    return value


def _principal(user_id: str, session_id: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=UUID(user_id),
        session_id=UUID(session_id),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )


def test_current_membership_authorization_and_connection_context_reset() -> None:
    async def exercise() -> None:
        authorization = PostgresAuthorizationPort(
            _database_url(),
            min_pool_size=1,
            max_pool_size=1,
        )
        await authorization.open()
        try:
            owner = _principal(
                "10000000-0000-4000-8000-000000000001",
                "10000000-0000-4000-8000-000000000011",
            )
            owner_access = await authorization.require_active_baby_access(
                owner,
                UUID("10000000-0000-4000-8000-000000000101"),
            )
            assert owner_access.role is MembershipRole.OWNER
            assert owner_access.membership_version == 1
            assert owner_access.baby_version == 1

            nonmember = _principal(
                "10000000-0000-4000-8000-000000000003",
                "10000000-0000-4000-8000-000000000013",
            )
            with pytest.raises(ApiException) as caught:
                await authorization.require_active_baby_access(
                    nonmember,
                    UUID("10000000-0000-4000-8000-000000000101"),
                )
            assert caught.value.code is ErrorCode.RESOURCE_NOT_FOUND

            caregiver = _principal(
                "10000000-0000-4000-8000-000000000002",
                "10000000-0000-4000-8000-000000000012",
            )
            caregiver_access = await authorization.require_active_baby_access(
                caregiver,
                UUID("10000000-0000-4000-8000-000000000101"),
            )
            assert caregiver_access.role is MembershipRole.CAREGIVER
            assert await authorization.probe() is True
        finally:
            await authorization.close()

    asyncio.run(exercise())


def test_database_probe_does_not_make_readiness_succeed_without_authentication() -> None:
    app = create_app(
        settings=Settings(
            environment=RuntimeEnvironment.TEST,
            database_url=_database_url(),
        )
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["checks"]["authentication"] == {
        "required": True,
        "configured": False,
        "ready": False,
    }
    assert payload["checks"]["database"] == {
        "required": True,
        "configured": True,
        "ready": True,
    }

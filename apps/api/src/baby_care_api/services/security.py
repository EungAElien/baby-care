from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, PositiveInt

from baby_care_api.core.errors import ApiException
from baby_care_api.models.base import ContractModel


class MembershipRole(StrEnum):
    OWNER = "OWNER"
    CAREGIVER = "CAREGIVER"


class AuthenticatedPrincipal(ContractModel):
    user_id: UUID
    session_id: UUID
    expires_at: AwareDatetime


class BabyAccessContext(ContractModel):
    principal: AuthenticatedPrincipal
    baby_id: UUID
    membership_id: UUID
    role: MembershipRole
    membership_version: PositiveInt
    baby_version: PositiveInt


class AuthenticationPort(Protocol):
    async def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal: ...


class AuthorizationPort(Protocol):
    async def require_active_baby_access(
        self, principal: AuthenticatedPrincipal, baby_id: UUID
    ) -> BabyAccessContext: ...


class UnconfiguredAuthenticationPort:
    """Fail closed until B-03 supplies verified JWT/session infrastructure."""

    async def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        del authorization_header
        raise ApiException.service_unavailable()


class UnconfiguredAuthorizationPort:
    """Fail closed until current membership and baby-state checks are implemented."""

    async def require_active_baby_access(
        self, principal: AuthenticatedPrincipal, baby_id: UUID
    ) -> BabyAccessContext:
        del principal, baby_id
        raise ApiException.service_unavailable()

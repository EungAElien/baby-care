from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidTokenError,
    PyJWKClientConnectionError,
    PyJWKClientError,
)
from pydantic import AwareDatetime, Field, PositiveInt

from baby_care_api.core.errors import ApiException
from baby_care_api.models.base import ContractModel
from baby_care_api.models.errors import ErrorCode


class MembershipRole(StrEnum):
    OWNER = "OWNER"
    CAREGIVER = "CAREGIVER"


class AuthenticationMethod(ContractModel):
    method: str
    timestamp: int


class AuthenticatedPrincipal(ContractModel):
    user_id: UUID
    session_id: UUID
    email: str = "unknown@example.invalid"
    issued_at: AwareDatetime = datetime(1970, 1, 1, tzinfo=UTC)
    expires_at: AwareDatetime
    authentication_methods: list[AuthenticationMethod] = Field(default_factory=list)


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
    """Fail closed until verified JWT/JWKS and session infrastructure is connected."""

    async def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        del authorization_header
        raise ApiException.service_unavailable()


class SupabaseJwtAuthenticationPort:
    """Verify Supabase access JWTs against the project's rotating asymmetric JWKS."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: tuple[str, ...] = ("ES256", "RS256"),
        jwks_lifespan_seconds: int = 600,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._algorithms = algorithms
        self._jwks_client = PyJWKClient(
            jwks_url,
            cache_keys=True,
            max_cached_keys=16,
            cache_jwk_set=True,
            lifespan=jwks_lifespan_seconds,
            timeout=5,
        )

    @staticmethod
    def _bearer_token(authorization_header: str | None) -> str:
        if authorization_header is None:
            raise ApiException(ErrorCode.AUTH_REQUIRED, "Authentication is required.")
        scheme, separator, token = authorization_header.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise ApiException(ErrorCode.INVALID_TOKEN, "The access token is invalid.")
        return token.strip()

    async def probe(self) -> bool:
        try:
            data = await asyncio.to_thread(self._jwks_client.fetch_data)
        except Exception:
            return False
        keys = data.get("keys") if isinstance(data, dict) else None
        return isinstance(keys, list) and bool(keys)

    async def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        token = self._bearer_token(authorization_header)
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            if algorithm not in self._algorithms:
                raise InvalidTokenError("disallowed signing algorithm")
            signing_key = await asyncio.to_thread(
                self._jwks_client.get_signing_key_from_jwt,
                token,
            )
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["aud", "exp", "iat", "iss", "session_id", "sub", "email"],
                },
            )
            if claims.get("role") != "authenticated" or claims.get("is_anonymous") is True:
                raise InvalidTokenError("non-user token")
            user_id = UUID(str(claims["sub"]))
            session_id = UUID(str(claims["session_id"]))
            email = str(claims["email"]).strip().lower()
            if "@" not in email:
                raise InvalidTokenError("invalid email claim")
            issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=UTC)
            expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
            authentication_methods = [
                AuthenticationMethod(
                    method=str(item["method"]),
                    timestamp=int(item["timestamp"]),
                )
                for item in claims.get("amr", [])
                if isinstance(item, dict) and "method" in item and "timestamp" in item
            ]
        except ExpiredSignatureError as exc:
            raise ApiException(ErrorCode.TOKEN_EXPIRED, "The access token has expired.") from exc
        except PyJWKClientConnectionError as exc:
            raise ApiException.service_unavailable() from exc
        except (InvalidTokenError, PyJWKClientError, KeyError, TypeError, ValueError) as exc:
            raise ApiException(ErrorCode.INVALID_TOKEN, "The access token is invalid.") from exc

        return AuthenticatedPrincipal(
            user_id=user_id,
            session_id=session_id,
            email=email,
            issued_at=issued_at,
            expires_at=expires_at,
            authentication_methods=authentication_methods,
        )


class UnconfiguredAuthorizationPort:
    """Fail closed until current membership and baby-state checks are implemented."""

    async def require_active_baby_access(
        self, principal: AuthenticatedPrincipal, baby_id: UUID
    ) -> BabyAccessContext:
        del principal, baby_id
        raise ApiException.service_unavailable()

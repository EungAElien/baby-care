from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError

from baby_care_api.core.errors import ApiException
from baby_care_api.models.errors import ErrorCode
from baby_care_api.services.security import SupabaseJwtAuthenticationPort

ISSUER = "http://127.0.0.1:54321/auth/v1"
AUDIENCE = "authenticated"
USER_ID = UUID("10000000-0000-4000-8000-000000000001")
SESSION_ID = UUID("10000000-0000-4000-8000-000000000011")


@pytest.fixture
def jwt_material() -> tuple[object, object]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int((now + timedelta(minutes=15)).timestamp()),
        "iat": int(now.timestamp()),
        "sub": str(USER_ID),
        "session_id": str(SESSION_ID),
        "email": "Owner@Example.Test",
        "role": "authenticated",
        "is_anonymous": False,
        "amr": [{"method": "otp", "timestamp": int(now.timestamp())}],
    }
    claims.update(overrides)
    return claims


def _port(public_key: object) -> SupabaseJwtAuthenticationPort:
    port = SupabaseJwtAuthenticationPort(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url="https://project.invalid/auth/v1/.well-known/jwks.json",
    )
    port._jwks_client.get_signing_key_from_jwt = lambda token: SimpleNamespace(  # type: ignore[method-assign]
        key=public_key
    )
    return port


def _token(private_key: object, claims: dict[str, object] | None = None) -> str:
    return jwt.encode(
        claims or _claims(),
        private_key,
        algorithm="RS256",
        headers={"kid": "local-unit-key"},
    )


def _authenticate(port: SupabaseJwtAuthenticationPort, token: str):  # type: ignore[no-untyped-def]
    return asyncio.run(port.authenticate(f"Bearer {token}"))


def test_verified_jwt_maps_required_identity_and_authentication_method(
    jwt_material: tuple[object, object],
) -> None:
    private_key, public_key = jwt_material

    principal = _authenticate(_port(public_key), _token(private_key))

    assert principal.user_id == USER_ID
    assert principal.session_id == SESSION_ID
    assert principal.email == "owner@example.test"
    assert [item.method for item in principal.authentication_methods] == ["otp"]


@pytest.mark.parametrize(
    ("authorization", "expected"),
    [
        (None, ErrorCode.AUTH_REQUIRED),
        ("Basic not-a-bearer", ErrorCode.INVALID_TOKEN),
        ("Bearer", ErrorCode.INVALID_TOKEN),
    ],
)
def test_missing_or_malformed_bearer_token_is_rejected(
    jwt_material: tuple[object, object], authorization: str | None, expected: ErrorCode
) -> None:
    _, public_key = jwt_material

    with pytest.raises(ApiException) as caught:
        asyncio.run(_port(public_key).authenticate(authorization))

    assert caught.value.code is expected


@pytest.mark.parametrize(
    ("claim_overrides", "expected"),
    [
        (
            {"exp": int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())},
            ErrorCode.TOKEN_EXPIRED,
        ),
        ({"iss": "https://other-project.invalid/auth/v1"}, ErrorCode.INVALID_TOKEN),
        ({"aud": "unexpected-audience"}, ErrorCode.INVALID_TOKEN),
    ],
)
def test_expired_wrong_issuer_and_wrong_audience_are_rejected(
    jwt_material: tuple[object, object],
    claim_overrides: dict[str, object],
    expected: ErrorCode,
) -> None:
    private_key, public_key = jwt_material

    with pytest.raises(ApiException) as caught:
        _authenticate(_port(public_key), _token(private_key, _claims(**claim_overrides)))

    assert caught.value.code is expected


def test_tampered_signature_is_rejected(jwt_material: tuple[object, object]) -> None:
    private_key, public_key = jwt_material
    parts = _token(private_key).split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]

    with pytest.raises(ApiException) as caught:
        _authenticate(_port(public_key), ".".join(parts))

    assert caught.value.code is ErrorCode.INVALID_TOKEN


def test_jwks_transport_failure_fails_closed(
    jwt_material: tuple[object, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key, public_key = jwt_material
    port = _port(public_key)

    def fail(_: str) -> None:
        raise PyJWKClientConnectionError("synthetic JWKS outage")

    monkeypatch.setattr(port._jwks_client, "get_signing_key_from_jwt", fail)

    with pytest.raises(ApiException) as caught:
        _authenticate(port, _token(private_key))

    assert caught.value.code is ErrorCode.SERVICE_UNAVAILABLE

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID


@dataclass(frozen=True)
class ProviderRevocationResult:
    http_status: int | None
    failure_code: str | None


@dataclass(frozen=True)
class AuthUserVerificationResult:
    verified: bool
    http_status: int | None
    unavailable: bool


class SupabaseSessionRevocationProvider:
    """End Supabase refresh sessions without persisting or logging bearer tokens."""

    def __init__(self, *, supabase_url: str, publishable_key: str) -> None:
        self._supabase_url = supabase_url.rstrip("/")
        self._publishable_key = publishable_key

    async def revoke(self, *, access_token: str, provider_scope: str) -> ProviderRevocationResult:
        return await asyncio.to_thread(
            self._revoke_sync,
            access_token=access_token,
            provider_scope=provider_scope,
        )

    async def verify_confirmed_email(
        self,
        *,
        access_token: str,
        expected_user_id: UUID,
        expected_email: str,
    ) -> AuthUserVerificationResult:
        return await asyncio.to_thread(
            self._verify_confirmed_email_sync,
            access_token=access_token,
            expected_user_id=expected_user_id,
            expected_email=expected_email,
        )

    def _verify_confirmed_email_sync(
        self,
        *,
        access_token: str,
        expected_user_id: UUID,
        expected_email: str,
    ) -> AuthUserVerificationResult:
        request = Request(
            f"{self._supabase_url}/auth/v1/user",
            method="GET",
            headers={
                "apikey": self._publishable_key,
                "Authorization": f"Bearer {access_token}",
            },
        )
        try:
            with urlopen(request, timeout=8) as response:
                status = response.status
                payload = json.loads(response.read())
        except HTTPError as exc:
            return AuthUserVerificationResult(
                verified=False,
                http_status=exc.code,
                unavailable=False,
            )
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            return AuthUserVerificationResult(
                verified=False,
                http_status=None,
                unavailable=True,
            )
        verified = (
            status == 200
            and str(payload.get("id")) == str(expected_user_id)
            and str(payload.get("email", "")).lower() == expected_email.lower()
            and payload.get("email_confirmed_at") is not None
        )
        return AuthUserVerificationResult(
            verified=verified,
            http_status=status,
            unavailable=False,
        )

    def _revoke_sync(self, *, access_token: str, provider_scope: str) -> ProviderRevocationResult:
        request = Request(
            f"{self._supabase_url}/auth/v1/logout?scope={provider_scope}",
            method="POST",
            headers={
                "apikey": self._publishable_key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            data=b"{}",
        )
        try:
            with urlopen(request, timeout=8) as response:
                status = response.status
        except HTTPError as exc:
            return ProviderRevocationResult(
                http_status=exc.code,
                failure_code="PROVIDER_REJECTED_REVOCATION",
            )
        except (URLError, TimeoutError, OSError):
            return ProviderRevocationResult(
                http_status=None,
                failure_code="PROVIDER_UNREACHABLE",
            )
        if 200 <= status < 300:
            return ProviderRevocationResult(http_status=status, failure_code=None)
        return ProviderRevocationResult(
            http_status=status,
            failure_code="PROVIDER_REJECTED_REVOCATION",
        )

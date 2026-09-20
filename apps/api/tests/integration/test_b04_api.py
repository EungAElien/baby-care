from __future__ import annotations

import base64
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.main import create_app

pytestmark = pytest.mark.integration


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        if os.environ.get("BABY_CARE_REQUIRE_INTEGRATION") == "1":
            pytest.fail(f"{name} is required when integration tests are mandatory")
        pytest.skip(f"{name} is required for the B-04 local integration test")
    return value


def _json_request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    api_key: str | None = None,
    access_token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if api_key is not None:
        headers["apikey"] = api_key
    if access_token is not None:
        headers["Authorization"] = f"Bearer {access_token}"
    request = Request(
        url,
        method=method,
        headers=headers,
        data=None if body is None else json.dumps(body).encode(),
    )
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, {} if not raw else json.loads(raw)
    except HTTPError as exc:
        raw = exc.read()
        return exc.code, {} if not raw else json.loads(raw)


def _jwt_claims(token: str) -> dict[str, Any]:
    encoded = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


@dataclass(frozen=True)
class AuthSession:
    email: str
    user_id: UUID
    session_id: UUID
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


class _RedactedHeaders(dict[str, str]):
    _sensitive_headers = frozenset({"authorization", "x-reauthentication-proof"})

    def __repr__(self) -> str:
        safe = {
            key: "[REDACTED]" if key.lower() in self._sensitive_headers else value
            for key, value in self.items()
        }
        return repr(safe)


class LocalAuth:
    def __init__(self) -> None:
        self.api_url = _required_env("BABY_CARE_TEST_SUPABASE_URL").rstrip("/")
        self.anon_key = _required_env("BABY_CARE_TEST_SUPABASE_ANON_KEY")
        self.service_role_key = _required_env("BABY_CARE_TEST_SUPABASE_SERVICE_ROLE_KEY")
        self.mailpit_url = _required_env("BABY_CARE_TEST_MAILPIT_URL").rstrip("/")

    def create_user(self, label: str) -> AuthSession:
        email = f"b04-{label}-{uuid4()}@example.test"
        password = f"Local-{uuid4()}-Aa1!"
        status, _ = _json_request(
            f"{self.api_url}/auth/v1/admin/users",
            method="POST",
            api_key=self.service_role_key,
            access_token=self.service_role_key,
            body={"email": email, "password": password, "email_confirm": True},
        )
        assert status == 200
        status, payload = _json_request(
            f"{self.api_url}/auth/v1/token?grant_type=password",
            method="POST",
            api_key=self.anon_key,
            body={"email": email, "password": password},
        )
        assert status == 200 and payload.get("access_token") and payload.get("refresh_token")
        return self._session(email, payload)

    def password_session(self, email: str, password: str) -> AuthSession:
        status, payload = _json_request(
            f"{self.api_url}/auth/v1/token?grant_type=password",
            method="POST",
            api_key=self.anon_key,
            body={"email": email, "password": password},
        )
        assert status == 200
        return self._session(email, payload)

    def create_user_with_two_sessions(self, label: str) -> tuple[AuthSession, AuthSession]:
        email = f"b04-{label}-{uuid4()}@example.test"
        password = f"Local-{uuid4()}-Aa1!"
        status, _ = _json_request(
            f"{self.api_url}/auth/v1/admin/users",
            method="POST",
            api_key=self.service_role_key,
            access_token=self.service_role_key,
            body={"email": email, "password": password, "email_confirm": True},
        )
        assert status == 200
        return self.password_session(email, password), self.password_session(email, password)

    def otp_session(self, email: str) -> AuthSession:
        status = 429
        for _ in range(3):
            status, _ = _json_request(
                f"{self.api_url}/auth/v1/otp",
                method="POST",
                api_key=self.anon_key,
                body={"email": email, "create_user": False},
            )
            if status != 429:
                break
            time.sleep(1.1)
        assert status == 200
        message_id = None
        for _ in range(20):
            with urlopen(f"{self.mailpit_url}/api/v1/messages", timeout=10) as response:
                messages = json.loads(response.read()).get("messages", [])
            matching = [
                message
                for message in messages
                if any(recipient.get("Address") == email for recipient in message.get("To", []))
            ]
            if matching:
                message_id = max(matching, key=lambda message: message["Created"])["ID"]
                break
            time.sleep(0.1)
        assert message_id is not None
        with urlopen(f"{self.mailpit_url}/api/v1/message/{message_id}", timeout=10) as response:
            message = json.loads(response.read())
        codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", message.get("Text", "") + message["HTML"])
        assert codes
        status, payload = _json_request(
            f"{self.api_url}/auth/v1/verify",
            method="POST",
            api_key=self.anon_key,
            body={"email": email, "token": codes[0], "type": "email"},
        )
        assert status == 200
        session = self._session(email, payload)
        assert [item["method"] for item in _jwt_claims(session.access_token)["amr"]] == ["otp"]
        return session

    def refresh_status(self, refresh_token: str) -> int:
        status, _ = _json_request(
            f"{self.api_url}/auth/v1/token?grant_type=refresh_token",
            method="POST",
            api_key=self.anon_key,
            body={"refresh_token": refresh_token},
        )
        return status

    @staticmethod
    def _session(email: str, payload: dict[str, Any]) -> AuthSession:
        access_token = str(payload["access_token"])
        claims = _jwt_claims(access_token)
        return AuthSession(
            email=email,
            user_id=UUID(claims["sub"]),
            session_id=UUID(claims["session_id"]),
            access_token=access_token,
            refresh_token=str(payload["refresh_token"]),
        )


def _headers(
    session: AuthSession,
    *,
    request_id: UUID | None = None,
    proof: str | None = None,
) -> dict[str, str]:
    headers = _RedactedHeaders(Authorization=f"Bearer {session.access_token}")
    if request_id is not None:
        headers["Idempotency-Key"] = str(request_id)
    if proof is not None:
        headers["X-Reauthentication-Proof"] = proof
    return headers


def _assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["code"] == code


def _reauthenticate(
    client: TestClient,
    auth: LocalAuth,
    current: AuthSession,
    *,
    baby_id: str,
    operation: str,
    prove_password_refresh_is_insufficient: bool = False,
    prove_replay: bool = False,
) -> tuple[AuthSession, str]:
    request_id = uuid4()
    challenge_response = client.post(
        "/v1/auth/reauthentication/challenges",
        headers=_headers(current, request_id=request_id),
        json={
            "client_request_id": str(request_id),
            "operation": operation,
            "baby_id": baby_id,
        },
    )
    assert challenge_response.status_code == 201
    challenge_id = challenge_response.json()["challenge_id"]
    if prove_password_refresh_is_insufficient:
        failed_id = uuid4()
        failed = client.post(
            "/v1/auth/reauthentication/proofs",
            headers=_headers(current, request_id=failed_id),
            json={"client_request_id": str(failed_id), "challenge_id": challenge_id},
        )
        _assert_error(failed, 401, "REAUTH_REQUIRED")
    otp = auth.otp_session(current.email)
    proof_id = uuid4()
    proof_response = client.post(
        "/v1/auth/reauthentication/proofs",
        headers=_headers(otp, request_id=proof_id),
        json={"client_request_id": str(proof_id), "challenge_id": challenge_id},
    )
    assert proof_response.status_code == 201
    proof = proof_response.json()["proof_token"]
    assert proof and proof_response.json()["session_id"] == str(otp.session_id)
    if prove_replay:
        replay = client.post(
            "/v1/auth/reauthentication/proofs",
            headers=_headers(otp, request_id=proof_id),
            json={"client_request_id": str(proof_id), "challenge_id": challenge_id},
        )
        assert replay.status_code == 201 and replay.json() == proof_response.json()
    return otp, proof


def _database_url() -> str:
    return _required_env("BABY_CARE_TEST_DATABASE_URL")


def _insert_episode_and_upload_grant(
    *,
    baby_id: UUID,
    owner: AuthSession,
) -> tuple[UUID, str]:
    episode_id = uuid4()
    audio_id = uuid4()
    upload_id = uuid4()
    object_key = f"{baby_id}/{audio_id}/{upload_id}"
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            insert into baby_data.episodes (
                episode_id, baby_id, created_by_user_id, source, timing_status,
                started_at, data_origin
            ) values (%s, %s, %s, 'MANUAL', 'KNOWN', clock_timestamp(), 'USER')
            """,
            (episode_id, baby_id, owner.user_id),
        )
        connection.execute(
            """
            insert into baby_data.audio_assets (
                audio_id, baby_id, episode_id, created_by_user_id, object_key,
                mime_type, bytes, status, data_origin
            ) values (%s, %s, %s, %s, %s, 'audio/wav', 0, 'ALLOCATED', 'USER')
            """,
            (audio_id, baby_id, episode_id, owner.user_id, object_key),
        )
        connection.execute(
            """
            insert into baby_data.audio_upload_grants (
                upload_id, baby_id, episode_id, audio_id, uploader_user_id,
                auth_session_id, object_key, method, max_bytes, expires_at
            ) values (%s, %s, %s, %s, %s, %s, %s, 'STANDARD', 25000000,
                      statement_timestamp() + interval '14 minutes 59 seconds')
            """,
            (
                upload_id,
                baby_id,
                episode_id,
                audio_id,
                owner.user_id,
                owner.session_id,
                object_key,
            ),
        )
    return episode_id, object_key


def _storage_upload_status(auth: LocalAuth, session: AuthSession, object_key: str) -> int:
    request = Request(
        f"{auth.api_url}/storage/v1/object/baby-audio/{object_key}",
        method="POST",
        headers={
            "apikey": auth.anon_key,
            "Authorization": f"Bearer {session.access_token}",
            "Content-Type": "audio/wav",
            "x-upsert": "false",
        },
        data=b"synthetic local integration bytes",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def test_b04_real_jwt_otp_shared_records_revocation_and_deletion_boundaries() -> None:
    auth = LocalAuth()
    owner, owner_other = auth.create_user_with_two_sessions("owner")
    caregiver = auth.create_user("caregiver")
    outsider = auth.create_user("outsider")
    spare = auth.create_user("spare")
    expired_recipient = auth.create_user("expired")

    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        database_url=_database_url(),
        supabase_jwt_issuer=_required_env("BABY_CARE_TEST_SUPABASE_ISSUER"),
        supabase_jwt_audience="authenticated",
        supabase_jwks_url=_required_env("BABY_CARE_TEST_SUPABASE_JWKS_URL"),
        supabase_url=auth.api_url,
        supabase_publishable_key=auth.anon_key,
        reauthentication_proof_secret="local-integration-proof-secret-at-least-32-bytes",
        child_data_production_enabled=False,
    )
    app = create_app(settings=settings)

    with TestClient(app) as client:
        readiness = client.get("/health/ready")
        assert readiness.status_code == 200
        assert readiness.json()["checks"]["authentication"]["ready"] is True

        _assert_error(client.get("/v1/babies"), 401, "AUTH_REQUIRED")
        token_parts = owner.access_token.split(".")
        token_parts[2] = ("A" if token_parts[2][0] != "A" else "B") + token_parts[2][1:]
        _assert_error(
            client.get(
                "/v1/babies",
                headers={"Authorization": f"Bearer {'.'.join(token_parts)}"},
            ),
            401,
            "INVALID_TOKEN",
        )

        assert client.get("/v1/babies", headers=_headers(owner)).json() == {"items": []}
        create_id = uuid4()
        create_body = {
            "client_request_id": str(create_id),
            "alias": "B-04 synthetic baby",
            "birth_date": "2026-01-01",
            "feeding_mode": "MIXED",
            "timezone": "Asia/Seoul",
        }
        created = client.post(
            "/v1/babies", headers=_headers(owner, request_id=create_id), json=create_body
        )
        assert created.status_code == 201
        baby_id = created.json()["baby"]["baby_id"]
        assert created.json()["baby"]["owner_user_id"] == str(owner.user_id)
        replay = client.post(
            "/v1/babies", headers=_headers(owner, request_id=create_id), json=create_body
        )
        assert replay.status_code == 201 and replay.json() == created.json()
        with TestClient(create_app(settings=settings)) as restarted_client:
            restarted_replay = restarted_client.post(
                "/v1/babies", headers=_headers(owner, request_id=create_id), json=create_body
            )
        assert restarted_replay.status_code == 201
        assert restarted_replay.json() == created.json()
        changed_body = {**create_body, "alias": "mismatched replay"}
        _assert_error(
            client.post(
                "/v1/babies", headers=_headers(owner, request_id=create_id), json=changed_body
            ),
            409,
            "IDEMPOTENCY_KEY_REUSED",
        )
        second_id = uuid4()
        _assert_error(
            client.post(
                "/v1/babies",
                headers=_headers(owner, request_id=second_id),
                json={**create_body, "client_request_id": str(second_id)},
            ),
            409,
            "OWNER_BABY_LIMIT",
        )

        assert client.get("/v1/babies/current", headers=_headers(owner)).json() == {"baby_id": None}
        active_id = uuid4()
        active = client.put(
            "/v1/me/active-baby",
            headers=_headers(owner, request_id=active_id),
            json={"client_request_id": str(active_id), "baby_id": baby_id},
        )
        assert active.status_code == 200 and active.json()["baby_id"] == baby_id

        patch_id = uuid4()
        patched = client.patch(
            f"/v1/babies/{baby_id}",
            headers=_headers(owner, request_id=patch_id),
            json={"client_request_id": str(patch_id), "version": 1, "alias": "Patched baby"},
        )
        assert patched.status_code == 200 and patched.json()["version"] == 2
        stale_id = uuid4()
        _assert_error(
            client.patch(
                f"/v1/babies/{baby_id}",
                headers=_headers(owner, request_id=stale_id),
                json={"client_request_id": str(stale_id), "version": 1, "alias": "stale"},
            ),
            409,
            "VERSION_CONFLICT",
        )
        verification = client.get(
            f"/v1/babies/{baby_id}/child-data-verification", headers=_headers(owner)
        )
        assert verification.status_code == 200
        assert verification.json()["production_processing_allowed"] is False

        owner_otp, invite_proof = _reauthenticate(
            client,
            auth,
            owner,
            baby_id=baby_id,
            operation="CREATE_INVITE",
            prove_password_refresh_is_insufficient=True,
            prove_replay=True,
        )
        wrong_operation_id = uuid4()
        _assert_error(
            client.delete(
                f"/v1/babies/{baby_id}/data?version=2&confirm=DELETE_BABY",
                headers=_headers(
                    owner_otp,
                    request_id=wrong_operation_id,
                    proof=invite_proof,
                ),
            ),
            401,
            "REAUTH_PROOF_INVALID",
        )
        invite_idempotency = uuid4()
        invite = client.post(
            f"/v1/babies/{baby_id}/invites",
            headers=_headers(owner_otp, request_id=invite_idempotency, proof=invite_proof),
            json={
                "client_request_id": str(invite_idempotency),
                "email": caregiver.email,
            },
        )
        assert invite.status_code == 201
        invite_token = urlsplit(invite.json()["invite_url"]).fragment
        replay_invite = client.post(
            f"/v1/babies/{baby_id}/invites",
            headers=_headers(owner_otp, request_id=invite_idempotency, proof=invite_proof),
            json={
                "client_request_id": str(invite_idempotency),
                "email": caregiver.email,
            },
        )
        assert replay_invite.status_code == 201
        assert replay_invite.json()["invite_url"] is None
        assert replay_invite.json()["link_reissue_required"] is True
        consumed_proof_id = uuid4()
        _assert_error(
            client.post(
                f"/v1/babies/{baby_id}/invites",
                headers=_headers(
                    owner_otp,
                    request_id=consumed_proof_id,
                    proof=invite_proof,
                ),
                json={
                    "client_request_id": str(consumed_proof_id),
                    "email": "unused-proof@example.test",
                },
            ),
            401,
            "REAUTH_PROOF_INVALID",
        )

        accept_id = uuid4()
        accepted = client.post(
            "/v1/invites/accept",
            headers=_headers(caregiver, request_id=accept_id),
            json={
                "client_request_id": str(accept_id),
                "token": invite_token,
                "accept_shared_use": True,
                "policy_version": "local-test-v1",
                "relationship": "FATHER",
            },
        )
        assert accepted.status_code == 200
        assert accepted.json()["membership"]["role"] == "CAREGIVER"
        _assert_error(
            client.get(f"/v1/babies/{baby_id}/members", headers=_headers(outsider)),
            404,
            "RESOURCE_NOT_FOUND",
        )
        members = client.get(f"/v1/babies/{baby_id}/members", headers=_headers(owner_otp))
        assert members.status_code == 200 and len(members.json()["items"]) == 2
        _assert_error(
            client.get(f"/v1/babies/{baby_id}/invites", headers=_headers(caregiver)),
            403,
            "OWNER_ONLY",
        )
        relationship_id = uuid4()
        relationship = client.patch(
            f"/v1/babies/{baby_id}/members/me",
            headers=_headers(caregiver, request_id=relationship_id),
            json={
                "client_request_id": str(relationship_id),
                "version": accepted.json()["membership"]["version"],
                "relationship": "GRANDPARENT",
            },
        )
        assert relationship.status_code == 200
        assert relationship.json()["relationship"] == "GRANDPARENT"

        consent_id = uuid4()
        consent = client.put(
            "/v1/consents",
            headers=_headers(owner_otp, request_id=consent_id),
            json={
                "client_request_id": str(consent_id),
                "baby_id": baby_id,
                "scope": "SERVICE_PROCESSING",
                "granted": True,
                "policy_version": "local-test-v1",
                "version": 0,
            },
        )
        assert consent.status_code == 200 and consent.json()["status"] == "GRANTED"
        personal_id = uuid4()
        personal = client.put(
            f"/v1/me/training-consents/{baby_id}",
            headers=_headers(caregiver, request_id=personal_id),
            json={
                "client_request_id": str(personal_id),
                "granted": True,
                "policy_version": "local-test-v1",
                "version": 0,
            },
        )
        assert personal.status_code == 200
        owner_consents = client.get(f"/v1/consents?baby_id={baby_id}", headers=_headers(owner_otp))
        assert {item["scope"] for item in owner_consents.json()["items"]} == {"SERVICE_PROCESSING"}
        caregiver_consents = client.get(
            f"/v1/consents?baby_id={baby_id}", headers=_headers(caregiver)
        )
        assert {item["scope"] for item in caregiver_consents.json()["items"]} == {
            "CONTRIBUTOR_TRAINING",
            "SHARED_USE",
            "SERVICE_PROCESSING",
        }

        owner_otp, training_proof = _reauthenticate(
            client,
            auth,
            owner_otp,
            baby_id=baby_id,
            operation="ENABLE_BABY_TRAINING",
        )
        training_id = uuid4()
        _assert_error(
            client.put(
                "/v1/consents",
                headers=_headers(owner_otp, request_id=training_id, proof=training_proof),
                json={
                    "client_request_id": str(training_id),
                    "baby_id": baby_id,
                    "scope": "BABY_TRAINING",
                    "granted": True,
                    "policy_version": "unapproved-local-policy",
                    "version": 0,
                },
            ),
            403,
            "CHILD_DATA_VERIFICATION_REQUIRED",
        )

        occurred_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        caregiver_event_id = uuid4()
        caregiver_event_body = {
            "client_request_id": str(caregiver_event_id),
            "event": {
                "type": "FEEDING",
                "occurred_at": occurred_at,
                "ended_at": None,
                "time_precision": "EXACT",
                "payload": {"mode": "FORMULA", "amount_ml": 90, "duration_minutes": None},
            },
        }

        def create_same_caregiver_event() -> Any:
            return client.post(
                f"/v1/babies/{baby_id}/care-events",
                headers=_headers(caregiver, request_id=caregiver_event_id),
                json=caregiver_event_body,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            event_replays = [
                future.result()
                for future in [executor.submit(create_same_caregiver_event) for _ in range(2)]
            ]
        assert [response.status_code for response in event_replays] == [201, 201]
        assert event_replays[0].json() == event_replays[1].json()
        caregiver_event = event_replays[0]
        event_id = caregiver_event.json()["care_event_id"]
        local_day = datetime.fromisoformat(occurred_at).astimezone(ZoneInfo("Asia/Seoul")).date()
        summary_url = f"/v1/babies/{baby_id}/summary?date={local_day}&timezone=Asia/Seoul"
        summary_response = client.get(summary_url, headers=_headers(owner_otp))
        assert summary_response.status_code == 200
        assert summary_response.json()["feeding"]["record_count"] == 1
        assert summary_response.json()["feeding"]["total_recorded_ml"] == 90
        assert "sleep" in summary_response.json()["missing_fields"]
        _assert_error(
            client.get(summary_url, headers=_headers(outsider)), 404, "RESOURCE_NOT_FOUND"
        )
        _assert_error(
            client.get(
                f"/v1/babies/{baby_id}/summary?date={local_day}&timezone=UTC",
                headers=_headers(owner_otp),
            ),
            409,
            "VERSION_CONFLICT",
        )
        owner_patch_id = uuid4()
        owner_edit = client.patch(
            f"/v1/care-events/{event_id}",
            headers=_headers(owner_otp, request_id=owner_patch_id),
            json={
                "client_request_id": str(owner_patch_id),
                "version": 1,
                "event": {
                    "type": "FEEDING",
                    "occurred_at": occurred_at,
                    "ended_at": None,
                    "time_precision": "EXACT",
                    "payload": {"mode": "FORMULA", "amount_ml": 100, "duration_minutes": None},
                },
            },
        )
        assert owner_edit.status_code == 200
        assert owner_edit.json()["created_by_user_id"] == str(caregiver.user_id)
        assert owner_edit.json()["updated_by_user_id"] == str(owner.user_id)
        assert client.get(summary_url, headers=_headers(caregiver)).json()["feeding"][
            "total_recorded_ml"
        ] == 100
        stale_event_id = uuid4()
        stale_event = client.patch(
            f"/v1/care-events/{event_id}",
            headers=_headers(caregiver, request_id=stale_event_id),
            json={
                "client_request_id": str(stale_event_id),
                "version": 1,
                "event": owner_edit.json()["event"],
            },
        )
        _assert_error(stale_event, 409, "VERSION_CONFLICT")
        assert stale_event.json()["details"]["current_version"] == 2

        concurrent_updates = [
            (owner_otp, uuid4(), 110),
            (caregiver, uuid4(), 120),
        ]

        def update_same_event(actor: AuthSession, request_id: UUID, amount_ml: int) -> Any:
            return client.patch(
                f"/v1/care-events/{event_id}",
                headers=_headers(actor, request_id=request_id),
                json={
                    "client_request_id": str(request_id),
                    "version": 2,
                    "event": {
                        "type": "FEEDING",
                        "occurred_at": occurred_at,
                        "ended_at": None,
                        "time_precision": "EXACT",
                        "payload": {
                            "mode": "FORMULA",
                            "amount_ml": amount_ml,
                            "duration_minutes": None,
                        },
                    },
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            update_race = [
                future.result()
                for future in [
                    executor.submit(update_same_event, actor, request_id, amount_ml)
                    for actor, request_id, amount_ml in concurrent_updates
                ]
            ]
        assert sorted(response.status_code for response in update_race) == [200, 409]
        successful_update = next(
            response for response in update_race if response.status_code == 200
        )
        conflicted_update = next(
            response for response in update_race if response.status_code == 409
        )
        _assert_error(conflicted_update, 409, "VERSION_CONFLICT")
        assert conflicted_update.json()["details"]["current_version"] == 3
        assert conflicted_update.json()["details"]["current_resource"] == successful_update.json()
        fetched_event = client.get(f"/v1/care-events/{event_id}", headers=_headers(caregiver))
        assert fetched_event.status_code == 200 and fetched_event.json() == successful_update.json()
        assert client.get(summary_url, headers=_headers(owner_otp)).json()["feeding"][
            "total_recorded_ml"
        ] == successful_update.json()["event"]["payload"]["amount_ml"]

        disposable_id = uuid4()
        disposable = client.post(
            f"/v1/babies/{baby_id}/care-events",
            headers=_headers(caregiver, request_id=disposable_id),
            json={
                "client_request_id": str(disposable_id),
                "event": {
                    "type": "DIAPER",
                    "occurred_at": occurred_at,
                    "ended_at": None,
                    "time_precision": "EXACT",
                    "payload": {"operation": "CHANGE", "condition": "WET"},
                },
            },
        )
        assert disposable.status_code == 201
        assert client.get(summary_url, headers=_headers(owner_otp)).json()["diaper"][
            "change_count"
        ] == 1
        disposable_event_id = disposable.json()["care_event_id"]
        stale_delete_id = uuid4()
        stale_delete = client.delete(
            f"/v1/care-events/{disposable_event_id}?version=2",
            headers=_headers(caregiver, request_id=stale_delete_id),
        )
        _assert_error(stale_delete, 409, "VERSION_CONFLICT")
        assert stale_delete.json()["details"]["current_version"] == 1
        delete_event_id = uuid4()
        deleted_event = client.delete(
            f"/v1/care-events/{disposable_event_id}?version=1",
            headers=_headers(caregiver, request_id=delete_event_id),
        )
        assert deleted_event.status_code == 202
        assert client.get(summary_url, headers=_headers(owner_otp)).json()["diaper"][
            "change_count"
        ] == 0
        replay_deleted_event = client.delete(
            f"/v1/care-events/{disposable_event_id}?version=1",
            headers=_headers(caregiver, request_id=delete_event_id),
        )
        assert replay_deleted_event.json() == deleted_event.json()
        _assert_error(
            client.get(f"/v1/care-events/{disposable_event_id}", headers=_headers(caregiver)),
            404,
            "RESOURCE_NOT_FOUND",
        )

        sleep_id = uuid4()
        second_sleep_id = uuid4()
        sleep_event = {
            "type": "SLEEP",
            "occurred_at": occurred_at,
            "ended_at": None,
            "time_precision": "EXACT",
            "payload": {},
        }

        def start_sleep(request_id: UUID) -> Any:
            return client.post(
                f"/v1/babies/{baby_id}/care-events",
                headers=_headers(owner_otp, request_id=request_id),
                json={
                    "client_request_id": str(request_id),
                    "event": sleep_event,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            sleep_race = [
                future.result()
                for future in [
                    executor.submit(start_sleep, request_id)
                    for request_id in (sleep_id, second_sleep_id)
                ]
            ]
        assert sorted(response.status_code for response in sleep_race) == [201, 409]
        sleep = next(response for response in sleep_race if response.status_code == 201)
        sleep_request_id = (sleep_id, second_sleep_id)[sleep_race.index(sleep)]
        sleep_conflict = next(response for response in sleep_race if response.status_code == 409)
        _assert_error(sleep_conflict, 409, "SLEEP_ALREADY_ACTIVE")
        assert sleep_conflict.json()["details"]["current_resource"] == sleep.json()
        timeline = client.get(f"/v1/babies/{baby_id}/timeline?limit=1", headers=_headers(owner_otp))
        assert timeline.status_code == 200 and timeline.json()["next_cursor"]
        next_page = client.get(
            f"/v1/babies/{baby_id}/timeline?limit=1&cursor={timeline.json()['next_cursor']}",
            headers=_headers(owner_otp),
        )
        assert next_page.status_code == 200 and len(next_page.json()["items"]) == 1
        _assert_error(
            client.get(
                f"/v1/babies/{baby_id}/timeline?from=2026-09-20T10:00:00Z&to=2026-09-20T09:00:00Z",
                headers=_headers(owner_otp),
            ),
            422,
            "VALIDATION_ERROR",
        )

        episode_id, object_key = _insert_episode_and_upload_grant(
            baby_id=UUID(baby_id), owner=owner_other
        )
        action_id = uuid4()
        action = client.post(
            f"/v1/episodes/{episode_id}/actions",
            headers=_headers(owner_otp, request_id=action_id),
            json={
                "client_request_id": str(action_id),
                "care_event_id": None,
                "new_care_event": {
                    "type": "SOOTHE",
                    "occurred_at": occurred_at,
                    "ended_at": None,
                    "time_precision": "EXACT",
                    "payload": {"action_kind": "HOLDING"},
                },
                "recommendation_id": None,
                "performed_by_user_id": str(caregiver.user_id),
                "sequence": 1,
            },
        )
        assert action.status_code == 201
        linked_action_id = uuid4()
        linked_action = client.post(
            f"/v1/episodes/{episode_id}/actions",
            headers=_headers(caregiver, request_id=linked_action_id),
            json={
                "client_request_id": str(linked_action_id),
                "care_event_id": event_id,
                "new_care_event": None,
                "recommendation_id": None,
                "performed_by_user_id": str(caregiver.user_id),
                "sequence": 2,
            },
        )
        assert linked_action.status_code == 201
        duplicate_link_id = uuid4()
        duplicate_link = client.post(
            f"/v1/episodes/{episode_id}/actions",
            headers=_headers(caregiver, request_id=duplicate_link_id),
            json={
                "client_request_id": str(duplicate_link_id),
                "care_event_id": event_id,
                "new_care_event": None,
                "recommendation_id": None,
                "performed_by_user_id": str(caregiver.user_id),
                "sequence": 3,
            },
        )
        assert duplicate_link.status_code == 201
        assert duplicate_link.json()["action_id"] == linked_action.json()["action_id"]

        owner_event_id = action.json()["care_event_id"]
        owner_event = client.get(f"/v1/care-events/{owner_event_id}", headers=_headers(owner_otp))
        owner_event_delete_id = uuid4()
        owner_event_deletion = client.delete(
            f"/v1/care-events/{owner_event_id}?version={owner_event.json()['version']}",
            headers=_headers(owner_otp, request_id=owner_event_delete_id),
        )
        assert owner_event_deletion.status_code == 202
        owner_deletion_job_id = owner_event_deletion.json()["deletion_job_id"]

        entry_create_id = uuid4()
        entry = client.post(
            f"/v1/babies/{baby_id}/care-entries",
            headers=_headers(caregiver, request_id=entry_create_id),
            json={
                "client_request_id": str(entry_create_id),
                "episode_id": str(episode_id),
                "supersedes_entry_id": None,
                "input_mode": "TEXT",
                "raw_text": "synthetic private caregiver note",
                "choices": [],
                "occurred_at": occurred_at,
                "time_precision": "EXACT",
                "base_record_versions": [],
            },
        )
        assert entry.status_code == 201
        entry_id = entry.json()["entry_id"]
        replay_entry = client.post(
            f"/v1/babies/{baby_id}/care-entries",
            headers=_headers(caregiver, request_id=entry_create_id),
            json={
                "client_request_id": str(entry_create_id),
                "episode_id": str(episode_id),
                "supersedes_entry_id": None,
                "input_mode": "TEXT",
                "raw_text": "synthetic private caregiver note",
                "choices": [],
                "occurred_at": occurred_at,
                "time_precision": "EXACT",
                "base_record_versions": [],
            },
        )
        assert replay_entry.status_code == 201 and replay_entry.json() == entry.json()
        own_entry = client.get(f"/v1/care-entries/{entry_id}", headers=_headers(caregiver))
        assert own_entry.status_code == 200 and own_entry.json() == entry.json()
        owner_private = client.get(f"/v1/care-entries/{entry_id}", headers=_headers(owner_otp))
        _assert_error(owner_private, 404, "RESOURCE_NOT_FOUND")
        own_entries = client.get(
            f"/v1/babies/{baby_id}/care-entries?status=DRAFT", headers=_headers(caregiver)
        )
        assert own_entries.status_code == 200 and len(own_entries.json()["items"]) == 1
        with psycopg.connect(_database_url()) as connection:
            normalization_id = uuid4()
            connection.execute(
                """
                insert into baby_data.normalization_runs (
                    run_id, baby_id, entry_id, input_revision, status,
                    execution_token, lease_expires_at, execution_mode,
                    prompt_version, schema_version, ontology_version
                ) values (%s, %s, %s, 1, 'RUNNING', %s,
                          clock_timestamp() + interval '30 seconds', 'STUB',
                          'local-prompt', 'local-schema', 'local-ontology')
                """,
                (normalization_id, baby_id, entry_id, uuid4()),
            )
        entry_patch_id = uuid4()
        entry_patch = client.patch(
            f"/v1/care-entries/{entry_id}",
            headers=_headers(caregiver, request_id=entry_patch_id),
            json={
                "client_request_id": str(entry_patch_id),
                "input_revision": 1,
                "input_mode": "TEXT",
                "raw_text": "synthetic revised private note",
                "choices": [],
                "occurred_at": occurred_at,
                "time_precision": "EXACT",
            },
        )
        assert entry_patch.status_code == 200 and entry_patch.json()["input_revision"] == 2
        with psycopg.connect(_database_url()) as connection:
            normalization_status = connection.execute(
                "select status::text from baby_data.normalization_runs where run_id = %s",
                (normalization_id,),
            ).fetchone()
        assert normalization_status == ("STALE",)
        stale_entry_id = uuid4()
        _assert_error(
            client.patch(
                f"/v1/care-entries/{entry_id}",
                headers=_headers(caregiver, request_id=stale_entry_id),
                json={
                    "client_request_id": str(stale_entry_id),
                    "input_revision": 1,
                    "input_mode": "TEXT",
                    "raw_text": "stale",
                    "choices": [],
                    "occurred_at": occurred_at,
                    "time_precision": "EXACT",
                },
            ),
            409,
            "SOURCE_REVISION_CHANGED",
        )
        with psycopg.connect(_database_url()) as connection:
            deletion_normalization_id = uuid4()
            connection.execute(
                """
                insert into baby_data.normalization_runs (
                    run_id, baby_id, entry_id, input_revision, status,
                    execution_token, lease_expires_at, execution_mode,
                    prompt_version, schema_version, ontology_version
                ) values (%s, %s, %s, 2, 'RUNNING', %s,
                          clock_timestamp() + interval '30 seconds', 'STUB',
                          'local-prompt', 'local-schema', 'local-ontology')
                """,
                (deletion_normalization_id, baby_id, entry_id, uuid4()),
            )
        entry_delete_id = uuid4()
        entry_delete = client.delete(
            f"/v1/care-entries/{entry_id}?version={entry_patch.json()['version']}",
            headers=_headers(caregiver, request_id=entry_delete_id),
        )
        assert entry_delete.status_code == 202 and entry_delete.json()["status"] == "PENDING"
        with psycopg.connect(_database_url()) as connection:
            assert connection.execute(
                "select status::text, lease_expires_at from baby_data.normalization_runs "
                "where run_id = %s",
                (deletion_normalization_id,),
            ).fetchone() == ("STALE", None)
        deletion_id = entry_delete.json()["deletion_job_id"]
        with psycopg.connect(_database_url()) as connection:
            connection.execute(
                """
                update baby_data.deletion_jobs
                   set status = 'RUNNING', version = version + 1
                 where deletion_job_id = %s
                """,
                (deletion_id,),
            )
            connection.execute(
                """
                update baby_data.deletion_jobs
                   set status = 'FAILED', failure = %s::jsonb, version = version + 1
                 where deletion_job_id = %s
                """,
                (
                    json.dumps({"code": "LOCAL_TEST", "message": "synthetic", "retryable": True}),
                    deletion_id,
                ),
            )
        wrong_attempt_id = uuid4()
        _assert_error(
            client.post(
                f"/v1/deletions/{deletion_id}/retry",
                headers=_headers(caregiver, request_id=wrong_attempt_id),
                json={"client_request_id": str(wrong_attempt_id), "expected_attempt": 2},
            ),
            409,
            "VERSION_CONFLICT",
        )
        retry_ids = [uuid4(), uuid4()]

        def retry_failed_deletion(request_id: UUID) -> Any:
            return client.post(
                f"/v1/deletions/{deletion_id}/retry",
                headers=_headers(caregiver, request_id=request_id),
                json={"client_request_id": str(request_id), "expected_attempt": 1},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            retry_race = [
                future.result()
                for future in [
                    executor.submit(retry_failed_deletion, request_id) for request_id in retry_ids
                ]
            ]
        assert sorted(response.status_code for response in retry_race) == [202, 409]
        retry = next(response for response in retry_race if response.status_code == 202)
        retry_conflict = next(response for response in retry_race if response.status_code == 409)
        assert retry.status_code == 202
        assert retry.json()["status"] == "RUNNING" and retry.json()["attempt_no"] == 2
        _assert_error(retry_conflict, 409, "INVALID_STATE")
        retry_id = retry_ids[retry_race.index(retry)]
        replay_retry = client.post(
            f"/v1/deletions/{deletion_id}/retry",
            headers=_headers(caregiver, request_id=retry_id),
            json={"client_request_id": str(retry_id), "expected_attempt": 1},
        )
        assert replay_retry.json() == retry.json()
        deletion_status = client.get(f"/v1/deletions/{deletion_id}", headers=_headers(caregiver))
        assert deletion_status.status_code == 200

        owner_otp, spare_proof = _reauthenticate(
            client, auth, owner_otp, baby_id=baby_id, operation="CREATE_INVITE"
        )
        spare_invite_id = uuid4()
        spare_invite = client.post(
            f"/v1/babies/{baby_id}/invites",
            headers=_headers(owner_otp, request_id=spare_invite_id, proof=spare_proof),
            json={"client_request_id": str(spare_invite_id), "email": spare.email},
        )
        old_spare_token = urlsplit(spare_invite.json()["invite_url"]).fragment
        old_spare_invite_id = spare_invite.json()["invite"]["invite_id"]
        owner_otp, reissue_proof = _reauthenticate(
            client, auth, owner_otp, baby_id=baby_id, operation="CREATE_INVITE"
        )
        reissue_id = uuid4()
        reissued = client.post(
            f"/v1/invites/{old_spare_invite_id}/reissue",
            headers=_headers(owner_otp, request_id=reissue_id, proof=reissue_proof),
            json={"client_request_id": str(reissue_id)},
        )
        assert reissued.status_code == 201
        new_spare_token = urlsplit(reissued.json()["invite_url"]).fragment
        revoked_accept_id = uuid4()
        _assert_error(
            client.post(
                "/v1/invites/accept",
                headers=_headers(spare, request_id=revoked_accept_id),
                json={
                    "client_request_id": str(revoked_accept_id),
                    "token": old_spare_token,
                    "accept_shared_use": True,
                    "policy_version": "local-test-v1",
                    "relationship": "OTHER",
                },
            ),
            410,
            "INVITE_REVOKED",
        )
        mismatch_id = uuid4()
        _assert_error(
            client.post(
                "/v1/invites/accept",
                headers=_headers(outsider, request_id=mismatch_id),
                json={
                    "client_request_id": str(mismatch_id),
                    "token": new_spare_token,
                    "accept_shared_use": True,
                    "policy_version": "local-test-v1",
                    "relationship": "OTHER",
                },
            ),
            403,
            "INVITE_EMAIL_MISMATCH",
        )

        def accept_spare() -> Any:
            request_id = uuid4()
            return client.post(
                "/v1/invites/accept",
                headers=_headers(spare, request_id=request_id),
                json={
                    "client_request_id": str(request_id),
                    "token": new_spare_token,
                    "accept_shared_use": True,
                    "policy_version": "local-test-v1",
                    "relationship": "OTHER",
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            race = [future.result() for future in [executor.submit(accept_spare) for _ in range(2)]]
        assert sorted(response.status_code for response in race) == [200, 409]
        race_conflict = next(response for response in race if response.status_code == 409)
        assert race_conflict.json()["code"] == "INVITE_ALREADY_USED"

        owner_otp, expired_proof = _reauthenticate(
            client, auth, owner_otp, baby_id=baby_id, operation="CREATE_INVITE"
        )
        expired_invite_request_id = uuid4()
        expired_invite = client.post(
            f"/v1/babies/{baby_id}/invites",
            headers=_headers(owner_otp, request_id=expired_invite_request_id, proof=expired_proof),
            json={
                "client_request_id": str(expired_invite_request_id),
                "email": expired_recipient.email,
            },
        )
        expired_token = urlsplit(expired_invite.json()["invite_url"]).fragment
        with psycopg.connect(_database_url()) as connection:
            connection.execute(
                """
                update baby_data.invitations
                   set expires_at = recorded_at + interval '1 microsecond',
                       version = version + 1
                 where invite_id = %s
                """,
                (expired_invite.json()["invite"]["invite_id"],),
            )
        expired_accept_id = uuid4()
        _assert_error(
            client.post(
                "/v1/invites/accept",
                headers=_headers(expired_recipient, request_id=expired_accept_id),
                json={
                    "client_request_id": str(expired_accept_id),
                    "token": expired_token,
                    "accept_shared_use": True,
                    "policy_version": "local-test-v1",
                    "relationship": "OTHER",
                },
            ),
            410,
            "INVITE_EXPIRED",
        )
        invite_page = client.get(f"/v1/babies/{baby_id}/invites", headers=_headers(owner_otp))
        assert invite_page.status_code == 200
        expired_invite_row = next(
            item
            for item in invite_page.json()["items"]
            if item["invite_id"] == expired_invite.json()["invite"]["invite_id"]
        )
        stale_revoke_id = uuid4()
        _assert_error(
            client.delete(
                f"/v1/invites/{expired_invite_row['invite_id']}?version=1",
                headers=_headers(owner_otp, request_id=stale_revoke_id),
            ),
            409,
            "VERSION_CONFLICT",
        )
        revoke_invite_id = uuid4()
        revoked_invite = client.delete(
            f"/v1/invites/{expired_invite_row['invite_id']}?version={expired_invite_row['version']}",
            headers=_headers(owner_otp, request_id=revoke_invite_id),
        )
        assert revoked_invite.status_code == 200
        assert revoked_invite.json()["status"] == "REVOKED"
        replay_revoked_invite = client.delete(
            f"/v1/invites/{expired_invite_row['invite_id']}?version={expired_invite_row['version']}",
            headers=_headers(owner_otp, request_id=revoke_invite_id),
        )
        assert replay_revoked_invite.json() == revoked_invite.json()
        accepted_revoke_id = uuid4()
        _assert_error(
            client.delete(
                f"/v1/invites/{invite.json()['invite']['invite_id']}?version=2",
                headers=_headers(owner_otp, request_id=accepted_revoke_id),
            ),
            409,
            "INVITE_ALREADY_USED",
        )

        assert client.get("/v1/babies", headers=_headers(owner_other)).status_code == 200
        revoke_others_id = uuid4()
        revoked_others = client.post(
            "/v1/auth/session-revocations",
            headers=_headers(owner_otp, request_id=revoke_others_id),
            json={"client_request_id": str(revoke_others_id), "scope": "OTHERS"},
        )
        assert revoked_others.status_code == 200
        assert revoked_others.json()["status"] == "COMPLETE"
        assert revoked_others.json()["provider_scope"] == "others"
        replay_revoked_others = client.post(
            "/v1/auth/session-revocations",
            headers=_headers(owner_otp, request_id=revoke_others_id),
            json={"client_request_id": str(revoke_others_id), "scope": "OTHERS"},
        )
        assert replay_revoked_others.json() == revoked_others.json()
        _assert_error(
            client.get("/v1/babies", headers=_headers(owner_other)), 401, "SESSION_REVOKED"
        )
        assert auth.refresh_status(owner_other.refresh_token) >= 400
        assert _storage_upload_status(auth, owner_other, object_key) >= 400
        revocation_id = revoked_others.json()["revocation_id"]
        assert (
            client.get(
                f"/v1/auth/session-revocations/{revocation_id}", headers=_headers(owner_otp)
            ).json()["status"]
            == "COMPLETE"
        )

        members = client.get(f"/v1/babies/{baby_id}/members", headers=_headers(owner_otp)).json()[
            "items"
        ]
        owner_membership = next(item for item in members if item["user_id"] == str(owner.user_id))
        owner_leave_id = uuid4()
        _assert_error(
            client.delete(
                f"/v1/babies/{baby_id}/members/{owner.user_id}"
                f"?version={owner_membership['version']}",
                headers=_headers(owner_otp, request_id=owner_leave_id),
            ),
            409,
            "OWNER_REQUIRED",
        )
        caregiver_membership = next(
            item for item in members if item["user_id"] == str(caregiver.user_id)
        )
        leave_id = uuid4()
        left = client.delete(
            f"/v1/babies/{baby_id}/members/{caregiver.user_id}"
            f"?version={caregiver_membership['version']}",
            headers=_headers(caregiver, request_id=leave_id),
        )
        assert left.status_code == 200 and left.json()["status"] == "LEFT"
        replay_left = client.delete(
            f"/v1/babies/{baby_id}/members/{caregiver.user_id}"
            f"?version={caregiver_membership['version']}",
            headers=_headers(caregiver, request_id=leave_id),
        )
        _assert_error(replay_left, 404, "RESOURCE_NOT_FOUND")
        assert client.get("/v1/babies", headers=_headers(caregiver)).json() == {"items": []}
        _assert_error(
            client.get(summary_url, headers=_headers(caregiver)), 404, "RESOURCE_NOT_FOUND"
        )
        hidden_request_id = uuid4()
        hidden_latest = client.patch(
            f"/v1/care-events/{event_id}",
            headers=_headers(caregiver, request_id=hidden_request_id),
            json={
                "client_request_id": str(hidden_request_id),
                "version": 2,
                "event": successful_update.json()["event"],
            },
        )
        _assert_error(hidden_latest, 404, "RESOURCE_NOT_FOUND")
        assert hidden_latest.json()["details"]["current_resource"] is None
        _assert_error(
            client.get(f"/v1/deletions/{owner_deletion_job_id}", headers=_headers(caregiver)),
            404,
            "RESOURCE_NOT_FOUND",
        )

        former_consents = client.get(f"/v1/consents?baby_id={baby_id}", headers=_headers(caregiver))
        assert former_consents.status_code == 200
        assert former_consents.json()["items"]
        assert {item["actor_user_id"] for item in former_consents.json()["items"]} == {
            str(caregiver.user_id)
        }
        withdraw_id = uuid4()
        withdrawn = client.put(
            f"/v1/me/training-consents/{baby_id}",
            headers=_headers(caregiver, request_id=withdraw_id),
            json={
                "client_request_id": str(withdraw_id),
                "granted": False,
                "policy_version": "local-test-v1",
                "version": 1,
            },
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json()["status"] == "REVOKED"
        forbidden_regrant_id = uuid4()
        _assert_error(
            client.put(
                f"/v1/me/training-consents/{baby_id}",
                headers=_headers(caregiver, request_id=forbidden_regrant_id),
                json={
                    "client_request_id": str(forbidden_regrant_id),
                    "granted": True,
                    "policy_version": "local-test-v2",
                    "version": 2,
                },
            ),
            403,
            "CONSENT_REQUIRED",
        )

        contribution_id = uuid4()
        contribution = client.delete(
            f"/v1/me/contributions/{baby_id}?confirm=DELETE_MY_CONTRIBUTIONS",
            headers=_headers(caregiver, request_id=contribution_id),
        )
        assert contribution.status_code == 202
        assert contribution.json()["scope"] == "MY_CONTRIBUTIONS"
        replay_contribution = client.delete(
            f"/v1/me/contributions/{baby_id}?confirm=DELETE_MY_CONTRIBUTIONS",
            headers=_headers(caregiver, request_id=contribution_id),
        )
        assert replay_contribution.json() == contribution.json()
        assert (
            client.get(
                f"/v1/deletions/{contribution.json()['deletion_job_id']}",
                headers=_headers(caregiver),
            ).status_code
            == 200
        )

        owner_otp, rejoin_proof = _reauthenticate(
            client, auth, owner_otp, baby_id=baby_id, operation="CREATE_INVITE"
        )
        rejoin_invite_id = uuid4()
        rejoin_invite = client.post(
            f"/v1/babies/{baby_id}/invites",
            headers=_headers(owner_otp, request_id=rejoin_invite_id, proof=rejoin_proof),
            json={"client_request_id": str(rejoin_invite_id), "email": caregiver.email},
        )
        rejoin_token = urlsplit(rejoin_invite.json()["invite_url"]).fragment
        rejoin_accept_id = uuid4()
        rejoined = client.post(
            "/v1/invites/accept",
            headers=_headers(caregiver, request_id=rejoin_accept_id),
            json={
                "client_request_id": str(rejoin_accept_id),
                "token": rejoin_token,
                "accept_shared_use": True,
                "policy_version": "local-test-v2",
                "relationship": "FATHER",
            },
        )
        assert rejoined.status_code == 200
        assert (
            rejoined.json()["membership"]["membership_id"] != caregiver_membership["membership_id"]
        )
        rejoined_membership = rejoined.json()["membership"]
        remove_rejoined_id = uuid4()
        removed_rejoined = client.delete(
            f"/v1/babies/{baby_id}/members/{caregiver.user_id}"
            f"?version={rejoined_membership['version']}",
            headers=_headers(owner_otp, request_id=remove_rejoined_id),
        )
        assert removed_rejoined.status_code == 200
        assert removed_rejoined.json()["status"] == "REVOKED"
        _assert_error(
            client.get(summary_url, headers=_headers(caregiver)), 404, "RESOURCE_NOT_FOUND"
        )

        spare_membership = next(item for item in members if item["user_id"] == str(spare.user_id))
        remove_id = uuid4()
        removed = client.delete(
            f"/v1/babies/{baby_id}/members/{spare.user_id}?version={spare_membership['version']}",
            headers=_headers(owner_otp, request_id=remove_id),
        )
        assert removed.status_code == 200 and removed.json()["status"] == "REVOKED"
        replay_removed = client.delete(
            f"/v1/babies/{baby_id}/members/{spare.user_id}?version={spare_membership['version']}",
            headers=_headers(owner_otp, request_id=remove_id),
        )
        assert replay_removed.json() == removed.json()
        assert client.get("/v1/babies", headers=_headers(spare)).json() == {"items": []}

        owner_otp, delete_proof = _reauthenticate(
            client, auth, owner_otp, baby_id=baby_id, operation="DELETE_BABY"
        )
        latest_baby = client.get("/v1/babies", headers=_headers(owner_otp)).json()["items"][0][
            "baby"
        ]
        delete_id = uuid4()
        baby_delete = client.delete(
            f"/v1/babies/{baby_id}/data?version={latest_baby['version']}&confirm=DELETE_BABY",
            headers=_headers(owner_otp, request_id=delete_id, proof=delete_proof),
        )
        assert baby_delete.status_code == 202
        assert baby_delete.json()["status"] == "PENDING"
        assert baby_delete.json()["pending_categories"]
        replay_baby_delete = client.delete(
            f"/v1/babies/{baby_id}/data?version={latest_baby['version']}&confirm=DELETE_BABY",
            headers=_headers(owner_otp, request_id=delete_id, proof=delete_proof),
        )
        assert replay_baby_delete.json() == baby_delete.json()
        _assert_error(
            client.get(f"/v1/babies/{baby_id}/timeline", headers=_headers(owner_otp)),
            404,
            "RESOURCE_NOT_FOUND",
        )
        late_write_id = uuid4()
        _assert_error(
            client.post(
                f"/v1/babies/{baby_id}/care-events",
                headers=_headers(owner_otp, request_id=late_write_id),
                json={"client_request_id": str(late_write_id), "event": sleep_event},
            ),
            404,
            "RESOURCE_NOT_FOUND",
        )
        _assert_error(
            client.post(
                f"/v1/babies/{baby_id}/care-events",
                headers=_headers(owner_otp, request_id=sleep_request_id),
                json={"client_request_id": str(sleep_request_id), "event": sleep_event},
            ),
            404,
            "RESOURCE_NOT_FOUND",
        )
        assert (
            client.get(
                f"/v1/deletions/{baby_delete.json()['deletion_job_id']}",
                headers=_headers(owner_otp),
            ).json()["status"]
            == "PENDING"
        )

    with psycopg.connect(_database_url()) as connection:
        cached_columns = {
            row[0]
            for row in connection.execute(
                """
                select column_name from information_schema.columns
                 where table_schema = 'baby_data' and table_name = 'idempotency_records'
                """
            ).fetchall()
        }
        assert not {"request_body", "response_body", "plaintext_token"} & cached_columns
        assert connection.execute(
            "select count(*) from baby_data.deletion_jobs where status = 'COMPLETE'"
        ).fetchone() == (0,)


def test_session_revocation_provider_failure_is_not_reported_as_success() -> None:
    auth = LocalAuth()
    user = auth.create_user("provider-failure")
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        database_url=_database_url(),
        supabase_jwt_issuer=_required_env("BABY_CARE_TEST_SUPABASE_ISSUER"),
        supabase_jwt_audience="authenticated",
        supabase_jwks_url=_required_env("BABY_CARE_TEST_SUPABASE_JWKS_URL"),
        supabase_url="http://127.0.0.1:1",
        supabase_publishable_key=auth.anon_key,
        reauthentication_proof_secret="local-integration-proof-secret-at-least-32-bytes",
    )
    with TestClient(create_app(settings=settings)) as client:
        request_id = uuid4()
        response = client.post(
            "/v1/auth/session-revocations",
            headers=_headers(user, request_id=request_id),
            json={"client_request_id": str(request_id), "scope": "CURRENT"},
        )
        _assert_error(response, 503, "AUTH_PROVIDER_REVOCATION_FAILED")
        assert response.json()["details"]["session_revocation_id"]
        replay = client.post(
            "/v1/auth/session-revocations",
            headers=_headers(user, request_id=request_id),
            json={"client_request_id": str(request_id), "scope": "CURRENT"},
        )
        _assert_error(replay, 503, "AUTH_PROVIDER_REVOCATION_FAILED")
        assert (
            replay.json()["details"]["session_revocation_id"]
            == response.json()["details"]["session_revocation_id"]
        )
        _assert_error(client.get("/v1/babies", headers=_headers(user)), 401, "SESSION_REVOKED")


def test_all_session_scope_blocks_old_jwt_and_provider_refresh() -> None:
    auth = LocalAuth()
    user, other = auth.create_user_with_two_sessions("all-scope")
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        database_url=_database_url(),
        supabase_jwt_issuer=_required_env("BABY_CARE_TEST_SUPABASE_ISSUER"),
        supabase_jwt_audience="authenticated",
        supabase_jwks_url=_required_env("BABY_CARE_TEST_SUPABASE_JWKS_URL"),
        supabase_url=auth.api_url,
        supabase_publishable_key=auth.anon_key,
        reauthentication_proof_secret="local-integration-proof-secret-at-least-32-bytes",
    )
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/v1/babies", headers=_headers(user)).status_code == 200
        assert client.get("/v1/babies", headers=_headers(other)).status_code == 200
        request_id = uuid4()
        response = client.post(
            "/v1/auth/session-revocations",
            headers=_headers(user, request_id=request_id),
            json={"client_request_id": str(request_id), "scope": "ALL"},
        )
        assert response.status_code == 200 and response.json()["status"] == "COMPLETE"
        replay = client.post(
            "/v1/auth/session-revocations",
            headers=_headers(user, request_id=request_id),
            json={"client_request_id": str(request_id), "scope": "ALL"},
        )
        assert replay.status_code == 200 and replay.json() == response.json()
        _assert_error(client.get("/v1/babies", headers=_headers(user)), 401, "SESSION_REVOKED")
        _assert_error(client.get("/v1/babies", headers=_headers(other)), 401, "SESSION_REVOKED")
        assert auth.refresh_status(user.refresh_token) >= 400
        assert auth.refresh_status(other.refresh_token) >= 400

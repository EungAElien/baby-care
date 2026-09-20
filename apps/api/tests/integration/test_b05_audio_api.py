from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import shutil
import wave
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_b04_api import (
    AuthSession,
    LocalAuth,
    _assert_error,
    _database_url,
    _headers,
    _required_env,
)

from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.main import create_app
from baby_care_api.services.audio import AudioCleanupWorker
from baby_care_api.services.audio_decoder import (
    DECODER_VERSION,
    PREPROCESSING_BOUNDARY_VERSION,
    AudioDecodeError,
    DecodedAudio,
    _measure_pcm,
)
from baby_care_api.services.storage import SupabaseAudioStorage

pytestmark = pytest.mark.integration


class SyntheticPcmDecoder:
    @property
    def configured(self) -> bool:
        return True

    async def decode(
        self,
        source: Path,
        destination: Path,
        *,
        declared_mime_type: str,
        max_seconds: int,
    ) -> DecodedAudio:
        if declared_mime_type not in {"audio/wav", "audio/x-wav"}:
            raise AudioDecodeError("UNSUPPORTED_CODEC", "Synthetic decoder accepts PCM WAV only")
        await asyncio.to_thread(shutil.copyfile, source, destination)
        try:
            rate, channels, frames, duration, reasons, rejection = await asyncio.to_thread(
                _measure_pcm, destination
            )
        except AudioDecodeError:
            raise
        if duration > max_seconds:
            raise AudioDecodeError("TOO_LONG", "Synthetic input exceeded the episode limit")
        payload = await asyncio.to_thread(destination.read_bytes)
        return DecodedAudio(
            source_mime_type="audio/wav",
            container="wav",
            codec="pcm_s16le",
            sample_rate_hz=rate,
            channels=channels,
            samples_per_channel=frames,
            duration_seconds=duration,
            pcm_path=destination,
            pcm_bytes=len(payload),
            pcm_checksum_sha256=hashlib.sha256(payload).hexdigest(),
            decoder_version=DECODER_VERSION,
            preprocessing_boundary_version=PREPROCESSING_BOUNDARY_VERSION,
            quality_reasons=reasons,
            rejection_code=rejection,
        )


def _wav_bytes(*, seconds: int, rate: int = 8_000, channels: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        frame = b"\x00\x10" * channels
        audio.writeframes(frame * (seconds * rate))
    return output.getvalue()


def _raw_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    data: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, method=method, headers=headers, data=data)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _upload_standard(
    auth: LocalAuth, session: AuthSession, endpoint: str, payload: bytes, mime: str
) -> int:
    status, _, _ = _raw_request(
        endpoint,
        method="POST",
        headers={
            "apikey": auth.anon_key,
            "Authorization": f"Bearer {session.access_token}",
            "Content-Type": mime,
            "x-upsert": "false",
        },
        data=payload,
    )
    return status


def _tus_create(
    auth: LocalAuth,
    session: AuthSession,
    endpoint: str,
    *,
    bucket: str,
    object_key: str,
    length: int,
    mime: str,
) -> tuple[int, str | None]:
    def encode(value: str) -> str:
        return base64.b64encode(value.encode()).decode()

    metadata = ",".join(
        [
            f"bucketName {encode(bucket)}",
            f"objectName {encode(object_key)}",
            f"contentType {encode(mime)}",
            f"cacheControl {encode('3600')}",
        ]
    )
    status, headers, _ = _raw_request(
        endpoint,
        method="POST",
        headers={
            "apikey": auth.anon_key,
            "Authorization": f"Bearer {session.access_token}",
            "Tus-Resumable": "1.0.0",
            "Upload-Length": str(length),
            "Upload-Metadata": metadata,
            "x-upsert": "false",
        },
    )
    location = headers.get("Location") or headers.get("location")
    return status, None if location is None else urljoin(endpoint, location)


def _tus_patch(
    auth: LocalAuth,
    session: AuthSession,
    location: str,
    *,
    offset: int,
    payload: bytes,
) -> int:
    status, _, _ = _raw_request(
        location,
        method="PATCH",
        headers={
            "apikey": auth.anon_key,
            "Authorization": f"Bearer {session.access_token}",
            "Tus-Resumable": "1.0.0",
            "Upload-Offset": str(offset),
            "Content-Type": "application/offset+octet-stream",
        },
        data=payload,
    )
    return status


def _setup_baby(owner: AuthSession) -> UUID:
    baby_id = uuid4()
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            insert into baby_data.babies (
                baby_id, owner_user_id, alias, birth_date, feeding_mode,
                timezone, status, context_revision, version
            ) values (%s, %s, 'B-05 synthetic baby', date '2026-06-01',
                      'MIXED', 'Asia/Seoul', 'ACTIVE', 0, 1)
            """,
            (baby_id, owner.user_id),
        )
        connection.execute(
            """
            insert into baby_data.baby_memberships (
                baby_id, user_id, role, relationship, display_name, status, version
            ) values (%s, %s, 'OWNER', 'MOTHER', 'B-05 owner', 'ACTIVE', 1)
            """,
            (baby_id, owner.user_id),
        )
        connection.execute(
            """
            insert into baby_data.consents (
                baby_id, actor_user_id, scope, status, policy_version,
                granted_at, version
            ) values
                (%s, %s, 'SERVICE_PROCESSING', 'GRANTED', 'b05-test-v1',
                 clock_timestamp(), 1),
                (%s, %s, 'AUDIO_RETENTION', 'GRANTED', 'b05-test-v1',
                 clock_timestamp(), 1)
            """,
            (baby_id, owner.user_id, baby_id, owner.user_id),
        )
    return baby_id


def _settings(auth: LocalAuth) -> Settings:
    return Settings(
        environment=RuntimeEnvironment.TEST,
        database_url=_database_url(),
        supabase_jwt_issuer=_required_env("BABY_CARE_TEST_SUPABASE_ISSUER"),
        supabase_jwt_audience="authenticated",
        supabase_jwks_url=_required_env("BABY_CARE_TEST_SUPABASE_JWKS_URL"),
        supabase_url=auth.api_url,
        supabase_publishable_key=auth.anon_key,
        supabase_secret_key=auth.service_role_key,
        reauthentication_proof_secret="local-integration-proof-secret-at-least-32-bytes",
        child_data_production_enabled=False,
    )


def _create_episode(client: TestClient, owner: AuthSession, baby_id: UUID) -> str:
    request_id = uuid4()
    response = client.post(
        "/v1/episodes",
        headers=_headers(owner, request_id=request_id),
        json={
            "client_request_id": str(request_id),
            "baby_id": str(baby_id),
            "source": "MANUAL",
            "timing_status": "KNOWN",
            "started_at": datetime.now(UTC).isoformat(),
            "observation_session_id": None,
            "data_origin": "DEMO",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["episode_id"])


def _create_upload(
    client: TestClient,
    owner: AuthSession,
    episode_id: str,
    payload: bytes,
    *,
    prefer_resumable: bool = False,
) -> tuple[dict[str, object], UUID]:
    request_id = uuid4()
    checksum = hashlib.sha256(payload).hexdigest()
    response = client.post(
        f"/v1/episodes/{episode_id}/uploads",
        headers=_headers(owner, request_id=request_id),
        json={
            "client_request_id": str(request_id),
            "mime_type": "audio/wav",
            "bytes": len(payload),
            "duration_seconds": None,
            "checksum_sha256": checksum,
            "prefer_resumable": prefer_resumable,
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), request_id


def test_b05_standard_completion_playback_rejection_consent_and_cleanup() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b05-owner")
    outsider = auth.create_user("b05-outsider")
    baby_id = _setup_baby(owner)
    settings = _settings(auth)
    app = create_app(settings=settings, audio_decoder=SyntheticPcmDecoder())
    payload = _wav_bytes(seconds=2)
    checksum = hashlib.sha256(payload).hexdigest()

    with TestClient(app) as client:
        capabilities = client.get("/v1/capabilities", headers=_headers(owner))
        assert capabilities.status_code == 200
        assert capabilities.json()["supported_mime_types"] == [
            "audio/wav",
            "audio/x-wav",
            "audio/webm",
            "audio/mp4",
            "audio/aac",
        ]
        assert capabilities.json()["audio_model"]["available"] is False
        assert capabilities.json()["automatic_detection_supported"] is False

        episode_id = _create_episode(client, owner, baby_id)
        upload, _ = _create_upload(client, owner, episode_id, payload)
        grant = upload["upload"]
        assert isinstance(grant, dict)
        assert grant["method"] == "STANDARD"
        assert str(grant["object_key"]).split("/")[0] == str(baby_id)
        assert ".wav" not in str(grant["object_key"])
        assert owner.email not in str(grant["object_key"])
        assert _upload_standard(
            auth,
            owner,
            str(grant["upload_endpoint"]),
            payload,
            "audio/wav",
        ) in {200, 201}

        complete_id = uuid4()
        completed = client.post(
            f"/v1/uploads/{grant['upload_id']}/complete",
            headers=_headers(owner, request_id=complete_id),
            json={"client_request_id": str(complete_id), "checksum_sha256": checksum},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "READY"
        assert completed.json()["bytes"] == len(payload)
        assert completed.json()["quality_reasons"] == []
        audio_id = completed.json()["audio_id"]

        replay = client.post(
            f"/v1/uploads/{grant['upload_id']}/complete",
            headers=_headers(owner, request_id=complete_id),
            json={"client_request_id": str(complete_id), "checksum_sha256": checksum},
        )
        assert replay.status_code == 200 and replay.json() == completed.json()

        detail = client.get(f"/v1/episodes/{episode_id}", headers=_headers(owner))
        assert detail.status_code == 200
        assert [item["audio_id"] for item in detail.json()["audio_assets"]] == [audio_id]
        assert detail.json()["analyses"] == []
        _assert_error(
            client.get(f"/v1/episodes/{episode_id}", headers=_headers(outsider)),
            404,
            "RESOURCE_NOT_FOUND",
        )

        playback = client.get(f"/v1/audio-assets/{audio_id}/playback", headers=_headers(owner))
        assert playback.status_code == 200, playback.text
        signed_url = playback.json()["playback_url"]
        status, _, signed_payload = _raw_request(signed_url, method="GET", headers={})
        assert status == 200 and signed_payload == payload

        direct_status, _, _ = _raw_request(
            f"{auth.api_url}/storage/v1/object/authenticated/baby-audio/{grant['object_key']}",
            method="GET",
            headers={
                "apikey": auth.anon_key,
                "Authorization": f"Bearer {owner.access_token}",
            },
        )
        assert direct_status >= 400

        revoke_id = uuid4()
        revoked = client.put(
            "/v1/consents",
            headers=_headers(owner, request_id=revoke_id),
            json={
                "client_request_id": str(revoke_id),
                "baby_id": str(baby_id),
                "scope": "AUDIO_RETENTION",
                "granted": False,
                "policy_version": "b05-test-v1",
                "version": 1,
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["status"] == "REVOKED"
        assert (
            client.get(f"/v1/audio-assets/{audio_id}/playback", headers=_headers(owner)).status_code
            != 200
        )
        # Provider-issued links cannot be revoked before their <=60 second expiry.
        status, _, _ = _raw_request(signed_url, method="GET", headers={})
        assert status == 200

    storage = SupabaseAudioStorage(
        supabase_url=auth.api_url,
        service_key=auth.service_role_key,
    )

    async def cleanup() -> dict[str, int]:
        worker = AudioCleanupWorker(_database_url(), storage=storage)
        await worker.open()
        try:
            return await worker.run_once()
        finally:
            await worker.close()

    cleanup_result = asyncio.run(cleanup())
    assert cleanup_result["completed"] == 1
    with psycopg.connect(_database_url()) as connection:
        stored = connection.execute(
            "select status::text from baby_data.audio_assets where audio_id = %s",
            (UUID(audio_id),),
        ).fetchone()
        derivative = connection.execute(
            "select status from baby_data.audio_derivatives where audio_id = %s",
            (UUID(audio_id),),
        ).fetchone()
    assert stored == ("DELETED",)
    assert derivative == ("DELETED",)


def test_b05_invalid_audio_is_persisted_and_idempotently_rejected() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b05-invalid")
    baby_id = _setup_baby(owner)
    app = create_app(settings=_settings(auth), audio_decoder=SyntheticPcmDecoder())
    payload = b"not a wave file"
    checksum = hashlib.sha256(payload).hexdigest()

    with TestClient(app) as client:
        episode_id = _create_episode(client, owner, baby_id)
        upload, _ = _create_upload(client, owner, episode_id, payload)
        grant = upload["upload"]
        assert isinstance(grant, dict)
        assert _upload_standard(
            auth,
            owner,
            str(grant["upload_endpoint"]),
            payload,
            "audio/wav",
        ) in {200, 201}
        complete_id = uuid4()
        body = {"client_request_id": str(complete_id), "checksum_sha256": checksum}
        rejected = client.post(
            f"/v1/uploads/{grant['upload_id']}/complete",
            headers=_headers(owner, request_id=complete_id),
            json=body,
        )
        _assert_error(rejected, 422, "INVALID_AUDIO")
        replay = client.post(
            f"/v1/uploads/{grant['upload_id']}/complete",
            headers=_headers(owner, request_id=complete_id),
            json=body,
        )
        _assert_error(replay, 422, "INVALID_AUDIO")
    with psycopg.connect(_database_url()) as connection:
        row = connection.execute(
            """
            select status::text, rejection_code
              from baby_data.audio_assets where audio_id = %s
            """,
            (UUID(str(upload["audio"]["audio_id"])),),
        ).fetchone()
    assert row == ("REJECTED", "DECODE_ERROR")


def test_b05_tus_requires_fixed_chunks_and_cancel_blocks_later_patch() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b05-tus")
    baby_id = _setup_baby(owner)
    app = create_app(settings=_settings(auth), audio_decoder=SyntheticPcmDecoder())
    payload = _wav_bytes(seconds=20, rate=96_000, channels=2)
    assert len(payload) > 6 * 1024 * 1024

    with TestClient(app) as client:
        episode_id = _create_episode(client, owner, baby_id)
        upload, _ = _create_upload(
            client,
            owner,
            episode_id,
            payload,
            prefer_resumable=True,
        )
        grant = upload["upload"]
        assert isinstance(grant, dict) and grant["method"] == "TUS"
        created, location = _tus_create(
            auth,
            owner,
            str(grant["upload_endpoint"]),
            bucket=str(grant["bucket"]),
            object_key=str(grant["object_key"]),
            length=len(payload),
            mime="audio/wav",
        )
        assert created in {200, 201} and location is not None
        chunk_size = 6 * 1024 * 1024
        assert (
            _tus_patch(
                auth,
                owner,
                location,
                offset=0,
                payload=payload[:chunk_size],
            )
            == 204
        )
        cancel_id = uuid4()
        canceled = client.post(
            f"/v1/uploads/{grant['upload_id']}/cancel",
            headers=_headers(owner, request_id=cancel_id),
            json={"client_request_id": str(cancel_id)},
        )
        assert canceled.status_code == 200
        assert canceled.json()["status"] == "DELETING"
        assert (
            _tus_patch(
                auth,
                owner,
                location,
                offset=chunk_size,
                payload=payload[chunk_size:],
            )
            >= 400
        )

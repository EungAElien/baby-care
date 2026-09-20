from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, status

from baby_care_api.core.config import API_V1_PREFIX
from baby_care_api.core.errors import ApiException
from baby_care_api.models.audio import (
    AudioAsset,
    AudioUpload,
    CancelUpload,
    CompleteUpload,
    CreateEpisode,
    CreateUpload,
    Episode,
    EpisodeDetail,
    Playback,
    ReissueUpload,
)
from baby_care_api.services.audio import PostgresAudioService
from baby_care_api.services.idempotency import ensure_idempotency_key_matches
from baby_care_api.services.security import AuthenticatedPrincipal, AuthenticationPort

router = APIRouter(prefix=API_V1_PREFIX)

AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]
IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


def _service(request: Request) -> PostgresAudioService:
    service = request.app.state.audio_service
    if service is None:
        raise ApiException.service_unavailable()
    return cast(PostgresAudioService, service)


async def _principal(request: Request, authorization: str | None) -> AuthenticatedPrincipal:
    authentication = cast(AuthenticationPort, request.app.state.authentication)
    return await authentication.authenticate(authorization)


def _path(request: Request) -> str:
    return request.url.path


@router.post(
    "/episodes",
    response_model=Episode,
    status_code=status.HTTP_201_CREATED,
    operation_id="createEpisode",
)
async def create_episode(
    body: CreateEpisode,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> Episode:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_episode(principal, body, path=_path(request))


@router.get(
    "/episodes/{episode_id}",
    response_model=EpisodeDetail,
    operation_id="getEpisode",
)
async def get_episode(
    episode_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> EpisodeDetail:
    principal = await _principal(request, authorization)
    return await _service(request).get_episode(principal, episode_id)


@router.post(
    "/episodes/{episode_id}/uploads",
    response_model=AudioUpload,
    status_code=status.HTTP_201_CREATED,
    operation_id="createUpload",
)
async def create_upload(
    episode_id: UUID,
    body: CreateUpload,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> AudioUpload:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_upload(principal, episode_id, body, path=_path(request))


@router.post(
    "/audio-assets/{audio_id}/uploads",
    response_model=AudioUpload,
    status_code=status.HTTP_201_CREATED,
    operation_id="reissueUpload",
)
async def reissue_upload(
    audio_id: UUID,
    body: ReissueUpload,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> AudioUpload:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).reissue_upload(principal, audio_id, body, path=_path(request))


@router.post(
    "/uploads/{upload_id}/complete",
    response_model=AudioAsset,
    operation_id="completeUpload",
)
async def complete_upload(
    upload_id: UUID,
    body: CompleteUpload,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> AudioAsset:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).complete_upload(principal, upload_id, body, path=_path(request))


@router.post(
    "/uploads/{upload_id}/cancel",
    response_model=AudioAsset,
    operation_id="cancelUpload",
)
async def cancel_upload(
    upload_id: UUID,
    body: CancelUpload,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> AudioAsset:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).cancel_upload(principal, upload_id, body, path=_path(request))


@router.get(
    "/audio-assets/{audio_id}/playback",
    response_model=Playback,
    operation_id="getPlayback",
)
async def get_playback(
    audio_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> Playback:
    principal = await _principal(request, authorization)
    return await _service(request).get_playback(principal, audio_id)

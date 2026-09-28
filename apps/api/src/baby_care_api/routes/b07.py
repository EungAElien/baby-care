from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request

from baby_care_api.core.config import API_V1_PREFIX
from baby_care_api.core.errors import ApiException
from baby_care_api.models.audio import Capabilities, DetectorInfo, ModelInfo
from baby_care_api.models.normalization import (
    ConfirmCareEntry,
    ConfirmedResources,
    CreateNormalization,
    NormalizationRun,
    StateObservation,
)
from baby_care_api.services.audio import PostgresAudioService
from baby_care_api.services.b07 import PostgresNormalizationService
from baby_care_api.services.idempotency import ensure_idempotency_key_matches
from baby_care_api.services.security import AuthenticatedPrincipal, AuthenticationPort

router = APIRouter(prefix=API_V1_PREFIX)

AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]
IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


def _service(request: Request) -> PostgresNormalizationService:
    service = request.app.state.b07_service
    if service is None:
        raise ApiException.service_unavailable()
    return cast(PostgresNormalizationService, service)


async def _principal(request: Request, authorization: str | None) -> AuthenticatedPrincipal:
    authentication = cast(AuthenticationPort, request.app.state.authentication)
    return await authentication.authenticate(authorization)


@router.get("/capabilities", response_model=Capabilities, operation_id="getCapabilities")
async def get_capabilities(request: Request) -> Capabilities:
    normalizer = request.app.state.b07_service
    normalizer_available = (
        isinstance(normalizer, PostgresNormalizationService) and normalizer.available
    )
    unavailable_reason = request.app.state.normalizer_unavailable_reason
    audio_service = request.app.state.audio_service
    if isinstance(audio_service, PostgresAudioService):
        return audio_service.capabilities(
            normalizer_available=normalizer_available,
            normalizer_unavailable_reason=unavailable_reason,
        )
    return Capabilities(
        audio_model=ModelInfo(
            available=False,
            model_version=None,
            preprocess_version=None,
            label_mapping_version=None,
            supported_labels=[],
            inference_mode="STUB",
        ),
        supported_mime_types=[],
        normalizer_available=normalizer_available,
        normalizer_unavailable_reason=unavailable_reason,
        automatic_detection_supported=False,
        detector=DetectorInfo(
            available=False,
            execution_mode="STUB",
            model_version=None,
            model_asset_url=None,
            weights_sha256=None,
            input_sample_rate_hz=None,
            policy_version=None,
            supported_clients=[],
        ),
    )


@router.post(
    "/care-entries/{entry_id}/normalizations",
    response_model=NormalizationRun,
    operation_id="createNormalization",
)
async def create_normalization(
    entry_id: UUID,
    body: CreateNormalization,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> NormalizationRun:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_normalization(
        principal, entry_id, body, path=request.url.path
    )


@router.get(
    "/normalizations/{run_id}",
    response_model=NormalizationRun,
    operation_id="getNormalization",
)
async def get_normalization(
    run_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> NormalizationRun:
    principal = await _principal(request, authorization)
    return await _service(request).get_normalization(principal, run_id)


@router.post(
    "/care-entries/{entry_id}/confirm",
    response_model=ConfirmedResources,
    operation_id="confirmCareEntry",
)
async def confirm_care_entry(
    entry_id: UUID,
    body: ConfirmCareEntry,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> ConfirmedResources:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).confirm_care_entry(
        principal, entry_id, body, path=request.url.path
    )


@router.get(
    "/state-observations/{state_observation_id}",
    response_model=StateObservation,
    operation_id="getStateObservation",
)
async def get_state_observation(
    state_observation_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> StateObservation:
    principal = await _principal(request, authorization)
    return await _service(request).get_state_observation(principal, state_observation_id)

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request

from baby_care_api.core.config import API_V1_PREFIX
from baby_care_api.core.errors import ApiException
from baby_care_api.models.audio import Analysis, CreateAnalysis, RetryAnalysis
from baby_care_api.services.analysis import PostgresAnalysisService
from baby_care_api.services.idempotency import ensure_idempotency_key_matches
from baby_care_api.services.security import AuthenticatedPrincipal, AuthenticationPort

router = APIRouter(prefix=API_V1_PREFIX)

AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]
IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


def _service(request: Request) -> PostgresAnalysisService:
    service = request.app.state.analysis_service
    if service is None:
        raise ApiException.service_unavailable()
    return cast(PostgresAnalysisService, service)


async def _principal(request: Request, authorization: str | None) -> AuthenticatedPrincipal:
    authentication = cast(AuthenticationPort, request.app.state.authentication)
    return await authentication.authenticate(authorization)


@router.post(
    "/episodes/{episode_id}/analyses",
    response_model=Analysis,
    operation_id="createAnalysis",
)
async def create_analysis(
    episode_id: UUID,
    body: CreateAnalysis,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> Analysis:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_analysis(
        principal, episode_id, body, path=request.url.path
    )


@router.get(
    "/analyses/{analysis_id}",
    response_model=Analysis,
    operation_id="getAnalysis",
)
async def get_analysis(
    analysis_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> Analysis:
    principal = await _principal(request, authorization)
    return await _service(request).get_analysis(principal, analysis_id)


@router.post(
    "/analyses/{analysis_id}/retry",
    response_model=Analysis,
    operation_id="retryAnalysis",
)
async def retry_analysis(
    analysis_id: UUID,
    body: RetryAnalysis,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> Analysis:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).retry_analysis(
        principal, analysis_id, body, path=request.url.path
    )

from __future__ import annotations

from http import HTTPStatus
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from baby_care_api.core.config import CONTRACT_VERSION, SERVICE_VERSION
from baby_care_api.models.health import LivenessResponse, ReadinessResponse
from baby_care_api.services.readiness import ReadinessService

router = APIRouter(prefix="/health", tags=["Operations"])


@router.get(
    "/live",
    operation_id="getLiveness",
    response_model=LivenessResponse,
    summary="Check whether the API process is running",
)
async def get_liveness() -> LivenessResponse:
    return LivenessResponse(
        status="ok",
        service_version=SERVICE_VERSION,
        contract_version=CONTRACT_VERSION,
    )


@router.get(
    "/ready",
    operation_id="getReadiness",
    response_model=ReadinessResponse,
    responses={
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "One or more required runtime dependencies are not ready.",
        }
    },
    summary="Report dependency readiness without claiming business capability",
)
async def get_readiness(request: Request) -> ReadinessResponse | JSONResponse:
    readiness = cast(ReadinessService, request.app.state.readiness)
    result = await readiness.snapshot()
    request.state.result_code = result.status.upper()
    if result.status == "ready":
        return result
    return JSONResponse(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        content=result.model_dump(mode="json"),
    )

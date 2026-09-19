from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from baby_care_api.core.config import (
    API_V1_PREFIX,
    CONTRACT_VERSION,
    SERVICE_VERSION,
    Settings,
    get_settings,
)
from baby_care_api.core.errors import install_exception_handlers
from baby_care_api.core.logging import configure_logging
from baby_care_api.core.request_context import install_request_context
from baby_care_api.routes.health import router as health_router
from baby_care_api.services.idempotency import UnconfiguredIdempotencyPort
from baby_care_api.services.readiness import ComponentName, ReadinessProbe, ReadinessService
from baby_care_api.services.security import (
    UnconfiguredAuthenticationPort,
    UnconfiguredAuthorizationPort,
)


def _install_openapi(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema["x-business-contract"] = {
            "source": "contracts/openapi계약.json",
            "version": CONTRACT_VERSION,
            "api_prefix": API_V1_PREFIX,
            "implemented_operations": [],
        }
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def create_app(
    *,
    settings: Settings | None = None,
    readiness_probes: Mapping[ComponentName, ReadinessProbe] | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    configure_logging(active_settings.log_level)

    app = FastAPI(
        title="Baby Care API",
        version=SERVICE_VERSION,
        description=(
            "B-01 server foundation. The canonical business contract is version 1.0.0 at "
            "contracts/openapi계약.json; no business operation is claimed as implemented yet."
        ),
    )
    app.state.settings = active_settings
    app.state.readiness = ReadinessService(readiness_probes)
    app.state.authentication = UnconfiguredAuthenticationPort()
    app.state.authorization = UnconfiguredAuthorizationPort()
    app.state.idempotency = UnconfiguredIdempotencyPort()

    install_exception_handlers(app)
    install_request_context(app)
    app.include_router(health_router)
    _install_openapi(app)
    return app


app = create_app()

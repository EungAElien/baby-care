from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from time import perf_counter
from typing import Final
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from baby_care_api.core.config import CONTRACT_VERSION, SERVICE_VERSION
from baby_care_api.core.errors import internal_error_response

REQUEST_ID_HEADER: Final = "X-Request-ID"
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger("baby_care_api.requests")


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def install_request_context(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid4()
        request.state.request_id = request_id
        request.state.result_code = "OK"
        token = request_id_context.set(str(request_id))
        started = perf_counter()

        try:
            try:
                response = await call_next(request)
            except Exception:
                response = internal_error_response(request)

            response.headers[REQUEST_ID_HEADER] = str(request_id)
            response.headers.setdefault("Cache-Control", "no-store")
            return response
        finally:
            duration_ms = round((perf_counter() - started) * 1000, 3)
            status_code = response.status_code if "response" in locals() else 500
            logger.info(
                "request_completed",
                extra={
                    "request_id": str(request_id),
                    "http_method": request.method,
                    "route": _route_template(request),
                    "status_code": status_code,
                    "result_code": request.state.result_code,
                    "duration_ms": duration_ms,
                    "service_version": SERVICE_VERSION,
                    "contract_version": CONTRACT_VERSION,
                },
            )
            request_id_context.reset(token)

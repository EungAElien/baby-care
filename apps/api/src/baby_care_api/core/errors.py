from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Final
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from baby_care_api.models.errors import ApiError, ErrorCode, ErrorDetails, FieldError

ERROR_STATUS: Final[Mapping[ErrorCode, int]] = {
    ErrorCode.AUTH_REQUIRED: HTTPStatus.UNAUTHORIZED,
    ErrorCode.TOKEN_EXPIRED: HTTPStatus.UNAUTHORIZED,
    ErrorCode.INVALID_TOKEN: HTTPStatus.UNAUTHORIZED,
    ErrorCode.OWNER_ONLY: HTTPStatus.FORBIDDEN,
    ErrorCode.AUTHOR_ONLY: HTTPStatus.FORBIDDEN,
    ErrorCode.INVITE_EMAIL_MISMATCH: HTTPStatus.FORBIDDEN,
    ErrorCode.CONSENT_REQUIRED: HTTPStatus.FORBIDDEN,
    ErrorCode.RESOURCE_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.VERSION_CONFLICT: HTTPStatus.CONFLICT,
    ErrorCode.SOURCE_REVISION_CHANGED: HTTPStatus.CONFLICT,
    ErrorCode.ANALYSIS_IN_PROGRESS: HTTPStatus.CONFLICT,
    ErrorCode.NORMALIZATION_IN_PROGRESS: HTTPStatus.CONFLICT,
    ErrorCode.OPERATION_IN_PROGRESS: HTTPStatus.CONFLICT,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: HTTPStatus.CONFLICT,
    ErrorCode.OWNER_REQUIRED: HTTPStatus.CONFLICT,
    ErrorCode.ACTIVE_SESSION_EXISTS: HTTPStatus.CONFLICT,
    ErrorCode.SLEEP_ALREADY_ACTIVE: HTTPStatus.CONFLICT,
    ErrorCode.RESOURCE_DELETING: HTTPStatus.CONFLICT,
    ErrorCode.ALREADY_MEMBER: HTTPStatus.CONFLICT,
    ErrorCode.INVITE_ALREADY_USED: HTTPStatus.CONFLICT,
    ErrorCode.OWNER_BABY_LIMIT: HTTPStatus.CONFLICT,
    ErrorCode.ALREADY_CONFIRMED: HTTPStatus.CONFLICT,
    ErrorCode.INVALID_STATE: HTTPStatus.CONFLICT,
    ErrorCode.INVITE_EXPIRED: HTTPStatus.GONE,
    ErrorCode.INVITE_REVOKED: HTTPStatus.GONE,
    ErrorCode.RESOURCE_DELETED: HTTPStatus.GONE,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.INVALID_AUDIO: 422,
    ErrorCode.RATE_LIMITED: HTTPStatus.TOO_MANY_REQUESTS,
    ErrorCode.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.MODEL_NOT_READY: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorCode.SERVICE_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
}


class ApiException(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        field_errors: list[FieldError] | None = None,
        details: ErrorDetails | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.field_errors = field_errors or []
        self.details = details or ErrorDetails.empty()
        self.status_code = ERROR_STATUS[code]

    @classmethod
    def service_unavailable(cls) -> ApiException:
        return cls(
            ErrorCode.SERVICE_UNAVAILABLE,
            "The required service is not available.",
            retryable=True,
        )


def _request_id(request: Request) -> UUID:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, UUID) else uuid4()


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
    field_errors: list[FieldError] | None = None,
    details: ErrorDetails | None = None,
) -> JSONResponse:
    request.state.result_code = code.value
    payload = ApiError(
        code=code,
        message=message,
        retryable=retryable,
        request_id=_request_id(request),
        field_errors=field_errors or [],
        details=details or ErrorDetails.empty(),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        field_errors=exc.field_errors,
        details=exc.details,
    )


_VALIDATION_MESSAGES: Final[Mapping[str, str]] = {
    "missing": "Field is required.",
    "extra_forbidden": "Field is not permitted.",
    "enum": "Value is not an allowed option.",
    "literal_error": "Value is not an allowed option.",
    "uuid_parsing": "Value must be a valid UUID.",
    "uuid_type": "Value must be a valid UUID.",
    "datetime_from_date_parsing": "Value must be an ISO 8601 date-time with a timezone.",
    "datetime_object_invalid": "Value must be an ISO 8601 date-time with a timezone.",
    "timezone_aware": "Value must include a timezone.",
}


def _field_name(location: tuple[str | int, ...]) -> str:
    public_parts = [
        str(part) for part in location if part not in {"body", "path", "query", "header"}
    ]
    return ".".join(public_parts) or "request"


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    field_errors: list[FieldError] = []
    seen: set[tuple[str, str]] = set()
    for error in exc.errors()[:50]:
        error_type = str(error.get("type", "invalid"))
        location = tuple(error.get("loc", ()))
        field = _field_name(location)
        dedupe_key = (field, error_type)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        field_errors.append(
            FieldError(
                field=field,
                code=error_type,
                message=_VALIDATION_MESSAGES.get(error_type, "Value is invalid."),
            )
        )

    return _error_response(
        request,
        status_code=ERROR_STATUS[ErrorCode.VALIDATION_ERROR],
        code=ErrorCode.VALIDATION_ERROR,
        message="The request did not match the API contract.",
        retryable=False,
        field_errors=field_errors,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Method mismatches are also hidden as not found until a contract route exists.
    return _error_response(
        request,
        status_code=ERROR_STATUS[ErrorCode.RESOURCE_NOT_FOUND],
        code=ErrorCode.RESOURCE_NOT_FOUND,
        message="The requested resource was not found.",
        retryable=False,
    )


def internal_error_response(request: Request) -> JSONResponse:
    return _error_response(
        request,
        status_code=ERROR_STATUS[ErrorCode.INTERNAL_ERROR],
        code=ErrorCode.INTERNAL_ERROR,
        message="An unexpected error occurred.",
        retryable=True,
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiException, api_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]

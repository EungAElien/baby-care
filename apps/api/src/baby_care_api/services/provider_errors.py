from __future__ import annotations

from typing import Any


def _error_code(exc: Exception) -> str | None:
    direct = getattr(exc, "code", None)
    if isinstance(direct, str):
        return direct
    body: Any = getattr(exc, "body", None)
    if isinstance(body, dict):
        value = body.get("code")
        if isinstance(value, str):
            return value
        nested = body.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("code"), str):
            return str(nested["code"])
    return None


def classify_provider_error(exc: Exception) -> tuple[str, bool]:
    """Classify without retaining provider messages, which may contain user input."""

    name = type(exc).__name__
    code = _error_code(exc)
    status_code = getattr(exc, "status_code", None)
    if name == "AuthenticationError" or status_code == 401:
        return "AUTHENTICATION_FAILED", False
    if name in {"PermissionDeniedError", "NotFoundError"} or status_code in {403, 404}:
        return "MODEL_ACCESS_UNAVAILABLE", False
    if code in {"model_not_found", "model_not_available", "unsupported_model"}:
        return "MODEL_ACCESS_UNAVAILABLE", False
    if name == "RateLimitError" or status_code == 429:
        return "LIMIT_OR_QUOTA_EXCEEDED", False
    if name in {"APITimeoutError", "TimeoutError"}:
        return "TIMEOUT", True
    if name == "APIConnectionError":
        return "CONNECTION_ERROR", True
    if status_code in {500, 502, 503, 504} or name == "InternalServerError":
        return "PROVIDER_SERVER_ERROR", True
    if name == "BadRequestError" or status_code == 400:
        return "INVALID_PROVIDER_REQUEST", False
    return "PROVIDER_ERROR", False

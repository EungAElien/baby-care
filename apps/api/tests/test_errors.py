from __future__ import annotations

import logging
from copy import deepcopy
from uuid import UUID

from fastapi.testclient import TestClient

from baby_care_api.core.config import Settings
from baby_care_api.core.logging import SafeJsonFormatter
from baby_care_api.main import create_app
from baby_care_api.models.care_events import CreateCareEvent


def _client_with_test_routes() -> TestClient:
    app = create_app(settings=Settings(environment="test"))

    @app.post("/_test/care-event", include_in_schema=False)
    async def validate_care_event(body: CreateCareEvent) -> CreateCareEvent:
        return body

    @app.get("/_test/internal-error", include_in_schema=False)
    async def internal_error() -> None:
        raise RuntimeError("synthetic-internal-secret")

    return TestClient(app, raise_server_exceptions=False)


def _valid_request() -> dict[str, object]:
    return {
        "client_request_id": "10000000-0000-4000-8000-000000000807",
        "event": {
            "type": "FEEDING",
            "occurred_at": "2026-09-19T09:00:00Z",
            "ended_at": None,
            "time_precision": "EXACT",
            "payload": {
                "mode": "FORMULA",
                "amount_ml": 80,
                "duration_minutes": None,
            },
        },
    }


def test_validation_error_uses_contract_shape_without_echoing_input() -> None:
    client = _client_with_test_routes()
    body = deepcopy(_valid_request())
    body["private_note"] = "must-not-appear"

    response = client.post("/_test/care-event", json=body)

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["retryable"] is False
    assert payload["field_errors"] == [
        {
            "field": "private_note",
            "code": "extra_forbidden",
            "message": "Field is not permitted.",
        }
    ]
    assert set(payload) == {
        "code",
        "message",
        "retryable",
        "request_id",
        "field_errors",
        "details",
    }
    assert "must-not-appear" not in response.text
    assert response.headers["X-Request-ID"] == payload["request_id"]
    UUID(payload["request_id"])


def test_internal_error_hides_exception_details() -> None:
    client = _client_with_test_routes()

    response = client.get("/_test/internal-error")

    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "INTERNAL_ERROR"
    assert payload["retryable"] is True
    assert "synthetic-internal-secret" not in response.text
    assert response.headers["X-Request-ID"] == payload["request_id"]


def test_not_found_and_method_mismatch_do_not_use_default_fastapi_errors(
    client: TestClient,
) -> None:
    missing = client.get("/does-not-exist")
    wrong_method = client.post("/health/live")

    for response in (missing, wrong_method):
        assert response.status_code == 404
        assert response.json()["code"] == "RESOURCE_NOT_FOUND"
        assert "detail" not in response.json()
        assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_inbound_request_id_is_not_trusted(client: TestClient) -> None:
    supplied = "10000000-0000-4000-8000-000000000999"

    first = client.get("/health/live", headers={"X-Request-ID": supplied})
    second = client.get("/health/live", headers={"X-Request-ID": supplied})

    assert first.headers["X-Request-ID"] != supplied
    assert second.headers["X-Request-ID"] != supplied
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_safe_log_formatter_ignores_sensitive_extras() -> None:
    record = logging.LogRecord(
        name="baby_care_api.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "10000000-0000-4000-8000-000000000900"
    record.authorization = "Bearer synthetic-secret"
    record.raw_text = "private caregiver note"
    record.signed_url = "https://storage.invalid/signed-secret"

    output = SafeJsonFormatter().format(record)

    assert "request_completed" in output
    assert "10000000-0000-4000-8000-000000000900" in output
    assert "synthetic-secret" not in output
    assert "private caregiver note" not in output
    assert "signed-secret" not in output


def test_safe_log_formatter_does_not_render_arbitrary_messages() -> None:
    record = logging.LogRecord(
        name="third_party.library",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Bearer synthetic-secret private caregiver note",
        args=(),
        exc_info=None,
    )

    output = SafeJsonFormatter().format(record)

    assert '"event":"application_event"' in output
    assert "synthetic-secret" not in output
    assert "private caregiver note" not in output


def test_openapi_is_honest_about_implemented_business_operations(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["x-business-contract"] == {
        "source": "contracts/openapi계약.json",
        "version": "1.0.0",
        "api_prefix": "/v1",
        "implemented_operations": [],
    }
    assert not any(path.startswith("/v1") for path in schema["paths"])
    assert set(schema["paths"]) == {"/health/live", "/health/ready"}

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from baby_care_api.core.config import CONTRACT_VERSION, SERVICE_VERSION, Settings
from baby_care_api.main import create_app
from baby_care_api.services.readiness import ComponentName


def test_liveness_only_claims_process_health(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service_version": SERVICE_VERSION,
        "contract_version": CONTRACT_VERSION,
    }
    UUID(response.headers["X-Request-ID"])
    assert response.headers["Cache-Control"] == "no-store"


def test_readiness_is_fail_closed_before_auth_and_database_are_wired(
    client: TestClient,
) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["checks"]["authentication"] == {
        "required": True,
        "configured": False,
        "ready": False,
    }
    assert payload["checks"]["database"] == {
        "required": True,
        "configured": False,
        "ready": False,
    }
    assert payload["checks"]["model"]["required"] is False
    assert payload["checks"]["external_services"]["required"] is False


def test_ready_does_not_claim_optional_model_or_external_services() -> None:
    async def ready_probe() -> bool:
        return True

    app = create_app(
        settings=Settings(environment="test"),
        readiness_probes={
            ComponentName.AUTHENTICATION: ready_probe,
            ComponentName.DATABASE: ready_probe,
        },
    )

    with TestClient(app) as test_client:
        response = test_client.get("/health/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["checks"]["model"] == {
        "required": False,
        "configured": False,
        "ready": False,
    }
    assert payload["checks"]["external_services"] == {
        "required": False,
        "configured": False,
        "ready": False,
    }


def test_failed_probe_never_becomes_ready() -> None:
    async def ready_probe() -> bool:
        return True

    async def broken_probe() -> bool:
        raise RuntimeError("synthetic probe failure")

    app = create_app(
        settings=Settings(environment="test"),
        readiness_probes={
            ComponentName.AUTHENTICATION: ready_probe,
            ComponentName.DATABASE: broken_probe,
        },
    )

    with TestClient(app) as test_client:
        response = test_client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {
        "required": True,
        "configured": True,
        "ready": False,
    }

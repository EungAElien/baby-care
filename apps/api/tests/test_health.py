from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from baby_care_api.core.config import CONTRACT_VERSION, SERVICE_VERSION, Settings
from baby_care_api.main import create_app
from baby_care_api.services.readiness import ComponentName


class FakeModelRuntime:
    def __init__(self) -> None:
        self.inference_calls = 0
        self.closed = False

    def infer_audio(self, path: Path) -> object:
        self.inference_calls += 1
        return {"path_name": path.name}

    def close(self) -> None:
        self.closed = True


class UnsafeCodedLoadError(RuntimeError):
    code = "PRIVATE_PATH_/sensitive/model.pt"


def _enabled_model_settings(tmp_path: Path) -> Settings:
    allowed = tmp_path / "runtime"
    bundle = allowed / "model"
    source = allowed / "source"
    bundle.mkdir(parents=True)
    source.mkdir()
    return Settings(
        environment="test",
        m2d_enabled=True,
        m2d_allowed_root=allowed,
        m2d_bundle_path=bundle,
        m2d_source_path=source,
    )


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


def test_browser_origin_is_explicit_and_preflight_allows_api_headers() -> None:
    app = create_app(
        settings=Settings(environment="test", browser_origins="http://localhost:3001")
    )
    with TestClient(app) as test_client:
        allowed = test_client.options(
            "/v1/babies",
            headers={
                "Origin": "http://localhost:3001",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        denied = test_client.options(
            "/v1/babies",
            headers={
                "Origin": "http://untrusted.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        unauthenticated = test_client.get(
            "/v1/babies", headers={"Origin": "http://localhost:3001"}
        )

    assert allowed.status_code == 200
    assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:3001"
    assert "authorization" in allowed.headers["Access-Control-Allow-Headers"].lower()
    assert denied.status_code == 400
    assert "Access-Control-Allow-Origin" not in denied.headers
    # This deliberately unconfigured app fails readiness before auth; CORS must
    # still expose the response to the configured browser origin.
    assert unauthenticated.status_code == 503
    assert unauthenticated.headers["Access-Control-Allow-Origin"] == "http://localhost:3001"


@pytest.mark.parametrize("origin", ["*", "http://example.test", "https://example.test/path"])
def test_browser_origin_rejects_wildcards_and_nonlocal_http(origin: str) -> None:
    with pytest.raises(ValueError, match="Browser origins must be exact"):
        Settings(environment="test", browser_origins=origin)


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


def test_enabled_model_is_required_and_loaded_once(tmp_path: Path) -> None:
    async def ready_probe() -> bool:
        return True

    runtime = FakeModelRuntime()
    factory_calls = 0

    def factory(_: Settings) -> FakeModelRuntime:
        nonlocal factory_calls
        factory_calls += 1
        return runtime

    app = create_app(
        settings=_enabled_model_settings(tmp_path),
        readiness_probes={
            ComponentName.AUTHENTICATION: ready_probe,
            ComponentName.DATABASE: ready_probe,
        },
        model_runtime_factory=factory,
    )

    with TestClient(app) as test_client:
        first = test_client.get("/health/ready")
        second = test_client.get("/health/ready")

        assert first.status_code == second.status_code == 200
        assert first.json()["checks"]["model"] == {
            "required": True,
            "configured": True,
            "ready": True,
        }
        assert app.state.model_runtime.load_attempts == 1
        assert factory_calls == 1
        assert runtime.inference_calls == 0

    assert runtime.closed is True


def test_enabled_model_missing_configuration_fails_readiness(tmp_path: Path) -> None:
    async def ready_probe() -> bool:
        return True

    app = create_app(
        settings=Settings(
            environment="test",
            m2d_enabled=True,
            m2d_allowed_root=tmp_path,
        ),
        readiness_probes={
            ComponentName.AUTHENTICATION: ready_probe,
            ComponentName.DATABASE: ready_probe,
        },
    )

    with TestClient(app) as test_client:
        response = test_client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["model"] == {
        "required": True,
        "configured": False,
        "ready": False,
    }
    assert app.state.model_runtime.load_attempts == 0


def test_enabled_model_cannot_be_replaced_by_an_injected_probe(tmp_path: Path) -> None:
    async def ready_probe() -> bool:
        return True

    app = create_app(
        settings=Settings(
            environment="test",
            m2d_enabled=True,
            m2d_allowed_root=tmp_path,
        ),
        readiness_probes={
            ComponentName.AUTHENTICATION: ready_probe,
            ComponentName.DATABASE: ready_probe,
            ComponentName.MODEL: ready_probe,
        },
    )

    with TestClient(app) as test_client:
        response = test_client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["model"] == {
        "required": True,
        "configured": False,
        "ready": False,
    }


def test_model_load_failure_keeps_liveness_and_never_retries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def ready_probe() -> bool:
        return True

    factory_calls = 0

    def broken_factory(_: Settings) -> FakeModelRuntime:
        nonlocal factory_calls
        factory_calls += 1
        raise UnsafeCodedLoadError("synthetic private detail")

    app = create_app(
        settings=_enabled_model_settings(tmp_path),
        readiness_probes={
            ComponentName.AUTHENTICATION: ready_probe,
            ComponentName.DATABASE: ready_probe,
        },
        model_runtime_factory=broken_factory,
    )

    with caplog.at_level(logging.ERROR), TestClient(app) as test_client:
        assert test_client.get("/health/live").status_code == 200
        for _ in range(3):
            response = test_client.get("/health/ready")
            assert response.status_code == 503
            assert response.json()["checks"]["model"] == {
                "required": True,
                "configured": True,
                "ready": False,
            }

    assert factory_calls == 1
    assert app.state.model_runtime.failure_code == "MODEL_LOAD_FAILED"
    assert "synthetic private detail" not in caplog.text
    assert UnsafeCodedLoadError.code not in caplog.text


def test_ready_model_does_not_hide_required_authentication_failure(tmp_path: Path) -> None:
    async def ready_probe() -> bool:
        return True

    async def failed_probe() -> bool:
        return False

    app = create_app(
        settings=_enabled_model_settings(tmp_path),
        readiness_probes={
            ComponentName.AUTHENTICATION: failed_probe,
            ComponentName.DATABASE: ready_probe,
        },
        model_runtime_factory=lambda _: FakeModelRuntime(),
    )

    with TestClient(app) as test_client:
        response = test_client.get("/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["checks"]["model"]["ready"] is True
    assert payload["checks"]["authentication"]["ready"] is False

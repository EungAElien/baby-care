from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(
        settings=Settings(
            environment=RuntimeEnvironment.TEST,
            log_level="INFO",
            host="127.0.0.1",
            port=8080,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

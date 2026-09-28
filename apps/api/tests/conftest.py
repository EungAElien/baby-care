from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.main import create_app

_REQUIRE_INTEGRATION = os.environ.get("BABY_CARE_REQUIRE_INTEGRATION") == "1"
if sys.platform == "win32" and _REQUIRE_INTEGRATION:
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_INTEGRATION_NODE_IDS: set[str] = set()
_INTEGRATION_REPORTS: dict[str, set[str]] = {
    "passed": set(),
    "failed": set(),
    "skipped": set(),
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if not _REQUIRE_INTEGRATION:
        return
    _INTEGRATION_NODE_IDS.clear()
    _INTEGRATION_NODE_IDS.update(
        item.nodeid for item in items if item.get_closest_marker("integration") is not None
    )
    if not _INTEGRATION_NODE_IDS:
        raise pytest.UsageError(
            "BABY_CARE_REQUIRE_INTEGRATION=1 but no integration tests were collected"
        )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if not _REQUIRE_INTEGRATION or report.nodeid not in _INTEGRATION_NODE_IDS:
        return
    if report.skipped:
        _INTEGRATION_REPORTS["skipped"].add(report.nodeid)
    elif report.when == "call":
        _INTEGRATION_REPORTS[report.outcome].add(report.nodeid)


def pytest_sessionfinish(session: pytest.Session) -> None:
    if not _REQUIRE_INTEGRATION:
        return
    executed = _INTEGRATION_REPORTS["passed"] | _INTEGRATION_REPORTS["failed"]
    if _INTEGRATION_REPORTS["skipped"] or not executed:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if not _REQUIRE_INTEGRATION:
        return
    terminalreporter.write_sep(
        "=",
        (
            "required integration guard: "
            f"collected={len(_INTEGRATION_NODE_IDS)} "
            f"executed={len(_INTEGRATION_REPORTS['passed'] | _INTEGRATION_REPORTS['failed'])} "
            f"skipped={len(_INTEGRATION_REPORTS['skipped'])}"
        ),
    )


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

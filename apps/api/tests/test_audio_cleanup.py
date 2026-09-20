from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from baby_care_api import audio_cleanup


def test_cleanup_requires_server_only_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        audio_cleanup,
        "get_settings",
        lambda: SimpleNamespace(
            database_url=None,
            supabase_url=None,
            supabase_secret_key=None,
        ),
    )
    with pytest.raises(SystemExit, match="server-only SUPABASE_SECRET_KEY"):
        asyncio.run(audio_cleanup._run(1))


def test_cleanup_opens_runs_and_closes_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    settings = SimpleNamespace(
        database_url=SecretStr("postgresql://synthetic"),
        supabase_url="https://project.example",
        supabase_storage_url="https://uploads.example",
        supabase_secret_key=SecretStr("server-only"),
    )
    monkeypatch.setattr(audio_cleanup, "get_settings", lambda: settings)

    class FakeStorage:
        def __init__(self, **kwargs: object) -> None:
            events.append(("storage", kwargs))

    class FakeWorker:
        def __init__(self, database_url: str, *, storage: object) -> None:
            events.append(("worker", database_url, storage.__class__.__name__))

        async def open(self, *, wait: bool = False) -> None:
            events.append(("open", wait))

        async def run_once(self, *, limit: int) -> dict[str, int]:
            events.append(("run", limit))
            return {"claimed": 2, "completed": 2, "failed": 0}

        async def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(audio_cleanup, "SupabaseAudioStorage", FakeStorage)
    monkeypatch.setattr(audio_cleanup, "AudioCleanupWorker", FakeWorker)
    result = asyncio.run(audio_cleanup._run(7))
    assert result == {"claimed": 2, "completed": 2, "failed": 0}
    assert events[-3:] == [("open", False), ("run", 7), "close"]


@pytest.mark.parametrize(("failed", "expected"), [(0, 0), (1, 1)])
def test_cleanup_cli_reports_failure_count(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed: int,
    expected: int,
) -> None:
    async def fake_run(limit: int) -> dict[str, int]:
        assert limit == 3
        return {"claimed": 1, "completed": 1 - failed, "failed": failed}

    monkeypatch.setattr(audio_cleanup, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["audio-cleanup", "--limit", "3"])
    assert audio_cleanup.main() == expected
    assert f'"failed": {failed}' in capsys.readouterr().out

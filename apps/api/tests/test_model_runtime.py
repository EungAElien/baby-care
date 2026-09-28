from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from baby_care_api.core.config import Settings
from baby_care_api.services.model_runtime import (
    ModelNotReadyError,
    ModelRuntimeBusyError,
    ModelRuntimeManager,
)


class SyntheticInputError(ValueError):
    pass


class BlockingRuntime:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.calls = 0
        self.closed = False
        self._lock = threading.Lock()

    def infer_audio(self, path: Path) -> object:
        if path.name == "invalid.wav":
            raise SyntheticInputError("bad individual input")
        with self._lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            time.sleep(0.04)
            self.calls += 1
            return path.name
        finally:
            with self._lock:
                self.active -= 1

    def close(self) -> None:
        self.closed = True


def _manager(runtime: BlockingRuntime) -> ModelRuntimeManager:
    settings = Settings(environment="test")
    return ModelRuntimeManager(
        enabled=True,
        configured=True,
        settings=settings,
        factory=lambda _: runtime,
    )


def test_inference_runs_off_event_loop_and_is_serialized() -> None:
    async def exercise() -> tuple[list[object], bool]:
        runtime = BlockingRuntime()
        manager = _manager(runtime)
        await manager.start()
        first = asyncio.create_task(manager.infer_audio(Path("first.wav")))
        second = asyncio.create_task(manager.infer_audio(Path("second.wav")))
        await asyncio.sleep(0.01)
        event_loop_remained_responsive = not first.done()
        results = await asyncio.gather(first, second)
        await manager.close()
        assert runtime.maximum_active == 1
        assert runtime.calls == 2
        assert runtime.closed is True
        return results, event_loop_remained_responsive

    results, responsive = asyncio.run(exercise())

    assert results == ["first.wav", "second.wav"]
    assert responsive is True


def test_input_error_is_not_converted_to_model_readiness_failure() -> None:
    async def exercise() -> ModelRuntimeManager:
        manager = _manager(BlockingRuntime())
        await manager.start()
        with pytest.raises(SyntheticInputError):
            await manager.infer_audio(Path("invalid.wav"))
        assert await manager.probe() is True
        await manager.close()
        return manager

    manager = asyncio.run(exercise())

    assert manager.load_attempts == 1


def test_inference_before_successful_start_is_rejected_without_loading() -> None:
    manager = ModelRuntimeManager(
        enabled=True,
        configured=False,
        settings=Settings(environment="test"),
        factory=lambda _: BlockingRuntime(),
    )

    with pytest.raises(ModelNotReadyError):
        asyncio.run(manager.infer_audio(Path("probe.wav")))

    assert manager.load_attempts == 0


def test_timed_out_thread_keeps_slot_and_queue_is_bounded() -> None:
    async def exercise() -> tuple[int, int]:
        runtime = BlockingRuntime()
        manager = _manager(runtime)
        await manager.start()
        first = manager.submit_inference(Path("first.wav"), deadline=time.monotonic() + 1)
        with pytest.raises(TimeoutError):
            await first.wait_until(time.monotonic() + 0.005)
        assert first.started is True

        second = manager.submit_inference(Path("second.wav"), deadline=time.monotonic() + 1)
        with pytest.raises(ModelRuntimeBusyError):
            manager.submit_inference(Path("third.wav"), deadline=time.monotonic() + 1)

        assert await second.wait_until(time.monotonic() + 1) == "second.wav"
        await manager.close()
        return runtime.maximum_active, runtime.calls

    maximum_active, calls = asyncio.run(exercise())

    assert maximum_active == 1
    assert calls == 2

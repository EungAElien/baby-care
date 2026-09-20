from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Protocol, TypeVar

from baby_care_api.core.config import Settings

logger = logging.getLogger(__name__)

_SAFE_START_FAILURE_CODES = frozenset(
    {
        "MODEL_CONFIGURATION_INVALID",
        "MODEL_INTEGRITY_FAILED",
        "MODEL_LOAD_FAILED",
        "MODEL_WARMUP_FAILED",
    }
)

InferenceResultT = TypeVar("InferenceResultT", covariant=True)


class SyncModelRuntime(Protocol[InferenceResultT]):
    """Synchronous CPU runtime isolated behind an async server boundary."""

    def infer_audio(self, path: Path) -> InferenceResultT: ...

    def close(self) -> None: ...


ModelRuntimeFactory = Callable[[Settings], SyncModelRuntime[object]]


class ModelNotReadyError(RuntimeError):
    """Raised before inference when the one startup load did not succeed."""


class ModelRuntimeBusyError(RuntimeError):
    """Raised when the single worker and its one bounded waiter are occupied."""


class InferenceDeadlineExceededError(TimeoutError):
    """Raised when an execution cannot start before its request deadline."""


class InferenceExecution:
    """A CPU execution whose task may outlive the requesting coroutine.

    Callers wait with ``wait_until``. A timeout never cancels the underlying
    thread, so the runtime slot remains held until CPU work really stops.
    """

    def __init__(self, task: asyncio.Task[object], started: asyncio.Event) -> None:
        self._task = task
        self._started = started

    @property
    def started(self) -> bool:
        return self._started.is_set()

    @property
    def done(self) -> bool:
        return self._task.done()

    async def wait_until(self, deadline: float) -> object:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("The analysis request deadline elapsed")
        return await asyncio.wait_for(asyncio.shield(self._task), timeout=remaining)

    def add_done_callback(self, callback: Callable[[], None]) -> None:
        def invoke(_: asyncio.Task[object]) -> None:
            callback()

        self._task.add_done_callback(invoke)


def m2d_configuration_complete(settings: Settings) -> bool:
    return all(
        value is not None
        for value in (
            settings.m2d_allowed_root,
            settings.m2d_bundle_path,
            settings.m2d_source_path,
        )
    )


def load_configured_m2d_runtime(settings: Settings) -> SyncModelRuntime[object]:
    """Import the heavy runtime only in the explicitly enabled model profile."""

    if not m2d_configuration_complete(settings):
        raise RuntimeError("M2D runtime paths are not configured")
    from baby_care_m2d.runtime import load_runtime

    assert settings.m2d_allowed_root is not None
    assert settings.m2d_bundle_path is not None
    assert settings.m2d_source_path is not None
    return load_runtime(
        allowed_root=settings.m2d_allowed_root,
        bundle_path=settings.m2d_bundle_path,
        source_path=settings.m2d_source_path,
        ffmpeg_path=settings.m2d_ffmpeg_path,
    )


class ModelRuntimeManager:
    """Own one process-local model load, one worker, and one bounded waiter."""

    def __init__(
        self,
        *,
        enabled: bool,
        configured: bool,
        settings: Settings,
        factory: ModelRuntimeFactory = load_configured_m2d_runtime,
        maximum_outstanding_inferences: int = 2,
    ) -> None:
        if maximum_outstanding_inferences < 1:
            raise ValueError("maximum_outstanding_inferences must be positive")
        self.enabled = enabled
        self.configured = configured
        self._settings = settings
        self._factory = factory
        self._runtime: SyncModelRuntime[object] | None = None
        self._attempted = False
        self._load_attempts = 0
        self._failure_code: str | None = None
        self._inference_gate = asyncio.Semaphore(1)
        self._maximum_outstanding_inferences = maximum_outstanding_inferences
        self._outstanding_inferences = 0
        self._execution_tasks: set[asyncio.Task[object]] = set()

    @property
    def load_attempts(self) -> int:
        return self._load_attempts

    @property
    def failure_code(self) -> str | None:
        return self._failure_code

    @property
    def ready(self) -> bool:
        return self._runtime is not None

    async def start(self) -> None:
        if not self.enabled or not self.configured or self._attempted:
            return
        self._attempted = True
        self._load_attempts += 1
        try:
            self._runtime = await asyncio.to_thread(self._factory, self._settings)
        except Exception as error:
            self._runtime = None
            candidate = getattr(error, "code", None)
            self._failure_code = (
                candidate if candidate in _SAFE_START_FAILURE_CODES else "MODEL_LOAD_FAILED"
            )
            logger.error(
                "model_runtime_start_failed",
                extra={
                    "result_code": self._failure_code,
                    "error_type": type(error).__name__,
                },
            )

    async def close(self) -> None:
        if self._execution_tasks:
            await asyncio.gather(*tuple(self._execution_tasks), return_exceptions=True)
        runtime, self._runtime = self._runtime, None
        if runtime is not None:
            await asyncio.to_thread(runtime.close)

    async def probe(self) -> bool:
        """Return cached startup state; never touch weights or run inference."""

        return self.ready

    async def _run_inference(
        self,
        runtime: SyncModelRuntime[object],
        path: Path,
        deadline: float,
        started: asyncio.Event,
    ) -> object:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise InferenceDeadlineExceededError("The inference deadline elapsed in the queue")
        try:
            await asyncio.wait_for(self._inference_gate.acquire(), timeout=remaining)
        except TimeoutError as exc:
            raise InferenceDeadlineExceededError(
                "The inference slot was not available before the deadline"
            ) from exc
        started.set()
        try:
            # The task itself is shielded by InferenceExecution.wait_until. Once
            # CPU work begins, its semaphore is released only after the thread
            # has really returned, even if the HTTP request has timed out.
            return await asyncio.to_thread(runtime.infer_audio, path)
        finally:
            self._inference_gate.release()

    def submit_inference(self, path: Path, *, deadline: float) -> InferenceExecution:
        runtime = self._runtime
        if runtime is None:
            raise ModelNotReadyError("The configured model is not ready")
        if self._outstanding_inferences >= self._maximum_outstanding_inferences:
            raise ModelRuntimeBusyError("The bounded inference queue is full")
        self._outstanding_inferences += 1
        started = asyncio.Event()
        task = asyncio.create_task(self._run_inference(runtime, path, deadline, started))
        self._execution_tasks.add(task)

        def release_tracking(completed: asyncio.Task[object]) -> None:
            self._execution_tasks.discard(completed)
            self._outstanding_inferences -= 1

        task.add_done_callback(release_tracking)
        return InferenceExecution(task, started)

    async def infer_audio(self, path: Path) -> object:
        """Compatibility helper for callers that do not impose a request deadline."""

        execution = self.submit_inference(path, deadline=float("inf"))
        return await execution.wait_until(float("inf"))

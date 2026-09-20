from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
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
    """Own exactly one process-local model load and one concurrent inference slot."""

    def __init__(
        self,
        *,
        enabled: bool,
        configured: bool,
        settings: Settings,
        factory: ModelRuntimeFactory = load_configured_m2d_runtime,
    ) -> None:
        self.enabled = enabled
        self.configured = configured
        self._settings = settings
        self._factory = factory
        self._runtime: SyncModelRuntime[object] | None = None
        self._attempted = False
        self._load_attempts = 0
        self._failure_code: str | None = None
        self._inference_gate = asyncio.Semaphore(1)

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
        runtime, self._runtime = self._runtime, None
        if runtime is not None:
            await asyncio.to_thread(runtime.close)

    async def probe(self) -> bool:
        """Return cached startup state; never touch weights or run inference."""

        return self.ready

    async def infer_audio(self, path: Path) -> object:
        runtime = self._runtime
        if runtime is None:
            raise ModelNotReadyError("The configured model is not ready")
        async with self._inference_gate:
            # CPU work must not block the FastAPI event loop.
            return await asyncio.to_thread(runtime.infer_audio, path)

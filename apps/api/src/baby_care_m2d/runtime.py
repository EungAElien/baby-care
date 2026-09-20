from __future__ import annotations

import gc
import importlib.metadata
import math
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from .audio import AudioPreprocessor, PreprocessedAudio, ffmpeg_identity
from .model import M2DClassifier, build_encoder, state_digest
from .registry import (
    ModelIntegrityError,
    ModelRuntimeError,
    ValidatedBundle,
    load_registry,
    validate_bundle,
    validate_runtime_paths,
    validate_source,
)


@dataclass(frozen=True)
class Score:
    label: str
    score: float


@dataclass(frozen=True)
class InferenceResult:
    scores: tuple[Score, ...]
    model_version: str
    preprocess_version: str
    label_mapping_version: str
    product_head: str
    calibration_status: str
    release_ready: bool


class M2DRuntime:
    def __init__(
        self,
        *,
        model: M2DClassifier,
        preprocessor: AudioPreprocessor,
        bundle: ValidatedBundle,
        ffmpeg_path: Path,
        ffmpeg: dict[str, str],
        packages: dict[str, str],
        load_seconds: float,
    ) -> None:
        self._model: M2DClassifier | None = model
        self._preprocessor: AudioPreprocessor | None = preprocessor
        self.bundle = bundle
        self.ffmpeg_path = ffmpeg_path
        self.ffmpeg = ffmpeg
        self.packages = packages
        self.load_seconds = load_seconds
        self.load_count = 1

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(self.bundle.registry["model"]["labels"])

    def infer_tensor(self, normalized: torch.Tensor) -> InferenceResult:
        model = self._model
        if model is None:
            raise RuntimeError("The model runtime is closed")
        if normalized.dtype != torch.float32 or normalized.device.type != "cpu":
            raise ValueError("Inference input must be CPU float32")
        if normalized.ndim != 3 or tuple(normalized.shape[:2]) != (1, 80):
            raise ValueError("Inference input must be [1,80,frames]")
        if normalized.shape[-1] < 32 or normalized.shape[-1] > 608:
            raise ValueError("Inference frame length is outside the fixed range")
        if normalized.shape[-1] % 16:
            raise ValueError("Inference frame length must be a multiple of 16")
        if not bool(torch.isfinite(normalized).all()):
            raise ValueError("Inference input contains a non-finite value")
        head = self.bundle.registry["model"]["product_head"]
        with torch.inference_mode():
            output = model(normalized.unsqueeze(0), head).softmax(dim=-1).cpu()
        if output.shape != (1, len(self.labels)) or output.dtype != torch.float32:
            raise RuntimeError("The model output shape or dtype changed")
        values = output[0].tolist()
        if any(not math.isfinite(value) for value in values):
            raise RuntimeError("The model returned a non-finite score")
        return InferenceResult(
            scores=tuple(
                Score(label=label, score=float(value))
                for label, value in zip(self.labels, values, strict=True)
            ),
            model_version=str(self.bundle.registry["model"]["model_version"]),
            preprocess_version=str(self.bundle.registry["preprocess_version"]),
            label_mapping_version=str(self.bundle.registry["label_mapping_version"]),
            product_head=head,
            calibration_status="NOT_VALIDATED",
            release_ready=False,
        )

    def preprocess(
        self, path: Path, *, allow_compressed_for_verification: bool = False
    ) -> PreprocessedAudio:
        preprocessor = self._preprocessor
        if preprocessor is None:
            raise RuntimeError("The model runtime is closed")
        return preprocessor.preprocess(
            path,
            allow_compressed_for_verification=allow_compressed_for_verification,
        )

    def infer_audio(self, path: Path) -> InferenceResult:
        return self.infer_tensor(self.preprocess(path).normalized)

    def close(self) -> None:
        self._model = None
        self._preprocessor = None
        gc.collect()


def _verify_runtime_versions(registry: dict[str, Any]) -> dict[str, str]:
    if platform.python_version() != registry["runtime"]["python"]:
        raise ModelIntegrityError("The Python runtime version differs from the registry")
    observed_packages: dict[str, str] = {}
    for distribution, expected in registry["runtime"]["packages"].items():
        try:
            observed = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as error:
            raise ModelIntegrityError("A pinned runtime dependency is unavailable") from error
        if observed != expected:
            raise ModelIntegrityError("A pinned runtime dependency version differs")
        observed_packages[distribution] = observed
    return observed_packages


def _load_model(bundle: ValidatedBundle, source_path: Path) -> M2DClassifier:
    spec = bundle.registry["model"]
    preprocessing = bundle.registry["preprocessing"]
    encoder = build_encoder(
        source_path,
        (
            float(preprocessing["normalization_mean"]),
            float(preprocessing["normalization_std"]),
        ),
        spec["architecture"],
    )
    labels = bundle.metadata["labels"]
    model = M2DClassifier(
        encoder,
        labels,
        bundle.metadata["trained_heads"],
        head_seed=int(spec["architecture"]["head_seed"]),
    )
    if model.feature_dim != int(spec["architecture"]["classifier_feature_dimension"]):
        raise ModelIntegrityError("The classifier feature dimension changed")
    try:
        payload = torch.load(
            bundle.path / "model.pt",
            map_location="cpu",
            weights_only=True,
        )
    except Exception as error:
        raise ModelRuntimeError(
            "MODEL_LOAD_FAILED", "The fixed model could not be loaded"
        ) from error
    if not isinstance(payload, dict) or set(payload) != {"full_state_dict"}:
        raise ModelIntegrityError("The model payload structure changed")
    state = payload["full_state_dict"]
    if not isinstance(state, dict) or any(
        not isinstance(value, torch.Tensor) for value in state.values()
    ):
        raise ModelIntegrityError("The model state contains a non-tensor value")
    try:
        model.load_state_dict(state, strict=True)
    except (RuntimeError, TypeError, ValueError) as error:
        raise ModelIntegrityError(
            "The model state does not match the fixed architecture"
        ) from error
    model = model.to(device="cpu", dtype=torch.float32).eval()
    if any(not bool(tensor.isfinite().all()) for tensor in model.state_dict().values()):
        raise ModelIntegrityError("The model contains a non-finite weight")
    if state_digest(model.state_dict()) != spec["full_state_sha256"]:
        raise ModelIntegrityError("The loaded model state digest changed")
    return model


def load_runtime(
    *,
    allowed_root: Path,
    bundle_path: Path,
    source_path: Path,
    ffmpeg_path: Path,
) -> M2DRuntime:
    started = time.perf_counter()
    registry = load_registry()
    paths = validate_runtime_paths(
        allowed_root=allowed_root,
        bundle_path=bundle_path,
        source_path=source_path,
    )
    bundle = validate_bundle(paths.bundle, registry)
    validate_source(paths.source, registry)
    packages = _verify_runtime_versions(registry)
    resolved_ffmpeg = ffmpeg_path.resolve(strict=True)
    ffmpeg = ffmpeg_identity(resolved_ffmpeg, registry["runtime"]["ffmpeg"])
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        # PyTorch only permits setting this before inter-op work starts. A fresh
        # service process takes the success path; test harnesses may already have
        # initialized it, while the fixed value remains one.
        if torch.get_num_interop_threads() != 1:
            raise ModelIntegrityError("PyTorch inter-op concurrency is not fixed to one") from None
    model = _load_model(bundle, paths.source)
    preprocessor = AudioPreprocessor(
        preprocessing=registry["preprocessing"],
        ffmpeg_path=resolved_ffmpeg,
    )
    runtime = M2DRuntime(
        model=model,
        preprocessor=preprocessor,
        bundle=bundle,
        ffmpeg_path=resolved_ffmpeg,
        ffmpeg=ffmpeg,
        packages=packages,
        load_seconds=0.0,
    )
    try:
        warmup = torch.zeros((1, 80, 32), dtype=torch.float32, device="cpu")
        result = runtime.infer_tensor(warmup)
        if len(result.scores) != int(registry["model"]["output_dimension"]):
            raise RuntimeError("The warmup output dimension changed")
    except Exception as error:
        runtime.close()
        raise ModelRuntimeError("MODEL_WARMUP_FAILED", "The fixed model warmup failed") from error
    runtime.load_seconds = time.perf_counter() - started
    return runtime

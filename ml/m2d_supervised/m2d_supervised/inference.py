"""Local research inference. Scores are NOT calibrated cause probabilities."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import torch

from .model import M2DClassifier, build_encoder, verify_m2d_source
from .util import read_json, sha256_file


def center_input(logmel: np.ndarray, config) -> torch.Tensor:
    prep = config["preprocessing"]
    if (
        logmel.dtype != np.float32
        or logmel.ndim != 3
        or logmel.shape[:2] != (1, 80)
        or not np.isfinite(logmel).all()
    ):
        raise ValueError("Expected finite float32 [1,80,time] unnormalized log-mel.")
    frames = logmel.shape[-1]
    used = min(prep["maximum_frames"], frames // prep["frame_multiple"] * prep["frame_multiple"])
    if used < prep["minimum_frames"]:
        raise ValueError("Insufficient audio frames; never pad or repeat.")
    start = (frames - used) // 2
    cropped = torch.from_numpy(np.ascontiguousarray(logmel[..., start : start + used]))
    return (cropped - prep["normalization_mean"]) / prep["normalization_std"]


def audio_logmel(path: Path, config) -> np.ndarray:
    """Same soundfile/afconvert, channel mean, soxr HQ and nnAudio path as preparation."""
    import soundfile as sf
    import soxr
    from nnAudio.features import MelSpectrogram

    try:
        audio, sr = sf.read(path, dtype="float32", always_2d=True)
    except (RuntimeError, sf.LibsndfileError):
        decoder = shutil.which("afconvert")
        if decoder is None:
            raise ValueError("Decoder unavailable for this file format.") from None
        with tempfile.TemporaryDirectory(prefix="m2d-inference-decode-") as directory:
            decoded = Path(directory) / "decoded.wav"
            subprocess.run(
                [decoder, "-f", "WAVE", "-d", "LEF32", str(path), str(decoded)],
                check=True,
                capture_output=True,
                timeout=30,
            )
            audio, sr = sf.read(decoded, dtype="float32", always_2d=True)
    if not len(audio) or sr <= 0 or not np.isfinite(audio).all():
        raise ValueError("Empty or non-finite audio.")
    mono = audio.mean(axis=1, dtype=np.float32)
    if np.max(np.abs(mono)) == 0:
        raise ValueError("Silent audio.")
    prep = config["preprocessing"]
    target = prep["sample_rate"]
    if sr != target:
        length = math.ceil(len(mono) * target / sr)
        converted = soxr.resample(mono, sr, target, quality="HQ")
        mono = np.pad(converted[:length], (0, max(0, length - len(converted))))
    mono = np.ascontiguousarray(mono, dtype=np.float32)
    if len(mono) <= prep["n_fft"] // 2:
        raise ValueError("Insufficient audio for reflect STFT.")
    extractor = MelSpectrogram(
        sr=target,
        n_fft=prep["n_fft"],
        win_length=prep["win_length"],
        hop_length=prep["hop_length"],
        n_mels=prep["n_mels"],
        fmin=prep["f_min"],
        fmax=prep["f_max"],
        center=prep["center"],
        power=prep["power"],
        window=prep["window"],
        pad_mode=prep["pad_mode"],
        htk=prep["htk"],
        norm=prep["mel_norm"],
        verbose=False,
    ).eval()
    with torch.inference_mode():
        result = (extractor(torch.from_numpy(mono)) + torch.finfo(torch.float32).eps).log()
    return result.numpy().astype(np.float32, copy=False)


def load_bundle(bundle: Path, source_root: Path, device="cpu"):
    """No initial/partial checkpoint, training dataset or research-root dependency."""
    metadata = read_json(bundle / "metadata.json")
    for name, digest in metadata["artifact_sha256"].items():
        if (bundle / name).resolve().parent != bundle.resolve():
            raise RuntimeError("Bundle artifact path escapes the bundle.")
        if sha256_file(bundle / name) != digest:
            raise RuntimeError(f"Bundle artifact changed: {name}")
    config = read_json(bundle / "config.json")
    verify_m2d_source(source_root, config["model"]["source_commit"])
    encoder = build_encoder(
        source_root,
        (
            config["preprocessing"]["normalization_mean"],
            config["preprocessing"]["normalization_std"],
        ),
    )
    model = M2DClassifier(encoder, metadata["labels"], metadata["trained_heads"], head_seed=42)
    payload = torch.load(bundle / "model.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(payload["full_state_dict"], strict=True)
    if any(not bool(torch.isfinite(t).all()) for t in model.state_dict().values()):
        raise RuntimeError("Non-finite exported weights.")
    return model.to(device).eval(), config, metadata

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import torch

MAX_INPUT_BYTES = 25_000_000
MAX_AUDIO_SECONDS = 60.0
MAX_CHANNELS = 8
MAX_DECODED_BYTES = 64 * 1024 * 1024
SUPPORTED_FORMATS = frozenset({"wav"})
SUPPORTED_CODECS = frozenset(
    {
        "pcm_f32le",
        "pcm_f64le",
        "pcm_s8",
        "pcm_s16le",
        "pcm_s24le",
        "pcm_s32le",
        "pcm_u8",
    }
)


class AudioInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DecodeIdentity:
    format_name: str
    codec_name: str
    sample_rate: int
    channels: int
    samples: int


@dataclass(frozen=True)
class PreprocessedAudio:
    normalized: torch.Tensor
    logmel: np.ndarray
    decode: DecodeIdentity


class MelExtractor(Protocol):
    def __call__(self, value: torch.Tensor) -> torch.Tensor: ...


class AudioPreprocessor:
    """Process-local, fixed audio frontend reused for every serialized request."""

    def __init__(self, *, preprocessing: dict[str, Any], ffmpeg_path: Path) -> None:
        from nnAudio.features import MelSpectrogram

        self.preprocessing = preprocessing
        self.ffmpeg_path = ffmpeg_path
        self.extractor: MelExtractor = MelSpectrogram(
            sr=int(preprocessing["sample_rate"]),
            n_fft=int(preprocessing["n_fft"]),
            win_length=int(preprocessing["win_length"]),
            hop_length=int(preprocessing["hop_length"]),
            n_mels=int(preprocessing["n_mels"]),
            fmin=float(preprocessing["f_min"]),
            fmax=float(preprocessing["f_max"]),
            center=bool(preprocessing["center"]),
            power=float(preprocessing["power"]),
            window=str(preprocessing["window"]),
            pad_mode=str(preprocessing["pad_mode"]),
            htk=bool(preprocessing["htk"]),
            norm=preprocessing["mel_norm"],
            verbose=False,
        ).eval()

    def preprocess(
        self, path: Path, *, allow_compressed_for_verification: bool = False
    ) -> PreprocessedAudio:
        audio, sample_rate, identity = decode_audio(
            path,
            ffmpeg_path=self.ffmpeg_path,
            allow_compressed_for_verification=allow_compressed_for_verification,
        )
        logmel = waveform_logmel(
            audio,
            sample_rate,
            self.preprocessing,
            extractor=self.extractor,
        )
        return PreprocessedAudio(
            normalized=center_input(logmel, self.preprocessing),
            logmel=logmel,
            decode=identity,
        )


def _run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AudioInputError("DECODE_ERROR", "Audio decoding did not complete") from error


def ffmpeg_identity(ffmpeg_path: Path, expected: dict[str, Any]) -> dict[str, str]:
    completed = _run([str(ffmpeg_path), "-version"], timeout=5)
    if completed.returncode != 0:
        raise RuntimeError("The pinned FFmpeg executable is unavailable")
    lines = completed.stdout.decode("utf-8", errors="replace").splitlines()
    if len(lines) < 2 or not lines[0].startswith(f"ffmpeg version {expected['version']}"):
        raise RuntimeError("The FFmpeg version differs from the runtime registry")
    if str(expected["build"]) not in lines[1]:
        raise RuntimeError("The FFmpeg build differs from the runtime registry")
    return {"version": lines[0], "build": lines[1]}


def _probe(path: Path, ffprobe_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    completed = _run(
        [
            str(ffprobe_path),
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels:format=format_name,duration",
            "-of",
            "json",
            str(path),
        ],
        timeout=10,
    )
    if completed.returncode != 0:
        raise AudioInputError("DECODE_ERROR", "The audio container could not be inspected")
    try:
        payload = json.loads(completed.stdout)
        streams = payload["streams"]
        audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
        if len(audio_streams) != 1 or len(streams) != 1:
            raise AudioInputError("UNSUPPORTED_CODEC", "Exactly one audio stream is required")
        return payload["format"], audio_streams[0]
    except AudioInputError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise AudioInputError("DECODE_ERROR", "The decoder metadata was invalid") from error


def decode_audio(
    path: Path,
    *,
    ffmpeg_path: Path,
    allow_compressed_for_verification: bool = False,
) -> tuple[np.ndarray, int, DecodeIdentity]:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise AudioInputError("DECODE_ERROR", "The audio file is unavailable") from error
    if not resolved.is_file():
        raise AudioInputError("DECODE_ERROR", "The audio input is not a regular file")
    if resolved.stat().st_size > MAX_INPUT_BYTES:
        raise AudioInputError("TOO_LARGE", "The audio file exceeds 25,000,000 bytes")

    ffmpeg = ffmpeg_path.resolve(strict=True)
    ffprobe = ffmpeg.with_name("ffprobe")
    if not ffprobe.is_file() or not os.access(ffmpeg, os.X_OK) or not os.access(ffprobe, os.X_OK):
        raise RuntimeError("The pinned FFmpeg tools are unavailable")
    container, stream = _probe(resolved, ffprobe)
    format_names = set(str(container.get("format_name", "")).split(","))
    codec = str(stream.get("codec_name", ""))
    if not allow_compressed_for_verification and (
        not format_names.intersection(SUPPORTED_FORMATS) or codec not in SUPPORTED_CODECS
    ):
        raise AudioInputError("UNSUPPORTED_CODEC", "Only verified PCM WAV input is supported")
    try:
        sample_rate = int(stream["sample_rate"])
        channels = int(stream["channels"])
    except (KeyError, TypeError, ValueError) as error:
        raise AudioInputError(
            "DECODE_ERROR", "Audio rate or channel metadata is invalid"
        ) from error
    if sample_rate <= 0 or not 1 <= channels <= MAX_CHANNELS:
        raise AudioInputError("UNSUPPORTED_CODEC", "Audio rate or channel count is unsupported")
    duration = container.get("duration")
    if duration not in (None, "N/A"):
        try:
            duration_seconds = float(duration)
        except (TypeError, ValueError) as error:
            raise AudioInputError("DECODE_ERROR", "Audio duration metadata is invalid") from error
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise AudioInputError("DECODE_ERROR", "Audio duration metadata is invalid")
        if duration_seconds > MAX_AUDIO_SECONDS + 0.05:
            raise AudioInputError("TOO_LONG", "The audio exceeds 60 seconds")

    import soundfile as sf

    with tempfile.TemporaryDirectory(prefix="baby-care-m2d-decode-") as directory:
        decoded = Path(directory) / "decoded.wav"
        completed = _run(
            [
                str(ffmpeg),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-threads",
                "1",
                "-i",
                str(resolved),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-t",
                "60.05",
                "-c:a",
                "pcm_f32le",
                "-fs",
                str(MAX_DECODED_BYTES),
                "-f",
                "wav",
                str(decoded),
            ],
            timeout=30,
        )
        if completed.returncode != 0:
            raise AudioInputError("DECODE_ERROR", "The audio stream could not be decoded")
        try:
            audio, decoded_rate = sf.read(decoded, dtype="float32", always_2d=True)
        except (RuntimeError, sf.LibsndfileError) as error:
            raise AudioInputError("DECODE_ERROR", "Decoded PCM could not be read") from error
    if decoded_rate != sample_rate or audio.shape[1] != channels:
        raise AudioInputError("DECODE_ERROR", "The decoder changed rate or channel count")
    if len(audio) > math.ceil(MAX_AUDIO_SECONDS * sample_rate):
        raise AudioInputError("TOO_LONG", "The decoded audio exceeds 60 seconds")
    if not len(audio) or not np.isfinite(audio).all():
        raise AudioInputError("DECODE_ERROR", "Decoded audio is empty or non-finite")
    return (
        audio,
        sample_rate,
        DecodeIdentity(
            format_name=str(container.get("format_name", "")),
            codec_name=codec,
            sample_rate=sample_rate,
            channels=channels,
            samples=len(audio),
        ),
    )


def waveform_logmel(
    audio: np.ndarray,
    sample_rate: int,
    config: dict[str, Any],
    *,
    extractor: MelExtractor,
) -> np.ndarray:
    import soxr

    if audio.dtype != np.float32 or audio.ndim != 2:
        raise AudioInputError("DECODE_ERROR", "Expected float32 channel-separated PCM")
    mono = audio.mean(axis=1, dtype=np.float32)
    if not np.isfinite(mono).all() or float(np.max(np.abs(mono))) == 0.0:
        raise AudioInputError("SILENCE", "Audio is silent or non-finite")
    target = int(config["sample_rate"])
    if sample_rate != target:
        length = math.ceil(len(mono) * target / sample_rate)
        converted = soxr.resample(mono, sample_rate, target, quality="HQ")
        mono = np.pad(converted[:length], (0, max(0, length - len(converted))))
    mono = np.ascontiguousarray(mono, dtype=np.float32)
    if len(mono) <= int(config["n_fft"]) // 2:
        raise AudioInputError("TOO_SHORT", "Audio is too short for reflect-padding STFT")
    with torch.inference_mode():
        result = (extractor(torch.from_numpy(mono)) + torch.finfo(torch.float32).eps).log()
    return result.numpy().astype(np.float32, copy=False)


def center_input(logmel: np.ndarray, config: dict[str, Any]) -> torch.Tensor:
    if (
        logmel.dtype != np.float32
        or logmel.ndim != 3
        or logmel.shape[:2] != (1, 80)
        or not np.isfinite(logmel).all()
    ):
        raise AudioInputError("DECODE_ERROR", "Expected finite float32 [1,80,time] log-mel")
    frames = logmel.shape[-1]
    frame_multiple = int(config["frame_multiple"])
    used = min(int(config["maximum_frames"]), frames // frame_multiple * frame_multiple)
    if used < int(config["minimum_frames"]):
        raise AudioInputError("TOO_SHORT", "Audio has too few frames; padding is not allowed")
    start = (frames - used) // 2
    cropped = torch.from_numpy(np.ascontiguousarray(logmel[..., start : start + used]))
    return (cropped - float(config["normalization_mean"])) / float(config["normalization_std"])

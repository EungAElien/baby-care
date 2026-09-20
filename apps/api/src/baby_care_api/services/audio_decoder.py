from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import resource
import subprocess
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

MAX_INPUT_BYTES = 25_000_000
MAX_DECODED_BYTES = 25_000_000
MAX_SAMPLE_RATE_HZ = 96_000
MAX_CHANNELS = 2
MIN_USABLE_SECONDS = 1.0
SILENCE_DBFS = -50.0
CLIPPING_AMPLITUDE = 32_734
CLIPPING_RATIO = 0.01
DECODER_VERSION = "ffmpeg-7.1.1-static"
PREPROCESSING_BOUNDARY_VERSION = "source-rate-pcm-s16le-v1"

SUPPORTED_MIME_TYPES = (
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/mp4",
    "audio/aac",
)

type CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]


class AudioDecodeError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class DecodedAudio:
    source_mime_type: str
    container: str
    codec: str
    sample_rate_hz: int
    channels: int
    samples_per_channel: int
    duration_seconds: float
    pcm_path: Path
    pcm_bytes: int
    pcm_checksum_sha256: str
    decoder_version: str
    preprocessing_boundary_version: str
    quality_reasons: tuple[str, ...]
    rejection_code: str | None


class AudioDecoder(Protocol):
    @property
    def configured(self) -> bool: ...

    async def decode(
        self,
        source: Path,
        destination: Path,
        *,
        declared_mime_type: str,
        max_seconds: int,
    ) -> DecodedAudio: ...


def _limit_child_resources() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (MAX_DECODED_BYTES + 1024 * 1024, MAX_DECODED_BYTES + 1024 * 1024),
    )
    address_space = 512 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _measure_pcm(path: Path) -> tuple[int, int, int, float, tuple[str, ...], str | None]:
    try:
        with wave.open(str(path), "rb") as decoded:
            channels = decoded.getnchannels()
            sample_rate = decoded.getframerate()
            sample_width = decoded.getsampwidth()
            frames = decoded.getnframes()
            raw = decoded.readframes(frames)
    except (OSError, EOFError, wave.Error) as exc:
        raise AudioDecodeError("DECODE_ERROR", "Decoded PCM could not be inspected.") from exc

    if sample_width != 2 or channels not in range(1, MAX_CHANNELS + 1):
        raise AudioDecodeError("DECODE_ERROR", "Decoded PCM shape was not canonical.")
    if not 1 <= sample_rate <= MAX_SAMPLE_RATE_HZ or frames <= 0:
        raise AudioDecodeError("DECODE_ERROR", "Decoded PCM timing was invalid.")
    if len(raw) != frames * channels * sample_width:
        raise AudioDecodeError("DECODE_ERROR", "Decoded PCM was truncated.")

    samples = memoryview(raw).cast("h")
    sample_count = len(samples)
    square_sum = 0
    clipped = 0
    for sample in samples:
        square_sum += sample * sample
        if abs(sample) >= CLIPPING_AMPLITUDE:
            clipped += 1
    rms = math.sqrt(square_sum / sample_count)
    dbfs = -math.inf if rms == 0 else 20.0 * math.log10(rms / 32768.0)
    duration = frames / sample_rate

    reasons: list[str] = []
    rejection: str | None = None
    if duration < MIN_USABLE_SECONDS:
        reasons.append("TOO_SHORT")
        rejection = "TOO_SHORT"
    if dbfs < SILENCE_DBFS:
        reasons.append("SILENCE")
        rejection = rejection or "SILENCE"
    if clipped / sample_count >= CLIPPING_RATIO:
        reasons.append("CLIPPING")
    return sample_rate, channels, frames, duration, tuple(reasons), rejection


class FfmpegAudioDecoder:
    """Decode a local, randomly named file with fixed tools and fixed arguments.

    The PCM derivative deliberately preserves source sample rate and channel count.
    B-06 remains the sole owner of model-specific downmix, resampling, and normalization.
    """

    def __init__(
        self,
        *,
        ffmpeg_path: Path,
        ffprobe_path: Path,
        expected_version_prefix: str = "ffmpeg version 7.1.1",
        max_concurrency: int = 2,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._expected_version_prefix = expected_version_prefix
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._runner = runner

    @property
    def configured(self) -> bool:
        return (
            self._ffmpeg_path.is_file()
            and os.access(self._ffmpeg_path, os.X_OK)
            and self._ffprobe_path.is_file()
            and os.access(self._ffprobe_path, os.X_OK)
        )

    def _run(self, command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
        try:
            return self._runner(
                list(command),
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                shell=False,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C"},
                preexec_fn=_limit_child_resources,
            )
        except subprocess.TimeoutExpired as exc:
            raise AudioDecodeError(
                "DECODE_TIMEOUT", "Audio decoding timed out.", retryable=True
            ) from exc
        except OSError as exc:
            raise AudioDecodeError(
                "DECODER_UNAVAILABLE", "The pinned decoder is unavailable.", retryable=True
            ) from exc

    def _verify_identity(self) -> None:
        completed = self._run([str(self._ffmpeg_path), "-version"], timeout=5)
        first_line = completed.stdout.decode("utf-8", errors="replace").splitlines()[:1]
        if completed.returncode != 0 or not first_line:
            raise AudioDecodeError(
                "DECODER_UNAVAILABLE", "The pinned decoder is unavailable.", retryable=True
            )
        if not first_line[0].startswith(self._expected_version_prefix):
            raise AudioDecodeError(
                "DECODER_VERSION_MISMATCH",
                "The decoder identity did not match the pinned runtime.",
                retryable=True,
            )

    @staticmethod
    def _format_identity(payload: dict[str, Any]) -> tuple[str, str, int, int, str]:
        try:
            streams = payload["streams"]
            container = str(payload["format"]["format_name"])
            audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
            if len(streams) != 1 or len(audio_streams) != 1:
                raise AudioDecodeError(
                    "UNSUPPORTED_CODEC", "Exactly one audio stream and no video are required."
                )
            stream = audio_streams[0]
            codec = str(stream["codec_name"])
            sample_rate = int(stream["sample_rate"])
            channels = int(stream["channels"])
        except AudioDecodeError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise AudioDecodeError("DECODE_ERROR", "Decoder metadata was invalid.") from exc

        formats = set(container.split(","))
        if "wav" in formats and codec.startswith("pcm_"):
            actual_mime = "audio/wav"
        elif formats.intersection({"matroska", "webm"}) and codec == "opus":
            actual_mime = "audio/webm"
        elif formats.intersection({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}) and codec == "aac":
            actual_mime = "audio/mp4"
        elif "aac" in formats and codec == "aac":
            actual_mime = "audio/aac"
        else:
            raise AudioDecodeError("UNSUPPORTED_CODEC", "The audio codec is not supported.")
        if not 1 <= sample_rate <= MAX_SAMPLE_RATE_HZ or not 1 <= channels <= MAX_CHANNELS:
            raise AudioDecodeError(
                "UNSUPPORTED_CODEC", "The audio rate or channels are unsupported."
            )
        return container, codec, sample_rate, channels, actual_mime

    def _decode_sync(
        self,
        source: Path,
        destination: Path,
        *,
        declared_mime_type: str,
        max_seconds: int,
    ) -> DecodedAudio:
        if not self.configured:
            raise AudioDecodeError(
                "DECODER_UNAVAILABLE", "The pinned decoder is unavailable.", retryable=True
            )
        try:
            source = source.resolve(strict=True)
            destination = destination.resolve(strict=False)
        except OSError as exc:
            raise AudioDecodeError(
                "DECODE_ERROR", "The local audio input was unavailable."
            ) from exc
        if not source.is_file():
            raise AudioDecodeError("DECODE_ERROR", "The local audio input was not a file.")
        if source.stat().st_size > MAX_INPUT_BYTES:
            raise AudioDecodeError("TOO_LARGE", "The audio exceeded 25,000,000 bytes.")

        self._verify_identity()
        probe = self._run(
            [
                str(self._ffprobe_path),
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_entries",
                "stream=codec_type,codec_name,sample_rate,channels:format=format_name,duration",
                "-of",
                "json",
                str(source),
            ],
            timeout=10,
        )
        if probe.returncode != 0 or len(probe.stdout) > 64 * 1024:
            raise AudioDecodeError("DECODE_ERROR", "The audio container could not be inspected.")
        try:
            payload = json.loads(probe.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AudioDecodeError("DECODE_ERROR", "Decoder metadata was invalid.") from exc
        container, codec, probed_rate, probed_channels, actual_mime = self._format_identity(payload)

        declared = declared_mime_type.lower().split(";", 1)[0].strip()
        equivalent = {"audio/wav", "audio/x-wav"} if actual_mime == "audio/wav" else {actual_mime}
        if declared not in equivalent:
            raise AudioDecodeError("UNSUPPORTED_CODEC", "Declared MIME did not match the bytes.")
        duration_value = payload.get("format", {}).get("duration")
        if duration_value not in (None, "N/A"):
            try:
                probed_duration = float(duration_value)
            except (TypeError, ValueError) as exc:
                raise AudioDecodeError("DECODE_ERROR", "Audio duration was invalid.") from exc
            if not math.isfinite(probed_duration) or probed_duration < 0:
                raise AudioDecodeError("DECODE_ERROR", "Audio duration was invalid.")
            if probed_duration > max_seconds + 0.05:
                raise AudioDecodeError("TOO_LONG", "The audio exceeded the episode limit.")

        completed = self._run(
            [
                str(self._ffmpeg_path),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-xerror",
                "-protocol_whitelist",
                "file,pipe",
                "-threads",
                "1",
                "-max_alloc",
                str(MAX_DECODED_BYTES),
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-t",
                f"{max_seconds + 0.05:.2f}",
                "-c:a",
                "pcm_s16le",
                "-fs",
                str(MAX_DECODED_BYTES),
                "-f",
                "wav",
                "-y",
                str(destination),
            ],
            timeout=30,
        )
        if completed.returncode != 0 or not destination.is_file():
            raise AudioDecodeError("DECODE_ERROR", "The audio stream could not be decoded.")
        pcm_bytes = destination.stat().st_size
        if not 1 <= pcm_bytes <= MAX_DECODED_BYTES:
            raise AudioDecodeError("DECODE_ERROR", "Decoded PCM size was invalid.")

        rate, channels, frames, duration, quality_reasons, rejection = _measure_pcm(destination)
        if rate != probed_rate or channels != probed_channels:
            raise AudioDecodeError("DECODE_ERROR", "The decoder changed rate or channel count.")
        if duration > max_seconds + (1 / rate):
            raise AudioDecodeError("TOO_LONG", "The decoded audio exceeded the episode limit.")
        return DecodedAudio(
            source_mime_type=actual_mime,
            container=container,
            codec=codec,
            sample_rate_hz=rate,
            channels=channels,
            samples_per_channel=frames,
            duration_seconds=duration,
            pcm_path=destination,
            pcm_bytes=pcm_bytes,
            pcm_checksum_sha256=_sha256(destination),
            decoder_version=DECODER_VERSION,
            preprocessing_boundary_version=PREPROCESSING_BOUNDARY_VERSION,
            quality_reasons=quality_reasons,
            rejection_code=rejection,
        )

    async def decode(
        self,
        source: Path,
        destination: Path,
        *,
        declared_mime_type: str,
        max_seconds: int,
    ) -> DecodedAudio:
        async with self._semaphore:
            return await asyncio.to_thread(
                self._decode_sync,
                source,
                destination,
                declared_mime_type=declared_mime_type,
                max_seconds=max_seconds,
            )

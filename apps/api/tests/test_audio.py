from __future__ import annotations

import asyncio
import math
import struct
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError

from baby_care_api.models.audio import CreateEpisode, CreateUpload
from baby_care_api.services.audio_decoder import (
    AudioDecodeError,
    FfmpegAudioDecoder,
    _measure_pcm,
)
from baby_care_api.services.storage import (
    StorageError,
    SupabaseAudioStorage,
    UnconfiguredAudioStorage,
)


def _write_pcm(path: Path, samples: list[int], *, rate: int = 8_000) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def test_pcm_quality_thresholds_are_measured_not_inferred(tmp_path: Path) -> None:
    silence = tmp_path / "silence.wav"
    _write_pcm(silence, [0] * 4_000)
    _, _, _, duration, reasons, rejection = _measure_pcm(silence)
    assert duration == 0.5
    assert reasons == ("TOO_SHORT", "SILENCE")
    assert rejection == "TOO_SHORT"

    clipped = tmp_path / "clipped.wav"
    _write_pcm(clipped, [32_767] * 8_800)
    _, _, _, _, reasons, rejection = _measure_pcm(clipped)
    assert reasons == ("CLIPPING",)
    assert rejection is None

    sine = tmp_path / "sine.wav"
    _write_pcm(
        sine,
        [int(8_000 * math.sin(2 * math.pi * 440 * index / 8_000)) for index in range(8_800)],
    )
    _, _, _, _, reasons, rejection = _measure_pcm(sine)
    assert reasons == ()
    assert rejection is None


@pytest.mark.parametrize(
    ("format_name", "codec", "expected_mime"),
    [
        ("wav", "pcm_s16le", "audio/wav"),
        ("matroska,webm", "opus", "audio/webm"),
        ("mov,mp4,m4a,3gp,3g2,mj2", "aac", "audio/mp4"),
        ("aac", "aac", "audio/aac"),
    ],
)
def test_decoder_accepts_only_explicit_container_codec_pairs(
    format_name: str, codec: str, expected_mime: str
) -> None:
    identity = FfmpegAudioDecoder._format_identity(
        {
            "format": {"format_name": format_name},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": codec,
                    "sample_rate": "48000",
                    "channels": 1,
                }
            ],
        }
    )
    assert identity[-1] == expected_mime


def test_decoder_rejects_video_multitrack_and_unsupported_rate() -> None:
    with pytest.raises(AudioDecodeError, match="Exactly one audio stream"):
        FfmpegAudioDecoder._format_identity(
            {
                "format": {"format_name": "mov,mp4"},
                "streams": [
                    {
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "sample_rate": "48000",
                        "channels": 1,
                    },
                    {"codec_type": "video", "codec_name": "h264"},
                ],
            }
        )
    with pytest.raises(AudioDecodeError, match="rate or channels"):
        FfmpegAudioDecoder._format_identity(
            {
                "format": {"format_name": "wav"},
                "streams": [
                    {
                        "codec_type": "audio",
                        "codec_name": "pcm_s16le",
                        "sample_rate": "192000",
                        "channels": 1,
                    }
                ],
            }
        )


def test_upload_models_keep_contract_limits_and_server_owned_fields_out() -> None:
    accepted = CreateUpload.model_validate(
        {
            "client_request_id": "10000000-0000-4000-8000-000000000001",
            "mime_type": "audio/webm",
            "bytes": 25_000_000,
            "duration_seconds": 60,
            "checksum_sha256": None,
            "prefer_resumable": True,
        }
    )
    assert accepted.bytes == 25_000_000
    with pytest.raises(ValidationError):
        CreateUpload.model_validate(
            {
                **accepted.model_dump(mode="json"),
                "bytes": 25_000_001,
            }
        )
    with pytest.raises(ValidationError):
        CreateEpisode.model_validate(
            {
                "client_request_id": "10000000-0000-4000-8000-000000000001",
                "baby_id": "10000000-0000-4000-8000-000000000101",
                "source": "MANUAL",
                "timing_status": "KNOWN",
                "started_at": "2026-09-20T00:00:00Z",
                "observation_session_id": None,
                "data_origin": "DEMO",
                "created_by_user_id": "10000000-0000-4000-8000-000000000001",
            }
        )


def test_storage_upload_endpoints_are_exact_and_never_signed() -> None:
    storage = SupabaseAudioStorage(
        supabase_url="https://project.example",
        storage_url="https://storage.example",
        service_key="synthetic-server-only",
    )
    key = "baby/audio/random"
    assert storage.upload_endpoint(method="STANDARD", bucket="baby-audio", object_key=key) == (
        "https://project.example/storage/v1/object/baby-audio/baby/audio/random"
    )
    assert storage.upload_endpoint(method="TUS", bucket="baby-audio", object_key=key) == (
        "https://storage.example/storage/v1/upload/resumable"
    )
    with pytest.raises(ValueError, match="Unknown upload method"):
        storage.upload_endpoint(method="SIGNED", bucket="baby-audio", object_key=key)


def test_unconfigured_storage_fails_closed(tmp_path: Path) -> None:
    storage = UnconfiguredAudioStorage()
    assert storage.configured is False

    async def exercise() -> None:
        await storage.download(
            bucket="baby-audio",
            object_key="baby/audio/random",
            destination=tmp_path / "source",
        )

    with pytest.raises(StorageError) as caught:
        asyncio.run(exercise())
    assert caught.value.retryable is True

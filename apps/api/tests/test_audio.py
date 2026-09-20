from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import struct
import subprocess
import wave
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from uuid import UUID

import pytest
from pydantic import ValidationError

from baby_care_api.core.errors import ApiException
from baby_care_api.models.audio import CreateEpisode, CreateUpload
from baby_care_api.models.errors import ErrorCode
from baby_care_api.services import storage as storage_module
from baby_care_api.services.audio import PostgresAudioService
from baby_care_api.services.audio_decoder import (
    MAX_INPUT_BYTES,
    AudioDecodeError,
    FfmpegAudioDecoder,
    _measure_pcm,
)
from baby_care_api.services.storage import (
    DownloadedObject,
    StorageError,
    SupabaseAudioStorage,
    UnconfiguredAudioStorage,
    _retryable_status,
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


def test_storage_playback_normalizes_only_same_origin_provider_paths() -> None:
    class ResponseStorage(SupabaseAudioStorage):
        response: str

        def _json_request(
            self, url: str, body: dict[str, object], *, method: str = "POST"
        ) -> dict[str, object]:
            assert url.endswith("/storage/v1/object/sign/baby-audio/baby/audio/random")
            assert body == {"expiresIn": 60}
            assert method == "POST"
            return {"signedURL": self.response}

    storage = ResponseStorage(
        supabase_url="https://project.example",
        service_key="synthetic-server-only",
    )
    storage.response = "/object/sign/baby-audio/baby/audio/random?token=synthetic"
    signed = asyncio.run(
        storage.sign_playback(
            bucket="baby-audio",
            object_key="baby/audio/random",
            expires_in_seconds=60,
        )
    )
    assert signed == (
        "https://project.example/storage/v1/object/sign/"
        "baby-audio/baby/audio/random?token=synthetic"
    )

    storage.response = "https://attacker.example/storage/v1/object/sign/leak"
    with pytest.raises(StorageError, match="STORAGE_RESPONSE_INVALID"):
        asyncio.run(
            storage.sign_playback(
                bucket="baby-audio",
                object_key="baby/audio/random",
                expires_in_seconds=60,
            )
        )


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


class _HttpResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = io.BytesIO(body)

    def __enter__(self) -> _HttpResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


def _storage() -> SupabaseAudioStorage:
    return SupabaseAudioStorage(
        supabase_url="https://project.example/",
        storage_url="https://uploads.example/",
        service_key="synthetic-server-only",
        timeout_seconds=1,
    )


def test_storage_http_helpers_and_success_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = _storage()
    assert storage.configured is True
    assert storage._object_path("baby audio", "a folder/source") == "baby%20audio/a%20folder/source"
    assert storage._headers(content_type="audio/wav")["Content-Type"] == "audio/wav"
    assert [_retryable_status(code) for code in (408, 425, 429, 500, 404)] == [
        True,
        True,
        True,
        True,
        False,
    ]

    requests: list[object] = []
    responses = [
        _HttpResponse(b"source-bytes", headers={"Content-Length": "12"}),
        _HttpResponse(status=201),
        _HttpResponse(b'{"signedUrl":"/storage/v1/object/sign/baby-audio/key?token=x"}'),
        _HttpResponse(status=204),
    ]

    def fake_urlopen(request: object, **_kwargs: object) -> _HttpResponse:
        requests.append(request)
        return responses.pop(0)

    monkeypatch.setattr(storage_module, "urlopen", fake_urlopen)
    destination = tmp_path / "downloaded"
    downloaded = asyncio.run(
        storage.download(bucket="baby-audio", object_key="key", destination=destination)
    )
    assert downloaded == DownloadedObject(
        bytes=12,
        checksum_sha256=hashlib.sha256(b"source-bytes").hexdigest(),
    )
    source = tmp_path / "pcm"
    source.write_bytes(b"pcm")
    asyncio.run(
        storage.upload(
            bucket="baby-audio", object_key="derived/key", source=source, mime_type="audio/wav"
        )
    )
    signed = asyncio.run(
        storage.sign_playback(bucket="baby-audio", object_key="key", expires_in_seconds=60)
    )
    assert signed.startswith("https://project.example/storage/v1/object/sign/")
    asyncio.run(storage.delete(bucket="baby-audio", object_keys=["key", "derived/key"]))
    assert len(requests) == 4


@pytest.mark.parametrize(
    ("response", "expected_code", "retryable"),
    [
        (_HttpResponse(b"", headers={"Content-Length": "0"}), "OBJECT_EMPTY", False),
        (
            _HttpResponse(b"ignored", headers={"Content-Length": str(MAX_INPUT_BYTES + 1)}),
            "TOO_LARGE",
            False,
        ),
        (
            HTTPError("https://project.example", 503, "failure", {}, None),
            "STORAGE_HTTP_ERROR",
            True,
        ),
        (URLError("offline"), "STORAGE_UNAVAILABLE", True),
    ],
)
def test_storage_download_errors_remove_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: _HttpResponse | Exception,
    expected_code: str,
    retryable: bool,
) -> None:
    def fake_urlopen(*_args: object, **_kwargs: object) -> _HttpResponse:
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(storage_module, "urlopen", fake_urlopen)
    destination = tmp_path / "partial"
    with pytest.raises(StorageError) as caught:
        asyncio.run(
            _storage().download(bucket="baby-audio", object_key="source", destination=destination)
        )
    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable
    assert not destination.exists()


def test_storage_upload_delete_and_json_response_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = _storage()
    missing = tmp_path / "missing"
    with pytest.raises(StorageError, match="DERIVATIVE_UNAVAILABLE"):
        asyncio.run(
            storage.upload(
                bucket="baby-audio", object_key="key", source=missing, mime_type="audio/wav"
            )
        )
    empty = tmp_path / "empty"
    empty.touch()
    with pytest.raises(StorageError, match="DERIVATIVE_SIZE_INVALID"):
        asyncio.run(
            storage.upload(
                bucket="baby-audio", object_key="key", source=empty, mime_type="audio/wav"
            )
        )
    asyncio.run(storage.delete(bucket="baby-audio", object_keys=[]))
    for invalid in (["../escape"], ["key"] * 101, [""]):
        with pytest.raises(StorageError, match="OBJECT_KEY_INVALID"):
            asyncio.run(storage.delete(bucket="baby-audio", object_keys=invalid))
    with pytest.raises(ValueError, match="between 1 and 60"):
        asyncio.run(
            storage.sign_playback(bucket="baby-audio", object_key="key", expires_in_seconds=61)
        )

    responses = [
        _HttpResponse(b"not-json"),
        _HttpResponse(b"[]"),
        _HttpResponse(b'{"unexpected":"value"}'),
        _HttpResponse(b'{"signedURL":"/wrong/path"}'),
        _HttpResponse(b"{}", status=202),
    ]

    def fake_urlopen(*_args: object, **_kwargs: object) -> _HttpResponse:
        return responses.pop(0)

    monkeypatch.setattr(storage_module, "urlopen", fake_urlopen)
    for _ in range(4):
        with pytest.raises(StorageError, match="STORAGE_RESPONSE_INVALID"):
            asyncio.run(
                storage.sign_playback(bucket="baby-audio", object_key="key", expires_in_seconds=60)
            )
    with pytest.raises(StorageError, match="STORAGE_HTTP_ERROR"):
        storage._json_request("https://project.example/test", {})


@pytest.mark.parametrize(
    ("failure", "expected_code", "retryable"),
    [
        (_HttpResponse(status=429), "STORAGE_HTTP_ERROR", True),
        (
            HTTPError("https://project.example", 400, "failure", {}, None),
            "STORAGE_HTTP_ERROR",
            False,
        ),
        (URLError("offline"), "STORAGE_UNAVAILABLE", True),
    ],
)
def test_storage_upload_maps_provider_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: _HttpResponse | Exception,
    expected_code: str,
    retryable: bool,
) -> None:
    def fake_urlopen(*_args: object, **_kwargs: object) -> _HttpResponse:
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(storage_module, "urlopen", fake_urlopen)
    source = tmp_path / "pcm"
    source.write_bytes(b"pcm")
    with pytest.raises(StorageError) as caught:
        asyncio.run(
            _storage().upload(
                bucket="baby-audio",
                object_key="derived/key",
                source=source,
                mime_type="audio/wav",
            )
        )
    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable


@pytest.mark.parametrize(
    ("failure", "expected_code", "retryable"),
    [
        (_HttpResponse(status=503), "STORAGE_HTTP_ERROR", True),
        (
            HTTPError("https://project.example", 404, "failure", {}, None),
            "STORAGE_HTTP_ERROR",
            False,
        ),
        (URLError("offline"), "STORAGE_UNAVAILABLE", True),
    ],
)
def test_storage_delete_maps_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: _HttpResponse | Exception,
    expected_code: str,
    retryable: bool,
) -> None:
    def fake_urlopen(*_args: object, **_kwargs: object) -> _HttpResponse:
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(storage_module, "urlopen", fake_urlopen)
    with pytest.raises(StorageError) as caught:
        asyncio.run(_storage().delete(bucket="baby-audio", object_keys=["source/key"]))
    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable


def _decoder_with_runner(
    tmp_path: Path,
    runner: object,
) -> FfmpegAudioDecoder:
    ffmpeg = tmp_path / "ffmpeg"
    ffprobe = tmp_path / "ffprobe"
    ffmpeg.touch(mode=0o755)
    ffprobe.touch(mode=0o755)
    ffmpeg.chmod(0o755)
    ffprobe.chmod(0o755)
    return FfmpegAudioDecoder(
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        runner=runner,  # type: ignore[arg-type]
    )


def _probe_payload(
    *,
    container: str = "wav",
    codec: str = "pcm_s16le",
    rate: int = 8_000,
    channels: int = 1,
    duration: object = "1.1",
) -> bytes:
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": codec,
                    "sample_rate": str(rate),
                    "channels": channels,
                }
            ],
            "format": {"format_name": container, "duration": duration},
        }
    ).encode()


def test_decoder_runs_fixed_local_pipeline_and_preserves_rate(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        if command[-1] == "-version":
            return subprocess.CompletedProcess(command, 0, b"ffmpeg version 7.1.1 synthetic\n", b"")
        if Path(command[0]).name == "ffprobe":
            return subprocess.CompletedProcess(command, 0, _probe_payload(), b"")
        _write_pcm(Path(command[-1]), [4_000] * 8_800)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    decoder = _decoder_with_runner(tmp_path, runner)
    source = tmp_path / "source"
    source.write_bytes(b"synthetic source")
    decoded = asyncio.run(
        decoder.decode(
            source,
            tmp_path / "decoded.wav",
            declared_mime_type="audio/x-wav; codecs=pcm",
            max_seconds=2,
        )
    )
    assert decoder.configured is True
    assert decoded.sample_rate_hz == 8_000
    assert decoded.channels == 1
    assert decoded.rejection_code is None
    assert all(isinstance(command, list) for command in commands)
    assert "file,pipe" in commands[1]
    assert "file,pipe" in commands[2]


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (subprocess.TimeoutExpired(["ffmpeg"], 1), "DECODE_TIMEOUT"),
        (OSError("missing"), "DECODER_UNAVAILABLE"),
    ],
)
def test_decoder_runner_maps_process_failures(
    tmp_path: Path, failure: Exception, expected_code: str
) -> None:
    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise failure

    decoder = _decoder_with_runner(tmp_path, runner)
    with pytest.raises(AudioDecodeError) as caught:
        decoder._run(["decoder", "-version"], timeout=1)
    assert caught.value.code == expected_code
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    ("result", "expected_code"),
    [
        (subprocess.CompletedProcess([], 1, b"", b"failed"), "DECODER_UNAVAILABLE"),
        (
            subprocess.CompletedProcess([], 0, b"ffmpeg version unexpected\n", b""),
            "DECODER_VERSION_MISMATCH",
        ),
    ],
)
def test_decoder_identity_must_match_pinned_version(
    tmp_path: Path,
    result: subprocess.CompletedProcess[bytes],
    expected_code: str,
) -> None:
    decoder = _decoder_with_runner(tmp_path, lambda *_args, **_kwargs: result)
    with pytest.raises(AudioDecodeError) as caught:
        decoder._verify_identity()
    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"streams": [{"codec_type": "audio"}], "format": {"format_name": "wav"}},
        {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "flac",
                    "sample_rate": "48000",
                    "channels": 1,
                }
            ],
            "format": {"format_name": "flac"},
        },
    ],
)
def test_decoder_rejects_invalid_or_unsupported_metadata(payload: dict[str, object]) -> None:
    with pytest.raises(AudioDecodeError):
        FfmpegAudioDecoder._format_identity(payload)


@pytest.mark.parametrize(
    ("probe_stdout", "probe_returncode", "declared_mime", "max_seconds", "expected_code"),
    [
        (b"{}", 1, "audio/wav", 2, "DECODE_ERROR"),
        (b"not-json", 0, "audio/wav", 2, "DECODE_ERROR"),
        (_probe_payload(), 0, "audio/webm", 2, "UNSUPPORTED_CODEC"),
        (_probe_payload(duration="invalid"), 0, "audio/wav", 2, "DECODE_ERROR"),
        (_probe_payload(duration="nan"), 0, "audio/wav", 2, "DECODE_ERROR"),
        (_probe_payload(duration="2.2"), 0, "audio/wav", 2, "TOO_LONG"),
    ],
)
def test_decoder_rejects_probe_and_declaration_failures(
    tmp_path: Path,
    probe_stdout: bytes,
    probe_returncode: int,
    declared_mime: str,
    max_seconds: int,
    expected_code: str,
) -> None:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if command[-1] == "-version":
            return subprocess.CompletedProcess(command, 0, b"ffmpeg version 7.1.1\n", b"")
        return subprocess.CompletedProcess(command, probe_returncode, probe_stdout, b"")

    decoder = _decoder_with_runner(tmp_path, runner)
    source = tmp_path / "source"
    source.write_bytes(b"source")
    with pytest.raises(AudioDecodeError) as caught:
        decoder._decode_sync(
            source,
            tmp_path / "decoded.wav",
            declared_mime_type=declared_mime,
            max_seconds=max_seconds,
        )
    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("process_failure", "DECODE_ERROR"),
        ("empty_output", "DECODE_ERROR"),
        ("rate_changed", "DECODE_ERROR"),
        ("decoded_too_long", "TOO_LONG"),
    ],
)
def test_decoder_rejects_invalid_decoded_outputs(
    tmp_path: Path, mode: str, expected_code: str
) -> None:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if command[-1] == "-version":
            return subprocess.CompletedProcess(command, 0, b"ffmpeg version 7.1.1\n", b"")
        if Path(command[0]).name == "ffprobe":
            probe_rate = 16_000 if mode == "rate_changed" else 8_000
            return subprocess.CompletedProcess(
                command,
                0,
                _probe_payload(rate=probe_rate, duration="N/A"),
                b"",
            )
        if mode == "process_failure":
            return subprocess.CompletedProcess(command, 1, b"", b"failure")
        destination = Path(command[-1])
        if mode == "empty_output":
            destination.touch()
        elif mode == "decoded_too_long":
            _write_pcm(destination, [4_000] * 8_800)
        else:
            _write_pcm(destination, [4_000] * 8_000)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    decoder = _decoder_with_runner(tmp_path, runner)
    source = tmp_path / "source"
    source.write_bytes(b"source")
    with pytest.raises(AudioDecodeError) as caught:
        decoder._decode_sync(
            source,
            tmp_path / "decoded.wav",
            declared_mime_type="audio/wav",
            max_seconds=1,
        )
    assert caught.value.code == expected_code


def test_decoder_rejects_missing_oversize_and_invalid_pcm(tmp_path: Path) -> None:
    decoder = _decoder_with_runner(
        tmp_path,
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, b"", b""),
    )
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(AudioDecodeError, match="not a file"):
        decoder._decode_sync(
            directory,
            tmp_path / "out",
            declared_mime_type="audio/wav",
            max_seconds=2,
        )
    large = tmp_path / "large"
    with large.open("wb") as output:
        output.truncate(MAX_INPUT_BYTES + 1)
    with pytest.raises(AudioDecodeError, match="25,000,000"):
        decoder._decode_sync(
            large,
            tmp_path / "out",
            declared_mime_type="audio/wav",
            max_seconds=2,
        )
    malformed = tmp_path / "malformed.wav"
    malformed.write_bytes(b"not wav")
    with pytest.raises(AudioDecodeError, match="could not be inspected"):
        _measure_pcm(malformed)
    eight_bit = tmp_path / "eight-bit.wav"
    with wave.open(str(eight_bit), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(1)
        output.setframerate(8_000)
        output.writeframes(b"\x80" * 8_000)
    with pytest.raises(AudioDecodeError, match="not canonical"):
        _measure_pcm(eight_bit)


def test_audio_service_error_mapping_is_explicit() -> None:
    assert PostgresAudioService._normalize_mime(" Audio/WAV; codecs=pcm ") == "audio/wav"
    assert PostgresAudioService._rejection_status("TOO_LARGE") == 413
    assert PostgresAudioService._rejection_status("UNSUPPORTED_CODEC") == 415
    assert PostgresAudioService._rejection_status("SILENCE") == 422
    for status, code in (
        (413, ErrorCode.FILE_TOO_LARGE),
        (415, ErrorCode.UNSUPPORTED_MEDIA_TYPE),
        (503, ErrorCode.SERVICE_UNAVAILABLE),
        (409, ErrorCode.INVALID_STATE),
        (422, ErrorCode.INVALID_AUDIO),
    ):
        error = PostgresAudioService._completion_exception(status, "SILENCE")
        assert isinstance(error, ApiException)
        assert error.code == code


def test_analysis_mapping_preserves_context_and_reviewed_recommendation() -> None:
    def identifier(value: int) -> UUID:
        return UUID(f"10000000-0000-4000-8000-{value:012d}")

    now = datetime(2026, 9, 20, tzinfo=UTC)
    row = {
        "analysis_id": identifier(1),
        "baby_id": identifier(2),
        "episode_id": identifier(3),
        "audio_id": identifier(4),
        "created_by_user_id": identifier(5),
        "analysis_status": "COMPLETE",
        "analysis_stage": "FINISHED",
        "attempt_no": 1,
        "lease_expires_at": None,
        "quality_status": "PASS",
        "analysis_quality_reasons": [],
        "cry_detected": True,
        "audio_candidates": [],
        "abstain_reason": None,
        "failure": None,
        "model_version": "model-v1",
        "preprocess_version": "pre-v1",
        "label_mapping_version": "labels-v1",
        "inference_mode": "REAL",
        "inference_executed": True,
        "analysis_data_origin": "DEMO",
        "analysis_recorded_at": now,
        "completed_at": now,
        "context_snapshot_id": identifier(6),
        "context_as_of": now,
        "context_known_at": now,
        "context_record_refs": [],
        "context_features": {
            "last_feeding_at": now,
            "minutes_since_last_feeding": 20,
            "current_sleep": False,
        },
        "context_missing_fields": ["last_diaper_event_at"],
        "recommendation_id": identifier(7),
        "recommendation_actions": [
            {"action_type": "HOLDING", "text": "안아 보기", "evidence_refs": []},
            "기타 확인",
        ],
        "recommendation_policy_version": "policy-v1",
        "supersedes_recommendation_id": None,
        "recommendation_recorded_at": now,
    }
    analysis = PostgresAudioService._analysis(row)
    assert analysis.context_snapshot is not None
    assert analysis.context_snapshot.values.minutes_since_last_feeding == 20
    assert analysis.recommendation is not None
    assert [action.action_type for action in analysis.recommendation.actions] == [
        "HOLDING",
        "OTHER",
    ]

    without_optional_rows = {**row, "context_snapshot_id": None, "recommendation_id": None}
    minimal = PostgresAudioService._analysis(without_optional_rows)
    assert minimal.context_snapshot is None
    assert minimal.recommendation is None


def test_unconfigured_storage_rejects_every_server_operation(tmp_path: Path) -> None:
    storage = UnconfiguredAudioStorage()
    with pytest.raises(StorageError, match="STORAGE_UNCONFIGURED"):
        storage.upload_endpoint(method="STANDARD", bucket="baby-audio", object_key="key")

    async def exercise() -> None:
        operations = (
            storage.upload(
                bucket="baby-audio",
                object_key="key",
                source=tmp_path / "source",
                mime_type="audio/wav",
            ),
            storage.delete(bucket="baby-audio", object_keys=["key"]),
            storage.sign_playback(bucket="baby-audio", object_key="key", expires_in_seconds=60),
        )
        for operation in operations:
            with pytest.raises(StorageError, match="STORAGE_UNCONFIGURED"):
                await operation

    asyncio.run(exercise())

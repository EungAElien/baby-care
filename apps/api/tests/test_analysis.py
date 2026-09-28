from __future__ import annotations

import asyncio
import hashlib
import io
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from types import MethodType
from typing import cast
from uuid import uuid4

import pytest
from psycopg_pool import PoolClosed

from baby_care_api.core.errors import ApiException
from baby_care_api.models.audio import AudioCandidate, RecommendedAction
from baby_care_api.models.b04 import Failure
from baby_care_api.models.errors import ErrorCode
from baby_care_api.services.analysis import (
    AnalysisDecision,
    AnalysisPolicyInfo,
    FixedV1BAnalysisPolicy,
    PostgresAnalysisService,
    RecommendationDraft,
    _AnalysisClaim,
)
from baby_care_api.services.model_runtime import ModelNotReadyError
from baby_care_api.services.security import AuthenticatedPrincipal
from baby_care_api.services.storage import DownloadedObject, StorageError


def _wav_bytes(*, rate: int = 8_000, channels: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x01\x00" * channels * rate)
    return output.getvalue()


def _claim(payload: bytes) -> _AnalysisClaim:
    return _AnalysisClaim(
        analysis_id=uuid4(),
        baby_id=uuid4(),
        episode_id=uuid4(),
        audio_id=uuid4(),
        attempt_no=1,
        execution_token=uuid4(),
        request_path=f"/v1/episodes/{uuid4()}/analyses",
        client_request_id=uuid4(),
        bucket="baby-audio",
        object_key=f"{uuid4()}/{uuid4()}/derived/source.wav",
        expected_bytes=len(payload),
        expected_checksum_sha256=hashlib.sha256(payload).hexdigest(),
        sample_rate_hz=8_000,
        channels=1,
        quality_reasons=("CLIPPING",),
        data_origin="DEMO",
    )


def _complete() -> AnalysisDecision:
    return AnalysisDecision(
        status="COMPLETE",
        quality_status="PASS",
        quality_reasons=(),
        cry_detected=True,
        candidates=(AudioCandidate(code="HUNGER", label="배고픔", rank=1),),
        abstain_reason=None,
        recommendation=RecommendationDraft(
            actions=(
                RecommendedAction(
                    action_type="FEEDING",
                    text="수유 필요 여부를 확인해 주세요.",
                    evidence_refs=[],
                ),
            ),
            policy_version="test-policy-v1",
        ),
    )


class _ConfiguredPort:
    @property
    def configured(self) -> bool:
        return True


class _ReadyRuntime:
    enabled = True
    ready = True


class _TestPolicy:
    @property
    def info(self) -> AnalysisPolicyInfo:
        return AnalysisPolicyInfo(
            product_ready=True,
            inference_mode="REAL",
            model_version="test-model-v1",
            preprocess_version="test-preprocess-v1",
            label_mapping_version="test-labels-v1",
            supported_labels=("HUNGER",),
            unavailable_reason=None,
        )

    def decide(self, runtime_result: object) -> AnalysisDecision:
        assert isinstance(runtime_result, AnalysisDecision)
        return runtime_result


class _Storage:
    def __init__(self, payload: bytes, *, corrupt_metadata: bool = False) -> None:
        self.payload = payload
        self.corrupt_metadata = corrupt_metadata
        self.destination: Path | None = None
        self.downloaded = asyncio.Event()

    @property
    def configured(self) -> bool:
        return True

    async def download(
        self, *, bucket: str, object_key: str, destination: Path
    ) -> DownloadedObject:
        assert bucket == "baby-audio" and object_key
        await asyncio.to_thread(destination.write_bytes, self.payload)
        self.destination = destination
        self.downloaded.set()
        return DownloadedObject(
            bytes=len(self.payload) + int(self.corrupt_metadata),
            checksum_sha256=hashlib.sha256(self.payload).hexdigest(),
        )


class _Execution:
    def __init__(self, *, error: Exception | None = None, block: bool = False) -> None:
        self.error = error
        self.block = block
        self.callbacks: list[object] = []

    @property
    def started(self) -> bool:
        return True

    def add_done_callback(self, callback: object) -> None:
        self.callbacks.append(callback)

    async def wait_until(self, deadline: float) -> object:
        try:
            if self.block:
                await asyncio.Event().wait()
            if self.error is not None:
                raise self.error
            return _complete()
        finally:
            for callback in self.callbacks:
                cast(object, callback)()


class _Runtime:
    enabled = True
    ready = True

    def __init__(self, execution: _Execution | Exception) -> None:
        self.execution = execution

    def submit_inference(self, path: Path, *, deadline: float) -> _Execution:
        assert path.exists() and deadline > 0
        if isinstance(self.execution, Exception):
            raise self.execution
        return self.execution


def _service_for_execution(
    payload: bytes,
    *,
    execution: _Execution | Exception,
    corrupt_metadata: bool = False,
) -> tuple[PostgresAnalysisService, _Storage, list[Failure | None], object]:
    service = object.__new__(PostgresAnalysisService)
    storage = _Storage(payload, corrupt_metadata=corrupt_metadata)
    service._storage = storage
    service._model_runtime = _Runtime(execution)
    service._analysis_policy = _TestPolicy()
    service._request_timeout_seconds = 1.0
    checkpoints: list[str] = []
    service._execution_checkpoint = lambda stage, _: checkpoints.append(stage)
    failures: list[Failure | None] = []
    terminal_result = object()

    async def advance(
        _: PostgresAnalysisService,
        principal: AuthenticatedPrincipal,
        claim: _AnalysisClaim,
    ) -> bool:
        assert principal is not None and claim.analysis_id
        return True

    async def finish(
        _: PostgresAnalysisService,
        principal: AuthenticatedPrincipal,
        claim: _AnalysisClaim,
        *,
        decision: AnalysisDecision | None,
        failure: Failure | None,
        inference_executed: bool,
    ) -> object:
        assert principal is not None and claim.analysis_id
        assert decision is None or decision.status == "COMPLETE"
        assert isinstance(inference_executed, bool)
        failures.append(failure)
        return terminal_result

    service._advance_to_inference = MethodType(advance, service)
    service._finish_terminal = MethodType(finish, service)
    return service, storage, failures, terminal_result


def test_fixed_policy_and_error_helpers_keep_product_gate_closed() -> None:
    policy = FixedV1BAnalysisPolicy()
    assert policy.info.product_ready is False
    assert policy.info.unavailable_reason == "PRODUCT_POLICY_NOT_VALIDATED"
    with pytest.raises(ModelNotReadyError):
        policy.decide(object())

    unavailable = PostgresAnalysisService._model_not_ready(None)
    assert unavailable.code == ErrorCode.MODEL_NOT_READY
    assert unavailable.retryable is True
    assert unavailable.details.retry_after_seconds == 5
    gated = PostgresAnalysisService._model_not_ready("PRODUCT_POLICY_NOT_VALIDATED")
    assert gated.retryable is False
    assert gated.details.retry_after_seconds is None
    in_progress_id = uuid4()
    in_progress = PostgresAnalysisService._in_progress(in_progress_id)
    assert in_progress.details.existing_analysis_id == in_progress_id


@pytest.mark.parametrize("timeout", [0, 45.01])
def test_analysis_constructor_rejects_timeout_outside_request_contract(timeout: float) -> None:
    with pytest.raises(ValueError, match="analysis request timeout"):
        PostgresAnalysisService(
            "postgresql://user:pass@localhost/test",
            invite_base_url="https://example.test/invite",
            proof_secret="x" * 32,
            child_data_production_enabled=False,
            storage=cast(object, _ConfiguredPort()),
            decoder=cast(object, _ConfiguredPort()),
            model_runtime=cast(object, _ReadyRuntime()),
            analysis_policy=_TestPolicy(),
            request_timeout_seconds=timeout,
        )


def test_analysis_constructor_keeps_sixty_second_lease_fixed() -> None:
    with pytest.raises(ValueError, match="lease must remain 60 seconds"):
        PostgresAnalysisService(
            "postgresql://user:pass@localhost/test",
            invite_base_url="https://example.test/invite",
            proof_secret="x" * 32,
            child_data_production_enabled=False,
            storage=cast(object, _ConfiguredPort()),
            decoder=cast(object, _ConfiguredPort()),
            model_runtime=cast(object, _ReadyRuntime()),
            analysis_policy=_TestPolicy(),
            lease_seconds=59,
        )


def test_decision_and_pcm_contract_reject_invalid_model_outputs(tmp_path: Path) -> None:
    invalid_complete = _complete()
    invalid_complete = AnalysisDecision(**{**invalid_complete.__dict__, "cry_detected": False})
    with pytest.raises(ValueError, match="COMPLETE decision"):
        PostgresAnalysisService._validate_decision(invalid_complete)

    bad_ranks = _complete()
    bad_ranks = AnalysisDecision(
        **{
            **bad_ranks.__dict__,
            "candidates": (AudioCandidate(code="HUNGER", label="배고픔", rank=2),),
        }
    )
    with pytest.raises(ValueError, match="ranks"):
        PostgresAnalysisService._validate_decision(bad_ranks)

    invalid_abstain = AnalysisDecision(
        status="ABSTAIN",
        quality_status="PASS",
        quality_reasons=(),
        cry_detected=None,
        candidates=(),
        abstain_reason=None,
        recommendation=None,
    )
    with pytest.raises(ValueError, match="ABSTAIN decision"):
        PostgresAnalysisService._validate_decision(invalid_abstain)

    payload = _wav_bytes(rate=16_000)
    path = tmp_path / "wrong-rate.wav"
    path.write_bytes(payload)
    with pytest.raises(StorageError, match="DERIVATIVE_INTEGRITY_FAILED"):
        PostgresAnalysisService._verify_pcm_file(path, _claim(payload))


def test_claim_mapping_and_closed_pool_fail_closed() -> None:
    payload = _wav_bytes()
    source = {
        "baby_id": uuid4(),
        "episode_id": uuid4(),
        "audio_id": uuid4(),
        "bucket_id": "baby-audio",
        "object_key": f"{uuid4()}/{uuid4()}/derived/source.wav",
        "derivative_bytes": len(payload),
        "derivative_checksum_sha256": hashlib.sha256(payload).hexdigest(),
        "sample_rate_hz": 8_000,
        "channels": 1,
        "quality_reasons": ["CLIPPING"],
        "data_origin": "DEMO",
    }
    analysis_id = uuid4()
    mapped = PostgresAnalysisService._claim_from_source(
        source,
        analysis_id=analysis_id,
        attempt_no=2,
        token=uuid4(),
        path="/v1/analyses/test/retry",
        client_request_id=uuid4(),
    )
    assert mapped.analysis_id == analysis_id
    assert mapped.quality_reasons == ("CLIPPING",)

    class ClosedPool:
        def connection(self) -> object:
            raise PoolClosed("closed")

    service = object.__new__(PostgresAnalysisService)
    service._pool = ClosedPool()
    with pytest.raises(ApiException) as recovered:
        asyncio.run(service.startup_recover())
    assert recovered.value.code == ErrorCode.SERVICE_UNAVAILABLE
    assert asyncio.run(service._fence_if_unavailable(mapped)) is False


def test_execute_claim_maps_integrity_and_runtime_errors_and_cleans_temp() -> None:
    payload = _wav_bytes()
    principal = cast(AuthenticatedPrincipal, object())

    corrupt, corrupt_storage, failures, corrupt_result = _service_for_execution(
        payload,
        execution=_Execution(),
        corrupt_metadata=True,
    )
    result = asyncio.run(corrupt._execute_claim(principal, _claim(payload)))
    assert result is corrupt_result
    assert failures[0] is not None and failures[0].code == "INFERENCE_ERROR"
    assert corrupt_storage.destination is not None
    assert not corrupt_storage.destination.parent.exists()

    execution = _Execution(error=ValueError("synthetic inference error"))
    failed, failed_storage, failures, failed_result = _service_for_execution(
        payload,
        execution=execution,
    )
    result = asyncio.run(failed._execute_claim(principal, _claim(payload)))
    assert result is failed_result
    assert failures[0] is not None and failures[0].code == "INFERENCE_ERROR"
    assert failed_storage.destination is not None
    assert not failed_storage.destination.parent.exists()


def test_execute_claim_handles_expired_deadline_stale_claim_and_missing_terminal() -> None:
    payload = _wav_bytes()
    principal = cast(AuthenticatedPrincipal, object())

    expired, _, failures, expired_result = _service_for_execution(
        payload,
        execution=_Execution(),
    )
    expired._request_timeout_seconds = -1
    result = asyncio.run(expired._execute_claim(principal, _claim(payload)))
    assert result is expired_result
    assert failures[0] is not None and failures[0].code == "ANALYSIS_TIMEOUT"

    stale, stale_storage, _, _ = _service_for_execution(
        payload,
        execution=_Execution(),
    )

    async def no_advance(
        _: PostgresAnalysisService,
        principal: AuthenticatedPrincipal,
        claim: _AnalysisClaim,
    ) -> bool:
        assert principal is not None and claim.analysis_id
        return False

    stale._advance_to_inference = MethodType(no_advance, stale)
    with pytest.raises(ApiException) as stale_error:
        asyncio.run(stale._execute_claim(principal, _claim(payload)))
    assert stale_error.value.code == ErrorCode.RESOURCE_DELETING
    assert stale_storage.destination is not None
    assert not stale_storage.destination.parent.exists()

    missing, missing_storage, _, _ = _service_for_execution(
        payload,
        execution=ModelNotReadyError("synthetic unavailable runtime"),
    )

    async def finish_missing(
        _: PostgresAnalysisService,
        principal: AuthenticatedPrincipal,
        claim: _AnalysisClaim,
        *,
        decision: AnalysisDecision | None,
        failure: Failure | None,
        inference_executed: bool,
    ) -> None:
        assert principal is not None and claim.analysis_id
        assert decision is None and failure is not None
        assert inference_executed is False

    missing._finish_terminal = MethodType(finish_missing, missing)
    with pytest.raises(ApiException) as missing_error:
        asyncio.run(missing._execute_claim(principal, _claim(payload)))
    assert missing_error.value.code == ErrorCode.RESOURCE_NOT_FOUND
    assert missing_storage.destination is not None
    assert not missing_storage.destination.parent.exists()


@pytest.mark.parametrize(
    ("audio_status", "derivative_status", "consent", "expected_code"),
    [
        ("DELETED", "DELETED", "GRANTED", ErrorCode.RESOURCE_DELETED),
        ("DELETING", "READY", "GRANTED", ErrorCode.RESOURCE_DELETING),
        ("READY", "READY", "REVOKED", ErrorCode.CONSENT_REQUIRED),
    ],
)
def test_finish_terminal_commits_access_fence_before_rejecting_response(
    audio_status: str,
    derivative_status: str,
    consent: str,
    expected_code: ErrorCode,
) -> None:
    payload = _wav_bytes()
    claim = _claim(payload)
    committed: list[bool] = []
    sentinel = object()

    class Cursor:
        def __init__(self, row: object) -> None:
            self.row = row

        async def fetchone(self) -> object:
            return self.row

    class Connection:
        async def execute(self, query: str, params: object = None) -> Cursor:
            del params
            if "select n.status::text as status" in query:
                return Cursor(
                    {
                        "status": "RUNNING",
                        "attempt_no": claim.attempt_no,
                        "execution_token": claim.execution_token,
                        "audio_status": audio_status,
                        "derivative_status": derivative_status,
                        "processing_consent": consent,
                    }
                )
            if "set stage = 'PERSISTING'" in query:
                return Cursor({"baby_id": claim.baby_id})
            if "change_version = change_version + 1" in query:
                return Cursor({"change_version": 2})
            return Cursor(None)

    service = object.__new__(PostgresAnalysisService)
    connection = Connection()

    @asynccontextmanager
    async def transaction(
        _: PostgresAnalysisService,
        principal: AuthenticatedPrincipal,
    ) -> object:
        assert principal is not None
        yield connection
        committed.append(True)

    async def noop(*_: object, **__: object) -> None:
        return None

    async def get_row(*_: object, **__: object) -> dict[str, object]:
        return {}

    async def fence(*_: object, **__: object) -> bool:
        return False

    service.transaction = MethodType(transaction, service)
    service._lock_shared_change_feed = noop
    service._ensure_processing_allowed = noop
    service._complete_idempotency = noop
    service._record_shared_changes = noop
    service._get_analysis_row = get_row
    service._analysis = lambda _: cast(object, sentinel)
    service._fence_if_unavailable = fence

    with pytest.raises(ApiException) as blocked:
        asyncio.run(
            service._finish_terminal(
                cast(AuthenticatedPrincipal, object()),
                claim,
                decision=_complete(),
                failure=None,
                inference_executed=True,
            )
        )
    assert blocked.value.code == expected_code
    assert committed == [True]


def test_execute_claim_cancellation_is_terminalized_before_propagation() -> None:
    async def exercise() -> tuple[list[Failure | None], Path | None]:
        payload = _wav_bytes()
        execution = _Execution(block=True)
        service, storage, failures, _ = _service_for_execution(
            payload,
            execution=execution,
        )
        task = asyncio.create_task(
            service._execute_claim(cast(AuthenticatedPrincipal, object()), _claim(payload))
        )
        await storage.downloaded.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return failures, storage.destination

    failures, destination = asyncio.run(exercise())
    assert failures[0] is not None and failures[0].code == "ANALYSIS_TIMEOUT"
    assert destination is not None
    assert not destination.parent.exists()

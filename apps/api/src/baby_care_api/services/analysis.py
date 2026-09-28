from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

import psycopg
from psycopg_pool import PoolClosed, PoolTimeout

from baby_care_api.core.errors import ApiException
from baby_care_api.models.audio import (
    Analysis,
    AudioCandidate,
    Capabilities,
    CreateAnalysis,
    DetectorInfo,
    ModelInfo,
    NormalizerUnavailableReason,
    QualityReason,
    RecommendedAction,
    RetryAnalysis,
)
from baby_care_api.models.b04 import Failure
from baby_care_api.models.errors import ErrorCode, ErrorDetails
from baby_care_api.services.audio import DatabaseConnection, DatabaseRow, PostgresAudioService
from baby_care_api.services.audio_decoder import (
    PREPROCESSING_BOUNDARY_VERSION,
    SUPPORTED_MIME_TYPES,
    AudioDecoder,
)
from baby_care_api.services.model_runtime import (
    InferenceDeadlineExceededError,
    InferenceExecution,
    ModelNotReadyError,
    ModelRuntimeBusyError,
    ModelRuntimeManager,
)
from baby_care_api.services.security import AuthenticatedPrincipal
from baby_care_api.services.storage import AudioStoragePort, StorageError
from baby_care_m2d.registry import LABEL_MAPPING_VERSION, MODEL_VERSION, PREPROCESS_VERSION

ANALYSIS_REQUEST_SECONDS = 45.0
ANALYSIS_LEASE_SECONDS = 60


@dataclass(frozen=True)
class AnalysisPolicyInfo:
    product_ready: bool
    inference_mode: Literal["REAL", "STUB"]
    model_version: str
    preprocess_version: str
    label_mapping_version: str
    supported_labels: tuple[str, ...]
    unavailable_reason: str | None


@dataclass(frozen=True)
class RecommendationDraft:
    actions: tuple[RecommendedAction, ...]
    policy_version: str


@dataclass(frozen=True)
class AnalysisDecision:
    status: Literal["COMPLETE", "ABSTAIN"]
    quality_status: Literal["PASS", "INSUFFICIENT", "INVALID"]
    quality_reasons: tuple[QualityReason, ...]
    cry_detected: bool | None
    candidates: tuple[AudioCandidate, ...]
    abstain_reason: (
        Literal[
            "NO_CRY",
            "LOW_QUALITY",
            "INSUFFICIENT_AUDIO",
            "LOW_CONFIDENCE",
            "UNSUPPORTED_SCOPE",
        ]
        | None
    )
    recommendation: RecommendationDraft | None


class AnalysisPolicy(Protocol):
    @property
    def info(self) -> AnalysisPolicyInfo: ...

    def decide(self, runtime_result: object) -> AnalysisDecision: ...


class FixedV1BAnalysisPolicy:
    """Production policy for the fixed V1 B runtime.

    The runtime is executable, but the checked-in evidence explicitly says its
    calibration, cry decision, product labels, abstention thresholds, and
    recommendation policy are not validated. Keeping ``product_ready`` false is
    therefore a release gate, not a runtime health statement.
    """

    @property
    def info(self) -> AnalysisPolicyInfo:
        return AnalysisPolicyInfo(
            product_ready=False,
            inference_mode="REAL",
            model_version=MODEL_VERSION,
            preprocess_version=PREPROCESS_VERSION,
            label_mapping_version=LABEL_MAPPING_VERSION,
            supported_labels=(),
            unavailable_reason="PRODUCT_POLICY_NOT_VALIDATED",
        )

    def decide(self, runtime_result: object) -> AnalysisDecision:
        raise ModelNotReadyError("The V1 B product decision policy is not validated")


@dataclass(frozen=True)
class _AnalysisClaim:
    analysis_id: UUID
    baby_id: UUID
    episode_id: UUID
    audio_id: UUID
    attempt_no: int
    execution_token: UUID
    request_path: str
    client_request_id: UUID
    bucket: str
    object_key: str
    expected_bytes: int
    expected_checksum_sha256: str
    sample_rate_hz: int
    channels: int
    quality_reasons: tuple[str, ...]
    data_origin: str


class PostgresAnalysisService(PostgresAudioService):
    """B-05 audio intake plus the B-06 durable analysis state machine."""

    def __init__(
        self,
        database_url: str,
        *,
        invite_base_url: str,
        proof_secret: str,
        child_data_production_enabled: bool,
        storage: AudioStoragePort,
        decoder: AudioDecoder,
        model_runtime: ModelRuntimeManager,
        analysis_policy: AnalysisPolicy,
        request_timeout_seconds: float = ANALYSIS_REQUEST_SECONDS,
        lease_seconds: int = ANALYSIS_LEASE_SECONDS,
        execution_checkpoint: Callable[[str, UUID], None] | None = None,
        min_pool_size: int = 0,
        max_pool_size: int = 10,
        pool_timeout_seconds: float = 3.0,
    ) -> None:
        super().__init__(
            database_url,
            invite_base_url=invite_base_url,
            proof_secret=proof_secret,
            child_data_production_enabled=child_data_production_enabled,
            storage=storage,
            decoder=decoder,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
            pool_timeout_seconds=pool_timeout_seconds,
        )
        if request_timeout_seconds <= 0 or request_timeout_seconds > ANALYSIS_REQUEST_SECONDS:
            raise ValueError("analysis request timeout must be in (0, 45]")
        if lease_seconds != ANALYSIS_LEASE_SECONDS:
            raise ValueError("analysis lease must remain 60 seconds")
        self._model_runtime = model_runtime
        self._analysis_policy = analysis_policy
        self._request_timeout_seconds = request_timeout_seconds
        self._lease_seconds = lease_seconds
        self._execution_checkpoint = execution_checkpoint

    @property
    def product_analysis_available(self) -> bool:
        return (
            self._storage.configured
            and self._model_runtime.ready
            and self._analysis_policy.info.product_ready
        )

    async def startup_recover(self) -> int:
        """Fence expired RUNNING rows before the application accepts traffic."""

        try:
            async with self._pool.connection() as connection, connection.transaction():
                await connection.execute("set local role baby_app")
                cursor = await connection.execute(
                    "select baby_private.expire_analysis_leases() as expired"
                )
                row = await cursor.fetchone()
                return 0 if row is None else cast(int, row["expired"])
        except (psycopg.Error, PoolClosed, PoolTimeout) as exc:
            raise ApiException.service_unavailable() from exc

    @staticmethod
    def _analysis_select() -> str:
        return """
            select n.analysis_id, n.baby_id, n.episode_id, n.audio_id,
                   n.created_by_user_id, n.status::text as analysis_status,
                   n.stage::text as analysis_stage, n.attempt_no, n.lease_expires_at,
                   n.quality_status::text as quality_status,
                   n.quality_reasons as analysis_quality_reasons, n.cry_detected,
                   n.audio_candidates, n.abstain_reason, n.failure, n.model_version,
                   n.preprocess_version, n.label_mapping_version,
                   n.inference_mode::text as inference_mode, n.inference_executed,
                   n.data_origin::text as analysis_data_origin,
                   n.recorded_at as analysis_recorded_at, n.completed_at,
                   c.context_snapshot_id, c.as_of as context_as_of,
                   c.known_at as context_known_at, c.record_refs as context_record_refs,
                   c.features as context_features, c.missing_fields as context_missing_fields,
                   r.recommendation_id, r.action_order as recommendation_actions,
                   r.policy_version as recommendation_policy_version,
                   r.supersedes_recommendation_id,
                   r.recorded_at as recommendation_recorded_at
              from baby_data.analyses n
              left join lateral (
                  select * from baby_data.context_snapshots c0
                   where c0.analysis_id = n.analysis_id
                   order by c0.recorded_at desc limit 1
              ) c on true
              left join lateral (
                  select * from baby_data.recommendations r0
                   where r0.analysis_id = n.analysis_id
                   order by r0.recorded_at desc limit 1
              ) r on true
        """

    async def _get_analysis_row(
        self, connection: DatabaseConnection, analysis_id: UUID
    ) -> DatabaseRow:
        cursor = await connection.execute(
            f"{self._analysis_select()} where n.analysis_id = %s", (analysis_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return row

    @staticmethod
    def _in_progress(analysis_id: UUID) -> ApiException:
        return ApiException(
            ErrorCode.ANALYSIS_IN_PROGRESS,
            "The analysis is already running.",
            details=ErrorDetails.empty().model_copy(
                update={
                    "existing_analysis_id": analysis_id,
                    "status_url": f"/v1/analyses/{analysis_id}",
                }
            ),
        )

    @staticmethod
    def _model_not_ready(reason: str | None) -> ApiException:
        message = "The audio model is not approved for product analysis."
        if reason is None:
            message = "The audio model is not ready."
        return ApiException(
            ErrorCode.MODEL_NOT_READY,
            message,
            retryable=reason is None,
            details=ErrorDetails.empty().model_copy(
                update={"retry_after_seconds": 5 if reason is None else None}
            ),
        )

    def capabilities(
        self,
        *,
        normalizer_available: bool,
        normalizer_unavailable_reason: NormalizerUnavailableReason | None,
    ) -> Capabilities:
        audio_ready = self._storage.configured and self._decoder.configured
        info = self._analysis_policy.info
        runtime_exposed = self._model_runtime.enabled and self._model_runtime.ready
        return Capabilities(
            audio_model=ModelInfo(
                available=self.product_analysis_available,
                model_version=info.model_version if runtime_exposed else None,
                preprocess_version=info.preprocess_version if runtime_exposed else None,
                label_mapping_version=(info.label_mapping_version if runtime_exposed else None),
                supported_labels=(
                    list(info.supported_labels) if self.product_analysis_available else []
                ),
                inference_mode=info.inference_mode if runtime_exposed else "STUB",
            ),
            supported_mime_types=list(SUPPORTED_MIME_TYPES) if audio_ready else [],
            normalizer_available=normalizer_available,
            normalizer_unavailable_reason=normalizer_unavailable_reason,
            automatic_detection_supported=False,
            detector=DetectorInfo(
                available=False,
                execution_mode="STUB",
                model_version=None,
                model_asset_url=None,
                weights_sha256=None,
                input_sample_rate_hz=None,
                policy_version=None,
                supported_clients=[],
            ),
        )

    async def _load_input_for_claim(
        self,
        connection: DatabaseConnection,
        principal: AuthenticatedPrincipal,
        *,
        episode_id: UUID,
        audio_id: UUID,
        lock: bool,
    ) -> DatabaseRow:
        # PostgreSQL cannot lock the nullable side of this LEFT JOIN. Locking
        # the episode and audio row is sufficient to serialize claim/delete;
        # derivative identity is captured and checked again before inference.
        lock_clause = "for update of e, a" if lock else ""
        cursor = await connection.execute(
            f"""
            select e.episode_id, e.baby_id, e.status::text as episode_status,
                   a.audio_id, a.status::text as audio_status,
                   a.data_origin::text as data_origin, a.quality_reasons,
                   d.derivative_id, d.kind, d.bucket_id, d.object_key,
                   d.mime_type, d.bytes as derivative_bytes,
                   d.checksum_sha256 as derivative_checksum_sha256,
                   d.sample_rate_hz, d.channels, d.preprocessing_boundary_version,
                   d.status as derivative_status
              from baby_data.episodes e
              join baby_data.audio_assets a
                on a.baby_id = e.baby_id and a.episode_id = e.episode_id
              left join baby_data.audio_derivatives d
                on d.baby_id = a.baby_id and d.audio_id = a.audio_id
               and d.kind = 'PCM_S16LE_SOURCE_RATE'
             where e.episode_id = %s and a.audio_id = %s
             {lock_clause}
            """,
            (episode_id, audio_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        await self._ensure_processing_allowed(
            connection,
            principal,
            baby_id=cast(UUID, row["baby_id"]),
            data_origin=cast(str, row["data_origin"]),
        )
        audio_status = cast(str, row["audio_status"])
        if audio_status == "DELETING":
            raise ApiException(ErrorCode.RESOURCE_DELETING, "The audio is being deleted.")
        if audio_status == "DELETED":
            raise ApiException(ErrorCode.RESOURCE_DELETED, "The audio was deleted.")
        if audio_status != "READY":
            raise ApiException(ErrorCode.INVALID_AUDIO, "Only READY audio can be analyzed.")
        if row["episode_status"] not in {"OPEN", "CLOSED"}:
            raise ApiException(ErrorCode.INVALID_STATE, "The episode is not available.")
        if row["derivative_id"] is None or row["derivative_status"] != "READY":
            raise ApiException(ErrorCode.INVALID_AUDIO, "The READY PCM derivative is unavailable.")
        if (
            row["kind"] != "PCM_S16LE_SOURCE_RATE"
            or row["mime_type"] != "audio/wav"
            or row["preprocessing_boundary_version"] != PREPROCESSING_BOUNDARY_VERSION
        ):
            raise ApiException(ErrorCode.INVALID_AUDIO, "The PCM derivative boundary is invalid.")
        expected_prefix = f"{row['baby_id']}/{row['audio_id']}/derived/"
        if row["bucket_id"] != "baby-audio" or not cast(str, row["object_key"]).startswith(
            expected_prefix
        ):
            raise ApiException(ErrorCode.INVALID_AUDIO, "The PCM derivative path is invalid.")
        return row

    async def _claim_create(
        self,
        principal: AuthenticatedPrincipal,
        episode_id: UUID,
        request: CreateAnalysis,
        *,
        path: str,
    ) -> _AnalysisClaim | Analysis:
        payload = request.model_dump(mode="json")
        async with self.transaction(principal) as connection:
            source = await self._load_input_for_claim(
                connection,
                principal,
                episode_id=episode_id,
                audio_id=request.audio_id,
                lock=False,
            )
            if not self.product_analysis_available:
                raise self._model_not_ready(self._analysis_policy.info.unavailable_reason)
            await self._lock_shared_change_feed(connection, cast(UUID, source["baby_id"]))
            source = await self._load_input_for_claim(
                connection,
                principal,
                episode_id=episode_id,
                audio_id=request.audio_id,
                lock=True,
            )
            await self._advisory_lock(connection, f"analysis:{request.analysis_id}")
            try:
                replay = await self._reserve_idempotency(
                    connection,
                    principal,
                    method="POST",
                    path=path,
                    key=request.client_request_id,
                    payload=payload,
                )
            except ApiException as exc:
                if exc.code == ErrorCode.OPERATION_IN_PROGRESS:
                    raise self._in_progress(request.analysis_id) from exc
                raise
            if replay is not None:
                return self._analysis(
                    await self._get_analysis_row(connection, cast(UUID, replay[1]))
                )

            cursor = await connection.execute(
                """
                select analysis_id, episode_id, audio_id, status::text as status
                  from baby_data.analyses
                 where analysis_id = %s or audio_id = %s
                 for update
                """,
                (request.analysis_id, request.audio_id),
            )
            existing = await cursor.fetchone()
            if existing is not None:
                existing_id = cast(UUID, existing["analysis_id"])
                if (
                    existing_id != request.analysis_id
                    or existing["episode_id"] != episode_id
                    or existing["audio_id"] != request.audio_id
                ):
                    raise ApiException(
                        ErrorCode.IDEMPOTENCY_KEY_REUSED,
                        "The analysis identifier is already bound to another input.",
                        details=ErrorDetails.empty().model_copy(
                            update={"existing_analysis_id": existing_id}
                        ),
                    )
                if existing["status"] == "RUNNING":
                    raise self._in_progress(existing_id)
                await self._complete_idempotency(
                    connection,
                    principal,
                    method="POST",
                    path=path,
                    key=request.client_request_id,
                    result_type="ANALYSIS",
                    result_id=existing_id,
                    response_status=200,
                )
                return self._analysis(await self._get_analysis_row(connection, existing_id))

            token = uuid4()
            info = self._analysis_policy.info
            await connection.execute(
                """
                insert into baby_data.analyses (
                    analysis_id, baby_id, episode_id, audio_id, created_by_user_id,
                    status, stage, attempt_no, quality_status, quality_reasons,
                    model_version, preprocess_version, label_mapping_version,
                    inference_mode, inference_executed, data_origin
                ) values (
                    %s, %s, %s, %s, %s, 'READY', 'READY', 1, 'PASS', %s,
                    %s, %s, %s, %s, false, %s
                )
                """,
                (
                    request.analysis_id,
                    source["baby_id"],
                    episode_id,
                    request.audio_id,
                    principal.user_id,
                    source["quality_reasons"],
                    info.model_version,
                    info.preprocess_version,
                    info.label_mapping_version,
                    info.inference_mode,
                    source["data_origin"],
                ),
            )
            await connection.execute(
                """
                update baby_data.analyses
                   set status = 'RUNNING', stage = 'QUALITY_CHECK', execution_token = %s,
                       lease_expires_at = clock_timestamp() + %s * interval '1 second'
                 where analysis_id = %s and status = 'READY'
                """,
                (token, self._lease_seconds, request.analysis_id),
            )
            await connection.execute(
                """
                insert into baby_data.analysis_execution_attempts (
                    baby_id, analysis_id, attempt_no, client_request_id, request_path,
                    requested_by_user_id, request_session_id, request_issued_at,
                    execution_token, status, lease_expires_at
                ) values (%s, %s, 1, %s, %s, %s, %s, %s, %s, 'RUNNING',
                          clock_timestamp() + %s * interval '1 second')
                """,
                (
                    source["baby_id"],
                    request.analysis_id,
                    request.client_request_id,
                    path,
                    principal.user_id,
                    principal.session_id,
                    principal.issued_at,
                    token,
                    self._lease_seconds,
                ),
            )
            await self._record_shared_changes(
                connection,
                baby_id=cast(UUID, source["baby_id"]),
                changes=[("ANALYSIS", request.analysis_id, 1, False)],
            )
            return self._claim_from_source(
                source,
                analysis_id=request.analysis_id,
                attempt_no=1,
                token=token,
                path=path,
                client_request_id=request.client_request_id,
            )

    @staticmethod
    def _claim_from_source(
        source: DatabaseRow,
        *,
        analysis_id: UUID,
        attempt_no: int,
        token: UUID,
        path: str,
        client_request_id: UUID,
    ) -> _AnalysisClaim:
        return _AnalysisClaim(
            analysis_id=analysis_id,
            baby_id=cast(UUID, source["baby_id"]),
            episode_id=cast(UUID, source["episode_id"]),
            audio_id=cast(UUID, source["audio_id"]),
            attempt_no=attempt_no,
            execution_token=token,
            request_path=path,
            client_request_id=client_request_id,
            bucket=cast(str, source["bucket_id"]),
            object_key=cast(str, source["object_key"]),
            expected_bytes=cast(int, source["derivative_bytes"]),
            expected_checksum_sha256=cast(str, source["derivative_checksum_sha256"]),
            sample_rate_hz=cast(int, source["sample_rate_hz"]),
            channels=cast(int, source["channels"]),
            quality_reasons=tuple(cast(list[str], source["quality_reasons"])),
            data_origin=cast(str, source["data_origin"]),
        )

    async def _claim_retry(
        self,
        principal: AuthenticatedPrincipal,
        analysis_id: UUID,
        request: RetryAnalysis,
        *,
        path: str,
    ) -> _AnalysisClaim | Analysis:
        payload = request.model_dump(mode="json")
        async with self.transaction(principal) as connection:
            existing = await self._get_analysis_row(connection, analysis_id)
            source = await self._load_input_for_claim(
                connection,
                principal,
                episode_id=cast(UUID, existing["episode_id"]),
                audio_id=cast(UUID, existing["audio_id"]),
                lock=False,
            )
            if not self.product_analysis_available:
                raise self._model_not_ready(self._analysis_policy.info.unavailable_reason)
            await self._lock_shared_change_feed(connection, cast(UUID, existing["baby_id"]))
            source = await self._load_input_for_claim(
                connection,
                principal,
                episode_id=cast(UUID, existing["episode_id"]),
                audio_id=cast(UUID, existing["audio_id"]),
                lock=True,
            )
            cursor = await connection.execute(
                """
                select status::text as status, attempt_no, change_version
                  from baby_data.analyses where analysis_id = %s for update
                """,
                (analysis_id,),
            )
            current = await cursor.fetchone()
            if current is None:
                raise self._not_found() from None
            try:
                replay = await self._reserve_idempotency(
                    connection,
                    principal,
                    method="POST",
                    path=path,
                    key=request.client_request_id,
                    payload=payload,
                )
            except ApiException as exc:
                if exc.code == ErrorCode.OPERATION_IN_PROGRESS:
                    raise self._in_progress(analysis_id) from exc
                raise
            if replay is not None:
                return self._analysis(await self._get_analysis_row(connection, analysis_id))
            if current["status"] == "RUNNING":
                raise self._in_progress(analysis_id)
            if current["status"] != "FAILED":
                raise ApiException(ErrorCode.INVALID_STATE, "Only FAILED analysis can be retried.")
            if current["attempt_no"] != request.expected_attempt:
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The analysis attempt changed before retry.",
                    details=ErrorDetails.empty().model_copy(
                        update={"current_version": current["attempt_no"]}
                    ),
                )
            token = uuid4()
            attempt = cast(int, current["attempt_no"]) + 1
            cursor = await connection.execute(
                """
                update baby_data.analyses
                   set status = 'RUNNING', stage = 'QUALITY_CHECK', attempt_no = %s,
                       execution_token = %s,
                       lease_expires_at = clock_timestamp() + %s * interval '1 second',
                       quality_status = 'PASS', quality_reasons = %s,
                       cry_detected = null, audio_candidates = '[]'::jsonb,
                       abstain_reason = null, failure = null,
                       inference_executed = false, completed_at = null,
                       change_version = change_version + 1
                 where analysis_id = %s
                 returning change_version
                """,
                (
                    attempt,
                    token,
                    self._lease_seconds,
                    source["quality_reasons"],
                    analysis_id,
                ),
            )
            change_version = (await cursor.fetchone())["change_version"]  # type: ignore[index]
            await connection.execute(
                """
                insert into baby_data.analysis_execution_attempts (
                    baby_id, analysis_id, attempt_no, client_request_id, request_path,
                    requested_by_user_id, request_session_id, request_issued_at,
                    execution_token, status, lease_expires_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'RUNNING',
                          clock_timestamp() + %s * interval '1 second')
                """,
                (
                    existing["baby_id"],
                    analysis_id,
                    attempt,
                    request.client_request_id,
                    path,
                    principal.user_id,
                    principal.session_id,
                    principal.issued_at,
                    token,
                    self._lease_seconds,
                ),
            )
            await self._record_shared_changes(
                connection,
                baby_id=cast(UUID, existing["baby_id"]),
                changes=[("ANALYSIS", analysis_id, change_version, False)],
            )
            return self._claim_from_source(
                source,
                analysis_id=analysis_id,
                attempt_no=attempt,
                token=token,
                path=path,
                client_request_id=request.client_request_id,
            )

    @staticmethod
    def _verify_pcm_file(path: Path, claim: _AnalysisClaim) -> None:
        try:
            with wave.open(str(path), "rb") as audio:
                if (
                    audio.getsampwidth() != 2
                    or audio.getframerate() != claim.sample_rate_hz
                    or audio.getnchannels() != claim.channels
                    or audio.getcomptype() != "NONE"
                ):
                    raise ValueError("PCM metadata mismatch")
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            raise StorageError("DERIVATIVE_INTEGRITY_FAILED", retryable=False) from exc

    async def _advance_to_inference(
        self, principal: AuthenticatedPrincipal, claim: _AnalysisClaim
    ) -> bool:
        try:
            async with self.transaction(principal) as connection:
                await self._lock_shared_change_feed(connection, claim.baby_id)
                source = await self._load_input_for_claim(
                    connection,
                    principal,
                    episode_id=claim.episode_id,
                    audio_id=claim.audio_id,
                    lock=True,
                )
                if (
                    source["object_key"] != claim.object_key
                    or source["derivative_checksum_sha256"] != claim.expected_checksum_sha256
                ):
                    raise StorageError("DERIVATIVE_INTEGRITY_FAILED", retryable=False)
                cursor = await connection.execute(
                    """
                    update baby_data.analyses
                       set stage = 'INFERENCE', change_version = change_version + 1
                     where analysis_id = %s and status = 'RUNNING'
                       and attempt_no = %s and execution_token = %s
                       and lease_expires_at > clock_timestamp()
                    returning change_version
                    """,
                    (claim.analysis_id, claim.attempt_no, claim.execution_token),
                )
                row = await cursor.fetchone()
                if row is None:
                    return False
                await self._record_shared_changes(
                    connection,
                    baby_id=claim.baby_id,
                    changes=[("ANALYSIS", claim.analysis_id, row["change_version"], False)],
                )
                return True
        except ApiException:
            await self._fence_if_unavailable(claim)
            raise

    @staticmethod
    def _validate_decision(decision: AnalysisDecision) -> None:
        if decision.status == "COMPLETE":
            if (
                decision.quality_status != "PASS"
                or decision.cry_detected is not True
                or not 1 <= len(decision.candidates) <= 3
                or decision.abstain_reason is not None
                or decision.recommendation is None
                or not decision.recommendation.actions
            ):
                raise ValueError("COMPLETE decision violates the analysis contract")
            ranks = [candidate.rank for candidate in decision.candidates]
            if ranks != list(range(1, len(ranks) + 1)):
                raise ValueError("Candidate ranks must be contiguous")
        elif (
            decision.candidates
            or decision.abstain_reason is None
            or decision.recommendation is not None
        ):
            raise ValueError("ABSTAIN decision violates the analysis contract")

    async def _finish_terminal(
        self,
        principal: AuthenticatedPrincipal,
        claim: _AnalysisClaim,
        *,
        decision: AnalysisDecision | None,
        failure: Failure | None,
        inference_executed: bool,
    ) -> Analysis | None:
        try:
            response_error: ApiException | None = None
            result: Analysis | None = None
            async with self.transaction(principal) as connection:
                await self._lock_shared_change_feed(connection, claim.baby_id)
                await self._ensure_processing_allowed(
                    connection,
                    principal,
                    baby_id=claim.baby_id,
                    data_origin=claim.data_origin,
                )
                cursor = await connection.execute(
                    """
                    select n.status::text as status, n.attempt_no, n.execution_token,
                           a.status::text as audio_status, d.status as derivative_status,
                           coalesce((
                               select c.status::text from baby_data.consents c
                                where c.baby_id = n.baby_id
                                  and c.scope = 'SERVICE_PROCESSING'
                                order by c.version desc, c.recorded_at desc limit 1
                           ), 'NOT_GRANTED') as processing_consent
                      from baby_data.analyses n
                      join baby_data.audio_assets a on a.audio_id = n.audio_id
                      left join baby_data.audio_derivatives d
                        on d.audio_id = n.audio_id and d.kind = 'PCM_S16LE_SOURCE_RATE'
                     where n.analysis_id = %s
                     for update of n, a
                    """,
                    (claim.analysis_id,),
                )
                current = await cursor.fetchone()
                if current is None:
                    raise self._not_found()
                if (
                    current["status"] != "RUNNING"
                    or current["attempt_no"] != claim.attempt_no
                    or current["execution_token"] != claim.execution_token
                ):
                    return None
                access_failure: Failure | None = None
                if current["audio_status"] != "READY" or current["derivative_status"] != "READY":
                    access_failure = Failure(
                        code="SOURCE_DELETED",
                        message="The analysis input is no longer available.",
                        retryable=False,
                    )
                elif current["processing_consent"] != "GRANTED":
                    access_failure = Failure(
                        code="ACCESS_REVOKED",
                        message="Access was revoked before analysis completed.",
                        retryable=False,
                    )
                effective_failure = access_failure or failure
                values: tuple[
                    str,
                    str,
                    list[str],
                    bool | None,
                    str,
                    str | None,
                    str | None,
                ]
                if effective_failure is not None:
                    values = (
                        "FAILED",
                        "INVALID" if effective_failure.code == "SOURCE_DELETED" else "PASS",
                        list(claim.quality_reasons),
                        None,
                        "[]",
                        None,
                        effective_failure.model_dump_json(),
                    )
                else:
                    assert decision is not None
                    quality_reasons = list(
                        dict.fromkeys(
                            (
                                *claim.quality_reasons,
                                *(reason.value for reason in decision.quality_reasons),
                            )
                        )
                    )
                    values = (
                        decision.status,
                        decision.quality_status,
                        quality_reasons,
                        decision.cry_detected,
                        json.dumps(
                            [item.model_dump(mode="json") for item in decision.candidates],
                            separators=(",", ":"),
                        ),
                        decision.abstain_reason,
                        None,
                    )
                cursor = await connection.execute(
                    """
                    update baby_data.analyses
                       set stage = 'PERSISTING'
                     where analysis_id = %s and status = 'RUNNING'
                       and attempt_no = %s and execution_token = %s
                    returning baby_id
                    """,
                    (claim.analysis_id, claim.attempt_no, claim.execution_token),
                )
                if await cursor.fetchone() is None:
                    return None
                if effective_failure is None and decision is not None and decision.recommendation:
                    await connection.execute(
                        """
                        insert into baby_data.recommendations (
                            baby_id, analysis_id, context_snapshot_id, action_order,
                            evidence_refs, policy_version
                        ) values (%s, %s, null, %s::jsonb, '[]'::jsonb, %s)
                        """,
                        (
                            claim.baby_id,
                            claim.analysis_id,
                            json.dumps(
                                [
                                    action.model_dump(mode="json")
                                    for action in decision.recommendation.actions
                                ],
                                separators=(",", ":"),
                            ),
                            decision.recommendation.policy_version,
                        ),
                    )
                cursor = await connection.execute(
                    """
                    update baby_data.analyses
                       set status = %s, stage = 'FINISHED', lease_expires_at = null,
                           execution_token = null, quality_status = %s,
                           quality_reasons = %s, cry_detected = %s,
                           audio_candidates = %s::jsonb, abstain_reason = %s,
                           failure = %s::jsonb, inference_executed = %s,
                           completed_at = clock_timestamp(),
                           change_version = change_version + 1
                     where analysis_id = %s and status = 'RUNNING'
                       and attempt_no = %s and execution_token = %s
                    returning change_version
                    """,
                    (
                        *values,
                        inference_executed,
                        claim.analysis_id,
                        claim.attempt_no,
                        claim.execution_token,
                    ),
                )
                updated = await cursor.fetchone()
                if updated is None:
                    return None
                await connection.execute(
                    """
                    update baby_data.analysis_execution_attempts
                       set status = %s, lease_expires_at = null,
                           failure = %s::jsonb, completed_at = clock_timestamp()
                     where analysis_id = %s and attempt_no = %s
                       and execution_token = %s and status = 'RUNNING'
                    """,
                    (
                        values[0],
                        values[6],
                        claim.analysis_id,
                        claim.attempt_no,
                        claim.execution_token,
                    ),
                )
                await self._complete_idempotency(
                    connection,
                    principal,
                    method="POST",
                    path=claim.request_path,
                    key=claim.client_request_id,
                    result_type="ANALYSIS",
                    result_id=claim.analysis_id,
                    response_status=200,
                )
                await self._record_shared_changes(
                    connection,
                    baby_id=claim.baby_id,
                    changes=[("ANALYSIS", claim.analysis_id, updated["change_version"], False)],
                )
                await connection.execute(
                    "select baby_private.enqueue_audio_cleanup_after_analysis(%s)",
                    (claim.audio_id,),
                )
                result = self._analysis(await self._get_analysis_row(connection, claim.analysis_id))
                if access_failure is not None:
                    if access_failure.code == "SOURCE_DELETED":
                        if current["audio_status"] == "DELETED":
                            response_error = ApiException(
                                ErrorCode.RESOURCE_DELETED, "The analysis input was deleted."
                            )
                        else:
                            response_error = ApiException(
                                ErrorCode.RESOURCE_DELETING,
                                "The analysis input is being deleted.",
                            )
                    else:
                        response_error = ApiException(
                            ErrorCode.CONSENT_REQUIRED,
                            "Current processing consent is required.",
                        )
            if response_error is not None:
                raise response_error
            return result
        except ApiException:
            await self._fence_if_unavailable(claim)
            raise

    async def _fence_if_unavailable(self, claim: _AnalysisClaim) -> bool:
        try:
            async with self._pool.connection() as connection, connection.transaction():
                await connection.execute("set local role baby_app")
                cursor = await connection.execute(
                    "select baby_private.fence_unavailable_analysis(%s, %s, %s) as fenced",
                    (claim.analysis_id, claim.attempt_no, claim.execution_token),
                )
                row = await cursor.fetchone()
                return row is not None and row["fenced"] is True
        except (psycopg.Error, PoolClosed, PoolTimeout):
            return False

    @staticmethod
    def _cleanup_temp(path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)

    async def _execute_claim(
        self, principal: AuthenticatedPrincipal, claim: _AnalysisClaim
    ) -> Analysis:
        deadline = monotonic() + self._request_timeout_seconds
        temp_root = Path(tempfile.mkdtemp(prefix="baby-care-analysis-"))
        local_input = temp_root / "input.wav"
        execution: InferenceExecution | None = None
        cleanup_deferred = False
        try:
            if self._execution_checkpoint is not None:
                self._execution_checkpoint("before_download", claim.analysis_id)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            downloaded = await asyncio.wait_for(
                self._storage.download(
                    bucket=claim.bucket,
                    object_key=claim.object_key,
                    destination=local_input,
                ),
                timeout=remaining,
            )
            if (
                downloaded.bytes != claim.expected_bytes
                or downloaded.checksum_sha256 != claim.expected_checksum_sha256
                or local_input.stat().st_size != claim.expected_bytes
            ):
                raise StorageError("DERIVATIVE_INTEGRITY_FAILED", retryable=False)
            await asyncio.to_thread(self._verify_pcm_file, local_input, claim)
            if not await self._advance_to_inference(principal, claim):
                raise ApiException(
                    ErrorCode.RESOURCE_DELETING,
                    "The analysis input changed before inference.",
                )
            if self._execution_checkpoint is not None:
                self._execution_checkpoint("before_inference", claim.analysis_id)
            execution = self._model_runtime.submit_inference(local_input, deadline=deadline)
            execution.add_done_callback(lambda: self._cleanup_temp(temp_root))
            cleanup_deferred = True
            raw = await execution.wait_until(deadline)
            decision = self._analysis_policy.decide(raw)
            self._validate_decision(decision)
            result = await self._finish_terminal(
                principal,
                claim,
                decision=decision,
                failure=None,
                inference_executed=(
                    self._analysis_policy.info.inference_mode == "REAL" and execution.started
                ),
            )
            if result is None:
                raise self._not_found() from None
            return result
        except asyncio.CancelledError:
            await asyncio.shield(
                self._finish_terminal(
                    principal,
                    claim,
                    decision=None,
                    failure=Failure(
                        code="ANALYSIS_TIMEOUT",
                        message="The analysis request was cancelled.",
                        retryable=True,
                    ),
                    inference_executed=(
                        execution is not None
                        and execution.started
                        and self._analysis_policy.info.inference_mode == "REAL"
                    ),
                )
            )
            raise
        except (TimeoutError, InferenceDeadlineExceededError):
            result = await self._finish_terminal(
                principal,
                claim,
                decision=None,
                failure=Failure(
                    code="ANALYSIS_TIMEOUT",
                    message="The analysis did not finish within 45 seconds.",
                    retryable=True,
                ),
                inference_executed=(
                    execution is not None
                    and execution.started
                    and self._analysis_policy.info.inference_mode == "REAL"
                ),
            )
            if result is None:
                raise self._not_found() from None
            return result
        except ApiException:
            raise
        except (StorageError, ModelNotReadyError, ModelRuntimeBusyError, ValueError, RuntimeError):
            result = await self._finish_terminal(
                principal,
                claim,
                decision=None,
                failure=Failure(
                    code="INFERENCE_ERROR",
                    message="The audio analysis could not be completed.",
                    retryable=True,
                ),
                inference_executed=(
                    execution is not None
                    and execution.started
                    and self._analysis_policy.info.inference_mode == "REAL"
                ),
            )
            if result is None:
                raise self._not_found() from None
            return result
        finally:
            if not cleanup_deferred:
                await asyncio.to_thread(self._cleanup_temp, temp_root)

    async def create_analysis(
        self,
        principal: AuthenticatedPrincipal,
        episode_id: UUID,
        request: CreateAnalysis,
        *,
        path: str,
    ) -> Analysis:
        claimed = await self._claim_create(principal, episode_id, request, path=path)
        if isinstance(claimed, Analysis):
            return claimed
        return await self._execute_claim(principal, claimed)

    async def retry_analysis(
        self,
        principal: AuthenticatedPrincipal,
        analysis_id: UUID,
        request: RetryAnalysis,
        *,
        path: str,
    ) -> Analysis:
        claimed = await self._claim_retry(principal, analysis_id, request, path=path)
        if isinstance(claimed, Analysis):
            return claimed
        return await self._execute_claim(principal, claimed)

    async def get_analysis(self, principal: AuthenticatedPrincipal, analysis_id: UUID) -> Analysis:
        await self.startup_recover()
        async with self.transaction(principal) as connection:
            row = await self._get_analysis_row(connection, analysis_id)
            await self._ensure_processing_allowed(
                connection,
                principal,
                baby_id=cast(UUID, row["baby_id"]),
                data_origin=cast(str, row["analysis_data_origin"]),
            )
            return self._analysis(row)

from __future__ import annotations

import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolClosed, PoolTimeout

from baby_care_api.core.errors import ApiException
from baby_care_api.models.audio import (
    ActionGroup,
    Analysis,
    AudioAsset,
    AudioUpload,
    CancelUpload,
    Capabilities,
    CompleteUpload,
    ContextSnapshot,
    ContextValues,
    CreateEpisode,
    CreateUpload,
    DetectorInfo,
    Episode,
    EpisodeDetail,
    ModelInfo,
    NormalizerUnavailableReason,
    Outcome,
    Playback,
    Recommendation,
    RecommendedAction,
    ReissueUpload,
    StateObservation,
    UploadGrant,
)
from baby_care_api.models.b04 import ActionAttempt
from baby_care_api.models.errors import ErrorCode, ErrorDetails
from baby_care_api.services.audio_decoder import (
    SUPPORTED_MIME_TYPES,
    AudioDecodeError,
    AudioDecoder,
    DecodedAudio,
)
from baby_care_api.services.b04 import PostgresBabyCareService
from baby_care_api.services.security import AuthenticatedPrincipal
from baby_care_api.services.storage import AudioStoragePort, DownloadedObject, StorageError

type DatabaseRow = dict[str, Any]
type DatabaseConnection = psycopg.AsyncConnection[DatabaseRow]

STANDARD_UPLOAD_LIMIT = 6 * 1024 * 1024
UPLOAD_GRANT_MINUTES = 15
PLAYBACK_SECONDS = 60
RETENTION_DAYS = 7
NO_RETENTION_MAX_HOURS = 1
MAX_ACTIVE_UPLOADS_PER_USER = 3
MAX_DAILY_RESERVED_BYTES = 250_000_000
EPISODE_MAX_SECONDS = {"AUTO": 20, "MANUAL": 30, "FILE": 60}


@dataclass(frozen=True)
class VerificationClaim:
    run_token: UUID
    upload_id: UUID
    audio_id: UUID
    baby_id: UUID
    episode_id: UUID
    episode_source: str
    bucket: str
    object_key: str
    declared_mime_type: str
    expected_bytes: int
    declared_checksum_sha256: str | None
    data_origin: str


@dataclass(frozen=True)
class VerificationMaterial:
    downloaded: DownloadedObject
    decoded: DecodedAudio | None
    derivative_id: UUID | None
    derivative_key: str | None


class PostgresAudioService(PostgresBabyCareService):
    """B-05 service layered on the B-04 request context and change feed."""

    def __init__(
        self,
        database_url: str,
        *,
        invite_base_url: str,
        proof_secret: str,
        child_data_production_enabled: bool,
        storage: AudioStoragePort,
        decoder: AudioDecoder,
        min_pool_size: int = 0,
        max_pool_size: int = 10,
        pool_timeout_seconds: float = 3.0,
    ) -> None:
        super().__init__(
            database_url,
            invite_base_url=invite_base_url,
            proof_secret=proof_secret,
            child_data_production_enabled=child_data_production_enabled,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
            pool_timeout_seconds=pool_timeout_seconds,
        )
        self._storage = storage
        self._decoder = decoder

    @staticmethod
    def _episode(row: DatabaseRow) -> Episode:
        return Episode(
            episode_id=row["episode_id"],
            baby_id=row["baby_id"],
            created_by_user_id=row["created_by_user_id"],
            status=row["status"],
            source=row["source"],
            timing_status=row["timing_status"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            closed_reason=row["closed_reason"],
            observation_session_id=row["observation_session_id"],
            data_origin=row["data_origin"],
            version=row["version"],
            recorded_at=row["recorded_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _audio(row: DatabaseRow) -> AudioAsset:
        return AudioAsset(
            audio_id=row["audio_id"],
            episode_id=row["episode_id"],
            baby_id=row["baby_id"],
            created_by_user_id=row["created_by_user_id"],
            mime_type=row["mime_type"],
            bytes=row["bytes"],
            duration_seconds=row["duration_seconds"],
            checksum_sha256=row["checksum_sha256"],
            status=row["status"],
            retention_until=row["retention_until"],
            rejection_code=row["rejection_code"],
            data_origin=row["data_origin"],
            version=row["version"],
            recorded_at=row["recorded_at"],
            updated_at=row["updated_at"],
            quality_reasons=row["quality_reasons"],
        )

    def _grant(self, row: DatabaseRow) -> UploadGrant:
        return UploadGrant(
            upload_id=row["upload_id"],
            audio_id=row["audio_id"],
            bucket=row["bucket_id"],
            object_key=row["object_key"],
            method=row["method"],
            upload_endpoint=self._storage.upload_endpoint(
                method=row["method"],
                bucket=row["bucket_id"],
                object_key=row["object_key"],
            ),
            expires_at=row["expires_at"],
            max_bytes=row["max_bytes"],
        )

    @staticmethod
    def _episode_select() -> str:
        return """
            select episode_id, baby_id, created_by_user_id, status::text as status,
                   source::text as source, timing_status::text as timing_status,
                   started_at, ended_at, closed_reason, observation_session_id,
                   data_origin::text as data_origin, version, recorded_at, updated_at
              from baby_data.episodes
        """

    @staticmethod
    def _audio_select() -> str:
        return """
            select audio_id, episode_id, baby_id, created_by_user_id, mime_type,
                   bytes, duration_seconds, checksum_sha256, status::text as status,
                   retention_until, rejection_code, data_origin::text as data_origin,
                   version, recorded_at, updated_at, quality_reasons
              from baby_data.audio_assets
        """

    async def _get_episode_row(
        self, connection: DatabaseConnection, episode_id: UUID | None
    ) -> DatabaseRow:
        cursor = await connection.execute(
            f"{self._episode_select()} where episode_id = %s", (episode_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return row

    async def _get_audio_row(
        self, connection: DatabaseConnection, audio_id: UUID | None
    ) -> DatabaseRow:
        cursor = await connection.execute(
            f"{self._audio_select()} where audio_id = %s", (audio_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return row

    async def _get_audio_upload(
        self, connection: DatabaseConnection, upload_id: UUID | None
    ) -> AudioUpload:
        cursor = await connection.execute(
            """
            select a.audio_id, a.episode_id, a.baby_id, a.created_by_user_id,
                   a.mime_type, a.bytes, a.duration_seconds, a.checksum_sha256,
                   a.status::text as status, a.retention_until, a.rejection_code,
                   a.data_origin::text as data_origin, a.version, a.recorded_at,
                   a.updated_at, a.quality_reasons,
                   g.upload_id, g.bucket_id, g.object_key, g.method::text as method,
                   g.expires_at, g.max_bytes
              from baby_data.audio_upload_grants g
              join baby_data.audio_assets a on a.audio_id = g.audio_id
             where g.upload_id = %s
            """,
            (upload_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return AudioUpload(audio=self._audio(row), upload=self._grant(row))

    async def _ensure_processing_allowed(
        self,
        connection: DatabaseConnection,
        principal: AuthenticatedPrincipal,
        *,
        baby_id: UUID,
        data_origin: str,
    ) -> None:
        await self._baby_access(connection, baby_id, principal.user_id)
        cursor = await connection.execute(
            """
            select status::text as status
              from baby_data.consents
             where baby_id = %s and scope = 'SERVICE_PROCESSING'
             order by version desc, recorded_at desc limit 1
            """,
            (baby_id,),
        )
        consent = await cursor.fetchone()
        if consent is None or consent["status"] != "GRANTED":
            raise ApiException(
                ErrorCode.CONSENT_REQUIRED,
                "Current service-processing consent is required.",
            )
        if data_origin == "USER":
            cursor = await connection.execute(
                """
                select 1 as verified
                  from baby_data.guardian_verifications v
                  join baby_data.baby_memberships m
                    on m.baby_id = v.baby_id and m.user_id = v.subject_user_id
                 where v.baby_id = %s and v.status = 'VERIFIED'
                   and m.role = 'OWNER' and m.status = 'ACTIVE'
                 limit 1
                """,
                (baby_id,),
            )
            if not self._child_data_production_enabled or await cursor.fetchone() is None:
                raise ApiException(
                    ErrorCode.CHILD_DATA_VERIFICATION_REQUIRED,
                    "Approved guardian verification is required for USER audio.",
                )

    async def _retention_granted(self, connection: DatabaseConnection, baby_id: UUID) -> bool:
        cursor = await connection.execute(
            """
            select status::text as status
              from baby_data.consents
             where baby_id = %s and scope = 'AUDIO_RETENTION'
             order by version desc, recorded_at desc limit 1
            """,
            (baby_id,),
        )
        row = await cursor.fetchone()
        return row is not None and row["status"] == "GRANTED"

    async def _touch_episode(
        self, connection: DatabaseConnection, *, baby_id: UUID, episode_id: UUID
    ) -> int:
        cursor = await connection.execute(
            """
            update baby_data.episodes
               set version = version + 1
             where baby_id = %s and episode_id = %s
            returning version
            """,
            (baby_id, episode_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        await self._record_shared_changes(
            connection,
            baby_id=baby_id,
            changes=[("EPISODE", episode_id, row["version"], False)],
        )
        return cast(int, row["version"])

    async def create_episode(
        self,
        principal: AuthenticatedPrincipal,
        request: CreateEpisode,
        *,
        path: str,
    ) -> Episode:
        if (request.timing_status == "KNOWN") != (request.started_at is not None):
            raise ApiException(
                ErrorCode.VALIDATION_ERROR,
                "KNOWN timing requires started_at and UNKNOWN timing forbids it.",
            )
        if request.source == "AUTO" and request.observation_session_id is None:
            raise ApiException(
                ErrorCode.VALIDATION_ERROR,
                "AUTO episodes require an active observation session.",
            )
        if request.source != "AUTO" and request.observation_session_id is not None:
            raise ApiException(
                ErrorCode.VALIDATION_ERROR,
                "Only AUTO episodes may reference an observation session.",
            )
        payload = request.model_dump(mode="json")
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=payload,
            )
            if replay is not None:
                return self._episode(await self._get_episode_row(connection, replay[1]))
            await self._ensure_processing_allowed(
                connection,
                principal,
                baby_id=request.baby_id,
                data_origin=request.data_origin.value,
            )
            if request.source == "AUTO":
                cursor = await connection.execute(
                    """
                    select 1 as active
                      from baby_data.observation_sessions
                     where session_id = %s and baby_id = %s
                       and created_by_user_id = %s and status = 'ACTIVE'
                       and last_heartbeat_at > clock_timestamp() - interval '30 seconds'
                     for share
                    """,
                    (
                        request.observation_session_id,
                        request.baby_id,
                        principal.user_id,
                    ),
                )
                if await cursor.fetchone() is None:
                    raise ApiException(
                        ErrorCode.INVALID_STATE,
                        "The AUTO observation session is not active.",
                    )
            cursor = await connection.execute(
                """
                insert into baby_data.episodes (
                    baby_id, created_by_user_id, source, timing_status, started_at,
                    observation_session_id, data_origin
                ) values (%s, %s, %s, %s, %s, %s, %s)
                returning episode_id
                """,
                (
                    request.baby_id,
                    principal.user_id,
                    request.source.value,
                    request.timing_status.value,
                    request.started_at,
                    request.observation_session_id,
                    request.data_origin.value,
                ),
            )
            episode_id = (await cursor.fetchone())["episode_id"]  # type: ignore[index]
            await self._record_shared_changes(
                connection,
                baby_id=request.baby_id,
                changes=[("EPISODE", episode_id, 1, False)],
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="EPISODE",
                result_id=episode_id,
                response_status=201,
            )
            return self._episode(await self._get_episode_row(connection, episode_id))

    @staticmethod
    def _analysis(row: DatabaseRow) -> Analysis:
        context: ContextSnapshot | None = None
        if row.get("context_snapshot_id") is not None:
            features = row.get("context_features") or {}
            context = ContextSnapshot(
                context_snapshot_id=row["context_snapshot_id"],
                baby_id=row["baby_id"],
                as_of=row["context_as_of"],
                known_at=row["context_known_at"],
                record_refs=row.get("context_record_refs") or [],
                values=ContextValues(
                    last_feeding_at=features.get("last_feeding_at"),
                    last_sleep_started_at=features.get("last_sleep_started_at"),
                    last_sleep_ended_at=features.get("last_sleep_ended_at"),
                    last_diaper_event_at=features.get("last_diaper_event_at"),
                    minutes_since_last_feeding=features.get("minutes_since_last_feeding"),
                    current_sleep=features.get("current_sleep"),
                ),
                missing_fields=row.get("context_missing_fields") or [],
                reproduction_status="AVAILABLE",
            )
        recommendation: Recommendation | None = None
        if row.get("recommendation_id") is not None:
            raw_actions = row.get("recommendation_actions") or []
            actions = [
                RecommendedAction.model_validate(
                    item
                    if isinstance(item, dict)
                    else {"action_type": "OTHER", "text": str(item), "evidence_refs": []}
                )
                for item in raw_actions
            ]
            recommendation = Recommendation(
                recommendation_id=row["recommendation_id"],
                analysis_id=row["analysis_id"],
                context_snapshot_id=row["context_snapshot_id"],
                status="READY",
                actions=actions,
                optional_questions=[],
                help_action=None,
                policy_version=row["recommendation_policy_version"],
                supersedes_id=row["supersedes_recommendation_id"],
                recorded_at=row["recommendation_recorded_at"],
            )
        return Analysis(
            analysis_id=row["analysis_id"],
            baby_id=row["baby_id"],
            episode_id=row["episode_id"],
            audio_id=row["audio_id"],
            created_by_user_id=row["created_by_user_id"],
            status=row["analysis_status"],
            stage=row["analysis_stage"],
            attempt_no=row["attempt_no"],
            lease_expires_at=row["lease_expires_at"],
            quality_status=row["quality_status"],
            quality_reasons=row["analysis_quality_reasons"],
            cry_detected=row["cry_detected"],
            audio_candidates=row["audio_candidates"],
            abstain_reason=row["abstain_reason"],
            failure=row["failure"],
            model_version=row["model_version"],
            preprocess_version=row["preprocess_version"],
            label_mapping_version=row["label_mapping_version"],
            context_snapshot=context,
            recommendation=recommendation,
            inference_mode=row["inference_mode"],
            inference_executed=row["inference_executed"],
            data_origin=row["analysis_data_origin"],
            recorded_at=row["analysis_recorded_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _action(row: DatabaseRow) -> ActionAttempt:
        return ActionAttempt(
            action_id=row["action_id"],
            baby_id=row["baby_id"],
            episode_id=row["episode_id"],
            care_event_id=row["care_event_id"],
            recommendation_id=row["recommendation_id"],
            created_by_user_id=row["created_by_user_id"],
            performed_by_user_id=row["performed_by_user_id"],
            performed_at=row["performed_at"],
            sequence=row["sequence_no"],
            status=row["status"],
            followup_status=row["followup_status"],
            data_origin=row["data_origin"],
            version=row["version"],
            recorded_at=row["recorded_at"],
            updated_at=row["updated_at"],
        )

    async def get_episode(
        self, principal: AuthenticatedPrincipal, episode_id: UUID
    ) -> EpisodeDetail:
        async with self.transaction(principal) as connection:
            episode_row = await self._get_episode_row(connection, episode_id)
            await self._baby_access(connection, episode_row["baby_id"], principal.user_id)
            cursor = await connection.execute(
                f"{self._audio_select()} where episode_id = %s order by recorded_at",
                (episode_id,),
            )
            audio_rows = await cursor.fetchall()
            cursor = await connection.execute(
                """
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
                       c.known_at as context_known_at,
                       c.record_refs as context_record_refs,
                       c.features as context_features,
                       c.missing_fields as context_missing_fields,
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
                 where n.episode_id = %s
                 order by n.recorded_at
                """,
                (episode_id,),
            )
            analysis_rows = await cursor.fetchall()
            cursor = await connection.execute(
                """
                select action_id, baby_id, episode_id, care_event_id, recommendation_id,
                       created_by_user_id, performed_by_user_id, performed_at,
                       sequence_no, status::text as status,
                       followup_status::text as followup_status,
                       data_origin::text as data_origin, version, recorded_at, updated_at
                  from baby_data.action_attempts
                 where episode_id = %s and status <> 'DELETED'
                 order by sequence_no, recorded_at
                """,
                (episode_id,),
            )
            action_rows = await cursor.fetchall()
            cursor = await connection.execute(
                """
                select g.action_group_id, g.baby_id, g.episode_id,
                       g.source_entry_id as entry_id, g.version,
                       array_agg(m.action_id order by m.sequence_no) as action_ids
                  from baby_data.action_groups g
                  join baby_data.action_group_members m
                    on m.action_group_id = g.action_group_id
                 where g.episode_id = %s and g.source_entry_id is not null
                 group by g.action_group_id
                having count(*) >= 2
                 order by g.recorded_at
                """,
                (episode_id,),
            )
            group_rows = await cursor.fetchall()
            cursor = await connection.execute(
                """
                select o.outcome_id, o.baby_id, o.action_id, o.action_group_id,
                       o.observed_at, o.time_precision::text as time_precision,
                       case o.response
                           when 'PARTLY_CALMED' then 'PARTIALLY_CALMED'
                           when 'CRIED_AGAIN' then 'CRYING_AGAIN'
                           else o.response
                       end as response,
                       o.caregiver_interpretation, o.created_by_user_id,
                       o.updated_by_user_id, o.data_origin::text as data_origin,
                       o.version, o.recorded_at, o.updated_at
                  from baby_data.outcomes o
                  left join baby_data.action_attempts a on a.action_id = o.action_id
                  left join baby_data.action_groups g on g.action_group_id = o.action_group_id
                 where coalesce(a.episode_id, g.episode_id) = %s
                 order by o.recorded_at
                """,
                (episode_id,),
            )
            outcome_rows = await cursor.fetchall()
            cursor = await connection.execute(
                """
                select state_observation_id, baby_id, episode_id, action_id,
                       source_entry_id, phase::text as phase, observed_at,
                       time_precision::text as time_precision, state_codes,
                       observation_source, confirmation_status, visual_state_code,
                       visual_mapping_version, created_by_user_id,
                       data_origin::text as data_origin, version, recorded_at, updated_at
                  from baby_data.state_observations
                 where episode_id = %s
                 order by recorded_at
                """,
                (episode_id,),
            )
            observation_rows = await cursor.fetchall()

        return EpisodeDetail(
            episode=self._episode(episode_row),
            audio_assets=[self._audio(row) for row in audio_rows],
            analyses=[self._analysis(row) for row in analysis_rows],
            actions=[self._action(row) for row in action_rows],
            action_groups=[ActionGroup.model_validate(row) for row in group_rows],
            outcomes=[Outcome.model_validate(row) for row in outcome_rows],
            state_observations=[StateObservation.model_validate(row) for row in observation_rows],
        )

    @staticmethod
    def _normalize_mime(value: str) -> str:
        return value.lower().split(";", 1)[0].strip()

    def _require_storage(self) -> None:
        if not self._storage.configured:
            raise ApiException.service_unavailable()

    async def create_upload(
        self,
        principal: AuthenticatedPrincipal,
        episode_id: UUID,
        request: CreateUpload,
        *,
        path: str,
    ) -> AudioUpload:
        self._require_storage()
        declared_mime = self._normalize_mime(request.mime_type)
        if declared_mime not in SUPPORTED_MIME_TYPES:
            raise ApiException(
                ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                "The declared audio MIME type is not supported.",
            )
        payload = request.model_dump(mode="json")
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=payload,
            )
            if replay is not None:
                return await self._get_audio_upload(connection, replay[1])
            episode = await self._get_episode_row(connection, episode_id)
            if episode["status"] != "OPEN":
                raise ApiException(ErrorCode.INVALID_STATE, "The episode is not open.")
            await self._ensure_processing_allowed(
                connection,
                principal,
                baby_id=episode["baby_id"],
                data_origin=episode["data_origin"],
            )
            maximum_seconds = EPISODE_MAX_SECONDS[episode["source"]]
            if request.duration_seconds is not None and request.duration_seconds > maximum_seconds:
                raise ApiException(
                    ErrorCode.INVALID_AUDIO,
                    f"The declared duration exceeds the {maximum_seconds}-second episode limit.",
                )
            await self._advisory_lock(connection, f"audio-upload:{principal.user_id}")
            await connection.execute(
                """
                update baby_data.audio_upload_grants
                   set reservation_released_at = clock_timestamp()
                 where uploader_user_id = %s and reservation_released_at is null
                   and canceled_at is null and completed_at is null
                   and superseded_at is null and expires_at <= clock_timestamp()
                """,
                (principal.user_id,),
            )
            cursor = await connection.execute(
                """
                select count(*)::integer as active_count
                  from baby_data.audio_upload_grants
                 where uploader_user_id = %s and reservation_released_at is null
                   and canceled_at is null and completed_at is null
                   and superseded_at is null and expires_at > clock_timestamp()
                """,
                (principal.user_id,),
            )
            if (await cursor.fetchone())["active_count"] >= MAX_ACTIVE_UPLOADS_PER_USER:  # type: ignore[index]
                raise ApiException(
                    ErrorCode.RATE_LIMITED,
                    "Too many unfinished audio uploads.",
                    retryable=True,
                    details=ErrorDetails.empty().model_copy(
                        update={"retry_after_seconds": UPLOAD_GRANT_MINUTES * 60}
                    ),
                )
            cursor = await connection.execute(
                """
                insert into baby_data.audio_upload_daily_usage (user_id, usage_date)
                values (%s, current_date)
                on conflict (user_id, usage_date) do update
                    set updated_at = clock_timestamp()
                returning allocated_bytes
                """,
                (principal.user_id,),
            )
            usage = (await cursor.fetchone())["allocated_bytes"]  # type: ignore[index]
            if usage + request.bytes > MAX_DAILY_RESERVED_BYTES:
                raise ApiException(
                    ErrorCode.RATE_LIMITED,
                    "The daily audio reservation limit was reached.",
                    retryable=False,
                )

            audio_id = uuid4()
            upload_id = uuid4()
            object_key = f"{episode['baby_id']}/{audio_id}/{uuid4().hex}"
            method = (
                "TUS"
                if request.prefer_resumable or request.bytes > STANDARD_UPLOAD_LIMIT
                else "STANDARD"
            )
            cursor = await connection.execute(
                """
                insert into baby_data.audio_assets (
                    audio_id, baby_id, episode_id, created_by_user_id,
                    object_key, mime_type, bytes, status, data_origin,
                    declared_mime_type, declared_bytes, declared_duration_seconds,
                    declared_checksum_sha256
                ) values (%s, %s, %s, %s, %s, %s, 0, 'ALLOCATED', %s, %s, %s, %s, %s)
                returning audio_id
                """,
                (
                    audio_id,
                    episode["baby_id"],
                    episode_id,
                    principal.user_id,
                    object_key,
                    declared_mime,
                    episode["data_origin"],
                    declared_mime,
                    request.bytes,
                    request.duration_seconds,
                    request.checksum_sha256,
                ),
            )
            await cursor.fetchone()
            await connection.execute(
                """
                insert into baby_data.audio_upload_grants (
                    upload_id, baby_id, episode_id, audio_id, uploader_user_id,
                    auth_session_id, object_key, method, max_bytes, expires_at,
                    declared_mime_type, expected_bytes, declared_duration_seconds,
                    declared_checksum_sha256, reserved_bytes
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    clock_timestamp() + interval '15 minutes', %s, %s, %s, %s, %s
                )
                """,
                (
                    upload_id,
                    episode["baby_id"],
                    episode_id,
                    audio_id,
                    principal.user_id,
                    principal.session_id,
                    object_key,
                    method,
                    request.bytes,
                    declared_mime,
                    request.bytes,
                    request.duration_seconds,
                    request.checksum_sha256,
                    request.bytes,
                ),
            )
            await connection.execute(
                """
                update baby_data.audio_upload_daily_usage
                   set allocated_bytes = allocated_bytes + %s,
                       allocation_count = allocation_count + 1,
                       updated_at = clock_timestamp()
                 where user_id = %s and usage_date = current_date
                """,
                (request.bytes, principal.user_id),
            )
            await self._touch_episode(connection, baby_id=episode["baby_id"], episode_id=episode_id)
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="UPLOAD",
                result_id=upload_id,
                response_status=201,
            )
            return await self._get_audio_upload(connection, upload_id)

    async def reissue_upload(
        self,
        principal: AuthenticatedPrincipal,
        audio_id: UUID,
        request: ReissueUpload,
        *,
        path: str,
    ) -> AudioUpload:
        self._require_storage()
        payload = request.model_dump(mode="json")
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=payload,
            )
            if replay is not None:
                return await self._get_audio_upload(connection, replay[1])
            cursor = await connection.execute(
                """
                select a.audio_id, a.baby_id, a.episode_id, a.object_key,
                       a.status::text as status, a.version, a.data_origin::text as data_origin,
                       g.upload_id, g.method::text as method, g.max_bytes,
                       g.declared_mime_type, g.expected_bytes,
                       g.declared_duration_seconds, g.declared_checksum_sha256,
                       g.expires_at, g.canceled_at, g.completed_at, g.superseded_at
                  from baby_data.audio_assets a
                  join lateral (
                      select * from baby_data.audio_upload_grants g0
                       where g0.audio_id = a.audio_id
                       order by g0.recorded_at desc limit 1
                 ) g on true
                 where a.audio_id = %s and a.created_by_user_id = %s
                 for update of a
                """,
                (audio_id, principal.user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise self._not_found()
            if row["version"] != request.version:
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The audio changed before reissue.",
                    details=ErrorDetails.empty().model_copy(
                        update={"current_version": row["version"], "resource_type": "AUDIO"}
                    ),
                )
            if row["status"] != "ALLOCATED" or row["completed_at"] is not None:
                raise ApiException(ErrorCode.INVALID_STATE, "The audio cannot be reissued.")
            if (
                row["canceled_at"] is None
                and row["superseded_at"] is None
                and row["expires_at"] > datetime.now(UTC)
            ):
                raise ApiException(
                    ErrorCode.INVALID_STATE,
                    "The existing upload grant has not expired.",
                )
            await self._ensure_processing_allowed(
                connection,
                principal,
                baby_id=row["baby_id"],
                data_origin=row["data_origin"],
            )
            await self._advisory_lock(connection, f"audio-upload:{principal.user_id}")
            await connection.execute(
                """
                update baby_data.audio_upload_grants
                   set superseded_at = coalesce(superseded_at, clock_timestamp()),
                       reservation_released_at = coalesce(
                           reservation_released_at, clock_timestamp()
                       )
                 where upload_id = %s and completed_at is null
                """,
                (row["upload_id"],),
            )
            cursor = await connection.execute(
                """
                select count(*)::integer as active_count
                  from baby_data.audio_upload_grants
                 where uploader_user_id = %s and reservation_released_at is null
                   and canceled_at is null and completed_at is null
                   and superseded_at is null and expires_at > clock_timestamp()
                """,
                (principal.user_id,),
            )
            if (await cursor.fetchone())["active_count"] >= MAX_ACTIVE_UPLOADS_PER_USER:  # type: ignore[index]
                raise ApiException(
                    ErrorCode.RATE_LIMITED,
                    "Too many unfinished audio uploads.",
                    retryable=True,
                )
            upload_id = uuid4()
            await connection.execute(
                """
                insert into baby_data.audio_upload_grants (
                    upload_id, baby_id, episode_id, audio_id, uploader_user_id,
                    auth_session_id, object_key, method, max_bytes, expires_at,
                    declared_mime_type, expected_bytes, declared_duration_seconds,
                    declared_checksum_sha256, reserved_bytes
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    clock_timestamp() + interval '15 minutes', %s, %s, %s, %s, %s
                )
                """,
                (
                    upload_id,
                    row["baby_id"],
                    row["episode_id"],
                    row["audio_id"],
                    principal.user_id,
                    principal.session_id,
                    row["object_key"],
                    row["method"],
                    row["max_bytes"],
                    row["declared_mime_type"],
                    row["expected_bytes"],
                    row["declared_duration_seconds"],
                    row["declared_checksum_sha256"],
                    row["expected_bytes"],
                ),
            )
            await connection.execute(
                """
                update baby_data.audio_assets
                   set version = version + 1
                 where audio_id = %s
                """,
                (audio_id,),
            )
            await self._touch_episode(
                connection, baby_id=row["baby_id"], episode_id=row["episode_id"]
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="UPLOAD",
                result_id=upload_id,
                response_status=201,
            )
            return await self._get_audio_upload(connection, upload_id)

    @staticmethod
    def _completion_exception(status_code: int, rejection_code: str | None) -> ApiException:
        if status_code == 413:
            return ApiException(ErrorCode.FILE_TOO_LARGE, "The uploaded object is too large.")
        if status_code == 415:
            return ApiException(
                ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                "The uploaded audio format is not supported.",
            )
        if status_code == 503:
            return ApiException.service_unavailable()
        if status_code == 409:
            return ApiException(
                ErrorCode.INVALID_STATE,
                "The upload is no longer eligible for completion.",
            )
        return ApiException(
            ErrorCode.INVALID_AUDIO,
            "The uploaded audio did not pass verification.",
            details=ErrorDetails.empty().model_copy(
                update={"resource_type": rejection_code or "AUDIO"}
            ),
        )

    @staticmethod
    def _rejection_status(code: str) -> int:
        if code == "TOO_LARGE":
            return 413
        if code == "UNSUPPORTED_CODEC":
            return 415
        return 422

    async def _claim_verification(
        self,
        principal: AuthenticatedPrincipal,
        upload_id: UUID,
        request: CompleteUpload,
        *,
        path: str,
    ) -> VerificationClaim | AudioAsset:
        payload = request.model_dump(mode="json")
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=payload,
            )
            if replay is not None:
                audio = self._audio(await self._get_audio_row(connection, replay[1]))
                if replay[2] >= 400:
                    raise self._completion_exception(replay[2], audio.rejection_code)
                return audio
            cursor = await connection.execute(
                """
                select g.upload_id, g.audio_id, g.baby_id, g.episode_id,
                       g.bucket_id, g.object_key, g.expected_bytes,
                       g.declared_mime_type, g.declared_checksum_sha256,
                       g.auth_session_id, g.expires_at, g.canceled_at,
                       g.completed_at, g.superseded_at, g.reservation_released_at,
                       a.status::text as audio_status, a.verification_token,
                       a.verification_lease_expires_at,
                       a.data_origin::text as data_origin,
                       e.source::text as episode_source
                  from baby_data.audio_upload_grants g
                  join baby_data.audio_assets a on a.audio_id = g.audio_id
                  join baby_data.episodes e on e.episode_id = g.episode_id
                 where g.upload_id = %s and g.uploader_user_id = %s
                 for update of g, a
                """,
                (upload_id, principal.user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise self._not_found()
            if row["auth_session_id"] != principal.session_id:
                raise self._not_found()
            if (
                row["canceled_at"] is not None
                or row["completed_at"] is not None
                or row["superseded_at"] is not None
                or row["reservation_released_at"] is not None
                or row["expires_at"] <= datetime.now(UTC)
            ):
                raise ApiException(ErrorCode.INVALID_STATE, "The upload grant is not active.")
            await self._ensure_processing_allowed(
                connection,
                principal,
                baby_id=row["baby_id"],
                data_origin=row["data_origin"],
            )
            if row["audio_status"] == "VERIFYING":
                if row["verification_lease_expires_at"] > datetime.now(UTC):
                    raise ApiException(
                        ErrorCode.OPERATION_IN_PROGRESS,
                        "Audio verification is already running.",
                        retryable=True,
                    )
                await connection.execute(
                    """
                    update baby_data.audio_verification_runs
                       set status = 'STALE', lease_expires_at = null,
                           failure_code = 'LEASE_EXPIRED', completed_at = clock_timestamp()
                     where audio_id = %s and status = 'RUNNING'
                    """,
                    (row["audio_id"],),
                )
                await connection.execute(
                    """
                    update baby_data.audio_assets
                       set status = 'ALLOCATED', verification_token = null,
                           verification_lease_expires_at = null, version = version + 1
                     where audio_id = %s and status = 'VERIFYING'
                    """,
                    (row["audio_id"],),
                )
                row["audio_status"] = "ALLOCATED"
            if row["audio_status"] != "ALLOCATED":
                raise ApiException(ErrorCode.INVALID_STATE, "The audio cannot be completed.")

            run_token = uuid4()
            await connection.execute(
                """
                update baby_data.audio_assets
                   set status = 'VERIFYING', verification_token = %s,
                       verification_lease_expires_at = clock_timestamp() + interval '2 minutes',
                       version = version + 1
                 where audio_id = %s and status = 'ALLOCATED'
                """,
                (run_token, row["audio_id"]),
            )
            await connection.execute(
                """
                insert into baby_data.audio_verification_runs (
                    run_token, baby_id, episode_id, audio_id, upload_id,
                    uploader_user_id, client_request_id, status, lease_expires_at
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, 'RUNNING',
                    clock_timestamp() + interval '2 minutes'
                )
                """,
                (
                    run_token,
                    row["baby_id"],
                    row["episode_id"],
                    row["audio_id"],
                    upload_id,
                    principal.user_id,
                    request.client_request_id,
                ),
            )
            await self._touch_episode(
                connection, baby_id=row["baby_id"], episode_id=row["episode_id"]
            )
            return VerificationClaim(
                run_token=run_token,
                upload_id=upload_id,
                audio_id=row["audio_id"],
                baby_id=row["baby_id"],
                episode_id=row["episode_id"],
                episode_source=row["episode_source"],
                bucket=row["bucket_id"],
                object_key=row["object_key"],
                declared_mime_type=row["declared_mime_type"],
                expected_bytes=row["expected_bytes"],
                declared_checksum_sha256=row["declared_checksum_sha256"],
                data_origin=row["data_origin"],
            )

    async def _finish_transient_failure(
        self,
        principal: AuthenticatedPrincipal,
        claim: VerificationClaim,
        request: CompleteUpload,
        *,
        path: str,
        failure_code: str,
    ) -> None:
        async with self.transaction(principal) as connection:
            cursor = await connection.execute(
                """
                update baby_data.audio_assets
                   set status = 'ALLOCATED', verification_token = null,
                       verification_lease_expires_at = null, version = version + 1
                 where audio_id = %s and status = 'VERIFYING'
                   and verification_token = %s
                returning audio_id
                """,
                (claim.audio_id, claim.run_token),
            )
            changed = await cursor.fetchone() is not None
            await connection.execute(
                """
                update baby_data.audio_verification_runs
                   set status = 'FAILED', lease_expires_at = null,
                       failure_code = %s, completed_at = clock_timestamp()
                 where run_token = %s and status = 'RUNNING'
                """,
                (failure_code[:80].upper().replace("-", "_"), claim.run_token),
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="AUDIO",
                result_id=claim.audio_id,
                response_status=503,
            )
            if changed:
                await self._touch_episode(
                    connection,
                    baby_id=claim.baby_id,
                    episode_id=claim.episode_id,
                )

    async def _finish_verification(
        self,
        principal: AuthenticatedPrincipal,
        claim: VerificationClaim,
        request: CompleteUpload,
        material: VerificationMaterial,
        *,
        path: str,
        rejection_code: str | None,
        rejection_status: int,
    ) -> AudioAsset:
        decoded = material.decoded
        async with self.transaction(principal) as connection:
            cursor = await connection.execute(
                """
                select a.status::text as audio_status, a.verification_token,
                       g.expires_at, g.canceled_at, g.completed_at, g.superseded_at,
                       g.reservation_released_at
                  from baby_data.audio_assets a
                  join baby_data.audio_upload_grants g on g.audio_id = a.audio_id
                 where a.audio_id = %s and g.upload_id = %s
                 for update of a, g
                """,
                (claim.audio_id, claim.upload_id),
            )
            current = await cursor.fetchone()
            if (
                current is None
                or current["audio_status"] != "VERIFYING"
                or current["verification_token"] != claim.run_token
                or current["canceled_at"] is not None
                or current["completed_at"] is not None
                or current["superseded_at"] is not None
                or current["reservation_released_at"] is not None
                or current["expires_at"] <= datetime.now(UTC)
            ):
                raise ApiException(
                    ErrorCode.INVALID_STATE,
                    "The verification claim is no longer current.",
                )
            await self._ensure_processing_allowed(
                connection,
                principal,
                baby_id=claim.baby_id,
                data_origin=claim.data_origin,
            )

            if rejection_code is not None:
                reasons = []
                if rejection_code in {
                    "TOO_SHORT",
                    "SILENCE",
                    "CLIPPING",
                    "UNSUPPORTED_CODEC",
                    "DECODE_ERROR",
                    "TOO_LONG",
                    "TOO_LARGE",
                }:
                    reasons.append(rejection_code)
                if decoded is not None:
                    reasons = list(decoded.quality_reasons)
                await connection.execute(
                    """
                    update baby_data.audio_assets
                       set mime_type = %s, bytes = %s, duration_seconds = %s,
                           checksum_sha256 = %s, status = 'REJECTED',
                           rejection_code = %s, quality_reasons = %s,
                           verified_container = %s, verified_codec = %s,
                           verified_sample_rate_hz = %s, verified_channels = %s,
                           decoder_version = %s, received_at = clock_timestamp(),
                           delete_after = clock_timestamp(), verification_token = null,
                           verification_lease_expires_at = null, version = version + 1
                     where audio_id = %s
                    """,
                    (
                        claim.declared_mime_type if decoded is None else decoded.source_mime_type,
                        material.downloaded.bytes,
                        None if decoded is None else decoded.duration_seconds,
                        material.downloaded.checksum_sha256,
                        rejection_code,
                        reasons,
                        None if decoded is None else decoded.container,
                        None if decoded is None else decoded.codec,
                        None if decoded is None else decoded.sample_rate_hz,
                        None if decoded is None else decoded.channels,
                        None if decoded is None else decoded.decoder_version,
                        claim.audio_id,
                    ),
                )
                await connection.execute(
                    """
                    insert into baby_data.audio_cleanup_jobs (baby_id, audio_id, reason)
                    values (%s, %s, 'REJECTED')
                    on conflict (audio_id) where status <> 'COMPLETE' do nothing
                    """,
                    (claim.baby_id, claim.audio_id),
                )
                run_status = "REJECTED"
            else:
                if (
                    decoded is None
                    or material.derivative_id is None
                    or material.derivative_key is None
                ):
                    raise ApiException.service_unavailable()
                retained = await self._retention_granted(connection, claim.baby_id)
                await connection.execute(
                    """
                    insert into baby_data.audio_derivatives (
                        derivative_id, baby_id, audio_id, kind, object_key, mime_type,
                        bytes, duration_seconds, checksum_sha256, sample_rate_hz,
                        channels, decoder_version, preprocessing_boundary_version, status
                    ) values (
                        %s, %s, %s, 'PCM_S16LE_SOURCE_RATE', %s, 'audio/wav',
                        %s, %s, %s, %s, %s, %s, %s, 'READY'
                    )
                    """,
                    (
                        material.derivative_id,
                        claim.baby_id,
                        claim.audio_id,
                        material.derivative_key,
                        decoded.pcm_bytes,
                        decoded.duration_seconds,
                        decoded.pcm_checksum_sha256,
                        decoded.sample_rate_hz,
                        decoded.channels,
                        decoded.decoder_version,
                        decoded.preprocessing_boundary_version,
                    ),
                )
                await connection.execute(
                    """
                    update baby_data.audio_assets
                       set mime_type = %s, bytes = %s, duration_seconds = %s,
                           checksum_sha256 = %s, status = 'READY', rejection_code = null,
                           quality_reasons = %s, verified_container = %s,
                           verified_codec = %s, verified_sample_rate_hz = %s,
                           verified_channels = %s, decoder_version = %s,
                           received_at = clock_timestamp(),
                           retention_until = case when %s then
                               clock_timestamp() + interval '7 days' else null end,
                           delete_after = case when %s then null else
                               clock_timestamp() + interval '1 hour' end,
                           verification_token = null,
                           verification_lease_expires_at = null,
                           version = version + 1
                     where audio_id = %s
                    """,
                    (
                        decoded.source_mime_type,
                        material.downloaded.bytes,
                        decoded.duration_seconds,
                        material.downloaded.checksum_sha256,
                        list(decoded.quality_reasons),
                        decoded.container,
                        decoded.codec,
                        decoded.sample_rate_hz,
                        decoded.channels,
                        decoded.decoder_version,
                        retained,
                        retained,
                        claim.audio_id,
                    ),
                )
                run_status = "COMPLETE"

            await connection.execute(
                """
                update baby_data.audio_upload_grants
                   set completed_at = clock_timestamp(),
                       reservation_released_at = clock_timestamp()
                 where upload_id = %s
                """,
                (claim.upload_id,),
            )
            await connection.execute(
                """
                update baby_data.audio_verification_runs
                   set status = %s, lease_expires_at = null, failure_code = %s,
                       completed_at = clock_timestamp()
                 where run_token = %s and status = 'RUNNING'
                """,
                (run_status, rejection_code, claim.run_token),
            )
            await self._touch_episode(
                connection, baby_id=claim.baby_id, episode_id=claim.episode_id
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="AUDIO",
                result_id=claim.audio_id,
                response_status=rejection_status,
            )
            return self._audio(await self._get_audio_row(connection, claim.audio_id))

    async def complete_upload(
        self,
        principal: AuthenticatedPrincipal,
        upload_id: UUID,
        request: CompleteUpload,
        *,
        path: str,
    ) -> AudioAsset:
        self._require_storage()
        if not self._decoder.configured:
            raise ApiException.service_unavailable()
        claimed = await self._claim_verification(principal, upload_id, request, path=path)
        if isinstance(claimed, AudioAsset):
            return claimed
        claim = claimed
        derivative_uploaded = False
        derivative_key: str | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="baby-care-audio-") as directory:
                source = Path(directory) / "source"
                pcm = Path(directory) / "decoded.wav"
                downloaded = await self._storage.download(
                    bucket=claim.bucket,
                    object_key=claim.object_key,
                    destination=source,
                )
                if downloaded.bytes != claim.expected_bytes:
                    raise AudioDecodeError(
                        "BYTE_COUNT_MISMATCH",
                        "Actual bytes did not match the upload reservation.",
                    )
                expected_checksums = {
                    value
                    for value in (claim.declared_checksum_sha256, request.checksum_sha256)
                    if value is not None
                }
                if expected_checksums and expected_checksums != {downloaded.checksum_sha256}:
                    raise AudioDecodeError(
                        "CHECKSUM_MISMATCH",
                        "Actual checksum did not match the completion request.",
                    )
                decoded = await self._decoder.decode(
                    source,
                    pcm,
                    declared_mime_type=claim.declared_mime_type,
                    max_seconds=EPISODE_MAX_SECONDS[claim.episode_source],
                )
                if decoded.rejection_code is not None:
                    material = VerificationMaterial(
                        downloaded=downloaded,
                        decoded=decoded,
                        derivative_id=None,
                        derivative_key=None,
                    )
                    status_code = self._rejection_status(decoded.rejection_code)
                    await self._finish_verification(
                        principal,
                        claim,
                        request,
                        material,
                        path=path,
                        rejection_code=decoded.rejection_code,
                        rejection_status=status_code,
                    )
                    raise self._completion_exception(status_code, decoded.rejection_code)

                derivative_id = uuid4()
                derivative_key = f"{claim.baby_id}/{claim.audio_id}/derived/{derivative_id.hex}"
                await self._storage.upload(
                    bucket=claim.bucket,
                    object_key=derivative_key,
                    source=decoded.pcm_path,
                    mime_type="audio/wav",
                )
                derivative_uploaded = True
                material = VerificationMaterial(
                    downloaded=downloaded,
                    decoded=decoded,
                    derivative_id=derivative_id,
                    derivative_key=derivative_key,
                )
                return await self._finish_verification(
                    principal,
                    claim,
                    request,
                    material,
                    path=path,
                    rejection_code=None,
                    rejection_status=200,
                )
        except AudioDecodeError as exc:
            if exc.retryable:
                await self._finish_transient_failure(
                    principal,
                    claim,
                    request,
                    path=path,
                    failure_code=exc.code,
                )
                raise ApiException.service_unavailable() from exc
            material = VerificationMaterial(
                downloaded=locals().get(
                    "downloaded",
                    DownloadedObject(bytes=0, checksum_sha256="0" * 64),
                ),
                decoded=None,
                derivative_id=None,
                derivative_key=None,
            )
            status_code = self._rejection_status(exc.code)
            await self._finish_verification(
                principal,
                claim,
                request,
                material,
                path=path,
                rejection_code=exc.code,
                rejection_status=status_code,
            )
            raise self._completion_exception(status_code, exc.code) from exc
        except StorageError as exc:
            if exc.retryable:
                await self._finish_transient_failure(
                    principal,
                    claim,
                    request,
                    path=path,
                    failure_code=exc.code,
                )
                raise ApiException.service_unavailable() from exc
            mapped = "TOO_LARGE" if exc.code == "TOO_LARGE" else "DECODE_ERROR"
            material = VerificationMaterial(
                downloaded=locals().get(
                    "downloaded",
                    DownloadedObject(bytes=0, checksum_sha256="0" * 64),
                ),
                decoded=None,
                derivative_id=None,
                derivative_key=None,
            )
            status_code = self._rejection_status(mapped)
            await self._finish_verification(
                principal,
                claim,
                request,
                material,
                path=path,
                rejection_code=mapped,
                rejection_status=status_code,
            )
            raise self._completion_exception(status_code, mapped) from exc
        finally:
            if derivative_uploaded and derivative_key is not None:
                # A successful DB finalization owns the object through the derivative row.
                # If it did not commit, remove the unreferenced object immediately.
                try:
                    async with self.transaction(principal) as connection:
                        cursor = await connection.execute(
                            """
                            select 1 as stored from baby_data.audio_derivatives
                             where audio_id = %s and object_key = %s
                            """,
                            (claim.audio_id, derivative_key),
                        )
                        stored = await cursor.fetchone() is not None
                except ApiException:
                    stored = False
                if not stored:
                    with suppress(StorageError):
                        await self._storage.delete(
                            bucket=claim.bucket, object_keys=[derivative_key]
                        )

    async def cancel_upload(
        self,
        principal: AuthenticatedPrincipal,
        upload_id: UUID,
        request: CancelUpload,
        *,
        path: str,
    ) -> AudioAsset:
        payload = request.model_dump(mode="json")
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=payload,
            )
            if replay is not None:
                return self._audio(await self._get_audio_row(connection, replay[1]))
            cursor = await connection.execute(
                """
                select g.audio_id, g.baby_id, g.episode_id, g.completed_at,
                       g.canceled_at, g.superseded_at,
                       a.status::text as audio_status
                  from baby_data.audio_upload_grants g
                  join baby_data.audio_assets a on a.audio_id = g.audio_id
                 where g.upload_id = %s and g.uploader_user_id = %s
                 for update of g, a
                """,
                (upload_id, principal.user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise self._not_found()
            if row["completed_at"] is not None or row["audio_status"] in {
                "READY",
                "REJECTED",
                "DELETED",
            }:
                raise ApiException(ErrorCode.INVALID_STATE, "The upload is already final.")
            if row["canceled_at"] is None and row["superseded_at"] is None:
                await connection.execute(
                    """
                    update baby_data.audio_upload_grants
                       set canceled_at = clock_timestamp(),
                           reservation_released_at = clock_timestamp()
                     where upload_id = %s
                    """,
                    (upload_id,),
                )
            cursor = await connection.execute(
                """
                select client_request_id
                  from baby_data.audio_verification_runs
                 where audio_id = %s and status = 'RUNNING'
                """,
                (row["audio_id"],),
            )
            run_rows = await cursor.fetchall()
            await connection.execute(
                """
                update baby_data.audio_verification_runs
                   set status = 'STALE', lease_expires_at = null,
                       failure_code = 'CANCELED', completed_at = clock_timestamp()
                 where audio_id = %s and status = 'RUNNING'
                """,
                (row["audio_id"],),
            )
            for run in run_rows:
                await connection.execute(
                    """
                    update baby_data.idempotency_records
                       set status = 'COMPLETE', result_type = 'AUDIO', result_id = %s,
                           response_status = 409, completed_at = clock_timestamp()
                     where user_id = %s and method = 'POST'
                       and target_path = %s
                       and idempotency_key = %s and status = 'IN_PROGRESS'
                    """,
                    (
                        row["audio_id"],
                        principal.user_id,
                        f"/v1/uploads/{upload_id}/complete",
                        run["client_request_id"],
                    ),
                )
            await connection.execute(
                """
                update baby_data.audio_assets
                   set status = 'DELETING', verification_token = null,
                       verification_lease_expires_at = null, version = version + 1
                 where audio_id = %s and status in ('ALLOCATED', 'VERIFYING')
                """,
                (row["audio_id"],),
            )
            await connection.execute(
                """
                insert into baby_data.audio_cleanup_jobs (baby_id, audio_id, reason)
                values (%s, %s, 'CANCELED')
                on conflict (audio_id) where status <> 'COMPLETE' do nothing
                """,
                (row["baby_id"], row["audio_id"]),
            )
            await self._touch_episode(
                connection, baby_id=row["baby_id"], episode_id=row["episode_id"]
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="AUDIO",
                result_id=row["audio_id"],
                response_status=200,
            )
            return self._audio(await self._get_audio_row(connection, row["audio_id"]))

    async def get_playback(self, principal: AuthenticatedPrincipal, audio_id: UUID) -> Playback:
        self._require_storage()
        async with self.transaction(principal) as connection:
            row = await self._get_audio_row(connection, audio_id)
            await self._baby_access(connection, row["baby_id"], principal.user_id)
            if row["status"] != "READY":
                raise ApiException(ErrorCode.RESOURCE_NOT_FOUND, "Playback is not available.")
            if row["retention_until"] is None or row["retention_until"] <= datetime.now(UTC):
                raise ApiException(
                    ErrorCode.CONSENT_REQUIRED,
                    "The audio is not retained for playback.",
                )
            if not await self._retention_granted(connection, row["baby_id"]):
                raise ApiException(
                    ErrorCode.CONSENT_REQUIRED,
                    "Current audio-retention consent is required.",
                )
            cursor = await connection.execute(
                """
                select bucket_id, object_key from baby_data.audio_assets
                 where audio_id = %s and status = 'READY'
                """,
                (audio_id,),
            )
            storage_row = await cursor.fetchone()
            if storage_row is None:
                raise self._not_found()
        issued_at = datetime.now(UTC)
        url = await self._storage.sign_playback(
            bucket=storage_row["bucket_id"],
            object_key=storage_row["object_key"],
            expires_in_seconds=PLAYBACK_SECONDS,
        )
        return Playback(
            audio_id=audio_id,
            playback_url=url,
            expires_at=issued_at + timedelta(seconds=PLAYBACK_SECONDS),
        )

    def capabilities(
        self,
        *,
        normalizer_available: bool,
        normalizer_unavailable_reason: NormalizerUnavailableReason | None,
    ) -> Capabilities:
        audio_ready = self._storage.configured and self._decoder.configured
        return Capabilities(
            audio_model=ModelInfo(
                available=False,
                model_version=None,
                preprocess_version=None,
                label_mapping_version=None,
                supported_labels=[],
                inference_mode="STUB",
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

    async def enqueue_post_analysis_cleanup(self, audio_id: UUID) -> bool:
        """B-06 hook: call only after a persisted terminal analysis state."""
        try:
            async with self._pool.connection() as connection, connection.transaction():
                await connection.execute("set local role baby_app")
                cursor = await connection.execute(
                    "select baby_private.enqueue_audio_cleanup_after_analysis(%s) as queued",
                    (audio_id,),
                )
                row = await cursor.fetchone()
                return row is not None and row["queued"] is True
        except (psycopg.Error, PoolClosed, PoolTimeout) as exc:
            raise ApiException.service_unavailable() from exc


class AudioCleanupWorker:
    """Durable one-shot cleanup worker for a scheduler or local CLI."""

    def __init__(
        self,
        database_url: str,
        *,
        storage: AudioStoragePort,
        min_pool_size: int = 0,
        max_pool_size: int = 2,
    ) -> None:
        self._pool = AsyncConnectionPool[DatabaseConnection](
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            open=False,
            kwargs={"application_name": "baby-care-audio-cleanup", "row_factory": dict_row},
        )
        self._storage = storage

    async def open(self) -> None:
        await self._pool.open(wait=False)

    async def close(self) -> None:
        await self._pool.close()

    async def _claim(self, token: UUID, limit: int) -> list[DatabaseRow]:
        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute("set local role baby_app")
            cursor = await connection.execute(
                "select * from baby_private.claim_audio_cleanup_jobs(%s, %s)",
                (token, limit),
            )
            return await cursor.fetchall()

    async def _finish(
        self,
        *,
        job_id: UUID,
        token: UUID,
        succeeded: bool,
        failure_code: str | None,
    ) -> bool:
        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute("set local role baby_app")
            cursor = await connection.execute(
                "select baby_private.finish_audio_cleanup_job(%s, %s, %s, %s) as finished",
                (job_id, token, succeeded, failure_code),
            )
            row = await cursor.fetchone()
            return row is not None and row["finished"] is True

    async def run_once(self, *, limit: int = 20) -> dict[str, int]:
        if not self._storage.configured:
            raise ApiException.service_unavailable()
        token = uuid4()
        try:
            jobs = await self._claim(token, limit)
        except (psycopg.Error, PoolClosed, PoolTimeout) as exc:
            raise ApiException.service_unavailable() from exc
        completed = 0
        failed = 0
        for job in jobs:
            try:
                await self._storage.delete(
                    bucket=job["bucket_id"],
                    object_keys=list(job["object_keys"]),
                )
            except StorageError as exc:
                await self._finish(
                    job_id=job["cleanup_job_id"],
                    token=token,
                    succeeded=False,
                    failure_code=exc.code[:80].upper().replace("-", "_"),
                )
                failed += 1
            else:
                await self._finish(
                    job_id=job["cleanup_job_id"],
                    token=token,
                    succeeded=True,
                    failure_code=None,
                )
                completed += 1
        return {"claimed": len(jobs), "completed": completed, "failed": failed}

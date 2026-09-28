from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg_pool import PoolClosed, PoolTimeout

from baby_care_api.core.errors import ApiException
from baby_care_api.models.b04 import Choice, MembershipRole
from baby_care_api.models.errors import ErrorCode, ErrorDetails, FieldError
from baby_care_api.models.normalization import (
    MODEL_ID,
    NORMALIZATION_ONTOLOGY_VERSION,
    NORMALIZATION_PROMPT_VERSION,
    NORMALIZATION_SCHEMA_VERSION,
    VISUAL_MAPPING_VERSION,
    ConfirmCareEntry,
    ConfirmedResources,
    CreateNormalization,
    NormalizationFailure,
    NormalizationRun,
    StateObservation,
)
from baby_care_api.services.b04 import (
    DatabaseConnection,
    PostgresBabyCareService,
    SharedChangeInput,
)
from baby_care_api.services.normalization_validation import (
    SemanticIssue,
    validate_normalized_content,
    visual_state_code,
)
from baby_care_api.services.normalizer import (
    NormalizerAdapter,
    NormalizerRequest,
    NormalizerResult,
)
from baby_care_api.services.security import AuthenticatedPrincipal

_FAILURE_MESSAGES: dict[str, str] = {
    "NORMALIZATION_DISABLED": (
        "External normalization is disabled; the original draft remains available."
    ),
    "NORMALIZATION_CREDENTIALS_MISSING": (
        "External normalization credentials are not configured; "
        "manual confirmation remains available."
    ),
    "NORMALIZATION_DEPENDENCY_MISSING": (
        "The normalization provider dependency is unavailable; "
        "manual confirmation remains available."
    ),
    "NORMALIZATION_TIMEOUT": "Normalization did not finish within 20 seconds.",
    "NORMALIZATION_LEASE_EXPIRED": "The normalization lease expired before completion.",
    "NORMALIZATION_REFUSED": "The provider declined to normalize this input.",
    "NORMALIZATION_INCOMPLETE": "The provider returned an incomplete normalization.",
    "NORMALIZATION_SCHEMA_INVALID": (
        "The normalization did not satisfy the product schema and evidence rules."
    ),
    "NORMALIZATION_PROVIDER_ERROR": "The normalization provider could not complete the request.",
    "SOURCE_DELETED": "The source draft was deleted before normalization completed.",
    "ACCESS_REVOKED": "Access was revoked before normalization completed.",
}


@dataclass(frozen=True)
class _ClaimedRun:
    run: NormalizationRun
    execution_token: UUID | None
    should_execute: bool
    raw_text: str | None
    choices: list[Choice]
    occurred_at: Any
    time_precision: str
    timezone: str


class PostgresNormalizationService:
    """B-07 orchestration over B-04 auth/idempotency and B-09 atomic feed writes."""

    def __init__(
        self,
        backend: PostgresBabyCareService,
        *,
        adapter: NormalizerAdapter | None,
        unavailable_code: str | None,
        lease_seconds: int = 30,
        confirmation_checkpoint: Callable[[], None] | None = None,
    ) -> None:
        self._backend = backend
        self._adapter = adapter
        self._unavailable_code = unavailable_code
        self._lease_seconds = lease_seconds
        self._confirmation_checkpoint = confirmation_checkpoint

    @property
    def available(self) -> bool:
        return self._adapter is not None and self._unavailable_code is None

    async def startup_recover(self) -> int:
        try:
            async with self._backend._pool.connection() as connection, connection.transaction():
                await connection.execute("set local role baby_app")
                cursor = await connection.execute(
                    "select baby_private.expire_normalization_leases() as expired"
                )
                row = await cursor.fetchone()
                return 0 if row is None else cast(int, row["expired"])
        except (psycopg.Error, PoolClosed, PoolTimeout) as exc:
            raise ApiException.service_unavailable() from exc

    async def close(self) -> None:
        if self._adapter is not None:
            await self._adapter.close()

    @staticmethod
    def _failure(code: str, *, retryable: bool) -> NormalizationFailure:
        return NormalizationFailure.model_validate(
            {
                "code": code,
                "message": _FAILURE_MESSAGES[code],
                "retryable": retryable,
            }
        )

    @classmethod
    def _run(cls, row: dict[str, Any]) -> NormalizationRun:
        return NormalizationRun(
            run_id=row["run_id"],
            entry_id=row["entry_id"],
            input_revision=row["input_revision"],
            status=row["status"],
            lease_expires_at=row["lease_expires_at"],
            execution_mode=row["execution_mode"],
            provider_call_executed=row["provider_call_executed"],
            provider=row["provider"],
            model=row["model"],
            prompt_version=row["prompt_version"],
            schema_version=row["schema_version"],
            ontology_version=row["ontology_version"],
            result=row["result"],
            failure=row["failure"],
            recorded_at=row["recorded_at"],
            completed_at=row["completed_at"],
        )

    async def _select_run(
        self,
        connection: DatabaseConnection,
        run_id: UUID | None,
        *,
        for_update: bool = False,
    ) -> dict[str, Any]:
        cursor = await connection.execute(
            f"""
            select run_id, entry_id, input_revision, status::text as status,
                   execution_token, lease_expires_at,
                   execution_mode::text as execution_mode,
                   provider_call_executed, provider, model, prompt_version,
                   schema_version, ontology_version, result, failure,
                   recorded_at, completed_at
              from baby_data.normalization_runs
             where run_id = %s
             {"for update" if for_update else ""}
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._backend._not_found()
        return row

    async def _expire_one_if_needed(
        self,
        connection: DatabaseConnection,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            row["status"] != "RUNNING"
            or row["lease_expires_at"] is None
            or row["lease_expires_at"] > datetime.now(UTC)
        ):
            return row
        failure = self._failure("NORMALIZATION_LEASE_EXPIRED", retryable=True)
        await connection.execute(
            """
            update baby_data.normalization_runs
               set status = 'FAILED', lease_expires_at = null,
                   failure = %s, completed_at = clock_timestamp()
             where run_id = %s and status = 'RUNNING'
            """,
            (json.dumps(failure.model_dump(mode="json")), row["run_id"]),
        )
        await connection.execute(
            """
            update baby_data.raw_care_entries
               set status = 'NEEDS_MANUAL_REVIEW', version = version + 1
             where entry_id = %s and input_revision = %s and status = 'NORMALIZING'
            """,
            (row["entry_id"], row["input_revision"]),
        )
        return await self._select_run(connection, row["run_id"])

    async def _claim(
        self,
        principal: AuthenticatedPrincipal,
        entry_id: UUID,
        request: CreateNormalization,
        *,
        path: str,
    ) -> _ClaimedRun:
        async with self._backend.transaction(principal) as connection:
            replay = await self._backend._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                _, saved_run_id, _ = replay
                row = await self._select_run(connection, saved_run_id, for_update=True)
                row = await self._expire_one_if_needed(connection, row)
                return _ClaimedRun(
                    run=self._run(row),
                    execution_token=None,
                    should_execute=False,
                    raw_text=None,
                    choices=[],
                    occurred_at=None,
                    time_precision="UNKNOWN",
                    timezone="UTC",
                )

            cursor = await connection.execute(
                """
                select e.*, b.timezone
                  from baby_data.raw_care_entries e
                  join baby_data.babies b on b.baby_id = e.baby_id
                 where e.entry_id = %s
                 for update of e
                """,
                (entry_id,),
            )
            entry = await cursor.fetchone()
            if entry is None or entry["author_user_id"] != principal.user_id:
                raise self._backend._not_found()
            if entry["status"] == "CONFIRMED":
                raise ApiException(ErrorCode.ALREADY_CONFIRMED, "The entry is already confirmed.")
            if entry["status"] in {"DELETING", "DELETED"}:
                raise ApiException(
                    ErrorCode.RESOURCE_DELETING, "The source draft is being deleted."
                )
            if entry["input_revision"] != request.input_revision:
                raise ApiException(
                    ErrorCode.SOURCE_REVISION_CHANGED,
                    "The draft source changed before normalization started.",
                    details=ErrorDetails.empty().model_copy(
                        update={
                            "current_version": entry["input_revision"],
                            "resource_type": "CARE_ENTRY",
                        }
                    ),
                )

            await self._backend._advisory_lock(
                connection, f"normalization-rate:{principal.user_id}"
            )
            cursor = await connection.execute(
                """
                select count(*) filter (
                           where n.recorded_at >= clock_timestamp() - interval '1 minute'
                       ) as minute_count,
                       count(*) filter (
                           where n.recorded_at >= clock_timestamp() - interval '24 hours'
                       ) as day_count
                  from baby_data.normalization_runs n
                  join baby_data.raw_care_entries e on e.entry_id = n.entry_id
                 where e.author_user_id = %s
                """,
                (principal.user_id,),
            )
            counts = await cursor.fetchone()
            if counts is not None and (counts["minute_count"] >= 10 or counts["day_count"] >= 100):
                raise ApiException(
                    ErrorCode.RATE_LIMITED,
                    "The normalization rate limit was reached.",
                    retryable=True,
                )

            cursor = await connection.execute(
                """
                select run_id from baby_data.normalization_runs
                 where entry_id = %s and input_revision = %s and status = 'RUNNING'
                 for update
                """,
                (entry_id, request.input_revision),
            )
            active = await cursor.fetchone()
            if active is not None:
                active_row = await self._select_run(connection, active["run_id"], for_update=True)
                active_row = await self._expire_one_if_needed(connection, active_row)
                if active_row["status"] == "RUNNING":
                    raise ApiException(
                        ErrorCode.NORMALIZATION_IN_PROGRESS,
                        "A normalization is already running for this draft revision.",
                        retryable=True,
                        details=ErrorDetails.empty().model_copy(
                            update={
                                "existing_run_id": active_row["run_id"],
                                "status_url": f"/v1/normalizations/{active_row['run_id']}",
                                "retry_after_seconds": 1,
                            }
                        ),
                    )

            cursor = await connection.execute(
                "select run_id from baby_data.normalization_runs where run_id = %s",
                (request.run_id,),
            )
            if await cursor.fetchone() is not None:
                existing = await self._select_run(connection, request.run_id)
                if (
                    existing["entry_id"] != entry_id
                    or existing["input_revision"] != request.input_revision
                ):
                    raise ApiException(
                        ErrorCode.IDEMPOTENCY_KEY_REUSED,
                        "The run ID was already used for another draft revision.",
                    )
                await self._backend._complete_idempotency(
                    connection,
                    principal,
                    method="POST",
                    path=path,
                    key=request.client_request_id,
                    result_type="NORMALIZATION_RUN",
                    result_id=request.run_id,
                    response_status=200,
                )
                return _ClaimedRun(
                    run=self._run(existing),
                    execution_token=None,
                    should_execute=False,
                    raw_text=None,
                    choices=[],
                    occurred_at=None,
                    time_precision="UNKNOWN",
                    timezone=entry["timezone"],
                )

            execution_token = uuid4()
            await connection.execute(
                """
                insert into baby_data.normalization_runs (
                    run_id, baby_id, entry_id, input_revision, status,
                    execution_token, lease_expires_at, execution_mode,
                    provider_call_executed, provider, model,
                    prompt_version, schema_version, ontology_version
                ) values (
                    %s, %s, %s, %s, 'RUNNING', %s,
                    clock_timestamp() + make_interval(secs => %s),
                    'REAL', false, 'openai', %s, %s, %s, %s
                )
                """,
                (
                    request.run_id,
                    entry["baby_id"],
                    entry_id,
                    request.input_revision,
                    execution_token,
                    self._lease_seconds,
                    MODEL_ID,
                    NORMALIZATION_PROMPT_VERSION,
                    NORMALIZATION_SCHEMA_VERSION,
                    NORMALIZATION_ONTOLOGY_VERSION,
                ),
            )
            await connection.execute(
                """
                update baby_data.raw_care_entries
                   set status = 'NORMALIZING', version = version + 1
                 where entry_id = %s
                """,
                (entry_id,),
            )
            await self._backend._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="NORMALIZATION_RUN",
                result_id=request.run_id,
                response_status=200,
            )
            row = await self._select_run(connection, request.run_id)
            return _ClaimedRun(
                run=self._run(row),
                execution_token=execution_token,
                should_execute=True,
                raw_text=entry["raw_text"],
                choices=[Choice.model_validate(item) for item in entry["choices"]],
                occurred_at=entry["occurred_at"],
                time_precision=entry["time_precision"],
                timezone=entry["timezone"],
            )

    async def create_normalization(
        self,
        principal: AuthenticatedPrincipal,
        entry_id: UUID,
        request: CreateNormalization,
        *,
        path: str,
    ) -> NormalizationRun:
        claimed = await self._claim(principal, entry_id, request, path=path)
        if not claimed.should_execute:
            return claimed.run
        assert claimed.execution_token is not None
        if self._adapter is None:
            result = NormalizerResult.failure(
                self._unavailable_code or "NORMALIZATION_DEPENDENCY_MISSING",
                provider_call_executed=False,
            )
        else:
            try:
                result = await self._adapter.normalize(
                    NormalizerRequest(
                        raw_text=claimed.raw_text,
                        choices=claimed.choices,
                        occurred_at=claimed.occurred_at,
                        time_precision=claimed.time_precision,
                        current_time=datetime.now(UTC),
                        timezone=claimed.timezone,
                    )
                )
            except Exception:
                result = NormalizerResult.failure("NORMALIZATION_PROVIDER_ERROR")
        return await self._finalize(
            principal,
            claimed.run.run_id,
            claimed.execution_token,
            claimed.raw_text,
            claimed.choices,
            result,
        )

    async def _reject_late_completion(
        self,
        principal: AuthenticatedPrincipal,
        run_id: UUID,
        execution_token: UUID,
    ) -> None:
        async with self._backend.transaction(principal, require_live_session=False) as connection:
            await connection.execute(
                "select baby_private.reject_normalization_completion(%s, %s)",
                (run_id, execution_token),
            )

    async def _finalize(
        self,
        principal: AuthenticatedPrincipal,
        run_id: UUID,
        execution_token: UUID,
        raw_text: str | None,
        choices: list[Choice],
        result: NormalizerResult,
    ) -> NormalizationRun:
        try:
            async with self._backend.transaction(principal) as connection:
                cursor = await connection.execute(
                    """
                    select n.run_id, n.entry_id, n.input_revision,
                           n.status::text as status, n.execution_token,
                           n.lease_expires_at, n.execution_mode::text as execution_mode,
                           n.provider_call_executed, n.provider, n.model,
                           n.prompt_version, n.schema_version, n.ontology_version,
                           n.result, n.failure, n.recorded_at, n.completed_at,
                           e.status::text as entry_status,
                           e.input_revision as current_input_revision,
                           e.author_user_id
                      from baby_data.normalization_runs n
                      join baby_data.raw_care_entries e on e.entry_id = n.entry_id
                     where n.run_id = %s
                     for update of n, e
                    """,
                    (run_id,),
                )
                row = await cursor.fetchone()
                if row is None or row["author_user_id"] != principal.user_id:
                    raise self._backend._not_found()
                if row["status"] != "RUNNING" or row["execution_token"] != execution_token:
                    return self._run(row)
                if (
                    row["current_input_revision"] != row["input_revision"]
                    or row["entry_status"] != "NORMALIZING"
                ):
                    await connection.execute(
                        """
                        update baby_data.normalization_runs
                           set status = 'STALE', lease_expires_at = null,
                               provider_call_executed = %s,
                               completed_at = clock_timestamp()
                         where run_id = %s and execution_token = %s and status = 'RUNNING'
                        """,
                        (result.provider_call_executed, run_id, execution_token),
                    )
                    return self._run(await self._select_run(connection, run_id))

                if result.content is not None:
                    issues = validate_normalized_content(
                        result.content,
                        raw_text=raw_text,
                        choices=choices,
                        allow_user_correction=False,
                        confirmation=False,
                        eventless=True,
                    )
                    if issues:
                        result = NormalizerResult.failure(
                            "NORMALIZATION_SCHEMA_INVALID",
                            provider_call_executed=result.provider_call_executed,
                        )

                if result.content is not None:
                    await connection.execute(
                        """
                        update baby_data.normalization_runs
                           set status = 'COMPLETE', lease_expires_at = null,
                               provider_call_executed = %s, result = %s,
                               completed_at = clock_timestamp()
                         where run_id = %s and execution_token = %s and status = 'RUNNING'
                        """,
                        (
                            result.provider_call_executed,
                            json.dumps(result.content.model_dump(mode="json")),
                            run_id,
                            execution_token,
                        ),
                    )
                    await connection.execute(
                        """
                        update baby_data.raw_care_entries
                           set status = 'REVIEW_READY', version = version + 1
                         where entry_id = %s and status = 'NORMALIZING'
                        """,
                        (row["entry_id"],),
                    )
                else:
                    failure_code = result.failure_code or "NORMALIZATION_PROVIDER_ERROR"
                    failure = self._failure(failure_code, retryable=result.retryable)
                    await connection.execute(
                        """
                        update baby_data.normalization_runs
                           set status = 'FAILED', lease_expires_at = null,
                               provider_call_executed = %s, failure = %s,
                               completed_at = clock_timestamp()
                         where run_id = %s and execution_token = %s and status = 'RUNNING'
                        """,
                        (
                            result.provider_call_executed,
                            json.dumps(failure.model_dump(mode="json")),
                            run_id,
                            execution_token,
                        ),
                    )
                    await connection.execute(
                        """
                        update baby_data.raw_care_entries
                           set status = 'NEEDS_MANUAL_REVIEW', version = version + 1
                         where entry_id = %s and status = 'NORMALIZING'
                        """,
                        (row["entry_id"],),
                    )
                return self._run(await self._select_run(connection, run_id))
        except ApiException as exc:
            if exc.code in {ErrorCode.SESSION_REVOKED, ErrorCode.RESOURCE_NOT_FOUND}:
                await self._reject_late_completion(principal, run_id, execution_token)
            raise

    async def get_normalization(
        self, principal: AuthenticatedPrincipal, run_id: UUID
    ) -> NormalizationRun:
        async with self._backend.transaction(principal) as connection:
            row = await self._select_run(connection, run_id, for_update=True)
            row = await self._expire_one_if_needed(connection, row)
            return self._run(row)

    @staticmethod
    def _issues_exception(issues: list[SemanticIssue]) -> ApiException:
        return ApiException(
            ErrorCode.VALIDATION_ERROR,
            "The confirmed content does not satisfy the eventless persistence rules.",
            field_errors=[
                FieldError(field=item.field, code=item.code, message=item.message)
                for item in issues[:50]
            ],
        )

    @staticmethod
    def _version_projection(values: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
        return sorted(
            (str(item["resource_type"]), str(item["resource_id"]), int(item["version"]))
            for item in values
        )

    @staticmethod
    def _event_fields(action: Any, entry: dict[str, Any]) -> tuple[str, Any, str, dict[str, Any]]:
        occurred_at = action.occurred_at or entry["occurred_at"]
        precision = (
            action.time_precision.value
            if action.occurred_at is not None or entry["occurred_at"] is None
            else entry["time_precision"]
        )
        if action.action_code == "FEEDING":
            payload = {
                "mode": action.feeding_mode or "UNSPECIFIED",
                "amount_ml": action.amount if action.unit == "ML" else None,
                "duration_minutes": action.amount if action.unit == "MINUTES" else None,
            }
            return "FEEDING", occurred_at, precision, payload
        if action.action_code in {"DIAPER_CHECK", "DIAPER_CHANGE"}:
            return (
                "DIAPER",
                occurred_at,
                precision,
                {
                    "operation": "CHECK" if action.action_code == "DIAPER_CHECK" else "CHANGE",
                    "condition": "UNKNOWN",
                },
            )
        return (
            "SOOTHE",
            occurred_at,
            precision,
            {"action_kind": action.action_code.value},
        )

    async def confirm_care_entry(
        self,
        principal: AuthenticatedPrincipal,
        entry_id: UUID,
        request: ConfirmCareEntry,
        *,
        path: str,
    ) -> ConfirmedResources:
        async with self._backend.transaction(principal) as connection:
            replay = await self._backend._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                _, replay_entry_id, _ = replay
                cursor = await connection.execute(
                    """
                    select confirmed_resources
                      from baby_data.raw_care_entries
                     where entry_id = %s
                    """,
                    (replay_entry_id,),
                )
                row = await cursor.fetchone()
                if row is None or row["confirmed_resources"] is None:
                    raise ApiException(
                        ErrorCode.OPERATION_IN_PROGRESS,
                        "The confirmation result cannot yet be recovered.",
                        retryable=True,
                    )
                return ConfirmedResources.model_validate(row["confirmed_resources"])

            cursor = await connection.execute(
                """
                select e.*, m.role::text as requester_role
                  from baby_data.raw_care_entries e
                  join baby_data.baby_memberships m
                    on m.baby_id = e.baby_id and m.user_id = %s and m.status = 'ACTIVE'
                 where e.entry_id = %s
                 for update of e
                """,
                (principal.user_id, entry_id),
            )
            entry = await cursor.fetchone()
            if entry is None or entry["author_user_id"] != principal.user_id:
                raise self._backend._not_found()
            if entry["status"] == "CONFIRMED":
                raise ApiException(ErrorCode.ALREADY_CONFIRMED, "The entry is already confirmed.")
            if entry["status"] in {"DELETING", "DELETED"}:
                raise ApiException(
                    ErrorCode.RESOURCE_DELETING, "The source draft is being deleted."
                )
            if entry["input_revision"] != request.input_revision:
                raise ApiException(
                    ErrorCode.SOURCE_REVISION_CHANGED,
                    "The source revision changed before confirmation.",
                    details=ErrorDetails.empty().model_copy(
                        update={
                            "current_version": entry["input_revision"],
                            "resource_type": "CARE_ENTRY",
                        }
                    ),
                )
            if entry["episode_id"] is not None:
                raise ApiException(
                    ErrorCode.INVALID_STATE,
                    "Episode-linked confirmation remains outside the eventless B-07 first slice.",
                )
            if self._version_projection(entry["base_record_versions"]) != self._version_projection(
                [item.model_dump(mode="json") for item in request.base_record_versions]
            ):
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The correction base versions differ from the saved draft.",
                )

            choices = [Choice.model_validate(item) for item in entry["choices"]]
            issues = validate_normalized_content(
                request.content,
                raw_text=entry["raw_text"],
                choices=choices,
                allow_user_correction=request.normalization_mode != "RULE",
                confirmation=True,
                eventless=True,
            )
            evidence_items: list[Any] = [
                *request.content.actions,
                *request.content.states,
                *request.content.outcomes,
                *request.content.caregiver_interpretations,
            ]
            if request.normalization_mode == "RULE" and (
                not choices
                or any(
                    evidence.source != "CHOICE"
                    for item in evidence_items
                    for evidence in item.evidence
                )
            ):
                issues.append(
                    SemanticIssue(
                        "content",
                        "RULE_REQUIRES_CHOICES",
                        "RULE confirmation must be grounded only in submitted choices.",
                    )
                )

            run_result: dict[str, Any] | None = None
            if request.normalization_mode == "LLM":
                run_row = await self._select_run(connection, request.run_id, for_update=True)
                if (
                    run_row["entry_id"] != entry_id
                    or run_row["input_revision"] != request.input_revision
                    or run_row["status"] != "COMPLETE"
                    or run_row["result"] is None
                ):
                    raise ApiException(
                        ErrorCode.INVALID_STATE,
                        "LLM confirmation requires the current successful normalization run.",
                    )
                run_result = cast(dict[str, Any], run_row["result"])
            if issues:
                raise self._issues_exception(issues)

            performed = [
                action for action in request.content.actions if action.assertion == "PERFORMED"
            ]
            care_event_ids: list[UUID] = []
            state_ids: list[UUID] = []
            event_versions: list[int] = []
            state_versions: list[int] = []
            correction = entry["supersedes_entry_id"] is not None
            prior_resources: dict[str, Any] | None = None

            await self._backend._lock_shared_change_feed(connection, entry["baby_id"])
            if correction:
                # The feed lock serializes contribution/baby deletion with this
                # correction. A row-locking SELECT would also apply the raw-entry
                # author-only UPDATE policy and incorrectly hide a caregiver's
                # confirmed entry from an owner correction.
                cursor = await connection.execute(
                    """
                    select original_author_user_id, confirmed_resources
                      from baby_data.raw_care_entries
                     where entry_id = %s and baby_id = %s and status = 'CONFIRMED'
                    """,
                    (entry["supersedes_entry_id"], entry["baby_id"]),
                )
                prior = await cursor.fetchone()
                if prior is None or prior["confirmed_resources"] is None:
                    raise self._backend._not_found()
                if (
                    prior["original_author_user_id"] != principal.user_id
                    and entry["requester_role"] != MembershipRole.OWNER.value
                ):
                    raise ApiException(
                        ErrorCode.AUTHOR_ONLY,
                        "Only the original author or owner can confirm a correction.",
                    )
                prior_resources = cast(dict[str, Any], prior["confirmed_resources"])
                expected_ids = {
                    ("CARE_EVENT", UUID(value)) for value in prior_resources["care_event_ids"]
                } | {
                    ("STATE_OBSERVATION", UUID(value))
                    for value in prior_resources["state_observation_ids"]
                }
                submitted_ids = {
                    (item.resource_type, item.resource_id) for item in request.base_record_versions
                }
                if expected_ids != submitted_ids or any(
                    item.resource_type in {"ACTION", "OUTCOME"}
                    for item in request.base_record_versions
                ):
                    raise ApiException(
                        ErrorCode.VERSION_CONFLICT,
                        "Correction versions must name every persisted eventless "
                        "resource exactly once.",
                    )
                if len(performed) != len(prior_resources["care_event_ids"]) or len(
                    request.content.states
                ) != len(prior_resources["state_observation_ids"]):
                    raise self._issues_exception(
                        [
                            SemanticIssue(
                                "content",
                                "CORRECTION_SHAPE_CHANGED",
                                "The first correction slice updates existing resources only; "
                                "add/remove remains unsupported.",
                            )
                        ]
                    )
                await connection.execute(
                    """
                    update baby_data.label_annotations
                       set status = 'SUPERSEDED', revision = revision + 1
                     where baby_id = %s and entry_id = %s and status = 'ACTIVE'
                    """,
                    (entry["baby_id"], entry["supersedes_entry_id"]),
                )

            version_by_id = {
                item.resource_id: item.version for item in request.base_record_versions
            }
            for index, action in enumerate(performed):
                event_type, occurred_at, precision, payload = self._event_fields(action, entry)
                if prior_resources is None:
                    cursor = await connection.execute(
                        """
                        insert into baby_data.care_events (
                            baby_id, event_type, occurred_at, ended_at, time_precision,
                            payload, source_entry_id, data_origin,
                            created_by_user_id, updated_by_user_id
                        ) values (%s, %s, %s, null, %s, %s, %s, 'USER', %s, %s)
                        returning care_event_id, version
                        """,
                        (
                            entry["baby_id"],
                            event_type,
                            occurred_at,
                            precision,
                            json.dumps(payload),
                            entry_id,
                            principal.user_id,
                            principal.user_id,
                        ),
                    )
                else:
                    resource_id = UUID(prior_resources["care_event_ids"][index])
                    expected_version = version_by_id[resource_id]
                    cursor = await connection.execute(
                        """
                        update baby_data.care_events
                           set event_type = %s, occurred_at = %s, ended_at = null,
                               time_precision = %s, payload = %s,
                               updated_by_user_id = %s, version = version + 1
                         where care_event_id = %s and baby_id = %s
                           and version = %s and status = 'ACTIVE'
                        returning care_event_id, version
                        """,
                        (
                            event_type,
                            occurred_at,
                            precision,
                            json.dumps(payload),
                            principal.user_id,
                            resource_id,
                            entry["baby_id"],
                            expected_version,
                        ),
                    )
                saved = await cursor.fetchone()
                if saved is None:
                    raise ApiException(
                        ErrorCode.VERSION_CONFLICT,
                        "A care event changed before correction confirmation.",
                    )
                care_event_ids.append(saved["care_event_id"])
                event_versions.append(saved["version"])

            content_edited = request.normalization_mode == "MANUAL" or (
                request.normalization_mode == "LLM"
                and run_result != request.content.model_dump(mode="json")
            )
            for index, state in enumerate(request.content.states):
                observed_at = state.observed_at or entry["occurred_at"]
                precision = (
                    state.time_precision.value
                    if state.observed_at is not None or entry["occurred_at"] is None
                    else entry["time_precision"]
                )
                confirmation_status = "USER_CORRECTED" if content_edited else "USER_CONFIRMED"
                visual = visual_state_code(list(state.state_codes))
                if prior_resources is None:
                    cursor = await connection.execute(
                        """
                        insert into baby_data.state_observations (
                            baby_id, episode_id, action_id, source_entry_id, phase,
                            observed_at, time_precision, state_codes, observation_source,
                            confirmation_status, visual_state_code, visual_mapping_version,
                            created_by_user_id, updated_by_user_id, confirmed_by_user_id,
                            data_origin
                        ) values (
                            %s, null, null, %s, %s, %s, %s, %s,
                            'SELF_REPORTED', %s, %s, %s, %s, %s, %s, 'USER'
                        ) returning state_observation_id, version
                        """,
                        (
                            entry["baby_id"],
                            entry_id,
                            state.phase,
                            observed_at,
                            precision,
                            list(state.state_codes),
                            confirmation_status,
                            visual,
                            VISUAL_MAPPING_VERSION,
                            principal.user_id,
                            principal.user_id,
                            principal.user_id,
                        ),
                    )
                else:
                    resource_id = UUID(prior_resources["state_observation_ids"][index])
                    expected_version = version_by_id[resource_id]
                    cursor = await connection.execute(
                        """
                        update baby_data.state_observations
                           set phase = %s, observed_at = %s, time_precision = %s,
                               state_codes = %s, observation_source = 'SELF_REPORTED',
                               confirmation_status = 'USER_CORRECTED',
                               visual_state_code = %s, visual_mapping_version = %s,
                               updated_by_user_id = %s, version = version + 1
                         where state_observation_id = %s and baby_id = %s and version = %s
                        returning state_observation_id, version
                        """,
                        (
                            state.phase,
                            observed_at,
                            precision,
                            list(state.state_codes),
                            visual,
                            VISUAL_MAPPING_VERSION,
                            principal.user_id,
                            resource_id,
                            entry["baby_id"],
                            expected_version,
                        ),
                    )
                saved = await cursor.fetchone()
                if saved is None:
                    raise ApiException(
                        ErrorCode.VERSION_CONFLICT,
                        "A state observation changed before correction confirmation.",
                    )
                state_ids.append(saved["state_observation_id"])
                state_versions.append(saved["version"])

            label_source = (
                "EXTRACTED"
                if request.normalization_mode == "LLM" and not content_edited
                else "EDITED"
                if request.normalization_mode == "LLM"
                else "CHOICE"
                if request.normalization_mode == "RULE"
                else "MANUAL"
            )
            label_ids: list[UUID] = []
            label_items: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
            for action in request.content.actions:
                value = action.model_dump(mode="json")
                label_items.append((f"ACTION:{action.action_ref}", value, value["evidence"]))
            for index, state in enumerate(request.content.states):
                value = state.model_dump(mode="json")
                label_items.append((f"STATE:{index}", value, value["evidence"]))
            for index, interpretation in enumerate(request.content.caregiver_interpretations):
                value = interpretation.model_dump(mode="json")
                label_items.append((f"INTERPRETATION:{index}", value, value["evidence"]))
            for index, unresolved in enumerate(request.content.unresolved):
                value = unresolved.model_dump(mode="json")
                label_items.append((f"UNRESOLVED:{index}", value, []))
            for code, value, evidence in label_items:
                cursor = await connection.execute(
                    """
                    insert into baby_data.label_annotations (
                        baby_id, entry_id, run_id, code, value, source,
                        evidence, confirmed_by_user_id, status
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE')
                    returning annotation_id
                    """,
                    (
                        entry["baby_id"],
                        entry_id,
                        request.run_id,
                        code,
                        json.dumps(value),
                        label_source,
                        json.dumps(evidence),
                        principal.user_id,
                    ),
                )
                label_ids.append((await cursor.fetchone())["annotation_id"])  # type: ignore[index]

            if self._confirmation_checkpoint is not None:
                self._confirmation_checkpoint()

            if entry["status"] == "NORMALIZING":
                await connection.execute(
                    """
                    update baby_data.normalization_runs
                       set status = 'STALE', lease_expires_at = null,
                           completed_at = clock_timestamp()
                     where entry_id = %s and input_revision = %s and status = 'RUNNING'
                    """,
                    (entry_id, request.input_revision),
                )
                await connection.execute(
                    """
                    update baby_data.raw_care_entries
                       set status = 'DRAFT', version = version + 1
                     where entry_id = %s and status = 'NORMALIZING'
                    """,
                    (entry_id,),
                )

            resources = ConfirmedResources(
                entry_id=entry_id,
                input_revision=request.input_revision,
                care_event_ids=care_event_ids,
                action_ids=[],
                action_group_ids=[],
                state_observation_ids=state_ids,
                outcome_ids=[],
                label_annotation_ids=label_ids,
            )
            cursor = await connection.execute(
                """
                update baby_data.raw_care_entries
                   set status = 'CONFIRMED', confirmed_resources = %s,
                       version = version + 1
                 where entry_id = %s and input_revision = %s
                   and status in ('DRAFT','REVIEW_READY','NEEDS_MANUAL_REVIEW')
                returning version
                """,
                (
                    json.dumps(resources.model_dump(mode="json")),
                    entry_id,
                    request.input_revision,
                ),
            )
            confirmed_entry = await cursor.fetchone()
            if confirmed_entry is None:
                raise ApiException(
                    ErrorCode.ALREADY_CONFIRMED,
                    "The entry was confirmed concurrently.",
                )

            changes: list[SharedChangeInput] = [
                ("CARE_EVENT", resource_id, version, False)
                for resource_id, version in zip(care_event_ids, event_versions, strict=True)
            ]
            changes.extend(
                ("STATE_OBSERVATION", resource_id, version, False)
                for resource_id, version in zip(state_ids, state_versions, strict=True)
            )
            if changes:
                baby_version = await self._backend._increment_context(
                    connection,
                    baby_id=entry["baby_id"],
                    resource_type="CARE_ENTRY",
                    resource_id=entry_id,
                    resource_version=confirmed_entry["version"],
                    reason="UPDATED" if correction else "CREATED",
                )
                changes.append(("BABY", entry["baby_id"], baby_version, False))
                await self._backend._record_shared_changes(
                    connection, baby_id=entry["baby_id"], changes=changes
                )

            await self._backend._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="CONFIRMED_RESOURCES",
                result_id=entry_id,
                response_status=200,
            )
            return resources

    @staticmethod
    def _state(row: dict[str, Any]) -> StateObservation:
        return StateObservation(
            state_observation_id=row["state_observation_id"],
            baby_id=row["baby_id"],
            episode_id=row["episode_id"],
            action_id=row["action_id"],
            source_entry_id=row["source_entry_id"],
            phase=row["phase"],
            observed_at=row["observed_at"],
            time_precision=row["time_precision"],
            state_codes=row["state_codes"],
            observation_source=row["observation_source"],
            confirmation_status=row["confirmation_status"],
            visual_state_code=row["visual_state_code"],
            visual_mapping_version=row["visual_mapping_version"],
            created_by_user_id=row["created_by_user_id"],
            updated_by_user_id=row["updated_by_user_id"],
            confirmed_by_user_id=row["confirmed_by_user_id"],
            data_origin=row["data_origin"],
            version=row["version"],
            recorded_at=row["recorded_at"],
            updated_at=row["updated_at"],
        )

    async def get_state_observation(
        self, principal: AuthenticatedPrincipal, state_observation_id: UUID
    ) -> StateObservation:
        async with self._backend.transaction(principal) as connection:
            cursor = await connection.execute(
                """
                select state_observation_id, baby_id, episode_id, action_id,
                       source_entry_id, phase::text as phase, observed_at,
                       time_precision::text as time_precision, state_codes,
                       observation_source, confirmation_status, visual_state_code,
                       visual_mapping_version, created_by_user_id, updated_by_user_id,
                       confirmed_by_user_id, data_origin::text as data_origin,
                       version, recorded_at, updated_at
                  from baby_data.state_observations
                 where state_observation_id = %s
                """,
                (state_observation_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise self._backend._not_found()
            return self._state(row)

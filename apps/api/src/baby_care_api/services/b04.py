from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import quote
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolClosed, PoolTimeout
from pydantic import TypeAdapter

from baby_care_api.core.errors import ApiException
from baby_care_api.models.b04 import (
    AcceptInvite,
    ActionAttempt,
    ActiveBaby,
    Baby,
    BabyAccess,
    BabyList,
    CareEntry,
    CareEntryPage,
    ChildDataVerification,
    Consent,
    ConsentPage,
    CreateAction,
    CreateBaby,
    CreateCareEntry,
    CreateInvite,
    CreateReauthenticationChallenge,
    DeletionJob,
    Failure,
    Invite,
    InvitePage,
    IssuedInvite,
    Membership,
    MembershipPage,
    MembershipRole,
    PatchBaby,
    PatchCareEntry,
    PatchMembership,
    ReauthenticationChallenge,
    ReauthenticationOperation,
    ReauthenticationProof,
    SessionRevocation,
    SessionRevocationScope,
    TimelineItem,
    TimelineItemPage,
)
from baby_care_api.models.care_events import (
    CareEvent,
    CareEventValue,
    CreateCareEvent,
    PatchCareEvent,
)
from baby_care_api.models.changes import Change, Changes
from baby_care_api.models.errors import ErrorCode, ErrorDetails
from baby_care_api.services.security import (
    AuthenticatedPrincipal,
    BabyAccessContext,
)
from baby_care_api.services.security import (
    MembershipRole as SecurityMembershipRole,
)

type DatabaseRow = dict[str, Any]
type DatabaseConnection = psycopg.AsyncConnection[DatabaseRow]
type IdempotencyReplay = tuple[str, UUID | None, int] | None
type SharedChangeInput = tuple[str, UUID, int, bool]


class PostgresBabyCareService:
    """B-04 application service using only the least-privilege ``baby_app`` role."""

    MAX_SHARED_CHANGES = 500

    def __init__(
        self,
        database_url: str,
        *,
        invite_base_url: str,
        proof_secret: str,
        child_data_production_enabled: bool,
        min_pool_size: int = 0,
        max_pool_size: int = 10,
        pool_timeout_seconds: float = 3.0,
    ) -> None:
        self._pool = AsyncConnectionPool[DatabaseConnection](
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            timeout=pool_timeout_seconds,
            open=False,
            kwargs={"application_name": "baby-care-api", "row_factory": dict_row},
        )
        self._invite_base_url = invite_base_url.rstrip("/")
        self._proof_secret = proof_secret.encode()
        self._child_data_production_enabled = child_data_production_enabled

    async def open(self) -> None:
        await self._pool.open(wait=False)

    async def close(self) -> None:
        await self._pool.close()

    async def probe(self) -> bool:
        try:
            async with self._pool.connection() as connection, connection.transaction():
                await connection.execute("set local role baby_app")
                cursor = await connection.execute("select 1 as ready")
                row = await cursor.fetchone()
                return row == {"ready": 1}
        except (psycopg.Error, PoolClosed, PoolTimeout):
            return False

    @asynccontextmanager
    async def transaction(
        self,
        principal: AuthenticatedPrincipal,
        *,
        require_live_session: bool = True,
    ) -> AsyncIterator[DatabaseConnection]:
        try:
            async with self._pool.connection() as connection, connection.transaction():
                await connection.execute("set local role baby_app")
                await connection.execute(
                    """
                    select
                        set_config('baby.request_user_id', %s, true),
                        set_config('baby.request_session_id', %s, true),
                        set_config('baby.request_issued_at', %s, true)
                    """,
                    (
                        str(principal.user_id),
                        str(principal.session_id),
                        principal.issued_at.isoformat(),
                    ),
                )
                if require_live_session:
                    cursor = await connection.execute(
                        "select baby_private.has_live_request_context() as live"
                    )
                    row = await cursor.fetchone()
                    if row is None or row["live"] is not True:
                        raise ApiException(
                            ErrorCode.SESSION_REVOKED,
                            "The authenticated session is no longer active.",
                        )
                    await connection.execute(
                        """
                        insert into baby_data.observed_auth_sessions (
                            user_id, session_id, token_issued_at, token_expires_at
                        ) values (%s, %s, %s, %s)
                        on conflict (user_id, session_id) do update
                            set token_issued_at = greatest(
                                    baby_data.observed_auth_sessions.token_issued_at,
                                    excluded.token_issued_at
                                ),
                                token_expires_at = greatest(
                                    baby_data.observed_auth_sessions.token_expires_at,
                                    excluded.token_expires_at
                                ),
                                last_seen_at = clock_timestamp()
                        """,
                        (
                            principal.user_id,
                            principal.session_id,
                            principal.issued_at,
                            principal.expires_at,
                        ),
                    )
                yield connection
        except ApiException:
            raise
        except (psycopg.Error, PoolClosed, PoolTimeout) as exc:
            raise ApiException.service_unavailable() from exc

    async def require_active_baby_access(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
    ) -> BabyAccessContext:
        async with self.transaction(principal) as connection:
            cursor = await connection.execute(
                """
                select b.baby_id, m.membership_id, m.role::text as role,
                       m.version as membership_version, b.version as baby_version
                  from baby_data.babies b
                  join baby_data.baby_memberships m on m.baby_id = b.baby_id
                 where b.baby_id = %s
                   and b.status = 'ACTIVE'
                   and m.user_id = %s
                   and m.status = 'ACTIVE'
                """,
                (baby_id, principal.user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return BabyAccessContext(
            principal=principal,
            baby_id=row["baby_id"],
            membership_id=row["membership_id"],
            role=SecurityMembershipRole(row["role"]),
            membership_version=row["membership_version"],
            baby_version=row["baby_version"],
        )

    @staticmethod
    def _not_found() -> ApiException:
        return ApiException(ErrorCode.RESOURCE_NOT_FOUND, "The requested resource was not found.")

    @staticmethod
    def _owner_only() -> ApiException:
        return ApiException(ErrorCode.OWNER_ONLY, "Only the owner can perform this action.")

    @staticmethod
    def _normalized_digest(payload: Mapping[str, Any]) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(normalized.encode()).hexdigest()

    @staticmethod
    async def _advisory_lock(connection: DatabaseConnection, namespace: str) -> None:
        await connection.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (namespace,),
        )

    async def _reserve_idempotency(
        self,
        connection: DatabaseConnection,
        principal: AuthenticatedPrincipal,
        *,
        method: str,
        path: str,
        key: UUID,
        payload: Mapping[str, Any],
    ) -> IdempotencyReplay:
        digest = self._normalized_digest(payload)
        cursor = await connection.execute(
            """
            insert into baby_data.idempotency_records (
                user_id, method, target_path, idempotency_key, request_sha256
            ) values (%s, %s, %s, %s, %s)
            on conflict (user_id, method, target_path, idempotency_key) do nothing
            returning status::text as status
            """,
            (principal.user_id, method, path, key, digest),
        )
        if await cursor.fetchone() is not None:
            return None
        cursor = await connection.execute(
            """
            select request_sha256, status::text as status, result_type, result_id,
                   response_status, expires_at
              from baby_data.idempotency_records
             where user_id = %s and method = %s and target_path = %s
               and idempotency_key = %s
             for update
            """,
            (principal.user_id, method, path, key),
        )
        row = await cursor.fetchone()
        if row is None or row["expires_at"] <= datetime.now(UTC):
            raise ApiException(
                ErrorCode.OPERATION_IN_PROGRESS, "The operation cannot be recovered."
            )
        if not hmac.compare_digest(row["request_sha256"], digest):
            raise ApiException(
                ErrorCode.IDEMPOTENCY_KEY_REUSED,
                "The idempotency key was already used for a different request.",
            )
        if row["status"] != "COMPLETE":
            raise ApiException(
                ErrorCode.OPERATION_IN_PROGRESS,
                "The original operation is still in progress.",
                retryable=True,
            )
        return row["result_type"], row["result_id"], row["response_status"]

    async def _complete_idempotency(
        self,
        connection: DatabaseConnection,
        principal: AuthenticatedPrincipal,
        *,
        method: str,
        path: str,
        key: UUID,
        result_type: str,
        result_id: UUID | None,
        response_status: int,
    ) -> None:
        await connection.execute(
            """
            update baby_data.idempotency_records
               set status = 'COMPLETE', result_type = %s, result_id = %s,
                   response_status = %s, completed_at = clock_timestamp()
             where user_id = %s and method = %s and target_path = %s
               and idempotency_key = %s and status = 'IN_PROGRESS'
            """,
            (
                result_type,
                result_id,
                response_status,
                principal.user_id,
                method,
                path,
                key,
            ),
        )

    @staticmethod
    def _baby(row: DatabaseRow, prefix: str = "") -> Baby:
        return Baby(
            baby_id=row[f"{prefix}baby_id"],
            owner_user_id=row[f"{prefix}owner_user_id"],
            alias=row[f"{prefix}alias"],
            birth_date=row[f"{prefix}birth_date"],
            feeding_mode=row[f"{prefix}feeding_mode"],
            timezone=row[f"{prefix}timezone"],
            status=row[f"{prefix}baby_status"],
            context_revision=row[f"{prefix}context_revision"],
            version=row[f"{prefix}baby_version"],
            recorded_at=row[f"{prefix}baby_recorded_at"],
            updated_at=row[f"{prefix}baby_updated_at"],
        )

    @staticmethod
    def _membership(row: DatabaseRow, prefix: str = "") -> Membership:
        return Membership(
            membership_id=row[f"{prefix}membership_id"],
            baby_id=row[f"{prefix}baby_id"],
            user_id=row[f"{prefix}user_id"],
            role=row[f"{prefix}role"],
            relationship=row[f"{prefix}relationship"],
            display_name=row[f"{prefix}display_name"],
            status=row[f"{prefix}membership_status"],
            version=row[f"{prefix}membership_version"],
            recorded_at=row[f"{prefix}membership_recorded_at"],
            updated_at=row[f"{prefix}membership_updated_at"],
        )

    @staticmethod
    def _invite(row: DatabaseRow) -> Invite:
        return Invite(
            invite_id=row["invite_id"],
            baby_id=row["baby_id"],
            inviter_user_id=row["inviter_user_id"],
            email=row["email"],
            status=row["status"],
            expires_at=row["expires_at"],
            version=row["version"],
            recorded_at=row["recorded_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _consent(row: DatabaseRow) -> Consent:
        return Consent(
            consent_id=row["consent_id"],
            baby_id=row["baby_id"],
            actor_user_id=row["actor_user_id"],
            scope=row["scope"],
            status=row["status"],
            policy_version=row["policy_version"],
            granted_at=row["granted_at"],
            revoked_at=row["revoked_at"],
            version=row["version"],
        )

    @staticmethod
    def _care_event(row: DatabaseRow) -> CareEvent:
        event = {
            "type": row["event_type"],
            "occurred_at": row["occurred_at"],
            "ended_at": row["ended_at"],
            "time_precision": row["time_precision"],
            "payload": row["payload"],
        }
        return CareEvent(
            care_event_id=row["care_event_id"],
            baby_id=row["baby_id"],
            created_by_user_id=row["created_by_user_id"],
            updated_by_user_id=row["updated_by_user_id"],
            source_entry_id=row["source_entry_id"],
            status=row["status"],
            event=TypeAdapter(CareEventValue).validate_python(event),
            data_origin=row["data_origin"],
            version=row["version"],
            recorded_at=row["recorded_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _care_entry(row: DatabaseRow) -> CareEntry:
        return CareEntry(
            entry_id=row["entry_id"],
            baby_id=row["baby_id"],
            author_user_id=row["author_user_id"],
            original_author_user_id=row["original_author_user_id"],
            episode_id=row["episode_id"],
            input_mode=row["input_mode"],
            raw_text=row["raw_text"],
            choices=row["choices"],
            occurred_at=row["occurred_at"],
            time_precision=row["time_precision"],
            input_revision=row["input_revision"],
            status=row["status"],
            normalization_run_id=row.get("normalization_run_id"),
            normalized_content=row.get("normalized_content"),
            supersedes_entry_id=row["supersedes_entry_id"],
            base_record_versions=row["base_record_versions"],
            confirmed_resources=row["confirmed_resources"],
            confirmed_by_user_id=row["confirmed_by_user_id"],
            confirmed_at=row["confirmed_at"],
            data_origin=row["data_origin"],
            version=row["version"],
            recorded_at=row["recorded_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _deletion(row: DatabaseRow) -> DeletionJob:
        return DeletionJob(
            deletion_job_id=row["deletion_job_id"],
            requester_user_id=row["requester_user_id"],
            baby_id=row["baby_id"],
            scope=row["scope"],
            resource_id=row["resource_id"],
            status=row["status"],
            access_blocked=row["access_blocked"],
            requested_at=row["requested_at"],
            completed_at=row["completed_at"],
            failure=row["failure"],
            pending_categories=row["pending_categories"],
            attempt_no=row["attempt_no"],
        )

    async def _baby_access(
        self,
        connection: DatabaseConnection,
        baby_id: UUID,
        user_id: UUID,
    ) -> BabyAccess:
        cursor = await connection.execute(
            """
            select b.baby_id, b.owner_user_id, b.alias, b.birth_date,
                   b.feeding_mode, b.timezone, b.status::text as baby_status,
                   b.context_revision, b.version as baby_version,
                   b.recorded_at as baby_recorded_at, b.updated_at as baby_updated_at,
                   m.membership_id, m.user_id, m.role::text as role,
                   m.relationship, m.display_name,
                   m.status::text as membership_status,
                   m.version as membership_version,
                   m.recorded_at as membership_recorded_at,
                   m.updated_at as membership_updated_at
              from baby_data.babies b
              join baby_data.baby_memberships m on m.baby_id = b.baby_id
             where b.baby_id = %s and b.status = 'ACTIVE'
               and m.user_id = %s and m.status = 'ACTIVE'
            """,
            (baby_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return BabyAccess(baby=self._baby(row), membership=self._membership(row))

    async def list_babies(self, principal: AuthenticatedPrincipal) -> BabyList:
        async with self.transaction(principal) as connection:
            cursor = await connection.execute(
                """
                select b.baby_id, b.owner_user_id, b.alias, b.birth_date,
                       b.feeding_mode, b.timezone, b.status::text as baby_status,
                       b.context_revision, b.version as baby_version,
                       b.recorded_at as baby_recorded_at, b.updated_at as baby_updated_at,
                       m.membership_id, m.user_id, m.role::text as role,
                       m.relationship, m.display_name,
                       m.status::text as membership_status,
                       m.version as membership_version,
                       m.recorded_at as membership_recorded_at,
                       m.updated_at as membership_updated_at
                  from baby_data.babies b
                  join baby_data.baby_memberships m on m.baby_id = b.baby_id
                 where b.status = 'ACTIVE' and m.user_id = %s and m.status = 'ACTIVE'
                 order by b.recorded_at
                """,
                (principal.user_id,),
            )
            rows = await cursor.fetchall()
        return BabyList(
            items=[
                BabyAccess(baby=self._baby(row), membership=self._membership(row)) for row in rows
            ]
        )

    async def create_baby(
        self,
        principal: AuthenticatedPrincipal,
        request: CreateBaby,
        *,
        path: str,
    ) -> BabyAccess:
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
                _, baby_id, _ = replay
                if baby_id is None:
                    raise self._not_found()
                return await self._baby_access(connection, baby_id, principal.user_id)
            try:
                cursor = await connection.execute(
                    """
                    select created_baby_id, created_membership_id
                      from baby_private.create_baby_with_owner(%s, %s, %s, %s, %s)
                    """,
                    (
                        request.alias,
                        request.birth_date,
                        request.feeding_mode.value,
                        request.timezone,
                        principal.email.split("@", 1)[0][:80],
                    ),
                )
                created = await cursor.fetchone()
                if created is None:
                    raise ApiException.service_unavailable()
                baby_id = created["created_baby_id"]
                membership_id = created["created_membership_id"]
            except psycopg.errors.UniqueViolation as exc:
                raise ApiException(
                    ErrorCode.OWNER_BABY_LIMIT,
                    "A user can own only one active baby.",
                ) from exc
            await self._record_shared_changes(
                connection,
                baby_id=baby_id,
                changes=[
                    ("BABY", baby_id, 1, False),
                    ("MEMBERSHIP", membership_id, 1, False),
                ],
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="BABY_ACCESS",
                result_id=baby_id,
                response_status=201,
            )
            return await self._baby_access(connection, baby_id, principal.user_id)

    async def get_active_baby(self, principal: AuthenticatedPrincipal) -> ActiveBaby:
        async with self.transaction(principal) as connection:
            cursor = await connection.execute(
                """
                select p.active_baby_id
                  from baby_data.user_preferences p
                  join baby_data.babies b on b.baby_id = p.active_baby_id
                  join baby_data.baby_memberships m on m.baby_id = b.baby_id
                 where p.user_id = %s and b.status = 'ACTIVE'
                   and m.user_id = %s and m.status = 'ACTIVE'
                """,
                (principal.user_id, principal.user_id),
            )
            row = await cursor.fetchone()
        return ActiveBaby(baby_id=None if row is None else row["active_baby_id"])

    async def set_active_baby(
        self,
        principal: AuthenticatedPrincipal,
        *,
        client_request_id: UUID,
        baby_id: UUID,
        path: str,
    ) -> ActiveBaby:
        payload = {"client_request_id": str(client_request_id), "baby_id": str(baby_id)}
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="PUT",
                path=path,
                key=client_request_id,
                payload=payload,
            )
            if replay is not None:
                await self._baby_access(connection, baby_id, principal.user_id)
                return ActiveBaby(baby_id=baby_id)
            await self._baby_access(connection, baby_id, principal.user_id)
            await connection.execute(
                """
                insert into baby_data.user_preferences (user_id, active_baby_id)
                values (%s, %s)
                on conflict (user_id) do update
                    set active_baby_id = excluded.active_baby_id,
                        version = baby_data.user_preferences.version + 1
                """,
                (principal.user_id, baby_id),
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="PUT",
                path=path,
                key=client_request_id,
                result_type="ACTIVE_BABY",
                result_id=baby_id,
                response_status=200,
            )
        return ActiveBaby(baby_id=baby_id)

    async def patch_baby(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        request: PatchBaby,
        *,
        path: str,
    ) -> Baby:
        payload = request.model_dump(mode="json")
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="PATCH",
                path=path,
                key=request.client_request_id,
                payload=payload,
            )
            if replay is None:
                cursor = await connection.execute(
                    "select baby_private.is_active_owner(%s) as owner",
                    (baby_id,),
                )
                if not (await cursor.fetchone())["owner"]:  # type: ignore[index]
                    await self._baby_access(connection, baby_id, principal.user_id)
                    raise self._owner_only()
                await self._lock_shared_change_feed(connection, baby_id)
                values = {
                    "alias": request.alias,
                    "birth_date": request.birth_date,
                    "feeding_mode": None
                    if request.feeding_mode is None
                    else request.feeding_mode.value,
                    "timezone": request.timezone,
                }
                cursor = await connection.execute(
                    """
                    update baby_data.babies
                       set alias = coalesce(%s, alias),
                           birth_date = coalesce(%s, birth_date),
                           feeding_mode = coalesce(%s, feeding_mode),
                           timezone = coalesce(%s, timezone),
                           version = version + 1
                     where baby_id = %s and status = 'ACTIVE' and version = %s
                    returning baby_id
                    """,
                    (
                        values["alias"],
                        values["birth_date"],
                        values["feeding_mode"],
                        values["timezone"],
                        baby_id,
                        request.version,
                    ),
                )
                if await cursor.fetchone() is None:
                    raise ApiException(
                        ErrorCode.VERSION_CONFLICT,
                        "The baby profile changed before this update.",
                    )
                await self._record_shared_changes(
                    connection,
                    baby_id=baby_id,
                    changes=[("BABY", baby_id, request.version + 1, False)],
                )
                await self._complete_idempotency(
                    connection,
                    principal,
                    method="PATCH",
                    path=path,
                    key=request.client_request_id,
                    result_type="BABY",
                    result_id=baby_id,
                    response_status=200,
                )
            access = await self._baby_access(connection, baby_id, principal.user_id)
            return access.baby

    async def list_members(
        self, principal: AuthenticatedPrincipal, baby_id: UUID
    ) -> MembershipPage:
        async with self.transaction(principal) as connection:
            await self._baby_access(connection, baby_id, principal.user_id)
            cursor = await connection.execute(
                """
                select membership_id, baby_id, user_id, role::text as role,
                       relationship, display_name, status::text as membership_status,
                       version as membership_version, recorded_at as membership_recorded_at,
                       updated_at as membership_updated_at
                  from baby_data.baby_memberships
                 where baby_id = %s and status = 'ACTIVE'
                 order by case role when 'OWNER' then 0 else 1 end, recorded_at
                """,
                (baby_id,),
            )
            rows = await cursor.fetchall()
        return MembershipPage(items=[self._membership(row) for row in rows])

    async def patch_my_relationship(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        request: PatchMembership,
        *,
        path: str,
    ) -> Membership:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="PATCH",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is None:
                await self._lock_shared_change_feed(connection, baby_id)
                cursor = await connection.execute(
                    """
                    update baby_data.baby_memberships
                       set relationship = %s, version = version + 1
                     where baby_id = %s and user_id = %s and status = 'ACTIVE'
                       and version = %s
                    returning membership_id
                    """,
                    (request.relationship.value, baby_id, principal.user_id, request.version),
                )
                row = await cursor.fetchone()
                if row is None:
                    await self._baby_access(connection, baby_id, principal.user_id)
                    raise ApiException(
                        ErrorCode.VERSION_CONFLICT,
                        "The membership changed before this update.",
                    )
                membership_id = row["membership_id"]
                await self._record_shared_changes(
                    connection,
                    baby_id=baby_id,
                    changes=[("MEMBERSHIP", membership_id, request.version + 1, False)],
                )
                await self._complete_idempotency(
                    connection,
                    principal,
                    method="PATCH",
                    path=path,
                    key=request.client_request_id,
                    result_type="MEMBERSHIP",
                    result_id=membership_id,
                    response_status=200,
                )
            else:
                _, membership_id, _ = replay
            cursor = await connection.execute(
                """
                select membership_id, baby_id, user_id, role::text as role,
                       relationship, display_name, status::text as membership_status,
                       version as membership_version, recorded_at as membership_recorded_at,
                       updated_at as membership_updated_at
                  from baby_data.baby_memberships where membership_id = %s
                """,
                (membership_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise self._not_found()
            return self._membership(row)

    async def remove_membership(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        target_user_id: UUID,
        *,
        version: int,
        idempotency_key: UUID,
        path: str,
    ) -> Membership:
        payload = {"version": version, "target_user_id": str(target_user_id)}
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                payload=payload,
            )
            if replay is not None:
                _, membership_id, _ = replay
                cursor = await connection.execute(
                    """
                    select membership_id, baby_id, user_id, role::text as role,
                           relationship, display_name, status::text as membership_status,
                           version as membership_version,
                           recorded_at as membership_recorded_at,
                           updated_at as membership_updated_at
                      from baby_data.baby_memberships where membership_id = %s
                    """,
                    (membership_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise self._not_found()
                return self._membership(row)

            access = await self._baby_access(connection, baby_id, principal.user_id)
            cursor = await connection.execute(
                """
                select membership_id, baby_id, user_id, role::text as role,
                       relationship, display_name, status::text as membership_status,
                       version as membership_version,
                       recorded_at as membership_recorded_at,
                       updated_at as membership_updated_at
                  from baby_data.baby_memberships
                 where baby_id = %s and user_id = %s and status = 'ACTIVE'
                """,
                (baby_id, target_user_id),
            )
            current = await cursor.fetchone()
            if current is None:
                raise self._not_found()
            if current["role"] == "OWNER":
                raise ApiException(
                    ErrorCode.OWNER_REQUIRED,
                    "The owner cannot leave an active baby space.",
                )
            if (
                target_user_id != principal.user_id
                and access.membership.role is not MembershipRole.OWNER
            ):
                raise self._owner_only()
            new_status = "LEFT" if target_user_id == principal.user_id else "REVOKED"
            if current["membership_version"] != version:
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The membership changed before this request.",
                )
            await self._record_shared_changes(
                connection,
                baby_id=baby_id,
                changes=[("MEMBERSHIP", current["membership_id"], version + 1, True)],
            )
            cursor = await connection.execute(
                """
                update baby_data.baby_memberships
                   set status = %s, version = version + 1
                 where membership_id = %s and version = %s
                returning updated_at
                """,
                (new_status, current["membership_id"], version),
            )
            updated = await cursor.fetchone()
            if updated is None:
                raise ApiException(ErrorCode.VERSION_CONFLICT, "The membership changed.")
            current["membership_status"] = new_status
            current["membership_version"] = version + 1
            current["membership_updated_at"] = updated["updated_at"]
            await self._complete_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                result_type="MEMBERSHIP",
                result_id=current["membership_id"],
                response_status=200,
            )
            return self._membership(current)

    async def _require_owner(
        self,
        connection: DatabaseConnection,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
    ) -> None:
        access = await self._baby_access(connection, baby_id, principal.user_id)
        if access.membership.role is not MembershipRole.OWNER:
            raise self._owner_only()

    async def list_invites(self, principal: AuthenticatedPrincipal, baby_id: UUID) -> InvitePage:
        async with self.transaction(principal) as connection:
            await self._require_owner(connection, principal, baby_id)
            cursor = await connection.execute(
                """
                select invite_id, baby_id, inviter_user_id, email, status::text as status,
                       expires_at, version, recorded_at, updated_at
                  from baby_data.invitations
                 where baby_id = %s order by recorded_at desc
                """,
                (baby_id,),
            )
            rows = await cursor.fetchall()
        return InvitePage(items=[self._invite(row) for row in rows])

    async def _consume_reauthentication_proof(
        self,
        connection: DatabaseConnection,
        principal: AuthenticatedPrincipal,
        *,
        proof_token: str | None,
        operation: ReauthenticationOperation,
        baby_id: UUID,
        request_id: UUID,
    ) -> None:
        if not proof_token:
            raise ApiException(ErrorCode.REAUTH_REQUIRED, "Fresh reauthentication is required.")
        token_hash = hashlib.sha256(proof_token.encode()).digest()
        cursor = await connection.execute(
            """
            select proof_id
              from baby_data.reauthentication_proofs
             where token_sha256 = %s and user_id = %s and session_id = %s
               and operation = %s and baby_id = %s and consumed_at is null
               and expires_at > statement_timestamp()
             for update
            """,
            (
                token_hash,
                principal.user_id,
                principal.session_id,
                operation.value,
                baby_id,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ApiException(
                ErrorCode.REAUTH_PROOF_INVALID,
                "The reauthentication proof is invalid, expired, or already used.",
            )
        await connection.execute(
            """
            update baby_data.reauthentication_proofs
               set consumed_at = clock_timestamp(), consumed_request_id = %s
             where proof_id = %s
            """,
            (request_id, row["proof_id"]),
        )

    async def _start_security_attempt(
        self,
        principal: AuthenticatedPrincipal,
        *,
        action: str,
        target: str,
        maximum: int,
        window_minutes: int = 60,
    ) -> int:
        target_hash = hashlib.sha256(target.encode()).hexdigest()
        async with self.transaction(principal) as connection:
            cursor = await connection.execute(
                """
                select count(*)::integer as attempts
                  from baby_data.security_attempts
                 where user_id = %s and action = %s and target_hash = %s
                   and recorded_at > statement_timestamp() - make_interval(mins => %s)
                """,
                (principal.user_id, action, target_hash, window_minutes),
            )
            attempts = (await cursor.fetchone())["attempts"]  # type: ignore[index]
            if attempts >= maximum:
                raise ApiException(
                    ErrorCode.RATE_LIMITED,
                    "Too many attempts were made. Try again later.",
                    retryable=True,
                    details=ErrorDetails.empty().model_copy(
                        update={"retry_after_seconds": float(window_minutes * 60)}
                    ),
                )
            cursor = await connection.execute(
                """
                insert into baby_data.security_attempts (
                    user_id, action, target_hash, succeeded
                ) values (%s, %s, %s, false)
                returning attempt_id
                """,
                (principal.user_id, action, target_hash),
            )
            return cast(int, (await cursor.fetchone())["attempt_id"])  # type: ignore[index]

    @staticmethod
    async def _mark_security_attempt_succeeded(
        connection: DatabaseConnection,
        attempt_id: int,
    ) -> None:
        await connection.execute(
            """
            update baby_data.security_attempts
               set succeeded = true
             where attempt_id = %s
            """,
            (attempt_id,),
        )

    def _invite_url(self, invite_id: UUID, token: str) -> str:
        return f"{self._invite_base_url}/{invite_id}#{quote(token, safe='')}"

    async def create_invite(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        request: CreateInvite,
        *,
        proof_token: str | None,
        path: str,
    ) -> IssuedInvite:
        attempt_id = await self._start_security_attempt(
            principal,
            action="INVITE_CREATE",
            target=f"{baby_id}:{request.email}",
            maximum=10,
        )
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                _, invite_id, _ = replay
                invite = await self._get_invite(connection, invite_id)
                await self._mark_security_attempt_succeeded(connection, attempt_id)
                return IssuedInvite(invite=invite, invite_url=None, link_reissue_required=True)
            await self._require_owner(connection, principal, baby_id)
            await self._advisory_lock(
                connection,
                f"invite:{baby_id}:{request.email.lower()}",
            )
            await self._consume_reauthentication_proof(
                connection,
                principal,
                proof_token=proof_token,
                operation=ReauthenticationOperation.CREATE_INVITE,
                baby_id=baby_id,
                request_id=request.client_request_id,
            )
            await connection.execute(
                """
                update baby_data.invitations
                   set status = 'REVOKED', version = version + 1
                 where baby_id = %s and lower(email) = lower(%s) and status = 'PENDING'
                """,
                (baby_id, request.email),
            )
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).digest()
            cursor = await connection.execute(
                """
                insert into baby_data.invitations (
                    baby_id, inviter_user_id, email, token_sha256, expires_at
                ) values (%s, %s, %s, %s, clock_timestamp() + interval '24 hours')
                returning invite_id
                """,
                (baby_id, principal.user_id, request.email, token_hash),
            )
            invite_id = (await cursor.fetchone())["invite_id"]  # type: ignore[index]
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="INVITE",
                result_id=invite_id,
                response_status=201,
            )
            await self._mark_security_attempt_succeeded(connection, attempt_id)
            invite = await self._get_invite(connection, invite_id)
            return IssuedInvite(
                invite=invite,
                invite_url=self._invite_url(invite_id, token),
                link_reissue_required=False,
            )

    async def _get_invite(self, connection: DatabaseConnection, invite_id: UUID | None) -> Invite:
        if invite_id is None:
            raise self._not_found()
        cursor = await connection.execute(
            """
            select invite_id, baby_id, inviter_user_id, email, status::text as status,
                   expires_at, version, recorded_at, updated_at
              from baby_data.invitations where invite_id = %s
            """,
            (invite_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return self._invite(row)

    async def reissue_invite(
        self,
        principal: AuthenticatedPrincipal,
        invite_id: UUID,
        *,
        client_request_id: UUID,
        proof_token: str | None,
        path: str,
    ) -> IssuedInvite:
        attempt_id = await self._start_security_attempt(
            principal,
            action="INVITE_CREATE",
            target=f"reissue:{invite_id}",
            maximum=10,
        )
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=client_request_id,
                payload={"client_request_id": str(client_request_id)},
            )
            if replay is not None:
                _, new_invite_id, _ = replay
                invite = await self._get_invite(connection, new_invite_id)
                await self._mark_security_attempt_succeeded(connection, attempt_id)
                return IssuedInvite(invite=invite, invite_url=None, link_reissue_required=True)
            old = await self._get_invite(connection, invite_id)
            await self._require_owner(connection, principal, old.baby_id)
            await self._advisory_lock(
                connection,
                f"invite:{old.baby_id}:{old.email.lower()}",
            )
            old = await self._get_invite(connection, invite_id)
            await self._consume_reauthentication_proof(
                connection,
                principal,
                proof_token=proof_token,
                operation=ReauthenticationOperation.CREATE_INVITE,
                baby_id=old.baby_id,
                request_id=client_request_id,
            )
            if old.status.value != "PENDING":
                code = (
                    ErrorCode.INVITE_ALREADY_USED
                    if old.status.value == "ACCEPTED"
                    else ErrorCode.INVITE_REVOKED
                )
                raise ApiException(code, "Only a pending invitation can be reissued.")
            await connection.execute(
                """
                update baby_data.invitations
                   set status = 'REVOKED', version = version + 1
                 where invite_id = %s and status = 'PENDING'
                """,
                (invite_id,),
            )
            token = secrets.token_urlsafe(32)
            cursor = await connection.execute(
                """
                insert into baby_data.invitations (
                    baby_id, inviter_user_id, email, token_sha256, expires_at
                ) values (%s, %s, %s, %s, clock_timestamp() + interval '24 hours')
                returning invite_id
                """,
                (
                    old.baby_id,
                    principal.user_id,
                    old.email,
                    hashlib.sha256(token.encode()).digest(),
                ),
            )
            new_invite_id = (await cursor.fetchone())["invite_id"]  # type: ignore[index]
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=client_request_id,
                result_type="INVITE",
                result_id=new_invite_id,
                response_status=201,
            )
            await self._mark_security_attempt_succeeded(connection, attempt_id)
            invite = await self._get_invite(connection, new_invite_id)
            return IssuedInvite(
                invite=invite,
                invite_url=self._invite_url(new_invite_id, token),
                link_reissue_required=False,
            )

    async def revoke_invite(
        self,
        principal: AuthenticatedPrincipal,
        invite_id: UUID,
        *,
        version: int,
        idempotency_key: UUID,
        path: str,
    ) -> Invite:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                payload={"version": version},
            )
            if replay is None:
                invite = await self._get_invite(connection, invite_id)
                await self._require_owner(connection, principal, invite.baby_id)
                if invite.status.value != "PENDING":
                    raise ApiException(
                        ErrorCode.INVITE_ALREADY_USED
                        if invite.status.value == "ACCEPTED"
                        else ErrorCode.INVITE_REVOKED,
                        "The invitation is no longer pending.",
                    )
                cursor = await connection.execute(
                    """
                    update baby_data.invitations
                       set status = 'REVOKED', version = version + 1
                     where invite_id = %s and version = %s and status = 'PENDING'
                    returning invite_id
                    """,
                    (invite_id, version),
                )
                if await cursor.fetchone() is None:
                    raise ApiException(ErrorCode.VERSION_CONFLICT, "The invitation changed.")
                await self._complete_idempotency(
                    connection,
                    principal,
                    method="DELETE",
                    path=path,
                    key=idempotency_key,
                    result_type="INVITE",
                    result_id=invite_id,
                    response_status=200,
                )
            return await self._get_invite(connection, invite_id)

    async def accept_invite(
        self,
        principal: AuthenticatedPrincipal,
        request: AcceptInvite,
        *,
        path: str,
    ) -> BabyAccess:
        token_hash = hashlib.sha256(request.token.encode()).digest()
        attempt_id = await self._start_security_attempt(
            principal,
            action="INVITE_ACCEPT",
            target=hashlib.sha256(request.token.encode()).hexdigest(),
            maximum=20,
        )
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                _, baby_id, _ = replay
                if baby_id is None:
                    raise self._not_found()
                await self._mark_security_attempt_succeeded(connection, attempt_id)
                return await self._baby_access(connection, baby_id, principal.user_id)
            try:
                cursor = await connection.execute(
                    """
                    select accepted_baby_id, accepted_membership_id
                      from baby_private.accept_invitation(%s, %s, %s, %s)
                    """,
                    (
                        token_hash,
                        principal.email,
                        request.relationship.value,
                        request.policy_version,
                    ),
                )
                accepted = await cursor.fetchone()
            except psycopg.Error as exc:
                message = str(exc)
                mapping = {
                    "B04_INVITE_NOT_FOUND": (ErrorCode.RESOURCE_NOT_FOUND, 404),
                    "B04_INVITE_ALREADY_USED": (ErrorCode.INVITE_ALREADY_USED, 409),
                    "B04_INVITE_REVOKED": (ErrorCode.INVITE_REVOKED, 410),
                    "B04_INVITE_EXPIRED": (ErrorCode.INVITE_EXPIRED, 410),
                    "B04_INVITE_EMAIL_MISMATCH": (ErrorCode.INVITE_EMAIL_MISMATCH, 403),
                    "B04_ALREADY_MEMBER": (ErrorCode.ALREADY_MEMBER, 409),
                }
                for marker, (code, _) in mapping.items():
                    if marker in message:
                        raise ApiException(code, "The invitation cannot be accepted.") from exc
                raise
            if accepted is None:
                raise self._not_found()
            baby_id = accepted["accepted_baby_id"]
            await self._record_shared_changes(
                connection,
                baby_id=baby_id,
                changes=[
                    (
                        "MEMBERSHIP",
                        accepted["accepted_membership_id"],
                        1,
                        False,
                    )
                ],
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="BABY_ACCESS",
                result_id=baby_id,
                response_status=200,
            )
            await self._mark_security_attempt_succeeded(connection, attempt_id)
            return await self._baby_access(connection, baby_id, principal.user_id)

    async def list_consents(self, principal: AuthenticatedPrincipal, baby_id: UUID) -> ConsentPage:
        async with self.transaction(principal) as connection:
            cursor = await connection.execute(
                """
                select consent_id, baby_id, actor_user_id, scope::text as scope,
                       status::text as status, policy_version, granted_at, revoked_at, version
                  from baby_data.consents
                 where baby_id = %s
                 order by recorded_at desc
                """,
                (baby_id,),
            )
            rows = await cursor.fetchall()
            if not rows:
                cursor = await connection.execute(
                    "select baby_private.has_membership_history(%s) as known",
                    (baby_id,),
                )
                if not (await cursor.fetchone())["known"]:  # type: ignore[index]
                    raise self._not_found()
        return ConsentPage(items=[self._consent(row) for row in rows])

    async def set_consent(
        self,
        principal: AuthenticatedPrincipal,
        *,
        baby_id: UUID,
        scope: str,
        granted: bool,
        policy_version: str,
        expected_version: int,
        client_request_id: UUID,
        path: str,
        proof_token: str | None = None,
        personal: bool = False,
    ) -> Consent:
        payload = {
            "client_request_id": str(client_request_id),
            "baby_id": str(baby_id),
            "scope": scope,
            "granted": granted,
            "policy_version": policy_version,
            "version": expected_version,
        }
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="PUT",
                path=path,
                key=client_request_id,
                payload=payload,
            )
            if replay is not None:
                _, consent_id, _ = replay
                return await self._get_consent(connection, consent_id)
            await self._advisory_lock(
                connection,
                f"consent:{baby_id}:{principal.user_id}:{scope}",
            )
            if personal:
                cursor = await connection.execute(
                    """
                    select baby_private.has_active_baby_access(%s) as active,
                           baby_private.has_membership_history(%s) as known
                    """,
                    (baby_id, baby_id),
                )
                membership = await cursor.fetchone()
                if membership is None or not membership["known"]:
                    raise self._not_found()
                if granted and not membership["active"]:
                    raise ApiException(
                        ErrorCode.CONSENT_REQUIRED,
                        "A former member cannot grant new training consent.",
                    )
            else:
                await self._require_owner(connection, principal, baby_id)
                if scope == "BABY_TRAINING" and granted:
                    await self._consume_reauthentication_proof(
                        connection,
                        principal,
                        proof_token=proof_token,
                        operation=ReauthenticationOperation.ENABLE_BABY_TRAINING,
                        baby_id=baby_id,
                        request_id=client_request_id,
                    )
                    verification = await self._child_verification(connection, principal, baby_id)
                    if not verification.production_processing_allowed:
                        raise ApiException(
                            ErrorCode.CHILD_DATA_VERIFICATION_REQUIRED,
                            "Approved guardian verification is required before "
                            "production training.",
                        )
            cursor = await connection.execute(
                """
                select consent_id, status::text as status, version
                  from baby_data.consents
                 where baby_id = %s and actor_user_id = %s and scope = %s
                 order by version desc limit 1
                """,
                (baby_id, principal.user_id, scope),
            )
            previous = await cursor.fetchone()
            current_version = 0 if previous is None else previous["version"]
            if current_version != expected_version:
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The consent changed before this request.",
                    details=ErrorDetails.empty().model_copy(
                        update={
                            "current_version": max(current_version, 1),
                            "resource_type": "CONSENT",
                        }
                    ),
                )
            status = "GRANTED" if granted else ("NOT_GRANTED" if previous is None else "REVOKED")
            cursor = await connection.execute(
                """
                insert into baby_data.consents (
                    baby_id, actor_user_id, scope, status, policy_version,
                    granted_at, revoked_at, supersedes_consent_id, version
                ) values (
                    %s, %s, %s, %s, %s,
                    case when %s then clock_timestamp() else null end,
                    case when %s then null when %s then null else clock_timestamp() end,
                    %s, %s
                ) returning consent_id
                """,
                (
                    baby_id,
                    principal.user_id,
                    scope,
                    status,
                    policy_version,
                    granted,
                    granted,
                    previous is None,
                    None if previous is None else previous["consent_id"],
                    current_version + 1,
                ),
            )
            consent_id = (await cursor.fetchone())["consent_id"]  # type: ignore[index]
            await self._complete_idempotency(
                connection,
                principal,
                method="PUT",
                path=path,
                key=client_request_id,
                result_type="CONSENT",
                result_id=consent_id,
                response_status=200,
            )
            return await self._get_consent(connection, consent_id)

    async def _get_consent(
        self, connection: DatabaseConnection, consent_id: UUID | None
    ) -> Consent:
        cursor = await connection.execute(
            """
            select consent_id, baby_id, actor_user_id, scope::text as scope,
                   status::text as status, policy_version, granted_at, revoked_at, version
              from baby_data.consents where consent_id = %s
            """,
            (consent_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return self._consent(row)

    async def _increment_context(
        self,
        connection: DatabaseConnection,
        *,
        baby_id: UUID,
        resource_type: Literal["CARE_EVENT", "CARE_ENTRY"],
        resource_id: UUID,
        resource_version: int,
        reason: Literal["CREATED", "UPDATED", "DELETION_REQUESTED"],
    ) -> int:
        cursor = await connection.execute(
            """
            select baby_private.advance_baby_context_revision(%s) as context_revision
            """,
            (baby_id,),
        )
        row = await cursor.fetchone()
        if row is None or row["context_revision"] is None:
            raise ApiException(ErrorCode.RESOURCE_DELETING, "The baby data is being deleted.")
        await connection.execute(
            """
            insert into baby_data.resource_invalidations (
                baby_id, source_resource_type, source_resource_id, source_version,
                reason, context_revision
            ) values (%s, %s, %s, %s, %s, %s)
            """,
            (
                baby_id,
                resource_type,
                resource_id,
                resource_version,
                reason,
                row["context_revision"],
            ),
        )
        cursor = await connection.execute(
            "select version from baby_data.babies where baby_id = %s",
            (baby_id,),
        )
        baby = await cursor.fetchone()
        if baby is None:
            raise self._not_found()
        return cast(int, baby["version"])

    async def _record_shared_changes(
        self,
        connection: DatabaseConnection,
        *,
        baby_id: UUID,
        changes: list[SharedChangeInput],
    ) -> int:
        payload = [
            {
                "resource_type": resource_type,
                "resource_id": str(resource_id),
                "version": version,
                "deleted": deleted,
            }
            for resource_type, resource_id, version, deleted in changes
        ]
        try:
            cursor = await connection.execute(
                "select baby_private.record_shared_changes(%s, %s::jsonb) as revision",
                (baby_id, json.dumps(payload)),
            )
            row = await cursor.fetchone()
        except psycopg.Error as exc:
            if "B09_RESOURCE_NOT_FOUND" in str(exc):
                raise self._not_found() from exc
            raise
        if row is None or row["revision"] is None:
            raise ApiException.service_unavailable()
        return cast(int, row["revision"])

    async def _lock_shared_change_feed(
        self,
        connection: DatabaseConnection,
        baby_id: UUID,
    ) -> None:
        """Serialize a feed-aware mutation before it changes business rows."""
        cursor = await connection.execute(
            """
            select baby_id
              from baby_data.shared_change_feed_state
             where baby_id = %s
             for update
            """,
            (baby_id,),
        )
        if await cursor.fetchone() is None:
            raise self._not_found()

    @staticmethod
    def _event_values(event: CareEventValue) -> tuple[str, Any, Any, str, dict[str, Any]]:
        return (
            event.type,
            event.occurred_at,
            event.ended_at,
            event.time_precision,
            event.payload.model_dump(mode="json"),
        )

    async def create_care_event(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        request: CreateCareEvent,
        *,
        path: str,
    ) -> CareEvent:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                _, event_id, _ = replay
                return await self._get_care_event(connection, event_id)
            await self._baby_access(connection, baby_id, principal.user_id)
            await self._lock_shared_change_feed(connection, baby_id)
            event_type, occurred_at, ended_at, precision, payload = self._event_values(
                request.event
            )
            try:
                async with connection.transaction():
                    cursor = await connection.execute(
                        """
                        insert into baby_data.care_events (
                            baby_id, event_type, occurred_at, ended_at, time_precision,
                            payload, data_origin, created_by_user_id, updated_by_user_id
                        ) values (%s, %s, %s, %s, %s, %s, 'USER', %s, %s)
                        returning care_event_id, version
                        """,
                        (
                            baby_id,
                            event_type,
                            occurred_at,
                            ended_at,
                            precision,
                            json.dumps(payload),
                            principal.user_id,
                            principal.user_id,
                        ),
                    )
                    row = await cursor.fetchone()
            except psycopg.errors.UniqueViolation as exc:
                if "care_events_one_active_sleep_per_baby" in str(exc):
                    cursor = await connection.execute(
                        """
                        select * from baby_data.care_events
                         where baby_id = %s and event_type = 'SLEEP'
                           and status = 'ACTIVE' and ended_at is null
                        """,
                        (baby_id,),
                    )
                    current = await cursor.fetchone()
                    raise ApiException(
                        ErrorCode.SLEEP_ALREADY_ACTIVE,
                        "An active sleep record already exists.",
                        details=ErrorDetails.empty().model_copy(
                            update={
                                "current_resource": None
                                if current is None
                                else self._care_event(current),
                                "resource_type": "CARE_EVENT",
                            }
                        ),
                    ) from exc
                raise
            if row is None:
                raise ApiException.service_unavailable()
            event_id = row["care_event_id"]
            baby_version = await self._increment_context(
                connection,
                baby_id=baby_id,
                resource_type="CARE_EVENT",
                resource_id=event_id,
                resource_version=row["version"],
                reason="CREATED",
            )
            await self._record_shared_changes(
                connection,
                baby_id=baby_id,
                changes=[
                    ("CARE_EVENT", event_id, row["version"], False),
                    ("BABY", baby_id, baby_version, False),
                ],
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="CARE_EVENT",
                result_id=event_id,
                response_status=201,
            )
            return await self._get_care_event(connection, event_id)

    async def _get_care_event(
        self,
        connection: DatabaseConnection,
        event_id: UUID | None,
        *,
        include_deleting: bool = False,
    ) -> CareEvent:
        cursor = await connection.execute(
            """
            select care_event_id, baby_id, event_type::text as event_type,
                   occurred_at, ended_at, time_precision::text as time_precision,
                   payload, source_entry_id, status::text as status,
                   created_by_user_id, updated_by_user_id, data_origin::text as data_origin,
                   version, recorded_at, updated_at
              from baby_data.care_events
             where care_event_id = %s
               and (%s or status = 'ACTIVE')
            """,
            (event_id, include_deleting),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return self._care_event(row)

    async def get_care_event(self, principal: AuthenticatedPrincipal, event_id: UUID) -> CareEvent:
        async with self.transaction(principal) as connection:
            return await self._get_care_event(connection, event_id)

    async def patch_care_event(
        self,
        principal: AuthenticatedPrincipal,
        event_id: UUID,
        request: PatchCareEvent,
        *,
        path: str,
    ) -> CareEvent:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="PATCH",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                return await self._get_care_event(connection, event_id)
            current = await self._get_care_event(connection, event_id)
            access = await self._baby_access(connection, current.baby_id, principal.user_id)
            if (
                current.created_by_user_id != principal.user_id
                and access.membership.role is not MembershipRole.OWNER
            ):
                raise ApiException(
                    ErrorCode.AUTHOR_ONLY,
                    "Only the original author or owner can change this record.",
                )
            if current.version != request.version:
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The care record changed before this update.",
                    details=ErrorDetails.empty().model_copy(
                        update={
                            "current_version": current.version,
                            "current_resource": current,
                            "resource_type": "CARE_EVENT",
                        }
                    ),
                )
            await self._lock_shared_change_feed(connection, current.baby_id)
            event_type, occurred_at, ended_at, precision, payload = self._event_values(
                request.event
            )
            try:
                cursor = await connection.execute(
                    """
                    update baby_data.care_events
                       set event_type = %s, occurred_at = %s, ended_at = %s,
                           time_precision = %s, payload = %s,
                           updated_by_user_id = %s, version = version + 1
                     where care_event_id = %s and version = %s and status = 'ACTIVE'
                    returning version
                    """,
                    (
                        event_type,
                        occurred_at,
                        ended_at,
                        precision,
                        json.dumps(payload),
                        principal.user_id,
                        event_id,
                        request.version,
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ApiException(
                    ErrorCode.SLEEP_ALREADY_ACTIVE,
                    "An active sleep record already exists.",
                ) from exc
            if await cursor.fetchone() is None:
                latest = await self._get_care_event(connection, event_id)
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The care record changed before this update.",
                    details=ErrorDetails.empty().model_copy(
                        update={
                            "current_version": latest.version,
                            "current_resource": latest,
                            "resource_type": "CARE_EVENT",
                        }
                    ),
                )
            baby_version = await self._increment_context(
                connection,
                baby_id=current.baby_id,
                resource_type="CARE_EVENT",
                resource_id=event_id,
                resource_version=request.version + 1,
                reason="UPDATED",
            )
            await self._record_shared_changes(
                connection,
                baby_id=current.baby_id,
                changes=[
                    ("CARE_EVENT", event_id, request.version + 1, False),
                    ("BABY", current.baby_id, baby_version, False),
                ],
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="PATCH",
                path=path,
                key=request.client_request_id,
                result_type="CARE_EVENT",
                result_id=event_id,
                response_status=200,
            )
            return await self._get_care_event(connection, event_id)

    async def _create_deletion_job(
        self,
        connection: DatabaseConnection,
        principal: AuthenticatedPrincipal,
        *,
        baby_id: UUID,
        scope: str,
        resource_id: UUID | None,
    ) -> UUID:
        cursor = await connection.execute(
            """
            insert into baby_data.deletion_jobs (
                requester_user_id, baby_id, scope, resource_id, pending_categories
            ) values (%s, %s, %s, %s, %s)
            returning deletion_job_id
            """,
            (
                principal.user_id,
                baby_id,
                scope,
                resource_id,
                ["AUDIO", "RAW_TEXT", "RECORDS", "ANALYSES", "DERIVED_FEATURES", "TRAINING_COPIES"],
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ApiException.service_unavailable()
        return cast(UUID, row["deletion_job_id"])

    async def delete_care_event(
        self,
        principal: AuthenticatedPrincipal,
        event_id: UUID,
        *,
        version: int,
        idempotency_key: UUID,
        path: str,
    ) -> DeletionJob:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                payload={"version": version},
            )
            if replay is not None:
                _, job_id, _ = replay
                return await self._get_deletion(connection, job_id)
            current = await self._get_care_event(connection, event_id)
            access = await self._baby_access(connection, current.baby_id, principal.user_id)
            if (
                current.created_by_user_id != principal.user_id
                and access.membership.role is not MembershipRole.OWNER
            ):
                raise ApiException(ErrorCode.AUTHOR_ONLY, "Only the author or owner can delete it.")
            if current.version != version:
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The care record changed before deletion.",
                    details=ErrorDetails.empty().model_copy(
                        update={
                            "current_version": current.version,
                            "current_resource": current,
                            "resource_type": "CARE_EVENT",
                        }
                    ),
                )
            await self._lock_shared_change_feed(connection, current.baby_id)
            cursor = await connection.execute(
                """
                update baby_data.care_events
                   set status = 'DELETING', updated_by_user_id = %s, version = version + 1
                 where care_event_id = %s and version = %s and status = 'ACTIVE'
                returning version
                """,
                (principal.user_id, event_id, version),
            )
            if await cursor.fetchone() is None:
                latest = await self._get_care_event(connection, event_id)
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The care record changed before deletion.",
                    details=ErrorDetails.empty().model_copy(
                        update={
                            "current_version": latest.version,
                            "current_resource": latest,
                            "resource_type": "CARE_EVENT",
                        }
                    ),
                )
            await connection.execute(
                """
                update baby_data.action_attempts
                   set status = 'DELETING', updated_by_user_id = %s, version = version + 1
                 where care_event_id = %s and status = 'ACTIVE'
                """,
                (principal.user_id, event_id),
            )
            job_id = await self._create_deletion_job(
                connection,
                principal,
                baby_id=current.baby_id,
                scope="CARE_EVENT",
                resource_id=event_id,
            )
            baby_version = await self._increment_context(
                connection,
                baby_id=current.baby_id,
                resource_type="CARE_EVENT",
                resource_id=event_id,
                resource_version=version + 1,
                reason="DELETION_REQUESTED",
            )
            await self._record_shared_changes(
                connection,
                baby_id=current.baby_id,
                changes=[
                    ("CARE_EVENT", event_id, version + 1, True),
                    ("BABY", current.baby_id, baby_version, False),
                ],
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                result_type="DELETION",
                result_id=job_id,
                response_status=202,
            )
            return await self._get_deletion(connection, job_id)

    async def create_care_entry(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        request: CreateCareEntry,
        *,
        path: str,
    ) -> CareEntry:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                _, entry_id, _ = replay
                return await self._get_care_entry(connection, entry_id)
            await self._baby_access(connection, baby_id, principal.user_id)
            if request.episode_id is not None:
                cursor = await connection.execute(
                    """
                    select 1 as found from baby_data.episodes
                     where baby_id = %s and episode_id = %s
                    """,
                    (baby_id, request.episode_id),
                )
                if await cursor.fetchone() is None:
                    raise self._not_found()
            if request.supersedes_entry_id is not None:
                cursor = await connection.execute(
                    """
                    select 1 as found from baby_data.raw_care_entries
                     where baby_id = %s and entry_id = %s and status = 'CONFIRMED'
                    """,
                    (baby_id, request.supersedes_entry_id),
                )
                if await cursor.fetchone() is None:
                    raise self._not_found()
            cursor = await connection.execute(
                """
                insert into baby_data.raw_care_entries (
                    baby_id, episode_id, author_user_id, input_mode, choices, raw_text,
                    occurred_at, time_precision, supersedes_entry_id,
                    base_record_versions, data_origin
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'USER')
                returning entry_id
                """,
                (
                    baby_id,
                    request.episode_id,
                    principal.user_id,
                    request.input_mode.value,
                    json.dumps([choice.model_dump(mode="json") for choice in request.choices]),
                    request.raw_text,
                    request.occurred_at,
                    request.time_precision,
                    request.supersedes_entry_id,
                    json.dumps(
                        [
                            version.model_dump(mode="json")
                            for version in request.base_record_versions
                        ]
                    ),
                ),
            )
            entry_id = (await cursor.fetchone())["entry_id"]  # type: ignore[index]
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="CARE_ENTRY",
                result_id=entry_id,
                response_status=201,
            )
            return await self._get_care_entry(connection, entry_id)

    async def _get_care_entry(
        self, connection: DatabaseConnection, entry_id: UUID | None
    ) -> CareEntry:
        cursor = await connection.execute(
            """
            select e.*,
                   n.run_id as normalization_run_id,
                   case when n.status = 'COMPLETE' then n.result else null end as normalized_content
              from baby_data.raw_care_entries e
              left join lateral (
                  select run_id, status, result
                    from baby_data.normalization_runs n
                   where n.baby_id = e.baby_id and n.entry_id = e.entry_id
                     and n.input_revision = e.input_revision
                   order by n.recorded_at desc limit 1
              ) n on true
             where e.entry_id = %s and e.status not in ('DELETING', 'DELETED')
            """,
            (entry_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return self._care_entry(row)

    async def get_care_entry(self, principal: AuthenticatedPrincipal, entry_id: UUID) -> CareEntry:
        async with self.transaction(principal) as connection:
            return await self._get_care_entry(connection, entry_id)

    def _cursor_token(self, timestamp: datetime, resource_id: UUID) -> str:
        body = json.dumps(
            {"timestamp": timestamp.isoformat(), "id": str(resource_id)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(self._proof_secret, body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(body + signature).rstrip(b"=").decode()

    def _parse_cursor(self, value: str | None) -> tuple[datetime, UUID] | None:
        if value is None:
            return None
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            body, signature = raw[:-32], raw[-32:]
            expected = hmac.new(self._proof_secret, body, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("bad signature")
            data = json.loads(body)
            return datetime.fromisoformat(data["timestamp"]), UUID(data["id"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ApiException(ErrorCode.VALIDATION_ERROR, "The cursor is invalid.") from exc

    async def list_my_care_entries(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        *,
        cursor: str | None,
        limit: int,
        status: str | None,
    ) -> CareEntryPage:
        parsed = self._parse_cursor(cursor)
        async with self.transaction(principal) as connection:
            await self._baby_access(connection, baby_id, principal.user_id)
            cursor_result = await connection.execute(
                """
                select e.*, null::uuid as normalization_run_id,
                       null::jsonb as normalized_content
                  from baby_data.raw_care_entries e
                 where e.baby_id = %s and e.author_user_id = %s
                   and e.status in ('DRAFT','NORMALIZING','REVIEW_READY','NEEDS_MANUAL_REVIEW')
                   and (%s::text is null or e.status::text = %s)
                   and (%s::timestamptz is null or (e.updated_at, e.entry_id) < (%s, %s))
                 order by e.updated_at desc, e.entry_id desc
                 limit %s
                """,
                (
                    baby_id,
                    principal.user_id,
                    status,
                    status,
                    None if parsed is None else parsed[0],
                    None if parsed is None else parsed[0],
                    None if parsed is None else parsed[1],
                    limit + 1,
                ),
            )
            rows = await cursor_result.fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = None
        if has_more and visible:
            next_cursor = self._cursor_token(visible[-1]["updated_at"], visible[-1]["entry_id"])
        return CareEntryPage(
            items=[self._care_entry(row) for row in visible], next_cursor=next_cursor
        )

    async def patch_care_entry(
        self,
        principal: AuthenticatedPrincipal,
        entry_id: UUID,
        request: PatchCareEntry,
        *,
        path: str,
    ) -> CareEntry:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="PATCH",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                return await self._get_care_entry(connection, entry_id)
            current = await self._get_care_entry(connection, entry_id)
            if current.author_user_id != principal.user_id:
                raise self._not_found()
            if current.status.value == "CONFIRMED":
                raise ApiException(
                    ErrorCode.INVALID_STATE,
                    "A confirmed entry must be corrected through a new revision draft.",
                )
            if current.input_revision != request.input_revision:
                raise ApiException(
                    ErrorCode.SOURCE_REVISION_CHANGED,
                    "The draft source changed before this update.",
                    details=ErrorDetails.empty().model_copy(
                        update={
                            "current_version": current.input_revision,
                            "resource_type": "CARE_ENTRY",
                        }
                    ),
                )
            await connection.execute(
                """
                update baby_data.normalization_runs
                   set status = 'STALE',
                       lease_expires_at = null,
                       completed_at = clock_timestamp()
                 where entry_id = %s and input_revision = %s
                   and status in ('RUNNING','COMPLETE','FAILED')
                """,
                (entry_id, request.input_revision),
            )
            cursor = await connection.execute(
                """
                update baby_data.raw_care_entries
                   set input_mode = %s, raw_text = %s, choices = %s,
                       occurred_at = %s, time_precision = %s,
                       input_revision = input_revision + 1,
                       status = 'DRAFT', version = version + 1
                 where entry_id = %s and author_user_id = %s
                   and input_revision = %s and status <> 'CONFIRMED'
                returning entry_id
                """,
                (
                    request.input_mode.value,
                    request.raw_text,
                    json.dumps([choice.model_dump(mode="json") for choice in request.choices]),
                    request.occurred_at,
                    request.time_precision,
                    entry_id,
                    principal.user_id,
                    request.input_revision,
                ),
            )
            if await cursor.fetchone() is None:
                raise ApiException(ErrorCode.SOURCE_REVISION_CHANGED, "The draft source changed.")
            await self._complete_idempotency(
                connection,
                principal,
                method="PATCH",
                path=path,
                key=request.client_request_id,
                result_type="CARE_ENTRY",
                result_id=entry_id,
                response_status=200,
            )
            return await self._get_care_entry(connection, entry_id)

    async def delete_care_entry(
        self,
        principal: AuthenticatedPrincipal,
        entry_id: UUID,
        *,
        version: int,
        idempotency_key: UUID,
        path: str,
    ) -> DeletionJob:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                payload={"version": version},
            )
            if replay is not None:
                return await self._get_deletion(connection, replay[1])
            current = await self._get_care_entry(connection, entry_id)
            if current.author_user_id != principal.user_id:
                raise self._not_found()
            if current.status.value == "CONFIRMED":
                raise ApiException(
                    ErrorCode.INVALID_STATE,
                    "Confirmed records use record or contribution deletion.",
                )
            if current.version != version:
                raise ApiException(ErrorCode.VERSION_CONFLICT, "The draft changed before deletion.")
            await connection.execute(
                """
                update baby_data.normalization_runs
                   set status = 'STALE', lease_expires_at = null,
                       completed_at = clock_timestamp()
                 where entry_id = %s and input_revision = %s
                   and status in ('RUNNING','COMPLETE','FAILED')
                """,
                (entry_id, current.input_revision),
            )
            await connection.execute(
                """
                update baby_data.raw_care_entries
                   set status = 'DELETING', version = version + 1
                 where entry_id = %s and version = %s
                """,
                (entry_id, version),
            )
            job_id = await self._create_deletion_job(
                connection,
                principal,
                baby_id=current.baby_id,
                scope="CARE_ENTRY",
                resource_id=entry_id,
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                result_type="DELETION",
                result_id=job_id,
                response_status=202,
            )
            return await self._get_deletion(connection, job_id)

    async def create_action(
        self,
        principal: AuthenticatedPrincipal,
        episode_id: UUID,
        request: CreateAction,
        *,
        path: str,
    ) -> ActionAttempt:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                return await self._get_action(connection, replay[1])
            cursor = await connection.execute(
                "select baby_id from baby_data.episodes where episode_id = %s",
                (episode_id,),
            )
            episode = await cursor.fetchone()
            if episode is None:
                raise self._not_found()
            baby_id = episode["baby_id"]
            await self._baby_access(connection, baby_id, principal.user_id)
            if request.new_care_event is not None:
                await self._lock_shared_change_feed(connection, baby_id)
            if request.care_event_id is not None:
                care_event = await self._get_care_event(connection, request.care_event_id)
                if care_event.baby_id != baby_id:
                    raise self._not_found()
                cursor = await connection.execute(
                    """
                    select action_id from baby_data.action_attempts
                     where episode_id = %s and care_event_id = %s
                    """,
                    (episode_id, request.care_event_id),
                )
                duplicate = await cursor.fetchone()
                if duplicate is not None:
                    action_id = duplicate["action_id"]
                    await self._complete_idempotency(
                        connection,
                        principal,
                        method="POST",
                        path=path,
                        key=request.client_request_id,
                        result_type="ACTION",
                        result_id=action_id,
                        response_status=201,
                    )
                    return await self._get_action(connection, action_id)
                care_event_id = request.care_event_id
                performed_at = care_event.event.occurred_at
                data_origin = care_event.data_origin.value
            else:
                assert request.new_care_event is not None
                event_type, occurred_at, ended_at, precision, payload = self._event_values(
                    request.new_care_event
                )
                cursor = await connection.execute(
                    """
                    insert into baby_data.care_events (
                        baby_id, event_type, occurred_at, ended_at, time_precision,
                        payload, data_origin, created_by_user_id, updated_by_user_id
                    ) values (%s, %s, %s, %s, %s, %s, 'USER', %s, %s)
                    returning care_event_id, version
                    """,
                    (
                        baby_id,
                        event_type,
                        occurred_at,
                        ended_at,
                        precision,
                        json.dumps(payload),
                        principal.user_id,
                        principal.user_id,
                    ),
                )
                created = await cursor.fetchone()
                care_event_id = created["care_event_id"]  # type: ignore[index]
                performed_at = occurred_at
                data_origin = "USER"
                baby_version = await self._increment_context(
                    connection,
                    baby_id=baby_id,
                    resource_type="CARE_EVENT",
                    resource_id=care_event_id,
                    resource_version=created["version"],  # type: ignore[index]
                    reason="CREATED",
                )
                await self._record_shared_changes(
                    connection,
                    baby_id=baby_id,
                    changes=[
                        (
                            "CARE_EVENT",
                            care_event_id,
                            created["version"],  # type: ignore[index]
                            False,
                        ),
                        ("BABY", baby_id, baby_version, False),
                    ],
                )
            if request.performed_by_user_id is not None:
                cursor = await connection.execute(
                    """
                    select 1 as found from baby_data.baby_memberships
                     where baby_id = %s and user_id = %s and status = 'ACTIVE'
                    """,
                    (baby_id, request.performed_by_user_id),
                )
                if await cursor.fetchone() is None:
                    raise self._not_found()
            cursor = await connection.execute(
                """
                insert into baby_data.action_attempts (
                    baby_id, episode_id, care_event_id, recommendation_id,
                    performed_at, performed_by_user_id, sequence_no, data_origin,
                    created_by_user_id, updated_by_user_id
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning action_id
                """,
                (
                    baby_id,
                    episode_id,
                    care_event_id,
                    request.recommendation_id,
                    performed_at,
                    request.performed_by_user_id,
                    request.sequence,
                    data_origin,
                    principal.user_id,
                    principal.user_id,
                ),
            )
            action_id = (await cursor.fetchone())["action_id"]  # type: ignore[index]
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="ACTION",
                result_id=action_id,
                response_status=201,
            )
            return await self._get_action(connection, action_id)

    async def _get_action(
        self, connection: DatabaseConnection, action_id: UUID | None
    ) -> ActionAttempt:
        cursor = await connection.execute(
            """
            select action_id, baby_id, episode_id, care_event_id, recommendation_id,
                   created_by_user_id, performed_by_user_id, performed_at,
                   sequence_no, status::text as status,
                   followup_status::text as followup_status,
                   data_origin::text as data_origin, version, recorded_at, updated_at
              from baby_data.action_attempts
             where action_id = %s and status = 'ACTIVE'
            """,
            (action_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
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

    async def timeline(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        *,
        from_time: datetime | None,
        to_time: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> TimelineItemPage:
        if from_time is not None and to_time is not None and from_time > to_time:
            raise ApiException(ErrorCode.VALIDATION_ERROR, "from must not be after to.")
        parsed = self._parse_cursor(cursor)
        async with self.transaction(principal) as connection:
            await self._baby_access(connection, baby_id, principal.user_id)
            cursor_result = await connection.execute(
                """
                select care_event_id, baby_id, event_type::text as event_type,
                       occurred_at, ended_at, time_precision::text as time_precision,
                       payload, source_entry_id, status::text as status,
                       created_by_user_id, updated_by_user_id,
                       data_origin::text as data_origin, version, recorded_at, updated_at,
                       coalesce(occurred_at, recorded_at) as sort_time
                  from baby_data.care_events
                 where baby_id = %s and status = 'ACTIVE'
                   and (%s::timestamptz is null or coalesce(occurred_at, recorded_at) >= %s)
                   and (%s::timestamptz is null or coalesce(occurred_at, recorded_at) < %s)
                   and (
                       %s::timestamptz is null
                       or (coalesce(occurred_at, recorded_at), care_event_id) < (%s, %s)
                   )
                 order by sort_time desc, care_event_id desc
                 limit %s
                """,
                (
                    baby_id,
                    from_time,
                    from_time,
                    to_time,
                    to_time,
                    None if parsed is None else parsed[0],
                    None if parsed is None else parsed[0],
                    None if parsed is None else parsed[1],
                    limit + 1,
                ),
            )
            rows = await cursor_result.fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [
            TimelineItem(
                kind="CARE_EVENT",
                resource_id=row["care_event_id"],
                baby_id=row["baby_id"],
                occurred_at=row["occurred_at"],
                version=row["version"],
                created_by_user_id=row["created_by_user_id"],
                data_origin=row["data_origin"],
                resource=self._care_event(row),
            )
            for row in visible
        ]
        next_cursor = None
        if has_more and visible:
            next_cursor = self._cursor_token(visible[-1]["sort_time"], visible[-1]["care_event_id"])
        return TimelineItemPage(items=items, next_cursor=next_cursor)

    async def get_changes(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        *,
        since_revision: int,
    ) -> Changes:
        async with self.transaction(principal) as connection:
            # Lock the feed state before the authorization rows. Every feed-aware
            # membership removal and whole-baby deletion advances this same state
            # before making access inactive. This explicit lock order avoids a
            # membership/state deadlock and gives the list one revision boundary.
            cursor = await connection.execute(
                """
                select current_revision, retained_from_revision,
                       clock_timestamp() as server_time
                  from baby_data.shared_change_feed_state
                 where baby_id = %s
                 for share
                """,
                (baby_id,),
            )
            boundary = await cursor.fetchone()
            if boundary is None:
                raise self._not_found()

            # Recheck the concrete rows after acquiring the feed boundary. Feed-
            # aware removal/deletion must advance state before making access
            # inactive, so it is already blocked by the state lock. These remain
            # plain SELECTs because PostgreSQL row-locking SELECTs also apply each
            # table's UPDATE RLS policy (a caregiver cannot update a Baby row).
            cursor = await connection.execute(
                """
                select 1
                  from baby_data.babies b
                  join baby_data.baby_memberships m on m.baby_id = b.baby_id
                 where b.baby_id = %s
                   and b.status = 'ACTIVE'
                   and m.user_id = %s
                   and m.status = 'ACTIVE'
                """,
                (baby_id, principal.user_id),
            )
            if await cursor.fetchone() is None:
                raise self._not_found()

            current_revision = cast(int, boundary["current_revision"])
            retained_from_revision = cast(int, boundary["retained_from_revision"])
            requires_resync = (
                since_revision == 0
                or since_revision < retained_from_revision
                or since_revision > current_revision
            )
            if requires_resync:
                return Changes(
                    baby_id=baby_id,
                    current_revision=current_revision,
                    changes=[],
                    resync_required=True,
                    server_time=boundary["server_time"],
                )

            cursor = await connection.execute(
                """
                with latest_per_resource as (
                    select distinct on (resource_type, resource_id)
                           resource_type::text as resource_type,
                           resource_id,
                           resource_version,
                           deleted,
                           change_revision
                      from baby_data.shared_changes
                     where baby_id = %s
                       and change_revision > %s
                       and change_revision <= %s
                     order by resource_type, resource_id, change_revision desc
                )
                select resource_type, resource_id, resource_version, deleted,
                       change_revision
                  from latest_per_resource
                 order by change_revision, resource_type, resource_id
                 limit %s
                """,
                (
                    baby_id,
                    since_revision,
                    current_revision,
                    self.MAX_SHARED_CHANGES + 1,
                ),
            )
            rows = await cursor.fetchall()
            if len(rows) > self.MAX_SHARED_CHANGES:
                return Changes(
                    baby_id=baby_id,
                    current_revision=current_revision,
                    changes=[],
                    resync_required=True,
                    server_time=boundary["server_time"],
                )

            return Changes(
                baby_id=baby_id,
                current_revision=current_revision,
                changes=[
                    Change(
                        resource_type=row["resource_type"],
                        resource_id=row["resource_id"],
                        version=row["resource_version"],
                        deleted=row["deleted"],
                    )
                    for row in rows
                ],
                resync_required=False,
                server_time=boundary["server_time"],
            )

    async def create_reauthentication_challenge(
        self,
        principal: AuthenticatedPrincipal,
        request: CreateReauthenticationChallenge,
        *,
        path: str,
    ) -> ReauthenticationChallenge:
        attempt_id = await self._start_security_attempt(
            principal,
            action="REAUTH_CHALLENGE",
            target=f"{request.operation.value}:{request.baby_id}",
            maximum=10,
        )
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                payload=request.model_dump(mode="json"),
            )
            if replay is not None:
                await self._mark_security_attempt_succeeded(connection, attempt_id)
                return await self._get_challenge(connection, replay[1])
            await self._require_owner(connection, principal, request.baby_id)
            cursor = await connection.execute(
                """
                insert into baby_data.reauthentication_challenges (
                    user_id, requested_session_id, operation, baby_id
                ) values (%s, %s, %s, %s)
                returning challenge_id
                """,
                (
                    principal.user_id,
                    principal.session_id,
                    request.operation.value,
                    request.baby_id,
                ),
            )
            challenge_id = (await cursor.fetchone())["challenge_id"]  # type: ignore[index]
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=request.client_request_id,
                result_type="REAUTH_CHALLENGE",
                result_id=challenge_id,
                response_status=201,
            )
            await self._mark_security_attempt_succeeded(connection, attempt_id)
            return await self._get_challenge(connection, challenge_id)

    async def _get_challenge(
        self, connection: DatabaseConnection, challenge_id: UUID | None
    ) -> ReauthenticationChallenge:
        cursor = await connection.execute(
            """
            select challenge_id, user_id, requested_session_id,
                   operation::text as operation, baby_id, status::text as status,
                   created_at, expires_at
              from baby_data.reauthentication_challenges where challenge_id = %s
            """,
            (challenge_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        status = cast(
            Literal["PENDING", "PROVED", "EXPIRED"],
            "EXPIRED"
            if row["status"] == "PENDING" and row["expires_at"] <= datetime.now(UTC)
            else row["status"],
        )
        return ReauthenticationChallenge(
            challenge_id=row["challenge_id"],
            user_id=row["user_id"],
            requested_session_id=row["requested_session_id"],
            operation=row["operation"],
            baby_id=row["baby_id"],
            status=status,
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def _proof_token(self, proof_id: UUID, principal: AuthenticatedPrincipal) -> str:
        material = f"{proof_id}:{principal.user_id}:{principal.session_id}".encode()
        digest = hmac.new(self._proof_secret, material, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(proof_id.bytes + digest).rstrip(b"=").decode()

    async def create_reauthentication_proof(
        self,
        principal: AuthenticatedPrincipal,
        *,
        challenge_id: UUID,
        client_request_id: UUID,
        path: str,
    ) -> ReauthenticationProof:
        payload = {
            "client_request_id": str(client_request_id),
            "challenge_id": str(challenge_id),
        }
        attempt_id = await self._start_security_attempt(
            principal,
            action="REAUTH_PROOF",
            target=str(challenge_id),
            maximum=10,
        )
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=client_request_id,
                payload=payload,
            )
            if replay is not None:
                await self._mark_security_attempt_succeeded(connection, attempt_id)
                return await self._get_proof(connection, principal, replay[1], include_token=True)
            cursor = await connection.execute(
                """
                select challenge_id, user_id, requested_session_id,
                       operation::text as operation, baby_id,
                       status::text as status, created_at, expires_at
                  from baby_data.reauthentication_challenges
                 where challenge_id = %s for update
                """,
                (challenge_id,),
            )
            challenge = await cursor.fetchone()
            if challenge is None:
                raise self._not_found()
            if challenge["user_id"] != principal.user_id or challenge["status"] != "PENDING":
                raise ApiException(ErrorCode.REAUTH_PROOF_INVALID, "The challenge is not valid.")
            if challenge["expires_at"] <= datetime.now(UTC):
                await connection.execute(
                    """
                    update baby_data.reauthentication_challenges
                       set status = 'EXPIRED' where challenge_id = %s
                    """,
                    (challenge_id,),
                )
                raise ApiException(ErrorCode.REAUTH_PROOF_INVALID, "The challenge has expired.")
            qualifying = [
                method
                for method in principal.authentication_methods
                if method.method in {"otp", "magiclink"}
                and method.timestamp >= int(challenge["created_at"].timestamp())
            ]
            if principal.session_id == challenge["requested_session_id"] or not qualifying:
                raise ApiException(
                    ErrorCode.REAUTH_REQUIRED,
                    "A fresh email OTP authentication is required; token refresh is insufficient.",
                    details=ErrorDetails.empty().model_copy(
                        update={"reauthentication_challenge_id": challenge_id}
                    ),
                )
            proof_id = uuid4()
            token = self._proof_token(proof_id, principal)
            cursor = await connection.execute(
                """
                insert into baby_data.reauthentication_proofs (
                    proof_id, challenge_id, user_id, session_id, operation,
                    baby_id, token_sha256
                ) values (%s, %s, %s, %s, %s, %s, %s)
                returning proof_id
                """,
                (
                    proof_id,
                    challenge_id,
                    principal.user_id,
                    principal.session_id,
                    challenge["operation"],
                    challenge["baby_id"],
                    hashlib.sha256(token.encode()).digest(),
                ),
            )
            await cursor.fetchone()
            await connection.execute(
                """
                update baby_data.reauthentication_challenges
                   set status = 'PROVED', proved_at = clock_timestamp()
                 where challenge_id = %s
                """,
                (challenge_id,),
            )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=client_request_id,
                result_type="REAUTH_PROOF",
                result_id=proof_id,
                response_status=201,
            )
            await self._mark_security_attempt_succeeded(connection, attempt_id)
            return await self._get_proof(connection, principal, proof_id, include_token=True)

    async def _get_proof(
        self,
        connection: DatabaseConnection,
        principal: AuthenticatedPrincipal,
        proof_id: UUID | None,
        *,
        include_token: bool,
    ) -> ReauthenticationProof:
        cursor = await connection.execute(
            """
            select proof_id, challenge_id, user_id, session_id,
                   operation::text as operation, baby_id, issued_at, expires_at
              from baby_data.reauthentication_proofs where proof_id = %s
            """,
            (proof_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        if row["user_id"] != principal.user_id or row["session_id"] != principal.session_id:
            raise ApiException(
                ErrorCode.REAUTH_PROOF_INVALID,
                "The reauthentication proof belongs to a different session.",
            )
        return ReauthenticationProof(
            proof_id=row["proof_id"],
            challenge_id=row["challenge_id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            operation=row["operation"],
            baby_id=row["baby_id"],
            proof_token=self._proof_token(row["proof_id"], principal) if include_token else None,
            token_reissue_required=not include_token,
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
        )

    async def _child_verification(
        self,
        connection: DatabaseConnection,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
    ) -> ChildDataVerification:
        await self._baby_access(connection, baby_id, principal.user_id)
        cursor = await connection.execute(
            """
            select status::text as status, method, policy_version, verified_at
              from baby_data.guardian_verifications
             where baby_id = %s and subject_user_id = %s
            """,
            (baby_id, principal.user_id),
        )
        row = await cursor.fetchone()
        verification_status = cast(
            Literal["UNVERIFIED", "SYNTHETIC_TEST_ONLY", "VERIFIED"],
            "UNVERIFIED" if row is None else row["status"],
        )
        allowed = verification_status == "VERIFIED" and self._child_data_production_enabled
        return ChildDataVerification(
            baby_id=baby_id,
            subject_user_id=principal.user_id,
            status=verification_status,
            method=None if row is None else row["method"],
            policy_version=None if row is None else row["policy_version"],
            verified_at=None if row is None else row["verified_at"],
            production_processing_allowed=allowed,
        )

    async def child_verification(
        self, principal: AuthenticatedPrincipal, baby_id: UUID
    ) -> ChildDataVerification:
        async with self.transaction(principal) as connection:
            return await self._child_verification(connection, principal, baby_id)

    async def begin_session_revocation(
        self,
        principal: AuthenticatedPrincipal,
        *,
        scope: SessionRevocationScope,
        client_request_id: UUID,
        path: str,
    ) -> SessionRevocation:
        payload = {"client_request_id": str(client_request_id), "scope": scope.value}
        # CURRENT and ALL revoke this JWT before the provider call finishes. A retry with
        # the exact same key must still be able to resume an interrupted provider call,
        # while a revoked session must not be allowed to start a new revocation request.
        async with self.transaction(principal, require_live_session=False) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=client_request_id,
                payload=payload,
            )
            if replay is not None:
                previous = await self._get_session_revocation(connection, replay[1])
                if previous.requester_session_id != principal.session_id:
                    raise ApiException(
                        ErrorCode.IDEMPOTENCY_KEY_REUSED,
                        "The idempotency key belongs to a different session.",
                    )
                return previous
            cursor = await connection.execute(
                "select baby_private.has_live_request_context() as live"
            )
            live = await cursor.fetchone()
            if live is None or live["live"] is not True:
                raise ApiException(
                    ErrorCode.SESSION_REVOKED,
                    "The authenticated session is no longer active.",
                )
            await connection.execute(
                """
                insert into baby_data.observed_auth_sessions (
                    user_id, session_id, token_issued_at, token_expires_at
                ) values (%s, %s, %s, %s)
                on conflict (user_id, session_id) do update
                    set token_issued_at = greatest(
                            baby_data.observed_auth_sessions.token_issued_at,
                            excluded.token_issued_at
                        ),
                        token_expires_at = greatest(
                            baby_data.observed_auth_sessions.token_expires_at,
                            excluded.token_expires_at
                        ),
                        last_seen_at = clock_timestamp()
                """,
                (
                    principal.user_id,
                    principal.session_id,
                    principal.issued_at,
                    principal.expires_at,
                ),
            )
            cursor = await connection.execute(
                "select * from baby_private.begin_session_revocation(%s)",
                (scope.value,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ApiException.service_unavailable()
            revocation_id = row["created_revocation_id"]
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=client_request_id,
                result_type="SESSION_REVOCATION",
                result_id=revocation_id,
                response_status=200,
            )
            return await self._get_session_revocation(connection, revocation_id)

    async def finish_session_revocation(
        self,
        principal: AuthenticatedPrincipal,
        revocation_id: UUID,
        *,
        provider_http_status: int | None,
        failure_code: str | None,
    ) -> SessionRevocation:
        async with self.transaction(principal, require_live_session=False) as connection:
            if failure_code is None:
                await connection.execute(
                    """
                    update baby_data.session_revocation_jobs
                       set status = 'COMPLETE', provider_http_status = %s,
                           completed_at = clock_timestamp()
                     where revocation_id = %s and requester_user_id = %s
                    """,
                    (provider_http_status, revocation_id, principal.user_id),
                )
            else:
                await connection.execute(
                    """
                    update baby_data.session_revocation_jobs
                       set status = 'FAILED', provider_http_status = %s,
                           failure_code = %s
                     where revocation_id = %s and requester_user_id = %s
                    """,
                    (provider_http_status, failure_code, revocation_id, principal.user_id),
                )
            return await self._get_session_revocation(connection, revocation_id)

    async def _get_session_revocation(
        self, connection: DatabaseConnection, revocation_id: UUID | None
    ) -> SessionRevocation:
        cursor = await connection.execute(
            """
            select revocation_id, requester_user_id, requester_session_id,
                   scope::text as scope, status::text as status, target_session_count,
                   provider_scope, provider_http_status, failure_code,
                   access_blocked, requested_at, completed_at
              from baby_data.session_revocation_jobs where revocation_id = %s
            """,
            (revocation_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        failure: Failure | None = None
        if row["failure_code"] is not None:
            failure = Failure(
                code=row["failure_code"],
                message="Provider refresh-session revocation did not complete.",
                retryable=True,
            )
        return SessionRevocation(
            revocation_id=row["revocation_id"],
            requester_user_id=row["requester_user_id"],
            requester_session_id=row["requester_session_id"],
            scope=row["scope"],
            status=row["status"],
            target_session_count=row["target_session_count"],
            provider_scope=row["provider_scope"],
            provider_http_status=row["provider_http_status"],
            failure=failure,
            access_blocked=row["access_blocked"],
            requested_at=row["requested_at"],
            completed_at=row["completed_at"],
        )

    async def get_session_revocation(
        self, principal: AuthenticatedPrincipal, revocation_id: UUID
    ) -> SessionRevocation:
        async with self.transaction(principal) as connection:
            return await self._get_session_revocation(connection, revocation_id)

    async def delete_baby_data(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        *,
        version: int,
        idempotency_key: UUID,
        proof_token: str | None,
        path: str,
    ) -> DeletionJob:
        payload = {"version": version, "confirm": "DELETE_BABY"}
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                payload=payload,
            )
            if replay is not None:
                return await self._get_deletion(connection, replay[1])
            await self._require_owner(connection, principal, baby_id)
            await self._consume_reauthentication_proof(
                connection,
                principal,
                proof_token=proof_token,
                operation=ReauthenticationOperation.DELETE_BABY,
                baby_id=baby_id,
                request_id=idempotency_key,
            )
            job_id = await self._create_deletion_job(
                connection,
                principal,
                baby_id=baby_id,
                scope="ALL",
                resource_id=None,
            )
            await self._record_shared_changes(
                connection,
                baby_id=baby_id,
                changes=[("BABY", baby_id, version + 1, True)],
            )
            cursor = await connection.execute(
                """
                update baby_data.babies
                   set status = 'DELETING', version = version + 1
                 where baby_id = %s and version = %s and status = 'ACTIVE'
                returning baby_id
                """,
                (baby_id, version),
            )
            if await cursor.fetchone() is None:
                raise ApiException(ErrorCode.VERSION_CONFLICT, "The baby changed before deletion.")
            await self._complete_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                result_type="DELETION",
                result_id=job_id,
                response_status=202,
            )
            return await self._get_deletion(connection, job_id)

    async def delete_my_contributions(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
        *,
        idempotency_key: UUID,
        path: str,
    ) -> DeletionJob:
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                payload={"confirm": "DELETE_MY_CONTRIBUTIONS"},
            )
            if replay is not None:
                return await self._get_deletion(connection, replay[1])
            try:
                cursor = await connection.execute(
                    "select baby_private.request_own_contribution_deletion(%s) as job_id",
                    (baby_id,),
                )
                job_id = (await cursor.fetchone())["job_id"]  # type: ignore[index]
            except psycopg.Error as exc:
                if "B04_RESOURCE_NOT_FOUND" in str(exc):
                    raise self._not_found() from exc
                raise
            await self._complete_idempotency(
                connection,
                principal,
                method="DELETE",
                path=path,
                key=idempotency_key,
                result_type="DELETION",
                result_id=job_id,
                response_status=202,
            )
            return await self._get_deletion(connection, job_id)

    async def _get_deletion(
        self, connection: DatabaseConnection, job_id: UUID | None
    ) -> DeletionJob:
        cursor = await connection.execute(
            "select * from baby_data.deletion_jobs where deletion_job_id = %s",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found()
        return self._deletion(row)

    async def get_deletion(self, principal: AuthenticatedPrincipal, job_id: UUID) -> DeletionJob:
        async with self.transaction(principal) as connection:
            return await self._get_deletion(connection, job_id)

    async def retry_deletion(
        self,
        principal: AuthenticatedPrincipal,
        job_id: UUID,
        *,
        expected_attempt: int,
        client_request_id: UUID,
        path: str,
    ) -> DeletionJob:
        payload = {
            "client_request_id": str(client_request_id),
            "expected_attempt": expected_attempt,
        }
        async with self.transaction(principal) as connection:
            replay = await self._reserve_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=client_request_id,
                payload=payload,
            )
            if replay is not None:
                return await self._get_deletion(connection, job_id)
            current = await self._get_deletion(connection, job_id)
            if current.status != "FAILED":
                raise ApiException(
                    ErrorCode.INVALID_STATE, "Only a failed deletion can be retried."
                )
            if current.attempt_no != expected_attempt:
                raise ApiException(ErrorCode.VERSION_CONFLICT, "The deletion attempt changed.")
            cursor = await connection.execute(
                """
                update baby_data.deletion_jobs
                   set status = 'RUNNING', attempt_no = attempt_no + 1,
                       failure = null, version = version + 1
                 where deletion_job_id = %s and status = 'FAILED' and attempt_no = %s
                returning deletion_job_id
                """,
                (job_id, expected_attempt),
            )
            if await cursor.fetchone() is None:
                latest = await self._get_deletion(connection, job_id)
                if latest.status != "FAILED":
                    raise ApiException(
                        ErrorCode.INVALID_STATE,
                        "Only a failed deletion can be retried.",
                    )
                raise ApiException(
                    ErrorCode.VERSION_CONFLICT,
                    "The deletion attempt changed.",
                )
            await self._complete_idempotency(
                connection,
                principal,
                method="POST",
                path=path,
                key=client_request_id,
                result_type="DELETION",
                result_id=job_id,
                response_status=202,
            )
            return await self._get_deletion(connection, job_id)

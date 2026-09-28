from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool, PoolClosed, PoolTimeout

from baby_care_api.core.errors import ApiException
from baby_care_api.models.errors import ErrorCode
from baby_care_api.services.security import (
    AuthenticatedPrincipal,
    BabyAccessContext,
    MembershipRole,
)


class PostgresAuthorizationPort:
    """Current-membership authorization through the least-privilege runtime role."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 0,
        max_pool_size: int = 5,
        pool_timeout_seconds: float = 3.0,
    ) -> None:
        self._pool = AsyncConnectionPool[psycopg.AsyncConnection[tuple[Any, ...]]](
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            timeout=pool_timeout_seconds,
            open=False,
            kwargs={"application_name": "baby-care-api"},
        )

    async def open(self) -> None:
        await self._pool.open(wait=False)

    async def close(self) -> None:
        await self._pool.close()

    async def probe(self) -> bool:
        try:
            async with self._pool.connection() as connection, connection.transaction():
                await connection.execute("set local role baby_app")
                cursor = await connection.execute("select 1")
                return await cursor.fetchone() == (1,)
        except (psycopg.Error, PoolClosed, PoolTimeout):
            return False

    async def require_active_baby_access(
        self,
        principal: AuthenticatedPrincipal,
        baby_id: UUID,
    ) -> BabyAccessContext:
        try:
            async with self._pool.connection() as connection, connection.transaction():
                await connection.execute("set local role baby_app")
                await connection.execute(
                    """
                    select
                        set_config('baby.request_user_id', %s, true),
                        set_config('baby.request_session_id', %s, true)
                    """,
                    (str(principal.user_id), str(principal.session_id)),
                )
                cursor = await connection.execute(
                    """
                    select
                        b.baby_id,
                        m.membership_id,
                        m.role::text,
                        m.version,
                        b.version
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
        except (psycopg.Error, PoolClosed, PoolTimeout) as exc:
            raise ApiException.service_unavailable() from exc

        if row is None:
            raise ApiException(
                ErrorCode.RESOURCE_NOT_FOUND,
                "The requested resource was not found.",
            )

        return BabyAccessContext(
            principal=principal,
            baby_id=row[0],
            membership_id=row[1],
            role=MembershipRole(row[2]),
            membership_version=row[3],
            baby_version=row[4],
        )

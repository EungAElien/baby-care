from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum

from baby_care_api.models.health import (
    ComponentReadiness,
    ReadinessChecks,
    ReadinessResponse,
)

ReadinessProbe = Callable[[], Awaitable[bool]]


class ComponentName(StrEnum):
    AUTHENTICATION = "authentication"
    DATABASE = "database"
    MODEL = "model"
    EXTERNAL_SERVICES = "external_services"


_REQUIRED_COMPONENTS = frozenset({ComponentName.AUTHENTICATION, ComponentName.DATABASE})


class ReadinessService:
    def __init__(self, probes: Mapping[ComponentName, ReadinessProbe] | None = None) -> None:
        self._probes = dict(probes or {})

    async def _component(self, name: ComponentName) -> ComponentReadiness:
        probe = self._probes.get(name)
        required = name in _REQUIRED_COMPONENTS
        if probe is None:
            return ComponentReadiness(required=required, configured=False, ready=False)
        try:
            ready = await probe()
        except Exception:
            ready = False
        return ComponentReadiness(required=required, configured=True, ready=ready)

    async def snapshot(self) -> ReadinessResponse:
        authentication = await self._component(ComponentName.AUTHENTICATION)
        database = await self._component(ComponentName.DATABASE)
        model = await self._component(ComponentName.MODEL)
        external_services = await self._component(ComponentName.EXTERNAL_SERVICES)
        checks = ReadinessChecks(
            authentication=authentication,
            database=database,
            model=model,
            external_services=external_services,
        )
        required_ready = all(
            component.ready
            for component in (checks.authentication, checks.database)
            if component.required
        )
        return ReadinessResponse(
            status="ready" if required_ready else "not_ready",
            checks=checks,
        )

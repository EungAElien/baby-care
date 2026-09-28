from __future__ import annotations

from typing import Literal

from baby_care_api.models.base import ContractModel


class ComponentReadiness(ContractModel):
    required: bool
    configured: bool
    ready: bool


class ReadinessChecks(ContractModel):
    authentication: ComponentReadiness
    database: ComponentReadiness
    model: ComponentReadiness
    external_services: ComponentReadiness


class LivenessResponse(ContractModel):
    status: Literal["ok"]
    service_version: str
    contract_version: str


class ReadinessResponse(ContractModel):
    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks

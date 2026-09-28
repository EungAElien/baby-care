from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from baby_care_api.core.config import API_V1_PREFIX
from baby_care_api.models.reminders import (
    PatchReminder,
    Patterns,
    Reminder,
    ReminderKind,
    ReminderPage,
    ReminderSetting,
    ReminderSettings,
    SetRecordCoverage,
    SetReminderSetting,
)
from baby_care_api.models.summary import RecordCoverage
from baby_care_api.routes.b04 import _path, _principal, _service
from baby_care_api.services.idempotency import ensure_idempotency_key_matches
from baby_care_api.services.reminders import (
    get_patterns,
    get_reminder_settings,
    list_reminders,
    patch_reminder,
    set_record_coverage,
    set_reminder_setting,
)

router = APIRouter(prefix=API_V1_PREFIX)
AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]
IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


@router.get("/babies/{baby_id}/patterns", response_model=Patterns, operation_id="getPatterns")
async def patterns(
    baby_id: UUID,
    request: Request,
    range_days: Annotated[int | None, Query(alias="range")] = None,
    kind: ReminderKind | None = None,
    authorization: AuthorizationHeader = None,
) -> Patterns:
    if range_days is not None and range_days != 7:
        from baby_care_api.core.errors import ApiException
        from baby_care_api.models.errors import ErrorCode

        raise ApiException(ErrorCode.VALIDATION_ERROR, "Only the seven-day range is supported.")
    principal = await _principal(request, authorization)
    return await get_patterns(_service(request), principal, baby_id, kind=kind)


@router.put(
    "/babies/{baby_id}/record-coverage",
    response_model=RecordCoverage,
    operation_id="setRecordCoverage",
)
async def record_coverage(
    baby_id: UUID,
    body: SetRecordCoverage,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> RecordCoverage:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await set_record_coverage(
        _service(request), principal, baby_id, body, path=_path(request)
    )


@router.get(
    "/babies/{baby_id}/reminder-settings",
    response_model=ReminderSettings,
    operation_id="getReminderSettings",
)
async def reminder_settings(
    baby_id: UUID, request: Request, authorization: AuthorizationHeader = None
) -> ReminderSettings:
    principal = await _principal(request, authorization)
    return await get_reminder_settings(_service(request), principal, baby_id)


@router.put(
    "/babies/{baby_id}/reminder-settings",
    response_model=ReminderSetting,
    operation_id="setReminderSetting",
)
async def update_reminder_setting(
    baby_id: UUID,
    body: SetReminderSetting,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> ReminderSetting:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await set_reminder_setting(
        _service(request), principal, baby_id, body, path=_path(request)
    )


@router.get(
    "/babies/{baby_id}/reminders", response_model=ReminderPage, operation_id="listReminders"
)
async def reminders(
    baby_id: UUID,
    request: Request,
    active_only: bool = False,
    authorization: AuthorizationHeader = None,
) -> ReminderPage:
    principal = await _principal(request, authorization)
    return await list_reminders(_service(request), principal, baby_id, active_only=active_only)


@router.patch("/reminders/{reminder_id}", response_model=Reminder, operation_id="patchReminder")
async def update_reminder(
    reminder_id: UUID,
    body: PatchReminder,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> Reminder:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await patch_reminder(
        _service(request), principal, reminder_id, body, path=_path(request)
    )

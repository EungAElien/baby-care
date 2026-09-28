from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from baby_care_api.core.errors import ApiException
from baby_care_api.models.audio import SourceRef
from baby_care_api.models.errors import ErrorCode, ErrorDetails
from baby_care_api.models.reminders import (
    PatchReminder,
    PatternEvidence,
    PatternRecord,
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
from baby_care_api.services.b04 import DatabaseConnection, DatabaseRow, PostgresBabyCareService
from baby_care_api.services.daily_summary import day_bounds
from baby_care_api.services.reminder_patterns import POLICY_VERSION, evaluate_pattern
from baby_care_api.services.security import AuthenticatedPrincipal

KINDS: tuple[ReminderKind, ...] = ("FEEDING", "SLEEP_PREPARATION", "DIAPER")
REMINDER_LIFETIME = timedelta(minutes=30)


def _coverage(row: DatabaseRow) -> RecordCoverage:
    return RecordCoverage.model_validate(
        {
            "baby_id": row["baby_id"],
            "date": row["date"],
            "type": "SLEEP" if row["kind"] == "SLEEP_PREPARATION" else row["kind"],
            "confirmed": row["confirmed"],
            "updated_by_user_id": row["created_by_user_id"],
            "version": row["version"],
        }
    )


async def _get_coverage(
    connection: DatabaseConnection,
    *,
    baby_id: UUID,
    day: date,
    kind: str,
    user_id: UUID,
) -> tuple[UUID, RecordCoverage] | None:
    cursor = await connection.execute(
        """
        select record_coverage_id, baby_id, date, kind::text as kind,
               confirmed, created_by_user_id, version
          from baby_data.record_coverage
         where baby_id = %s and date = %s and kind = %s
           and created_by_user_id = %s
        """,
        (baby_id, day, kind, user_id),
    )
    row = await cursor.fetchone()
    return (row["record_coverage_id"], _coverage(row)) if row else None


async def set_record_coverage(
    service: PostgresBabyCareService,
    principal: AuthenticatedPrincipal,
    baby_id: UUID,
    request: SetRecordCoverage,
    *,
    path: str,
) -> RecordCoverage:
    kind = "SLEEP_PREPARATION" if request.type == "SLEEP" else request.type
    async with service.transaction(principal) as connection:
        replay = await service._reserve_idempotency(
            connection,
            principal,
            method="PUT",
            path=path,
            key=request.client_request_id,
            payload=request.model_dump(mode="json"),
        )
        await service._baby_access(connection, baby_id, principal.user_id)
        if replay is not None:
            current = await _get_coverage(
                connection,
                baby_id=baby_id,
                day=request.date,
                kind=kind,
                user_id=principal.user_id,
            )
            if current is None or current[0] != replay[1]:
                raise service._not_found()
            return current[1]
        await service._advisory_lock(
            connection, f"coverage:{baby_id}:{request.date}:{kind}:{principal.user_id}"
        )
        current = await _get_coverage(
            connection,
            baby_id=baby_id,
            day=request.date,
            kind=kind,
            user_id=principal.user_id,
        )
        current_version = current[1].version if current else 0
        if request.version != current_version:
            raise ApiException(
                ErrorCode.VERSION_CONFLICT,
                "The record coverage changed before this update.",
                details=ErrorDetails.empty().model_copy(
                    update={
                        "current_version": current_version or None,
                        "current_resource": current[1].model_dump(mode="json") if current else None,
                        "resource_type": "RECORD_COVERAGE",
                    }
                ),
            )
        if current is None:
            cursor = await connection.execute(
                """
                insert into baby_data.record_coverage
                    (baby_id, date, kind, confirmed, created_by_user_id)
                values (%s, %s, %s, %s, %s)
                returning record_coverage_id
                """,
                (baby_id, request.date, kind, request.confirmed, principal.user_id),
            )
            row = await cursor.fetchone()
            assert row is not None
            coverage_id = row["record_coverage_id"]
        else:
            coverage_id = current[0]
            await connection.execute(
                """
                update baby_data.record_coverage
                   set confirmed = %s, version = version + 1,
                       updated_at = clock_timestamp()
                 where record_coverage_id = %s
                """,
                (request.confirmed, coverage_id),
            )
        await service._complete_idempotency(
            connection,
            principal,
            method="PUT",
            path=path,
            key=request.client_request_id,
            result_type="RECORD_COVERAGE",
            result_id=coverage_id,
            response_status=200,
        )
        result = await _get_coverage(
            connection,
            baby_id=baby_id,
            day=request.date,
            kind=kind,
            user_id=principal.user_id,
        )
        assert result is not None
        return result[1]


async def _pattern_evidence(
    connection: DatabaseConnection,
    *,
    baby_id: UUID,
    timezone: str,
    as_of: datetime,
    kinds: tuple[ReminderKind, ...],
) -> list[PatternEvidence]:
    local_day = as_of.astimezone(ZoneInfo(timezone)).date()
    start, _ = day_bounds(local_day - timedelta(days=6), timezone)
    cursor = await connection.execute(
        """
        select record_coverage_id, baby_id, date, kind::text as kind,
               confirmed, created_by_user_id, version
          from baby_data.record_coverage
         where baby_id = %s and date between %s and %s
        """,
        (baby_id, local_day - timedelta(days=6), local_day),
    )
    coverage = [_coverage(row) for row in await cursor.fetchall()]
    cursor = await connection.execute(
        """
        select care_event_id, event_type::text as event_type,
               occurred_at, ended_at, payload, version
          from baby_data.care_events
         where baby_id = %s and status = 'ACTIVE'
           and event_type in ('FEEDING', 'SLEEP', 'DIAPER')
           and time_precision = 'EXACT'
           and occurred_at >= %s and occurred_at <= %s
         order by occurred_at, care_event_id
        """,
        (baby_id, start, as_of),
    )
    records: list[PatternRecord] = []
    for row in await cursor.fetchall():
        kind = "SLEEP_PREPARATION" if row["event_type"] == "SLEEP" else row["event_type"]
        records.append(
            PatternRecord.model_validate(
                {
                    "event_id": row["care_event_id"],
                    "kind": kind,
                    "occurred_at": row["occurred_at"],
                    "ended_at": row["ended_at"],
                    "feeding_mode": row["payload"].get("mode") if kind == "FEEDING" else None,
                    "version": row["version"],
                }
            )
        )
    return [
        evaluate_pattern(
            kind=kind,
            records=records,
            coverage=coverage,
            timezone=timezone,
            as_of=as_of,
        )
        for kind in kinds
    ]


async def get_patterns(
    service: PostgresBabyCareService,
    principal: AuthenticatedPrincipal,
    baby_id: UUID,
    *,
    kind: ReminderKind | None,
) -> Patterns:
    async with service.transaction(principal, consistent_read=True) as connection:
        access = await service._baby_access(connection, baby_id, principal.user_id)
        cursor = await connection.execute("select clock_timestamp() as as_of")
        row = await cursor.fetchone()
        assert row is not None
        as_of = cast(datetime, row["as_of"])
        evidence = await _pattern_evidence(
            connection,
            baby_id=baby_id,
            timezone=access.baby.timezone,
            as_of=as_of,
            kinds=(kind,) if kind else KINDS,
        )
        return Patterns(baby_id=baby_id, as_of=as_of, items=[item.pattern for item in evidence])


def _setting(row: DatabaseRow) -> ReminderSetting:
    options = row["options"]
    return ReminderSetting.model_validate(
        {
            "baby_id": row["baby_id"],
            "user_id": row["recipient_user_id"],
            "kind": row["kind"],
            "enabled": row["enabled"],
            "lead_minutes": 10,
            "manual_interval_minutes": options.get("manual_interval_minutes"),
            "version": row["version"],
        }
    )


def _reminder(row: DatabaseRow) -> Reminder:
    return Reminder.model_validate(row)


async def _settings(
    connection: DatabaseConnection, baby_id: UUID, user_id: UUID
) -> list[ReminderSetting]:
    cursor = await connection.execute(
        """
        select baby_id, recipient_user_id, kind::text as kind,
               enabled, options, version
          from baby_data.reminder_settings
         where baby_id = %s and recipient_user_id = %s
         order by kind
        """,
        (baby_id, user_id),
    )
    return [_setting(row) for row in await cursor.fetchall()]


async def get_reminder_settings(
    service: PostgresBabyCareService,
    principal: AuthenticatedPrincipal,
    baby_id: UUID,
) -> ReminderSettings:
    async with service.transaction(principal) as connection:
        await service._baby_access(connection, baby_id, principal.user_id)
        return ReminderSettings(items=await _settings(connection, baby_id, principal.user_id))


async def set_reminder_setting(
    service: PostgresBabyCareService,
    principal: AuthenticatedPrincipal,
    baby_id: UUID,
    request: SetReminderSetting,
    *,
    path: str,
) -> ReminderSetting:
    if request.kind == "DIAPER" and request.enabled and request.manual_interval_minutes is None:
        raise ApiException(
            ErrorCode.VALIDATION_ERROR,
            "A manual interval is required for diaper reminders.",
        )
    if request.kind != "DIAPER" and request.manual_interval_minutes is not None:
        raise ApiException(
            ErrorCode.VALIDATION_ERROR,
            "A manual interval is only available for diaper reminders.",
        )
    async with service.transaction(principal) as connection:
        replay = await service._reserve_idempotency(
            connection,
            principal,
            method="PUT",
            path=path,
            key=request.client_request_id,
            payload=request.model_dump(mode="json"),
        )
        await service._baby_access(connection, baby_id, principal.user_id)
        await service._advisory_lock(connection, f"reminder:{baby_id}:{principal.user_id}")
        cursor = await connection.execute(
            """
            select reminder_setting_id, baby_id, recipient_user_id,
                   kind::text as kind, enabled, options, version
              from baby_data.reminder_settings
             where baby_id = %s and recipient_user_id = %s and kind = %s
            """,
            (baby_id, principal.user_id, request.kind),
        )
        current = await cursor.fetchone()
        if replay is not None:
            if current is None or current["reminder_setting_id"] != replay[1]:
                raise service._not_found()
            return _setting(current)
        current_version = current["version"] if current else 0
        if request.version != current_version:
            raise ApiException(
                ErrorCode.VERSION_CONFLICT,
                "The reminder setting changed before this update.",
                details=ErrorDetails.empty().model_copy(
                    update={
                        "current_version": current_version or None,
                        "current_resource": _setting(current).model_dump(mode="json")
                        if current
                        else None,
                        "resource_type": "REMINDER_SETTING",
                    }
                ),
            )
        options = json.dumps(
            {"manual_interval_minutes": request.manual_interval_minutes}
            if request.kind == "DIAPER"
            else {}
        )
        if current is None:
            cursor = await connection.execute(
                """
                insert into baby_data.reminder_settings
                    (baby_id, recipient_user_id, kind, enabled, options)
                values (%s, %s, %s, %s, %s)
                returning reminder_setting_id
                """,
                (baby_id, principal.user_id, request.kind, request.enabled, options),
            )
            row = await cursor.fetchone()
            assert row is not None
            setting_id = row["reminder_setting_id"]
        else:
            setting_id = current["reminder_setting_id"]
            await connection.execute(
                """
                update baby_data.reminder_settings
                   set enabled = %s, options = %s, version = version + 1,
                       updated_at = clock_timestamp()
                 where reminder_setting_id = %s
                """,
                (request.enabled, options, setting_id),
            )
        if not request.enabled:
            await connection.execute(
                """
                update baby_data.reminders
                   set state = 'EXPIRED', snoozed_until = null,
                       version = version + 1, updated_at = clock_timestamp()
                 where baby_id = %s and recipient_user_id = %s and kind = %s
                   and state in ('SCHEDULED', 'DUE', 'SNOOZED')
                """,
                (baby_id, principal.user_id, request.kind),
            )
        await service._complete_idempotency(
            connection,
            principal,
            method="PUT",
            path=path,
            key=request.client_request_id,
            result_type="REMINDER_SETTING",
            result_id=setting_id,
            response_status=200,
        )
        cursor = await connection.execute(
            """
            select baby_id, recipient_user_id, kind::text as kind,
                   enabled, options, version
              from baby_data.reminder_settings
             where reminder_setting_id = %s
            """,
            (setting_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        return _setting(row)


async def _candidate_reminders(
    connection: DatabaseConnection,
    *,
    baby_id: UUID,
    timezone: str,
    as_of: datetime,
    settings: list[ReminderSetting],
) -> dict[ReminderKind, tuple[UUID, datetime, list[SourceRef]]]:
    enabled = [setting for setting in settings if setting.enabled]
    if not enabled:
        return {}
    evidence = await _pattern_evidence(
        connection,
        baby_id=baby_id,
        timezone=timezone,
        as_of=as_of,
        kinds=tuple(setting.kind for setting in enabled),
    )
    candidates: dict[ReminderKind, tuple[UUID, datetime, list[SourceRef]]] = {}
    for setting, item in zip(enabled, evidence, strict=True):
        pattern = item.pattern
        if setting.kind == "DIAPER":
            cursor = await connection.execute(
                """
                select care_event_id, occurred_at, version
                  from baby_data.care_events
                 where baby_id = %s and event_type = 'DIAPER'
                   and status = 'ACTIVE' and time_precision = 'EXACT'
                   and occurred_at <= %s
                 order by occurred_at desc, care_event_id desc
                 limit 1
                """,
                (baby_id, as_of),
            )
            row = await cursor.fetchone()
            if row is None or setting.manual_interval_minutes is None:
                continue
            anchor = row["care_event_id"]
            due = row["occurred_at"] + timedelta(
                minutes=setting.manual_interval_minutes - setting.lead_minutes
            )
            refs = [SourceRef(kind="CARE_EVENT", resource_id=anchor, version=row["version"])]
        else:
            if pattern.status != "READY" or pattern.anchor_event_id is None:
                continue
            assert pattern.estimated_due_at is not None
            anchor = pattern.anchor_event_id
            source_type = "SLEEP" if setting.kind == "SLEEP_PREPARATION" else "FEEDING"
            cursor = await connection.execute(
                """
                select care_event_id
                  from baby_data.care_events
                 where baby_id = %s and event_type = %s
                   and status = 'ACTIVE' and time_precision = 'EXACT'
                   and occurred_at <= %s
                 order by occurred_at desc, care_event_id desc
                 limit 1
                """,
                (baby_id, source_type, as_of),
            )
            latest = await cursor.fetchone()
            if latest is None or latest["care_event_id"] != anchor:
                continue
            due = pattern.estimated_due_at - timedelta(minutes=setting.lead_minutes)
            refs = item.source_refs
        candidates[setting.kind] = (anchor, due, refs)
    return candidates


async def _refresh_reminders(
    connection: DatabaseConnection,
    *,
    baby_id: UUID,
    user_id: UUID,
    timezone: str,
    as_of: datetime,
) -> None:
    settings = await _settings(connection, baby_id, user_id)
    candidates = await _candidate_reminders(
        connection, baby_id=baby_id, timezone=timezone, as_of=as_of, settings=settings
    )
    cursor = await connection.execute(
        """
        select reminder_id, baby_id, recipient_user_id, kind::text as kind,
               anchor_event_id, state::text as state, due_at, snoozed_until,
               seen_at, evidence_refs, policy_version, version,
               recorded_at, updated_at
          from baby_data.reminders
         where baby_id = %s and recipient_user_id = %s
         order by recorded_at desc, reminder_id desc
        """,
        (baby_id, user_id),
    )
    history = [_reminder(row) for row in await cursor.fetchall()]
    for item in history:
        if item.state not in ("SCHEDULED", "DUE", "SNOOZED"):
            continue
        candidate = candidates.get(item.kind)
        obsolete = (
            candidate is None
            or item.anchor_event_id != candidate[0]
            or item.due_at != candidate[1]
            or item.evidence_refs != candidate[2]
            or item.policy_version != POLICY_VERSION
            or as_of >= item.due_at + REMINDER_LIFETIME
        )
        if obsolete:
            await connection.execute(
                """
                update baby_data.reminders
                   set state = 'EXPIRED', snoozed_until = null,
                       version = version + 1, updated_at = clock_timestamp()
                 where reminder_id = %s
                """,
                (item.reminder_id,),
            )
            continue
        if (item.state == "SCHEDULED" and as_of >= item.due_at) or (
            item.state == "SNOOZED"
            and item.snoozed_until is not None
            and as_of >= item.snoozed_until
        ):
            await connection.execute(
                """
                update baby_data.reminders
                   set state = 'DUE', snoozed_until = null,
                       version = version + 1, updated_at = clock_timestamp()
                 where reminder_id = %s
                """,
                (item.reminder_id,),
            )
    for kind, (anchor, due, refs) in candidates.items():
        if as_of >= due + REMINDER_LIFETIME:
            continue
        if any(
            item.kind == kind
            and item.anchor_event_id == anchor
            and ((item.evidence_refs == refs and item.due_at == due) or item.state == "DISMISSED")
            for item in history
        ):
            continue
        await connection.execute(
            """
            insert into baby_data.reminders
                (baby_id, recipient_user_id, kind, anchor_event_id, state,
                 due_at, evidence_refs, policy_version)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                baby_id,
                user_id,
                kind,
                anchor,
                "DUE" if as_of >= due else "SCHEDULED",
                due,
                json.dumps([ref.model_dump(mode="json") for ref in refs]),
                POLICY_VERSION,
            ),
        )


async def list_reminders(
    service: PostgresBabyCareService,
    principal: AuthenticatedPrincipal,
    baby_id: UUID,
    *,
    active_only: bool,
) -> ReminderPage:
    async with service.transaction(principal) as connection:
        access = await service._baby_access(connection, baby_id, principal.user_id)
        await service._advisory_lock(connection, f"reminder:{baby_id}:{principal.user_id}")
        cursor = await connection.execute("select clock_timestamp() as as_of")
        row = await cursor.fetchone()
        assert row is not None
        await _refresh_reminders(
            connection,
            baby_id=baby_id,
            user_id=principal.user_id,
            timezone=access.baby.timezone,
            as_of=row["as_of"],
        )
        cursor = await connection.execute(
            """
            select reminder_id, baby_id, recipient_user_id, kind::text as kind,
                   anchor_event_id, state::text as state, due_at, snoozed_until,
                   seen_at, evidence_refs, policy_version, version,
                   recorded_at, updated_at
              from baby_data.reminders
             where baby_id = %s and recipient_user_id = %s
               and (not %s or state in ('SCHEDULED', 'DUE', 'SNOOZED'))
             order by due_at desc, reminder_id desc
            """,
            (baby_id, principal.user_id, active_only),
        )
        return ReminderPage(items=[_reminder(row) for row in await cursor.fetchall()])


async def _get_reminder(
    connection: DatabaseConnection, reminder_id: UUID, user_id: UUID
) -> Reminder:
    cursor = await connection.execute(
        """
        select reminder_id, baby_id, recipient_user_id, kind::text as kind,
               anchor_event_id, state::text as state, due_at, snoozed_until,
               seen_at, evidence_refs, policy_version, version,
               recorded_at, updated_at
          from baby_data.reminders
         where reminder_id = %s and recipient_user_id = %s
        """,
        (reminder_id, user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ApiException(ErrorCode.RESOURCE_NOT_FOUND, "The reminder was not found.")
    return _reminder(row)


async def patch_reminder(
    service: PostgresBabyCareService,
    principal: AuthenticatedPrincipal,
    reminder_id: UUID,
    request: PatchReminder,
    *,
    path: str,
) -> Reminder:
    async with service.transaction(principal) as connection:
        replay = await service._reserve_idempotency(
            connection,
            principal,
            method="PATCH",
            path=path,
            key=request.client_request_id,
            payload=request.model_dump(mode="json"),
        )
        current = await _get_reminder(connection, reminder_id, principal.user_id)
        access = await service._baby_access(connection, current.baby_id, principal.user_id)
        if replay is not None:
            return current
        await service._advisory_lock(connection, f"reminder:{current.baby_id}:{principal.user_id}")
        cursor = await connection.execute("select clock_timestamp() as as_of")
        row = await cursor.fetchone()
        assert row is not None
        await _refresh_reminders(
            connection,
            baby_id=current.baby_id,
            user_id=principal.user_id,
            timezone=access.baby.timezone,
            as_of=row["as_of"],
        )
        current = await _get_reminder(connection, reminder_id, principal.user_id)
        if current.version != request.version:
            raise ApiException(
                ErrorCode.VERSION_CONFLICT,
                "The reminder changed before this update.",
                details=ErrorDetails.empty().model_copy(
                    update={
                        "current_version": current.version,
                        "current_resource": current.model_dump(mode="json"),
                        "resource_type": "REMINDER",
                    }
                ),
            )
        if current.state not in ("SCHEDULED", "DUE", "SNOOZED"):
            raise ApiException(ErrorCode.INVALID_STATE, "This reminder is no longer active.")
        if request.action == "SNOOZE_10_MIN" and current.state != "DUE":
            raise ApiException(ErrorCode.INVALID_STATE, "Only a due reminder can be snoozed.")
        if request.action == "MARK_SEEN" and current.state == "SCHEDULED":
            raise ApiException(ErrorCode.INVALID_STATE, "Only a due reminder can be marked seen.")
        if request.action == "SNOOZE_10_MIN":
            cursor = await connection.execute(
                """
                update baby_data.reminders
                   set state = 'SNOOZED', snoozed_until = clock_timestamp() + interval '10 minutes',
                       version = version + 1, updated_at = clock_timestamp()
                 where reminder_id = %s and version = %s and state = 'DUE'
                returning reminder_id
                """,
                (reminder_id, request.version),
            )
        elif request.action == "DISMISS_OCCURRENCE":
            cursor = await connection.execute(
                """
                update baby_data.reminders
                   set state = 'DISMISSED', snoozed_until = null,
                       version = version + 1, updated_at = clock_timestamp()
                 where reminder_id = %s and version = %s
                   and state in ('SCHEDULED', 'DUE', 'SNOOZED')
                returning reminder_id
                """,
                (reminder_id, request.version),
            )
        else:
            cursor = await connection.execute(
                """
                update baby_data.reminders
                   set seen_at = coalesce(seen_at, clock_timestamp()),
                       version = version + 1, updated_at = clock_timestamp()
                 where reminder_id = %s and version = %s
                   and state in ('DUE', 'SNOOZED')
                returning reminder_id
                """,
                (reminder_id, request.version),
            )
        if await cursor.fetchone() is None:
            raise ApiException(
                ErrorCode.VERSION_CONFLICT, "The reminder changed before this update."
            )
        await service._complete_idempotency(
            connection,
            principal,
            method="PATCH",
            path=path,
            key=request.client_request_id,
            result_type="REMINDER",
            result_id=reminder_id,
            response_status=200,
        )
        return await _get_reminder(connection, reminder_id, principal.user_id)

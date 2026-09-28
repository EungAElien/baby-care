from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from baby_care_api.models.audio import StateObservation
from baby_care_api.models.care_events import (
    CareEvent,
    DiaperEventValue,
    FeedingEventValue,
    FeedingMode,
    SleepEventValue,
)
from baby_care_api.models.summary import (
    DailySummary,
    DiaperSummary,
    FeedingSummary,
    RecordCoverage,
    SleepSummary,
)


def day_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def build_daily_summary(
    *,
    baby_id: UUID,
    day: date,
    timezone: str,
    as_of: datetime,
    context_revision: int,
    events: list[CareEvent],
    coverage: list[RecordCoverage],
    latest_confirmed_state: StateObservation | None = None,
) -> DailySummary:
    """Aggregate source records without storing another copy of their totals.

    B-14 can use this same function after its own current-membership query.
    A coverage confirmation is the only evidence for a true zero-record day.
    """
    start, end = day_bounds(day, timezone)
    cutoff = min(end, as_of.astimezone(UTC))
    feeds = 0
    known_amounts = 0
    unknown_amounts = 0
    total_ml = 0.0
    breastfeeding_minutes: float | None = None
    sleeps = 0
    sleep_minutes = 0.0
    active_sleep_id: UUID | None = None
    unknown_sleep_duration = False
    diaper_checks = 0
    diaper_changes = 0

    for record in events:
        if record.baby_id != baby_id or record.status != "ACTIVE":
            continue
        event = record.event
        occurred_at = event.occurred_at
        if occurred_at is None:
            # A recording time is not evidence of when an imprecise event happened.
            continue
        occurred_at = occurred_at.astimezone(UTC)
        if isinstance(event, SleepEventValue):
            ended_at = event.ended_at.astimezone(UTC) if event.ended_at else cutoff
            if occurred_at >= cutoff or (ended_at <= start and occurred_at < start):
                continue
            sleeps += 1
            sleep_minutes += max(
                0.0, (min(ended_at, cutoff) - max(occurred_at, start)).total_seconds() / 60
            )
            if event.ended_at is None:
                unknown_sleep_duration = True
                active_sleep_id = record.care_event_id
            continue
        if not start <= occurred_at < cutoff:
            continue
        if isinstance(event, FeedingEventValue):
            feeds += 1
            amount = event.payload.amount_ml
            if amount is None:
                unknown_amounts += 1
            else:
                known_amounts += 1
                total_ml += amount
            if (
                event.payload.mode in (FeedingMode.BREAST, FeedingMode.MIXED)
                and event.payload.duration_minutes is not None
            ):
                breastfeeding_minutes = (
                    breastfeeding_minutes or 0
                ) + event.payload.duration_minutes
        elif isinstance(event, DiaperEventValue):
            if event.payload.operation == "CHECK":
                diaper_checks += 1
            else:
                diaper_changes += 1

    confirmed = {item.type for item in coverage if item.confirmed}
    missing_fields: list[str] = []
    if feeds == 0 and "FEEDING" not in confirmed:
        missing_fields.append("feeding")
    elif unknown_amounts:
        missing_fields.append("feeding.amount")
    if sleeps == 0 and "SLEEP" not in confirmed:
        missing_fields.append("sleep")
    elif unknown_sleep_duration:
        missing_fields.append("sleep.duration")
    if diaper_checks + diaper_changes == 0 and "DIAPER" not in confirmed:
        missing_fields.append("diaper")

    return DailySummary(
        baby_id=baby_id,
        date=day,
        timezone=timezone,
        as_of=as_of,
        context_revision=context_revision,
        has_records=bool(feeds or sleeps or diaper_checks or diaper_changes),
        feeding=FeedingSummary(
            record_count=feeds,
            known_amount_count=known_amounts,
            unknown_amount_count=unknown_amounts,
            total_recorded_ml=total_ml
            if known_amounts or (feeds == 0 and "FEEDING" in confirmed)
            else None,
            breastfeeding_minutes=(
                0.0 if feeds == 0 and "FEEDING" in confirmed else breastfeeding_minutes
            ),
        ),
        sleep=SleepSummary(
            record_count=sleeps,
            recorded_minutes_in_day=sleep_minutes,
            active_sleep_id=active_sleep_id,
            has_unknown_duration=unknown_sleep_duration,
        ),
        diaper=DiaperSummary(check_count=diaper_checks, change_count=diaper_changes),
        record_coverage=coverage,
        latest_confirmed_state=latest_confirmed_state,
        missing_fields=missing_fields,
    )

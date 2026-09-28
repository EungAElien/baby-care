from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import pairwise
from statistics import median
from uuid import UUID
from zoneinfo import ZoneInfo

from baby_care_api.models.audio import SourceRef
from baby_care_api.models.reminders import (
    Pattern,
    PatternEvidence,
    PatternHoldReason,
    PatternRecord,
    ReminderKind,
)
from baby_care_api.models.summary import RecordCoverage

POLICY_VERSION = "care-preparation-v1"


def _percentile(values: list[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def evaluate_pattern(
    *,
    kind: ReminderKind,
    records: list[PatternRecord],
    coverage: list[RecordCoverage],
    timezone: str,
    as_of: datetime,
) -> PatternEvidence:
    """Evaluate a seven-day preparation pattern without treating missing days as zero."""
    zone = ZoneInfo(timezone)
    today = as_of.astimezone(zone).date()
    first_day = today - timedelta(days=6)
    coverage_kind = "SLEEP" if kind == "SLEEP_PREPARATION" else kind
    daily_coverage: dict[date, list[bool]] = {}
    for item in coverage:
        if item.type == coverage_kind and first_day <= item.date <= today:
            daily_coverage.setdefault(item.date, []).append(item.confirmed)
    confirmed_days = {day for day, values in daily_coverage.items() if all(values)}
    valid_days = len(confirmed_days)
    eligible = sorted(
        (
            record
            for record in records
            if record.kind == kind
            and first_day <= record.occurred_at.astimezone(zone).date() <= today
            and record.occurred_at <= as_of
            and record.occurred_at.astimezone(zone).date() in confirmed_days
        ),
        key=lambda item: (item.occurred_at, item.event_id),
    )
    anchor = eligible[-1] if eligible else None
    anchor_time = (
        (anchor.ended_at if kind == "SLEEP_PREPARATION" and anchor else None)
        if kind == "SLEEP_PREPARATION"
        else (anchor.occurred_at if anchor else None)
    )
    intervals: list[float] = []
    evidence: dict[UUID, PatternRecord] = {}
    for previous, current in pairwise(eligible):
        start_day = previous.occurred_at.astimezone(zone).date()
        end_day = current.occurred_at.astimezone(zone).date()
        if not all(
            start_day + timedelta(days=offset) in confirmed_days
            for offset in range((end_day - start_day).days + 1)
        ):
            continue
        if kind == "FEEDING" and previous.feeding_mode != current.feeding_mode:
            continue
        start = previous.ended_at if kind == "SLEEP_PREPARATION" else previous.occurred_at
        if start is None:
            continue
        minutes = (current.occurred_at - start).total_seconds() / 60
        if minutes <= 0:
            continue
        intervals.append(minutes)
        evidence[previous.event_id] = previous
        evidence[current.event_id] = current
    intervals.sort()
    middle = float(median(intervals)) if intervals else None
    p25 = _percentile(intervals, 0.25) if intervals else None
    p75 = _percentile(intervals, 0.75) if intervals else None
    reason: PatternHoldReason | None
    if kind == "DIAPER":
        reason = "MANUAL_ONLY"
    elif valid_days < 3:
        reason = "UNCONFIRMED_DAYS"
    elif len(intervals) < 10:
        reason = "INSUFFICIENT_RECORDS"
    elif middle is not None and p25 is not None and p75 is not None and p75 - p25 > middle / 2:
        reason = "HIGH_VARIABILITY"
    else:
        reason = None
    ready = reason is None and anchor is not None and anchor_time is not None and middle is not None
    if reason is None and not ready:
        reason = "INSUFFICIENT_RECORDS"
    due_at = (
        anchor_time + timedelta(minutes=middle)
        if ready and anchor_time is not None and middle is not None
        else None
    )
    pattern = Pattern(
        kind=kind,
        status="READY" if ready else "ON_HOLD",
        reason=reason,
        valid_days=valid_days,
        interval_count=len(intervals),
        median_minutes=middle,
        p25_minutes=p25,
        p75_minutes=p75,
        anchor_event_id=anchor.event_id if anchor else None,
        estimated_due_at=due_at,
        policy_version=POLICY_VERSION,
    )
    refs = [
        SourceRef(kind="CARE_EVENT", resource_id=item.event_id, version=item.version)
        for item in sorted(evidence.values(), key=lambda value: (value.occurred_at, value.event_id))
    ]
    return PatternEvidence(pattern=pattern, source_refs=refs)

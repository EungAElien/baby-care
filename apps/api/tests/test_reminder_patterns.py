from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from baby_care_api.models.reminders import PatternRecord
from baby_care_api.models.summary import RecordCoverage
from baby_care_api.services.reminder_patterns import evaluate_pattern

BABY = UUID("10000000-0000-4000-8000-000000000101")
USER = UUID("10000000-0000-4000-8000-000000000001")
AS_OF = datetime(2026, 9, 20, 7, tzinfo=UTC)


def coverage(day: date, *, confirmed: bool = True, kind: str = "FEEDING") -> RecordCoverage:
    return RecordCoverage(
        baby_id=BABY,
        date=day,
        type=kind,
        confirmed=confirmed,
        updated_by_user_id=USER,
        version=1,
    )


def records(*, spacing: tuple[int, ...] = (0, 2, 4, 6)) -> list[PatternRecord]:
    result = []
    for index, day in enumerate((18, 19, 20)):
        for offset, hour in enumerate(spacing):
            result.append(
                PatternRecord(
                    event_id=UUID(f"10000000-0000-4000-8000-{index * 10 + offset + 1:012d}"),
                    kind="FEEDING",
                    occurred_at=datetime(2026, 9, day, hour, tzinfo=UTC),
                    version=1,
                )
            )
    return result


def test_ready_pattern_requires_confirmed_days_and_ten_intervals() -> None:
    result = evaluate_pattern(
        kind="FEEDING",
        records=records(),
        coverage=[coverage(date(2026, 9, day)) for day in (18, 19, 20)],
        timezone="UTC",
        as_of=AS_OF,
    )
    assert result.pattern.status == "READY"
    assert result.pattern.valid_days == 3
    assert result.pattern.interval_count == 11
    assert result.pattern.median_minutes == 120
    assert result.pattern.estimated_due_at == datetime(2026, 9, 20, 8, tzinfo=UTC)
    assert len(result.source_refs) == 12


def test_unconfirmed_day_excludes_its_records_and_crossing_interval() -> None:
    result = evaluate_pattern(
        kind="FEEDING",
        records=records(),
        coverage=[
            coverage(date(2026, 9, 18)),
            coverage(date(2026, 9, 19), confirmed=False),
            coverage(date(2026, 9, 20)),
        ],
        timezone="UTC",
        as_of=AS_OF,
    )
    assert result.pattern.status == "ON_HOLD"
    assert result.pattern.reason == "UNCONFIRMED_DAYS"
    assert result.pattern.interval_count == 6
    assert result.pattern.estimated_due_at is None


def test_high_variability_holds_even_with_enough_records() -> None:
    widely_spaced = records(spacing=(0, 1, 4, 5, 8, 9))
    result = evaluate_pattern(
        kind="FEEDING",
        records=widely_spaced,
        coverage=[coverage(date(2026, 9, day)) for day in (18, 19, 20)],
        timezone="UTC",
        as_of=AS_OF + timedelta(hours=3),
    )
    assert result.pattern.interval_count >= 10
    assert result.pattern.status == "ON_HOLD"
    assert result.pattern.reason == "HIGH_VARIABILITY"


def test_diaper_pattern_is_manual_only() -> None:
    result = evaluate_pattern(
        kind="DIAPER",
        records=[],
        coverage=[],
        timezone="Asia/Seoul",
        as_of=AS_OF,
    )
    assert result.pattern.status == "ON_HOLD"
    assert result.pattern.reason == "MANUAL_ONLY"
    assert result.pattern.estimated_due_at is None


def test_feeding_requires_consecutive_matching_modes() -> None:
    items = records()
    items[1] = items[1].model_copy(update={"feeding_mode": "FORMULA"})
    result = evaluate_pattern(
        kind="FEEDING",
        records=items,
        coverage=[coverage(date(2026, 9, day)) for day in (18, 19, 20)],
        timezone="UTC",
        as_of=AS_OF,
    )
    assert result.pattern.interval_count == 9
    assert result.pattern.reason == "INSUFFICIENT_RECORDS"


def test_sleep_preparation_uses_wake_to_next_sleep_and_latest_wake() -> None:
    items = [
        record.model_copy(
            update={
                "kind": "SLEEP_PREPARATION",
                "ended_at": record.occurred_at + timedelta(hours=1),
            }
        )
        for record in records()
    ]
    result = evaluate_pattern(
        kind="SLEEP_PREPARATION",
        records=items,
        coverage=[coverage(date(2026, 9, day), kind="SLEEP") for day in (18, 19, 20)],
        timezone="UTC",
        as_of=AS_OF,
    )
    assert result.pattern.status == "READY"
    assert result.pattern.median_minutes == 60
    assert result.pattern.estimated_due_at == datetime(2026, 9, 20, 8, tzinfo=UTC)

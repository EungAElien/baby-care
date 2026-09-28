from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from baby_care_api.models.care_events import CareEvent
from baby_care_api.models.summary import RecordCoverage
from baby_care_api.services.daily_summary import build_daily_summary, day_bounds

BABY_ID = UUID("10000000-0000-4000-8000-000000000101")
USER_ID = UUID("10000000-0000-4000-8000-000000000001")
DAY = date(2026, 9, 19)
AS_OF = datetime(2026, 9, 20, 3, tzinfo=UTC)


def event(
    kind: str,
    occurred_at: datetime | None,
    *,
    ended_at: datetime | None = None,
    payload: dict[str, object] | None = None,
    status: str = "ACTIVE",
) -> CareEvent:
    if kind == "FEEDING":
        payload = {"mode": "FORMULA", "amount_ml": None, "duration_minutes": None} | (payload or {})
    return CareEvent.model_validate(
        {
            "care_event_id": uuid4(),
            "baby_id": BABY_ID,
            "created_by_user_id": USER_ID,
            "updated_by_user_id": USER_ID,
            "source_entry_id": None,
            "status": status,
            "event": {
                "type": kind,
                "occurred_at": occurred_at,
                "ended_at": ended_at,
                "time_precision": "EXACT" if occurred_at else "UNKNOWN",
                "payload": payload or {},
            },
            "data_origin": "USER",
            "version": 1,
            "recorded_at": AS_OF,
            "updated_at": AS_OF,
        }
    )


def summary(
    records: list[CareEvent],
    *,
    day: date = DAY,
    as_of: datetime = AS_OF,
    coverage: list[RecordCoverage] | None = None,
    timezone: str = "Asia/Seoul",
):
    return build_daily_summary(
        baby_id=BABY_ID,
        day=day,
        timezone=timezone,
        as_of=as_of,
        context_revision=3,
        events=records,
        coverage=coverage or [],
    )


def test_empty_day_and_confirmed_zero_are_distinct() -> None:
    empty = summary([])
    assert empty.has_records is False
    assert empty.feeding.total_recorded_ml is None
    assert empty.missing_fields == ["feeding", "sleep", "diaper"]

    covered = [
        RecordCoverage(
            baby_id=BABY_ID,
            date=DAY,
            type=kind,
            confirmed=True,
            updated_by_user_id=USER_ID,
            version=1,
        )
        for kind in ("FEEDING", "SLEEP", "DIAPER")
    ]
    zero = summary([], coverage=covered)
    assert zero.has_records is False
    assert zero.missing_fields == []
    assert zero.feeding.total_recorded_ml == 0
    assert zero.feeding.breastfeeding_minutes == 0
    assert zero.record_coverage == covered


def test_unknown_amount_and_actual_zero_remain_distinct() -> None:
    when = datetime(2026, 9, 19, 4, tzinfo=UTC)
    unknown = event("FEEDING", when, payload={"mode": "FORMULA", "amount_ml": None})
    result = summary([unknown])
    assert result.feeding.record_count == 1
    assert result.feeding.unknown_amount_count == 1
    assert result.feeding.total_recorded_ml is None
    assert "feeding.amount" in result.missing_fields

    known_zero = event("FEEDING", when, payload={"mode": "FORMULA", "amount_ml": 0})
    mixed = summary([unknown, known_zero])
    assert mixed.feeding.known_amount_count == 1
    assert mixed.feeding.unknown_amount_count == 1
    assert mixed.feeding.total_recorded_ml == 0


def test_sleep_is_split_at_local_midnight_without_changing_source() -> None:
    source = event(
        "SLEEP",
        datetime(2026, 9, 19, 14, tzinfo=UTC),
        ended_at=datetime(2026, 9, 19, 16, tzinfo=UTC),
    )
    first = summary([source], day=date(2026, 9, 19))
    second = summary([source], day=date(2026, 9, 20))
    assert first.sleep.record_count == second.sleep.record_count == 1
    assert first.sleep.recorded_minutes_in_day == 60
    assert second.sleep.recorded_minutes_in_day == 60


def test_dst_day_bounds_use_real_elapsed_time() -> None:
    start, end = day_bounds(date(2025, 3, 9), "America/New_York")
    assert (end - start).total_seconds() == 23 * 3600
    start, end = day_bounds(date(2025, 11, 2), "America/New_York")
    assert (end - start).total_seconds() == 25 * 3600


def test_active_sleep_stops_at_as_of_and_marks_unknown_duration() -> None:
    source = event("SLEEP", datetime(2026, 9, 19, 14, tzinfo=UTC))
    result = summary([source], as_of=datetime(2026, 9, 19, 15, tzinfo=UTC))
    assert result.sleep.recorded_minutes_in_day == 60
    assert result.sleep.active_sleep_id == source.care_event_id
    assert result.sleep.has_unknown_duration is True
    assert "sleep.duration" in result.missing_fields


def test_recomputation_uses_current_version_and_excludes_deleting_records() -> None:
    original = event(
        "FEEDING",
        datetime(2026, 9, 19, 4, tzinfo=UTC),
        payload={"mode": "FORMULA", "amount_ml": 120},
    )
    changed = original.model_copy(deep=True)
    changed.event.payload.amount_ml = 90  # type: ignore[union-attr]
    changed.version = 2
    removed = original.model_copy(update={"status": "DELETING"})
    assert summary([original]).feeding.total_recorded_ml == 120
    assert summary([changed]).feeding.total_recorded_ml == 90
    assert summary([removed]).feeding.total_recorded_ml is None


def test_unknown_time_is_not_assigned_to_recording_date() -> None:
    unknown = event("FEEDING", None, payload={"mode": "FORMULA", "amount_ml": 80})
    assert summary([unknown]).feeding.record_count == 0


@pytest.mark.parametrize("kind", ["CHECK", "CHANGE"])
def test_diaper_operations_count_separately(kind: str) -> None:
    record = event(
        "DIAPER",
        datetime(2026, 9, 19, 4, tzinfo=UTC),
        payload={"operation": kind, "condition": "CLEAN"},
    )
    result = summary([record])
    assert result.diaper.check_count == int(kind == "CHECK")
    assert result.diaper.change_count == int(kind == "CHANGE")

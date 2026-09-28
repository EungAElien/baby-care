from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_b04_api import LocalAuth, _database_url, _headers, _required_env

from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.main import create_app

pytestmark = pytest.mark.integration


def test_b11_patterns_and_personal_reminder_lifecycle() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b11-owner")
    caregiver = auth.create_user("b11-caregiver")
    outsider = auth.create_user("b11-outsider")
    now = datetime.now(UTC)
    timezone = "Asia/Tokyo" if now.hour < 3 or now.hour > 22 else "UTC"
    zone = ZoneInfo(timezone)
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        database_url=_database_url(),
        supabase_jwt_issuer=_required_env("BABY_CARE_TEST_SUPABASE_ISSUER"),
        supabase_jwt_audience="authenticated",
        supabase_jwks_url=_required_env("BABY_CARE_TEST_SUPABASE_JWKS_URL"),
        supabase_url=auth.api_url,
        supabase_publishable_key=auth.anon_key,
        reauthentication_proof_secret="local-integration-proof-secret-at-least-32-bytes",
        child_data_production_enabled=False,
    )
    with TestClient(create_app(settings=settings)) as client:
        create_id = uuid4()
        created = client.post(
            "/v1/babies",
            headers=_headers(owner, request_id=create_id),
            json={
                "client_request_id": str(create_id),
                "alias": "B-11 synthetic baby",
                "birth_date": "2026-01-01",
                "feeding_mode": "FORMULA",
                "timezone": timezone,
            },
        )
        assert created.status_code == 201, created.text
        baby_id = created.json()["baby"]["baby_id"]
        local_day = now.astimezone(zone).date()
        with psycopg.connect(_database_url()) as connection:
            connection.execute(
                """
                insert into baby_data.baby_memberships
                    (baby_id, user_id, role, relationship, display_name)
                values (%s, %s, 'CAREGIVER', 'OTHER', 'B-11 caregiver')
                """,
                (baby_id, caregiver.user_id),
            )
            for days_ago, hours in ((2, (0, 2, 4, 6, 8, 10)), (1, (0, 2, 4, 6, 8))):
                day = local_day - timedelta(days=days_ago)
                for hour in hours:
                    occurred_at = datetime.combine(
                        day, datetime.min.time(), tzinfo=zone
                    ) + timedelta(hours=hour)
                    connection.execute(
                        """
                        insert into baby_data.care_events
                            (baby_id, event_type, occurred_at, time_precision,
                             payload, data_origin, created_by_user_id, updated_by_user_id)
                        values (%s, 'FEEDING', %s, 'EXACT', %s, 'USER', %s, %s)
                        """,
                        (
                            baby_id,
                            occurred_at,
                            json.dumps(
                                {
                                    "mode": "FORMULA",
                                    "amount_ml": None,
                                    "duration_minutes": None,
                                }
                            ),
                            owner.user_id,
                            owner.user_id,
                        ),
                    )
            connection.execute(
                """
                insert into baby_data.care_events
                    (baby_id, event_type, occurred_at, time_precision,
                     payload, data_origin, created_by_user_id, updated_by_user_id)
                values (%s, 'FEEDING', %s, 'EXACT', %s, 'USER', %s, %s)
                """,
                (
                    baby_id,
                    now - timedelta(minutes=115),
                    json.dumps({"mode": "FORMULA", "amount_ml": None, "duration_minutes": None}),
                    owner.user_id,
                    owner.user_id,
                ),
            )
        for days_ago in (2, 1, 0):
            key = uuid4()
            response = client.put(
                f"/v1/babies/{baby_id}/record-coverage",
                headers=_headers(owner, request_id=key),
                json={
                    "client_request_id": str(key),
                    "date": (local_day - timedelta(days=days_ago)).isoformat(),
                    "type": "FEEDING",
                    "confirmed": True,
                    "version": 0,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["version"] == 1
        pattern = client.get(f"/v1/babies/{baby_id}/patterns", headers=_headers(caregiver))
        assert pattern.status_code == 200, pattern.text
        feeding = next(item for item in pattern.json()["items"] if item["kind"] == "FEEDING")
        assert feeding["status"] == "READY"
        assert feeding["interval_count"] >= 10
        assert (
            client.get(f"/v1/babies/{baby_id}/patterns", headers=_headers(outsider)).status_code
            == 404
        )

        setting_id = uuid4()
        enabled = client.put(
            f"/v1/babies/{baby_id}/reminder-settings",
            headers=_headers(owner, request_id=setting_id),
            json={
                "client_request_id": str(setting_id),
                "kind": "FEEDING",
                "enabled": True,
                "manual_interval_minutes": None,
                "version": 0,
            },
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["enabled"] is True
        assert client.get(
            f"/v1/babies/{baby_id}/reminder-settings", headers=_headers(caregiver)
        ).json() == {"items": []}
        owner_list = client.get(
            f"/v1/babies/{baby_id}/reminders?active_only=true", headers=_headers(owner)
        )
        assert owner_list.status_code == 200, owner_list.text
        assert len(owner_list.json()["items"]) == 1
        due = owner_list.json()["items"][0]
        assert due["state"] == "DUE"
        assert client.get(
            f"/v1/babies/{baby_id}/reminders", headers=_headers(caregiver)
        ).json() == {"items": [], "next_cursor": None}
        reminder_id = due["reminder_id"]
        snooze_id = uuid4()
        snoozed = client.patch(
            f"/v1/reminders/{reminder_id}",
            headers=_headers(owner, request_id=snooze_id),
            json={
                "client_request_id": str(snooze_id),
                "version": due["version"],
                "action": "SNOOZE_10_MIN",
            },
        )
        assert snoozed.status_code == 200, snoozed.text
        assert snoozed.json()["state"] == "SNOOZED"
        foreign_id = uuid4()
        assert client.patch(
            f"/v1/reminders/{reminder_id}",
            headers=_headers(caregiver, request_id=foreign_id),
            json={
                "client_request_id": str(foreign_id),
                "version": snoozed.json()["version"],
                "action": "MARK_SEEN",
            },
        ).status_code in (403, 404)
        assert (
            len(
                client.get(
                    f"/v1/babies/{baby_id}/reminders?active_only=true", headers=_headers(owner)
                ).json()["items"]
            )
            == 1
        )
        dismiss_id = uuid4()
        dismissed = client.patch(
            f"/v1/reminders/{reminder_id}",
            headers=_headers(owner, request_id=dismiss_id),
            json={
                "client_request_id": str(dismiss_id),
                "version": snoozed.json()["version"],
                "action": "DISMISS_OCCURRENCE",
            },
        )
        assert dismissed.status_code == 200, dismissed.text
        assert dismissed.json()["state"] == "DISMISSED"
        assert (
            client.get(
                f"/v1/babies/{baby_id}/reminders?active_only=true", headers=_headers(owner)
            ).json()["items"]
            == []
        )

        new_event_id = uuid4()
        new_event = client.post(
            f"/v1/babies/{baby_id}/care-events",
            headers=_headers(owner, request_id=new_event_id),
            json={
                "client_request_id": str(new_event_id),
                "event": {
                    "type": "FEEDING",
                    "occurred_at": (now - timedelta(seconds=2)).isoformat(),
                    "ended_at": None,
                    "time_precision": "EXACT",
                    "payload": {"mode": "FORMULA", "amount_ml": None, "duration_minutes": None},
                },
            },
        )
        assert new_event.status_code == 201, new_event.text
        refreshed = client.get(
            f"/v1/babies/{baby_id}/reminders?active_only=true", headers=_headers(owner)
        )
        assert refreshed.status_code == 200, refreshed.text
        assert len(refreshed.json()["items"]) == 1
        replacement = refreshed.json()["items"][0]
        assert replacement["reminder_id"] != reminder_id
        assert replacement["state"] == "SCHEDULED"
        assert replacement["anchor_event_id"] == new_event.json()["care_event_id"]

        delete_id = uuid4()
        deleted = client.delete(
            f"/v1/care-events/{new_event.json()['care_event_id']}?version=1",
            headers=_headers(owner, request_id=delete_id),
        )
        assert deleted.status_code == 202, deleted.text
        assert (
            client.get(
                f"/v1/babies/{baby_id}/reminders?active_only=true", headers=_headers(owner)
            ).json()["items"]
            == []
        )

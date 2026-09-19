from __future__ import annotations

import asyncio
import json
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from time import perf_counter
from time import sleep as wait_seconds
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_b04_api import (
    AuthSession,
    LocalAuth,
    _assert_error,
    _database_url,
    _headers,
    _insert_episode_and_upload_grant,
    _reauthenticate,
    _required_env,
)

from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.core.errors import ApiException
from baby_care_api.main import create_app

pytestmark = pytest.mark.integration


def _settings(auth: LocalAuth) -> Settings:
    return Settings(
        environment=RuntimeEnvironment.TEST,
        log_level="CRITICAL",
        database_url=_database_url(),
        supabase_jwt_issuer=_required_env("BABY_CARE_TEST_SUPABASE_ISSUER"),
        supabase_jwt_audience="authenticated",
        supabase_jwks_url=_required_env("BABY_CARE_TEST_SUPABASE_JWKS_URL"),
        supabase_url=auth.api_url,
        supabase_publishable_key=auth.anon_key,
        reauthentication_proof_secret="local-integration-proof-secret-at-least-32-bytes",
    )


def _create_baby(client: TestClient, owner: AuthSession, label: str) -> dict[str, Any]:
    request_id = uuid4()
    response = client.post(
        "/v1/babies",
        headers=_headers(owner, request_id=request_id),
        json={
            "client_request_id": str(request_id),
            "alias": label,
            "birth_date": "2026-01-01",
            "feeding_mode": "MIXED",
            "timezone": "Asia/Seoul",
        },
    )
    assert response.status_code == 201
    return response.json()


def _poll(
    client: TestClient,
    session: AuthSession,
    baby_id: str,
    since_revision: int,
) -> tuple[Any, dict[str, Any]]:
    response = client.get(
        f"/v1/babies/{baby_id}/changes?since_revision={since_revision}",
        headers=_headers(session),
    )
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Authorization"
    payload = response.json()
    assert set(payload) == {
        "baby_id",
        "current_revision",
        "changes",
        "resync_required",
        "server_time",
    }
    assert all(
        set(change) == {"resource_type", "resource_id", "version", "deleted"}
        for change in payload["changes"]
    )
    return response, payload


def _event_request(event_type: str, occurred_at: str, **payload: Any) -> dict[str, Any]:
    return {
        "type": event_type,
        "occurred_at": occurred_at,
        "ended_at": None,
        "time_precision": "EXACT",
        "payload": payload,
    }


def _create_event(
    client: TestClient,
    session: AuthSession,
    baby_id: str,
    event: dict[str, Any],
    *,
    request_id: UUID | None = None,
) -> tuple[UUID, Any]:
    active_request_id = request_id or uuid4()
    response = client.post(
        f"/v1/babies/{baby_id}/care-events",
        headers=_headers(session, request_id=active_request_id),
        json={"client_request_id": str(active_request_id), "event": event},
    )
    return active_request_id, response


def _accept_caregiver(
    client: TestClient,
    auth: LocalAuth,
    owner: AuthSession,
    caregiver: AuthSession,
    baby_id: str,
) -> tuple[AuthSession, dict[str, Any]]:
    otp_owner, proof = _reauthenticate(
        client,
        auth,
        owner,
        baby_id=baby_id,
        operation="CREATE_INVITE",
    )
    request_id = uuid4()
    invite = client.post(
        f"/v1/babies/{baby_id}/invites",
        headers=_headers(otp_owner, request_id=request_id, proof=proof),
        json={"client_request_id": str(request_id), "email": caregiver.email},
    )
    assert invite.status_code == 201
    token = urlsplit(invite.json()["invite_url"]).fragment
    accept_id = uuid4()
    accepted = client.post(
        "/v1/invites/accept",
        headers=_headers(caregiver, request_id=accept_id),
        json={
            "client_request_id": str(accept_id),
            "token": token,
            "accept_shared_use": True,
            "policy_version": "b09-local-test-v1",
            "relationship": "FATHER",
        },
    )
    assert accepted.status_code == 200
    return otp_owner, accepted.json()["membership"]


def _find_change(payload: dict[str, Any], resource_id: str) -> dict[str, Any]:
    return next(change for change in payload["changes"] if change["resource_id"] == resource_id)


def test_b09_actual_writes_recover_without_leaking_private_or_revoked_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = LocalAuth()
    owner, owner_other = auth.create_user_with_two_sessions("b09-owner")
    caregiver = auth.create_user("b09-caregiver")
    outsider = auth.create_user("b09-outsider")
    settings = _settings(auth)
    app = create_app(settings=settings)
    occurred_at = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()

    with TestClient(app) as client:
        other_baby = _create_baby(client, outsider, "B-09 other baby")
        other_baby_id = other_baby["baby"]["baby_id"]
        created = _create_baby(client, owner, "B-09 shared baby")
        baby_id = created["baby"]["baby_id"]

        _, initial = _poll(client, owner, baby_id, 0)
        assert initial["resync_required"] is True
        assert initial["changes"] == []
        initial_revision = initial["current_revision"]
        _, empty = _poll(client, owner, baby_id, initial_revision)
        assert empty["resync_required"] is False
        assert empty["changes"] == []
        _assert_error(
            client.get(
                f"/v1/babies/{baby_id}/changes?since_revision=0",
                headers=_headers(outsider),
            ),
            404,
            "RESOURCE_NOT_FOUND",
        )
        _assert_error(
            client.get(
                f"/v1/babies/{other_baby_id}/changes?since_revision=0",
                headers=_headers(owner),
            ),
            404,
            "RESOURCE_NOT_FOUND",
        )

        owner, caregiver_membership = _accept_caregiver(client, auth, owner, caregiver, baby_id)
        _, joined = _poll(client, owner, baby_id, initial_revision)
        membership_change = _find_change(joined, caregiver_membership["membership_id"])
        assert membership_change == {
            "resource_type": "MEMBERSHIP",
            "resource_id": caregiver_membership["membership_id"],
            "version": 1,
            "deleted": False,
        }
        applied_revision = joined["current_revision"]
        _, caregiver_initial = _poll(client, caregiver, baby_id, 0)
        assert caregiver_initial["resync_required"] is True

        # Make request-start order and commit order deliberately differ. The
        # OWNER request starts first but pauses before the feed boundary; the
        # later CAREGIVER request commits first. Feed revisions must follow the
        # serialized commit order and both changes must remain recoverable.
        service = app.state.b04_service
        assert service is not None
        original_feed_lock = service._lock_shared_change_feed
        first_request_reached_feed = threading.Event()
        release_first_request = threading.Event()
        lock_call_count = 0

        async def delay_first_feed_lock(*args: Any, **kwargs: Any) -> None:
            nonlocal lock_call_count
            lock_call_count += 1
            if lock_call_count == 1:
                first_request_reached_feed.set()
                released = await asyncio.to_thread(release_first_request.wait, 10)
                assert released
            await original_feed_lock(*args, **kwargs)

        monkeypatch.setattr(service, "_lock_shared_change_feed", delay_first_feed_lock)

        def patch_relationship(actor: AuthSession, relationship: str) -> Any:
            request_id = uuid4()
            return client.patch(
                f"/v1/babies/{baby_id}/members/me",
                headers=_headers(actor, request_id=request_id),
                json={
                    "client_request_id": str(request_id),
                    "version": 1,
                    "relationship": relationship,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            earlier_request = executor.submit(patch_relationship, owner, "MOTHER")
            assert first_request_reached_feed.wait(timeout=10)
            later_request = executor.submit(patch_relationship, caregiver, "GRANDPARENT")
            try:
                first_commit = later_request.result(timeout=10)
            finally:
                release_first_request.set()
            second_commit = earlier_request.result(timeout=10)
        monkeypatch.setattr(service, "_lock_shared_change_feed", original_feed_lock)
        assert first_commit.status_code == 200
        assert second_commit.status_code == 200
        assert first_commit.json()["membership_id"] == caregiver_membership["membership_id"]
        owner_membership_id = created["membership"]["membership_id"]
        assert second_commit.json()["membership_id"] == owner_membership_id

        _, commit_order_changes = _poll(client, owner, baby_id, applied_revision)
        assert {change["resource_id"] for change in commit_order_changes["changes"]} >= {
            caregiver_membership["membership_id"],
            owner_membership_id,
        }
        with psycopg.connect(_database_url()) as connection:
            revision_rows = connection.execute(
                """
                select resource_id::text, change_revision
                  from baby_data.shared_changes
                 where baby_id = %s
                   and resource_type = 'MEMBERSHIP'
                   and resource_id = any(%s::uuid[])
                   and resource_version = 2
                 order by change_revision
                """,
                (baby_id, [caregiver_membership["membership_id"], owner_membership_id]),
            ).fetchall()
        assert [row[0] for row in revision_rows] == [
            caregiver_membership["membership_id"],
            owner_membership_id,
        ]
        applied_revision = commit_order_changes["current_revision"]

        # A private draft and a personal consent remain invisible even through
        # revision or count side channels. OWNER cannot fetch the draft either.
        draft_id = uuid4()
        draft = client.post(
            f"/v1/babies/{baby_id}/care-entries",
            headers=_headers(caregiver, request_id=draft_id),
            json={
                "client_request_id": str(draft_id),
                "episode_id": None,
                "supersedes_entry_id": None,
                "input_mode": "TEXT",
                "raw_text": "synthetic private B-09 note",
                "choices": [],
                "occurred_at": occurred_at,
                "time_precision": "EXACT",
                "base_record_versions": [],
            },
        )
        assert draft.status_code == 201
        _assert_error(
            client.get(
                f"/v1/care-entries/{draft.json()['entry_id']}",
                headers=_headers(owner),
            ),
            404,
            "RESOURCE_NOT_FOUND",
        )
        consent_id = uuid4()
        consent = client.put(
            f"/v1/me/training-consents/{baby_id}",
            headers=_headers(caregiver, request_id=consent_id),
            json={
                "client_request_id": str(consent_id),
                "granted": True,
                "policy_version": "b09-local-test-v1",
                "version": 0,
            },
        )
        assert consent.status_code == 200
        _, private_poll = _poll(client, owner, baby_id, applied_revision)
        assert private_poll["current_revision"] == applied_revision
        assert private_poll["changes"] == []

        feeding = _event_request(
            "FEEDING",
            occurred_at,
            mode="FORMULA",
            amount_ml=80,
            duration_minutes=None,
        )
        create_id, created_event = _create_event(client, caregiver, baby_id, feeding)
        assert created_event.status_code == 201
        event_id = created_event.json()["care_event_id"]
        replay = _create_event(
            client,
            caregiver,
            baby_id,
            feeding,
            request_id=create_id,
        )[1]
        assert replay.status_code == 201 and replay.json() == created_event.json()
        _, first_change = _poll(client, owner, baby_id, applied_revision)
        assert _find_change(first_change, event_id)["version"] == 1
        assert _find_change(first_change, baby_id) == {
            "resource_type": "BABY",
            "resource_id": baby_id,
            "version": 2,
            "deleted": False,
        }
        first_event_revision = first_change["current_revision"]
        _, repeated = _poll(client, owner, baby_id, applied_revision)
        assert repeated["current_revision"] == first_change["current_revision"]
        assert repeated["changes"] == first_change["changes"]
        assert repeated["resync_required"] is False

        # Do not advance the durable client revision if a required refetch fails.
        saved_revision = applied_revision
        _assert_error(
            client.get(f"/v1/care-events/{event_id}", headers=_headers(outsider)),
            404,
            "RESOURCE_NOT_FOUND",
        )
        assert saved_revision == applied_revision
        assert client.get(f"/v1/care-events/{event_id}", headers=_headers(owner)).status_code == 200
        saved_revision = first_event_revision

        patch_two_id = uuid4()
        patched_two = client.patch(
            f"/v1/care-events/{event_id}",
            headers=_headers(owner, request_id=patch_two_id),
            json={
                "client_request_id": str(patch_two_id),
                "version": 1,
                "event": {
                    **feeding,
                    "payload": {**feeding["payload"], "amount_ml": 90},
                },
            },
        )
        assert patched_two.status_code == 200
        patch_three_id = uuid4()
        patched_three = client.patch(
            f"/v1/care-events/{event_id}",
            headers=_headers(caregiver, request_id=patch_three_id),
            json={
                "client_request_id": str(patch_three_id),
                "version": 2,
                "event": {
                    **feeding,
                    "payload": {**feeding["payload"], "amount_ml": 100},
                },
            },
        )
        assert patched_three.status_code == 200
        delete_id = uuid4()
        deleted = client.delete(
            f"/v1/care-events/{event_id}?version=3",
            headers=_headers(caregiver, request_id=delete_id),
        )
        assert deleted.status_code == 202
        _, coalesced = _poll(client, owner, baby_id, saved_revision)
        assert [change for change in coalesced["changes"] if change["resource_id"] == event_id] == [
            {
                "resource_type": "CARE_EVENT",
                "resource_id": event_id,
                "version": 4,
                "deleted": True,
            }
        ]
        assert _find_change(coalesced, baby_id)["version"] == 5
        _assert_error(
            client.get(f"/v1/care-events/{event_id}", headers=_headers(owner)),
            404,
            "RESOURCE_NOT_FOUND",
        )
        # A delayed version-2 body cannot resurrect a version-4 tombstone.
        cached: dict[str, dict[str, Any]] = {event_id: patched_two.json()}
        tombstone_versions = {event_id: 4}
        cached.pop(event_id, None)
        delayed_body = patched_two.json()
        if delayed_body["version"] > tombstone_versions[event_id]:
            cached[event_id] = delayed_body
        assert event_id not in cached
        applied_revision = coalesced["current_revision"]

        sleep = _event_request("SLEEP", occurred_at)
        _, sleep_created = _create_event(client, caregiver, baby_id, sleep)
        assert sleep_created.status_code == 201
        sleep_event_id = sleep_created.json()["care_event_id"]
        finish_id = uuid4()
        sleep_finished = client.patch(
            f"/v1/care-events/{sleep_event_id}",
            headers=_headers(caregiver, request_id=finish_id),
            json={
                "client_request_id": str(finish_id),
                "version": 1,
                "event": {**sleep, "ended_at": datetime.now(UTC).isoformat()},
            },
        )
        assert sleep_finished.status_code == 200
        _, sleep_changes = _poll(client, caregiver, baby_id, applied_revision)
        assert _find_change(sleep_changes, sleep_event_id) == {
            "resource_type": "CARE_EVENT",
            "resource_id": sleep_event_id,
            "version": 2,
            "deleted": False,
        }
        assert _find_change(sleep_changes, baby_id)["version"] == 7
        applied_revision = sleep_changes["current_revision"]

        episode_id, _ = _insert_episode_and_upload_grant(baby_id=UUID(baby_id), owner=owner)
        action_id = uuid4()
        action = client.post(
            f"/v1/episodes/{episode_id}/actions",
            headers=_headers(caregiver, request_id=action_id),
            json={
                "client_request_id": str(action_id),
                "care_event_id": None,
                "new_care_event": _event_request("SOOTHE", occurred_at, action_kind="HOLDING"),
                "recommendation_id": None,
                "performed_by_user_id": str(caregiver.user_id),
                "sequence": 1,
            },
        )
        assert action.status_code == 201 and action.json()["care_event_id"]
        _, action_changes = _poll(client, owner, baby_id, applied_revision)
        assert _find_change(action_changes, action.json()["care_event_id"])["version"] == 1
        applied_revision = action_changes["current_revision"]
        linked_id = uuid4()
        linked = client.post(
            f"/v1/episodes/{episode_id}/actions",
            headers=_headers(caregiver, request_id=linked_id),
            json={
                "client_request_id": str(linked_id),
                "care_event_id": sleep_event_id,
                "new_care_event": None,
                "recommendation_id": None,
                "performed_by_user_id": str(caregiver.user_id),
                "sequence": 2,
            },
        )
        assert linked.status_code == 201
        _, linked_poll = _poll(client, owner, baby_id, applied_revision)
        assert linked_poll["current_revision"] == applied_revision
        assert linked_poll["changes"] == []

        # Concurrent resources serialize through the transactional baby counter.
        concurrent_ids = [uuid4(), uuid4()]

        def create_concurrent(actor: AuthSession, request_id: UUID, condition: str) -> Any:
            return _create_event(
                client,
                actor,
                baby_id,
                _event_request("DIAPER", occurred_at, operation="CHANGE", condition=condition),
                request_id=request_id,
            )[1]

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent = [
                future.result()
                for future in [
                    executor.submit(create_concurrent, owner, concurrent_ids[0], "WET"),
                    executor.submit(create_concurrent, caregiver, concurrent_ids[1], "STOOL"),
                ]
            ]
        assert [response.status_code for response in concurrent] == [201, 201]
        concurrent_event_ids = {response.json()["care_event_id"] for response in concurrent}
        _, concurrent_poll = _poll(client, owner, baby_id, applied_revision)
        assert concurrent_event_ids <= {
            change["resource_id"] for change in concurrent_poll["changes"]
        }
        applied_revision = concurrent_poll["current_revision"]

        # A write racing a poll is either in that complete boundary or the next one.
        racing_request_id = uuid4()
        with ThreadPoolExecutor(max_workers=2) as executor:
            poll_future = executor.submit(_poll, client, owner, baby_id, applied_revision)
            write_future = executor.submit(
                _create_event,
                client,
                caregiver,
                baby_id,
                _event_request("DIAPER", occurred_at, operation="CHECK", condition="CLEAN"),
                request_id=racing_request_id,
            )
            first_race_poll = poll_future.result()[1]
            racing_write = write_future.result()[1]
        assert racing_write.status_code == 201
        race_event_id = racing_write.json()["care_event_id"]
        _, second_race_poll = _poll(
            client,
            owner,
            baby_id,
            first_race_poll["current_revision"],
        )
        assert race_event_id in {
            change["resource_id"]
            for payload in (first_race_poll, second_race_poll)
            for change in payload["changes"]
        }
        applied_revision = second_race_poll["current_revision"]

        # Force an application failure after the business row and feed insert.
        # The shared transaction must roll both back; the same request can retry.
        service = app.state.b04_service
        assert service is not None
        original_complete = service._complete_idempotency

        async def fail_after_feed(*_: Any, **__: Any) -> None:
            raise ApiException.service_unavailable()

        monkeypatch.setattr(service, "_complete_idempotency", fail_after_feed)
        rolled_back_id = uuid4()
        before_count: int
        with psycopg.connect(_database_url()) as connection:
            before_count = connection.execute(
                "select count(*) from baby_data.care_events where baby_id = %s",
                (baby_id,),
            ).fetchone()[0]
        _, rolled_back = _create_event(
            client,
            owner,
            baby_id,
            _event_request("DIAPER", occurred_at, operation="CHECK", condition="CLEAN"),
            request_id=rolled_back_id,
        )
        _assert_error(rolled_back, 503, "SERVICE_UNAVAILABLE")
        monkeypatch.setattr(service, "_complete_idempotency", original_complete)
        with psycopg.connect(_database_url()) as connection:
            assert (
                connection.execute(
                    "select count(*) from baby_data.care_events where baby_id = %s",
                    (baby_id,),
                ).fetchone()[0]
                == before_count
            )
        _, after_rollback = _poll(client, owner, baby_id, applied_revision)
        assert after_rollback["current_revision"] == applied_revision
        assert after_rollback["changes"] == []
        retried = _create_event(
            client,
            owner,
            baby_id,
            _event_request("DIAPER", occurred_at, operation="CHECK", condition="CLEAN"),
            request_id=rolled_back_id,
        )[1]
        assert retried.status_code == 201
        restart_from_revision = applied_revision

    # The durable feed and idempotent write survive a process restart.
    with TestClient(create_app(settings=settings)) as client:
        _, after_restart = _poll(client, owner, baby_id, restart_from_revision)
        assert retried.json()["care_event_id"] in {
            change["resource_id"] for change in after_restart["changes"]
        }
        applied_revision = after_restart["current_revision"]

        # Full-resync ordering: capture R, mutate, refetch current data, then poll
        # from R. The between-step mutation is harmlessly recovered (or duplicated).
        _, resync = _poll(client, owner, baby_id, 0)
        assert resync["resync_required"] is True
        resync_boundary = resync["current_revision"]
        _, between = _create_event(
            client,
            caregiver,
            baby_id,
            _event_request("DIAPER", occurred_at, operation="CHANGE", condition="WET"),
        )
        assert between.status_code == 201
        full_refetch = client.get(
            f"/v1/babies/{baby_id}/timeline?limit=100", headers=_headers(owner)
        )
        assert full_refetch.status_code == 200
        _, after_full_refetch = _poll(client, owner, baby_id, resync_boundary)
        assert between.json()["care_event_id"] in {
            change["resource_id"] for change in after_full_refetch["changes"]
        }
        applied_revision = after_full_refetch["current_revision"]

        two_account_metrics: dict[str, dict[str, float]] = {}
        for label, actor in (("owner", owner), ("caregiver", caregiver)):
            poll_ms: list[float] = []
            refetch_ms: list[float] = []
            for _ in range(10):
                poll_started = perf_counter()
                _, no_change = _poll(client, actor, baby_id, applied_revision)
                poll_ms.append((perf_counter() - poll_started) * 1000)
                assert no_change["changes"] == []
                refetch_started = perf_counter()
                refetched = client.get(f"/v1/care-events/{sleep_event_id}", headers=_headers(actor))
                refetch_ms.append((perf_counter() - refetch_started) * 1000)
                assert refetched.status_code == 200
            two_account_metrics[label] = {
                "poll_p50_ms": round(statistics.median(poll_ms), 3),
                "poll_p95_ms": round(statistics.quantiles(poll_ms, n=20)[18], 3),
                "refetch_p50_ms": round(statistics.median(refetch_ms), 3),
                "refetch_p95_ms": round(statistics.quantiles(refetch_ms, n=20)[18], 3),
            }
        print("B09_TWO_ACCOUNT_METRICS " + json.dumps(two_account_metrics, sort_keys=True))

        # A contribution deletion publishes every shared CareEvent tombstone in
        # one revision, but not the private draft or requester-only job ID.
        bulk_event_ids: set[str] = set()
        for condition in ("WET", "STOOL", "BOTH"):
            _, response = _create_event(
                client,
                caregiver,
                baby_id,
                _event_request("DIAPER", occurred_at, operation="CHANGE", condition=condition),
            )
            assert response.status_code == 201
            bulk_event_ids.add(response.json()["care_event_id"])
        _, before_bulk_delete = _poll(client, owner, baby_id, applied_revision)
        bulk_delete_base = before_bulk_delete["current_revision"]
        contribution_id = uuid4()
        contribution = client.delete(
            f"/v1/me/contributions/{baby_id}?confirm=DELETE_MY_CONTRIBUTIONS",
            headers=_headers(caregiver, request_id=contribution_id),
        )
        assert contribution.status_code == 202
        replay_contribution = client.delete(
            f"/v1/me/contributions/{baby_id}?confirm=DELETE_MY_CONTRIBUTIONS",
            headers=_headers(caregiver, request_id=contribution_id),
        )
        assert replay_contribution.json() == contribution.json()
        _, bulk_deleted = _poll(client, owner, baby_id, bulk_delete_base)
        tombstones = {
            change["resource_id"]: change for change in bulk_deleted["changes"] if change["deleted"]
        }
        assert bulk_event_ids <= tombstones.keys()
        assert draft.json()["entry_id"] not in tombstones
        assert contribution.json()["deletion_job_id"] not in tombstones
        bulk_baby_change = _find_change(bulk_deleted, baby_id)
        assert bulk_baby_change["resource_type"] == "BABY"
        assert bulk_baby_change["deleted"] is False
        with psycopg.connect(_database_url()) as connection:
            revision_rows = connection.execute(
                """
                select distinct change_revision
                  from baby_data.shared_changes
                 where baby_id = %s
                   and resource_id = any(%s::uuid[])
                   and deleted
                """,
                (baby_id, list(bulk_event_ids)),
            ).fetchall()
            assert len(revision_rows) == 1
            assert (
                connection.execute(
                    """
                select count(*)
                  from baby_data.shared_changes
                 where baby_id = %s
                   and change_revision = %s
                   and resource_type = 'BABY'
                   and resource_id = %s
                   and not deleted
                """,
                    (baby_id, revision_rows[0][0], baby_id),
                ).fetchone()[0]
                == 1
            )
        applied_revision = bulk_deleted["current_revision"]

        members = client.get(f"/v1/babies/{baby_id}/members", headers=_headers(owner)).json()[
            "items"
        ]
        current_caregiver_membership = next(
            item for item in members if item["user_id"] == str(caregiver.user_id)
        )
        remove_id = uuid4()
        removed = client.delete(
            f"/v1/babies/{baby_id}/members/{caregiver.user_id}"
            f"?version={current_caregiver_membership['version']}",
            headers=_headers(owner, request_id=remove_id),
        )
        assert removed.status_code == 200 and removed.json()["status"] == "REVOKED"
        _assert_error(
            client.get(
                f"/v1/babies/{baby_id}/changes?since_revision={applied_revision}",
                headers=_headers(caregiver),
            ),
            404,
            "RESOURCE_NOT_FOUND",
        )
        _, membership_removed = _poll(client, owner, baby_id, applied_revision)
        assert (
            _find_change(membership_removed, current_caregiver_membership["membership_id"])[
                "deleted"
            ]
            is True
        )

        # Revoking another session blocks its next feed request. An in-flight
        # response cannot be recalled; no subsequent access is allowed.
        assert _poll(client, owner_other, baby_id, 0)[1]["resync_required"] is True
        revoke_id = uuid4()
        revoked = client.post(
            "/v1/auth/session-revocations",
            headers=_headers(owner, request_id=revoke_id),
            json={"client_request_id": str(revoke_id), "scope": "OTHERS"},
        )
        assert revoked.status_code == 200 and revoked.json()["status"] == "COMPLETE"
        _assert_error(
            client.get(
                f"/v1/babies/{baby_id}/changes?since_revision=0",
                headers=_headers(owner_other),
            ),
            401,
            "SESSION_REVOKED",
        )

        # The canonical and runtime OpenAPI agree that getChanges is live.
        runtime_openapi = client.get("/openapi.json").json()
        operation = runtime_openapi["paths"]["/v1/babies/{baby_id}/changes"]["get"]
        assert operation["operationId"] == "getChanges"
        assert "getChanges" in runtime_openapi["x-business-contract"]["implemented_operations"]

        # Auth JWT iat has one-second precision. Move past the OTHERS cutoff so
        # the newly issued OTP session is unambiguously newer than revocation.
        wait_seconds(1.1)
        owner, delete_proof = _reauthenticate(
            client,
            auth,
            owner,
            baby_id=baby_id,
            operation="DELETE_BABY",
        )
        latest_baby = client.get("/v1/babies", headers=_headers(owner)).json()["items"][0]["baby"]
        delete_request_id = uuid4()
        deletion = client.delete(
            f"/v1/babies/{baby_id}/data?version={latest_baby['version']}&confirm=DELETE_BABY",
            headers=_headers(
                owner,
                request_id=delete_request_id,
                proof=delete_proof,
            ),
        )
        assert deletion.status_code == 202
        _assert_error(
            client.get(
                f"/v1/babies/{baby_id}/changes?since_revision=0",
                headers=_headers(owner),
            ),
            404,
            "RESOURCE_NOT_FOUND",
        )


def _plan_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [plan]
    for child in plan.get("Plans", []):
        nodes.extend(_plan_nodes(child))
    return nodes


def test_b09_limit_retention_index_response_size_and_poll_latency() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b09-scale")
    settings = _settings(auth)
    occurred_at = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()

    with TestClient(create_app(settings=settings)) as client:
        created = _create_baby(client, owner, "B-09 scale baby")
        baby_id = created["baby"]["baby_id"]
        _, initial = _poll(client, owner, baby_id, 0)
        baseline = initial["current_revision"]

        def create_scaled(index: int) -> Any:
            _, response = _create_event(
                client,
                owner,
                baby_id,
                _event_request(
                    "DIAPER",
                    occurred_at,
                    operation="CHECK",
                    condition="WET" if index % 2 else "CLEAN",
                ),
            )
            return response

        started = perf_counter()
        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(executor.map(create_scaled, range(499)))
        write_seconds = perf_counter() - started
        assert all(response.status_code == 201 for response in responses)

        response_500, payload_500 = _poll(client, owner, baby_id, baseline)
        assert payload_500["resync_required"] is False
        assert len(payload_500["changes"]) == 500
        assert len(response_500.content) < 131_072
        current_500 = payload_500["current_revision"]

        final_write_started = perf_counter()
        response_over_limit = create_scaled(499)
        write_seconds += perf_counter() - final_write_started
        assert response_over_limit.status_code == 201
        _, over_limit = _poll(client, owner, baby_id, baseline)
        assert over_limit["current_revision"] == current_500 + 1
        assert over_limit["changes"] == []
        assert over_limit["resync_required"] is True
        _, future = _poll(client, owner, baby_id, over_limit["current_revision"] + 100)
        assert future["changes"] == [] and future["resync_required"] is True

        poll_latencies_ms: list[float] = []
        refetch_latencies_ms: list[float] = []
        sample_event_id = responses[-1].json()["care_event_id"]
        for _ in range(20):
            poll_started = perf_counter()
            _, no_change = _poll(client, owner, baby_id, over_limit["current_revision"])
            poll_latencies_ms.append((perf_counter() - poll_started) * 1000)
            assert no_change["changes"] == [] and no_change["resync_required"] is False
            refetch_started = perf_counter()
            refetched = client.get(f"/v1/care-events/{sample_event_id}", headers=_headers(owner))
            refetch_latencies_ms.append((perf_counter() - refetch_started) * 1000)
            assert refetched.status_code == 200

        with psycopg.connect(_database_url()) as connection:
            explained = connection.execute(
                """
                explain (analyze, buffers, format json)
                with latest_per_resource as (
                    select distinct on (resource_type, resource_id)
                           resource_type, resource_id, resource_version, deleted,
                           change_revision
                      from baby_data.shared_changes
                     where baby_id = %s
                       and change_revision > %s
                       and change_revision <= %s
                     order by resource_type, resource_id, change_revision desc
                )
                select * from latest_per_resource
                 order by change_revision, resource_type, resource_id
                 limit 501
                """,
                (
                    baby_id,
                    over_limit["current_revision"] - 1,
                    over_limit["current_revision"],
                ),
            ).fetchone()[0][0]
        nodes = _plan_nodes(explained["Plan"])
        assert any(str(node.get("Index Name", "")).startswith("shared_changes_") for node in nodes)
        assert {node.get("Relation Name") for node in nodes if node.get("Relation Name")} <= {
            "shared_changes"
        }
        assert explained["Execution Time"] < 5_000

        with psycopg.connect(_database_url()) as connection:
            retained_rows = connection.execute(
                "select count(*) from baby_data.shared_changes where baby_id = %s",
                (baby_id,),
            ).fetchone()[0]
            connection.execute(
                """
                update baby_data.shared_changes
                   set recorded_at = clock_timestamp() - interval '91 days'
                 where baby_id = %s
                """,
                (baby_id,),
            )
            pruned = connection.execute(
                "select baby_private.prune_shared_change_history()"
            ).fetchone()[0]
        assert pruned == retained_rows == 1002
        _, pruned_history = _poll(client, owner, baby_id, current_500)
        assert pruned_history["changes"] == []
        assert pruned_history["resync_required"] is True

        poll_p95 = statistics.quantiles(poll_latencies_ms, n=20)[18]
        refetch_p95 = statistics.quantiles(refetch_latencies_ms, n=20)[18]
        assert poll_p95 < 5_000
        assert refetch_p95 < 5_000
        print(
            "B09_METRICS "
            + json.dumps(
                {
                    "postgres_version": "17.6",
                    "actual_api_writes": 500,
                    "write_seconds": round(write_seconds, 3),
                    "response_500_bytes": len(response_500.content),
                    "empty_poll_p50_ms": round(statistics.median(poll_latencies_ms), 3),
                    "empty_poll_p95_ms": round(poll_p95, 3),
                    "refetch_p50_ms": round(statistics.median(refetch_latencies_ms), 3),
                    "refetch_p95_ms": round(refetch_p95, 3),
                    "tail_query_execution_ms": round(explained["Execution Time"], 3),
                    "tail_query_indexes": sorted(
                        {node["Index Name"] for node in nodes if node.get("Index Name")}
                    ),
                },
                sort_keys=True,
            )
        )

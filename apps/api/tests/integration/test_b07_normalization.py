from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
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
    _required_env,
)

from baby_care_api.core.config import RuntimeEnvironment, Settings
from baby_care_api.main import create_app
from baby_care_api.models.normalization import NormalizedContent
from baby_care_api.services.normalizer import NormalizerRequest, NormalizerResult

pytestmark = pytest.mark.integration


def _settings(auth: LocalAuth, *, enabled: bool = True) -> Settings:
    return Settings(
        environment=RuntimeEnvironment.TEST,
        log_level="CRITICAL",
        database_url=_database_url(),
        supabase_jwt_issuer=_required_env("BABY_CARE_TEST_SUPABASE_ISSUER"),
        supabase_jwt_audience="authenticated",
        supabase_jwt_algorithms="ES256,RS256",
        supabase_jwks_url=_required_env("BABY_CARE_TEST_SUPABASE_JWKS_URL"),
        supabase_url=auth.api_url,
        supabase_publishable_key=auth.anon_key,
        reauthentication_proof_secret="local-integration-proof-secret-at-least-32-bytes",
        external_normalization_enabled=enabled,
        openai_api_key="synthetic-not-a-live-key" if enabled else None,
    )


def _create_baby(client: TestClient, owner: AuthSession, label: str) -> str:
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
    assert response.status_code == 201, response.text
    return str(response.json()["baby"]["baby_id"])


def _add_caregiver(baby_id: str, caregiver: AuthSession) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            insert into baby_data.baby_memberships (
                baby_id, user_id, role, relationship, display_name
            ) values (%s, %s, 'CAREGIVER', 'OTHER', 'B07 synthetic caregiver')
            """,
            (baby_id, caregiver.user_id),
        )


def _create_entry(
    client: TestClient,
    author: AuthSession,
    baby_id: str,
    *,
    input_mode: str,
    raw_text: str | None,
    choices: list[dict[str, Any]],
    supersedes_entry_id: str | None = None,
    base_record_versions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request_id = uuid4()
    response = client.post(
        f"/v1/babies/{baby_id}/care-entries",
        headers=_headers(author, request_id=request_id),
        json={
            "client_request_id": str(request_id),
            "episode_id": None,
            "input_mode": input_mode,
            "raw_text": raw_text,
            "choices": choices,
            "occurred_at": None,
            "time_precision": "UNKNOWN",
            "supersedes_entry_id": supersedes_entry_id,
            "base_record_versions": base_record_versions or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _text_content(raw: str) -> NormalizedContent:
    state_quote = "잠들었어요"
    state_start = raw.index(state_quote)
    return NormalizedContent.model_validate(
        {
            "actions": [
                {
                    "action_ref": "a1",
                    "action_code": "FEEDING",
                    "assertion": "PERFORMED",
                    "performed_by_user_id": None,
                    "occurred_at": None,
                    "relative_time": None,
                    "time_precision": "UNKNOWN",
                    "sequence": 1,
                    "amount": 80,
                    "unit": "ML",
                    "feeding_mode": "FORMULA",
                    "evidence": [
                        {
                            "source": "TEXT",
                            "choice_id": None,
                            "span_start": 0,
                            "span_end": len(raw),
                            "quote": raw,
                        }
                    ],
                }
            ],
            "states": [
                {
                    "state_codes": ["ASLEEP"],
                    "phase": "AFTER",
                    "observed_at": None,
                    "time_precision": "UNKNOWN",
                    "linked_action_refs": ["a1"],
                    "evidence": [
                        {
                            "source": "TEXT",
                            "choice_id": None,
                            "span_start": state_start,
                            "span_end": state_start + len(state_quote),
                            "quote": state_quote,
                        }
                    ],
                }
            ],
            "outcomes": [],
            "caregiver_interpretations": [],
            "unresolved": [
                {
                    "field": "actions.a1.occurred_at",
                    "code": "UNKNOWN_TIME",
                    "message": "수유 시각을 모름으로 확인해 주세요.",
                },
                {
                    "field": "states.0.observed_at",
                    "code": "UNKNOWN_TIME",
                    "message": "관찰 시각을 모름으로 확인해 주세요.",
                },
            ],
        }
    )


class FakeNormalizer:
    def __init__(self, content: NormalizedContent) -> None:
        self.content = content
        self.calls = 0
        self.closed = False

    async def normalize(self, request: NormalizerRequest) -> NormalizerResult:
        self.calls += 1
        assert request.raw_text is not None
        return NormalizerResult.success(self.content)

    async def close(self) -> None:
        self.closed = True


class BlockingNormalizer(FakeNormalizer):
    def __init__(self, content: NormalizedContent) -> None:
        super().__init__(content)
        self.started = threading.Event()
        self.release = threading.Event()

    async def normalize(self, request: NormalizerRequest) -> NormalizerResult:
        self.calls += 1
        self.started.set()
        released = await asyncio.to_thread(self.release.wait, 10)
        assert released
        return NormalizerResult.success(self.content)


def _run_normalization(
    client: TestClient,
    author: AuthSession,
    entry_id: str,
    revision: int,
    *,
    request_id: UUID | None = None,
    run_id: UUID | None = None,
) -> tuple[Any, UUID, UUID]:
    request_id = request_id or uuid4()
    run_id = run_id or uuid4()
    response = client.post(
        f"/v1/care-entries/{entry_id}/normalizations",
        headers=_headers(author, request_id=request_id),
        json={
            "client_request_id": str(request_id),
            "run_id": str(run_id),
            "input_revision": revision,
        },
    )
    return response, request_id, run_id


def _confirm(
    client: TestClient,
    author: AuthSession,
    entry_id: str,
    *,
    content: dict[str, Any],
    mode: str,
    run_id: UUID | None = None,
    request_id: UUID | None = None,
    input_revision: int = 1,
    base_record_versions: list[dict[str, Any]] | None = None,
) -> Any:
    request_id = request_id or uuid4()
    return client.post(
        f"/v1/care-entries/{entry_id}/confirm",
        headers=_headers(author, request_id=request_id),
        json={
            "client_request_id": str(request_id),
            "input_revision": input_revision,
            "run_id": None if run_id is None else str(run_id),
            "normalization_mode": mode,
            "content": content,
            "base_record_versions": base_record_versions or [],
        },
    )


def test_eventless_llm_confirmation_is_atomic_private_then_refetchable() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b07-owner")
    caregiver = auth.create_user("b07-author")
    outsider = auth.create_user("b07-outsider")
    raw = "🍼 분유 80mL 먹였고 아기가 잠들었어요"
    adapter = FakeNormalizer(_text_content(raw))
    app = create_app(settings=_settings(auth), normalizer_adapter=adapter)

    with TestClient(app) as client:
        assert client.get("/v1/capabilities").json()["normalizer_available"] is True
        baby_id = _create_baby(client, owner, "B07 eventless")
        _add_caregiver(baby_id, caregiver)
        initial_feed = client.get(
            f"/v1/babies/{baby_id}/changes?since_revision=0",
            headers=_headers(owner),
        ).json()["current_revision"]
        entry = _create_entry(
            client,
            caregiver,
            baby_id,
            input_mode="TEXT",
            raw_text=raw,
            choices=[],
        )
        entry_id = entry["entry_id"]
        _assert_error(
            client.get(f"/v1/care-entries/{entry_id}", headers=_headers(owner)),
            404,
            "RESOURCE_NOT_FOUND",
        )
        before = client.get(
            f"/v1/babies/{baby_id}/changes?since_revision={initial_feed}",
            headers=_headers(owner),
        ).json()
        assert before["changes"] == []

        normalized, request_id, run_id = _run_normalization(client, caregiver, entry_id, 1)
        assert normalized.status_code == 200, normalized.text
        assert normalized.json()["status"] == "COMPLETE"
        assert normalized.json()["provider_call_executed"] is True
        replay, _, _ = _run_normalization(
            client,
            caregiver,
            entry_id,
            1,
            request_id=request_id,
            run_id=run_id,
        )
        assert replay.json() == normalized.json()
        assert adapter.calls == 1
        recovered_by_run, _, _ = _run_normalization(
            client,
            caregiver,
            entry_id,
            1,
            request_id=uuid4(),
            run_id=run_id,
        )
        assert recovered_by_run.json() == normalized.json()
        assert adapter.calls == 1
        mismatched_replay, _, _ = _run_normalization(
            client,
            caregiver,
            entry_id,
            1,
            request_id=request_id,
            run_id=uuid4(),
        )
        _assert_error(mismatched_replay, 409, "IDEMPOTENCY_KEY_REUSED")
        _assert_error(
            client.get(f"/v1/normalizations/{run_id}", headers=_headers(owner)),
            404,
            "RESOURCE_NOT_FOUND",
        )
        _assert_error(
            client.get(f"/v1/normalizations/{run_id}", headers=_headers(outsider)),
            404,
            "RESOURCE_NOT_FOUND",
        )

        edited = normalized.json()["result"]
        edited["actions"][0]["amount"] = 90
        edited["actions"][0]["evidence"] = [
            {
                "source": "USER_CORRECTION",
                "choice_id": None,
                "span_start": None,
                "span_end": None,
                "quote": "사용자가 90mL로 수정",
            }
        ]
        confirm_id = uuid4()
        confirmed = _confirm(
            client,
            caregiver,
            entry_id,
            content=edited,
            mode="LLM",
            run_id=run_id,
            request_id=confirm_id,
        )
        assert confirmed.status_code == 200, confirmed.text
        resources = confirmed.json()
        assert len(resources["care_event_ids"]) == 1
        assert len(resources["state_observation_ids"]) == 1
        assert resources["action_ids"] == resources["outcome_ids"] == []
        replay_confirm = _confirm(
            client,
            caregiver,
            entry_id,
            content=edited,
            mode="LLM",
            run_id=run_id,
            request_id=confirm_id,
        )
        assert replay_confirm.json() == resources
        _assert_error(
            _confirm(
                client,
                caregiver,
                entry_id,
                content=edited,
                mode="LLM",
                run_id=run_id,
            ),
            409,
            "ALREADY_CONFIRMED",
        )

        event = client.get(
            f"/v1/care-events/{resources['care_event_ids'][0]}",
            headers=_headers(owner),
        )
        assert event.status_code == 200
        assert event.json()["event"]["payload"]["amount_ml"] == 90
        state = client.get(
            f"/v1/state-observations/{resources['state_observation_ids'][0]}",
            headers=_headers(owner),
        )
        assert state.status_code == 200
        assert state.json()["visual_state_code"] == "ASLEEP"
        assert state.json()["visual_mapping_version"] == "care-visual-v1"
        assert state.json()["confirmation_status"] == "USER_CORRECTED"
        assert (
            client.get(f"/v1/care-entries/{entry_id}", headers=_headers(owner)).status_code == 200
        )

        changes = client.get(
            f"/v1/babies/{baby_id}/changes?since_revision={initial_feed}",
            headers=_headers(owner),
        ).json()["changes"]
        assert {item["resource_type"] for item in changes} == {
            "BABY",
            "CARE_EVENT",
            "STATE_OBSERVATION",
        }
        assert raw not in str(changes)

    assert adapter.closed is True
    with psycopg.connect(_database_url()) as connection:
        assert connection.execute(
            "select distinct source from baby_data.label_annotations where entry_id = %s",
            (entry_id,),
        ).fetchall() == [("EDITED",)]


def test_rule_manual_disabled_and_blocked_content_paths() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b07-rules")
    exploding = FakeNormalizer(_text_content("분유 먹였고 잠들었어요"))
    app = create_app(
        settings=_settings(auth, enabled=False),
        normalizer_adapter=exploding,
    )

    choices = [
        {
            "choice_id": "performed",
            "kind": "ACTION",
            "code": "FEEDING",
            "assertion": "PERFORMED",
        },
        {
            "choice_id": "performed-diaper",
            "kind": "ACTION",
            "code": "DIAPER_CHECK",
            "assertion": "PERFORMED",
        },
        {
            "choice_id": "performed-holding",
            "kind": "ACTION",
            "code": "HOLDING",
            "assertion": "PERFORMED",
        },
        {
            "choice_id": "planned",
            "kind": "ACTION",
            "code": "SLEEP_PREPARATION",
            "assertion": "PLANNED",
        },
        {
            "choice_id": "negated",
            "kind": "ACTION",
            "code": "BURPING",
            "assertion": "NEGATED",
        },
        {
            "choice_id": "uncertain",
            "kind": "ACTION",
            "code": "OTHER",
            "assertion": "UNCERTAIN",
        },
    ]
    with TestClient(app) as client:
        capabilities = client.get("/v1/capabilities").json()
        assert capabilities["normalizer_available"] is False
        assert capabilities["normalizer_unavailable_reason"] == "DISABLED"
        baby_id = _create_baby(client, owner, "B07 rules")
        entry = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="CHOICE",
            raw_text=None,
            choices=choices,
        )
        failed, _, _ = _run_normalization(client, owner, entry["entry_id"], 1)
        assert failed.status_code == 200
        assert failed.json()["status"] == "FAILED"
        assert failed.json()["failure"]["code"] == "NORMALIZATION_DISABLED"
        assert failed.json()["provider_call_executed"] is False
        assert exploding.calls == 0

        rule_actions = []
        for sequence, choice in enumerate(choices, start=1):
            rule_actions.append(
                {
                    "action_ref": f"a{sequence}",
                    "action_code": choice["code"],
                    "assertion": choice["assertion"],
                    "performed_by_user_id": None,
                    "occurred_at": None,
                    "relative_time": None,
                    "time_precision": "UNKNOWN",
                    "sequence": sequence,
                    "amount": None,
                    "unit": None,
                    "feeding_mode": "UNSPECIFIED" if choice["code"] == "FEEDING" else None,
                    "evidence": [
                        {
                            "source": "CHOICE",
                            "choice_id": choice["choice_id"],
                            "span_start": None,
                            "span_end": None,
                            "quote": None,
                        }
                    ],
                }
            )
        rule_content = {
            "actions": rule_actions,
            "states": [],
            "outcomes": [],
            "caregiver_interpretations": [],
            "unresolved": [
                {
                    "field": "actions.a1.occurred_at",
                    "code": "UNKNOWN_TIME",
                    "message": "시간 모름",
                },
                {
                    "field": "actions.a1.amount",
                    "code": "UNKNOWN_VALUE",
                    "message": "수유량 모름",
                },
            ],
        }
        confirmed = _confirm(
            client,
            owner,
            entry["entry_id"],
            content=rule_content,
            mode="RULE",
        )
        assert confirmed.status_code == 200, confirmed.text
        assert len(confirmed.json()["care_event_ids"]) == 3
        assert len(confirmed.json()["label_annotation_ids"]) == 8
        preserved = client.get(
            f"/v1/care-entries/{entry['entry_id']}", headers=_headers(owner)
        ).json()
        assert preserved["status"] == "CONFIRMED"
        assert exploding.calls == 0

        raw = "아마 달랬고 지금은 괜찮은 것 같아요"
        mixed = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="MIXED",
            raw_text=raw,
            choices=[
                {
                    "choice_id": "calm",
                    "kind": "STATE",
                    "code": "CALM",
                    "assertion": None,
                }
            ],
        )
        invalid_content = {
            "actions": [],
            "states": [],
            "outcomes": [
                {
                    "response_code": "CALMED",
                    "observed_at": None,
                    "time_precision": "UNKNOWN",
                    "linked_action_refs": [],
                    "attribution": "UNKNOWN",
                    "evidence": [
                        {
                            "source": "TEXT",
                            "choice_id": None,
                            "span_start": 0,
                            "span_end": len(raw),
                            "quote": raw,
                        }
                    ],
                }
            ],
            "caregiver_interpretations": [],
            "unresolved": [],
        }
        blocked_id = uuid4()
        blocked = _confirm(
            client,
            owner,
            mixed["entry_id"],
            content=invalid_content,
            mode="MANUAL",
            request_id=blocked_id,
        )
        _assert_error(blocked, 422, "VALIDATION_ERROR")
        assert {item["code"] for item in blocked.json()["field_errors"]} == {"EVENT_REQUIRED"}

        manual_content = {
            "actions": [],
            "states": [
                {
                    "state_codes": ["CALM"],
                    "phase": "UNRELATED",
                    "observed_at": None,
                    "time_precision": "UNKNOWN",
                    "linked_action_refs": [],
                    "evidence": [
                        {
                            "source": "USER_CORRECTION",
                            "choice_id": None,
                            "span_start": None,
                            "span_end": None,
                            "quote": "보호자가 차분함을 확인",
                        }
                    ],
                }
            ],
            "outcomes": [],
            "caregiver_interpretations": [],
            "unresolved": [
                {
                    "field": "states.0.observed_at",
                    "code": "UNKNOWN_TIME",
                    "message": "시간 모름",
                }
            ],
        }
        recovered = _confirm(
            client,
            owner,
            mixed["entry_id"],
            content=manual_content,
            mode="MANUAL",
            request_id=blocked_id,
        )
        assert recovered.status_code == 200, recovered.text
        state = client.get(
            f"/v1/state-observations/{recovered.json()['state_observation_ids'][0]}",
            headers=_headers(owner),
        ).json()
        assert state["visual_state_code"] == "CALM"
        assert state["confirmation_status"] == "USER_CORRECTED"


def test_stale_recovery_concurrency_and_confirmation_rollback() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b07-race")
    raw = "🍼 분유 80mL 먹였고 아기가 잠들었어요"
    adapter = BlockingNormalizer(_text_content(raw))
    app = create_app(settings=_settings(auth), normalizer_adapter=adapter)

    with TestClient(app) as client:
        baby_id = _create_baby(client, owner, "B07 race")
        entry = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="TEXT",
            raw_text=raw,
            choices=[],
        )
        request_id, run_id = uuid4(), uuid4()
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                _run_normalization,
                client,
                owner,
                entry["entry_id"],
                1,
                request_id=request_id,
                run_id=run_id,
            )
            assert adapter.started.wait(5)
            replay, _, _ = _run_normalization(
                client,
                owner,
                entry["entry_id"],
                1,
                request_id=request_id,
                run_id=run_id,
            )
            assert replay.json()["status"] == "RUNNING"
            competing, _, _ = _run_normalization(
                client,
                owner,
                entry["entry_id"],
                1,
            )
            _assert_error(competing, 409, "NORMALIZATION_IN_PROGRESS")
            assert competing.json()["details"]["existing_run_id"] == str(run_id)
            patch_id = uuid4()
            patched = client.patch(
                f"/v1/care-entries/{entry['entry_id']}",
                headers=_headers(owner, request_id=patch_id),
                json={
                    "client_request_id": str(patch_id),
                    "input_revision": 1,
                    "input_mode": "TEXT",
                    "raw_text": raw + " 수정",
                    "choices": [],
                    "occurred_at": None,
                    "time_precision": "UNKNOWN",
                },
            )
            assert patched.status_code == 200
            adapter.release.set()
            stale = pending.result(timeout=10)[0]
        assert stale.status_code == 200
        assert stale.json()["status"] == "STALE"
        assert adapter.calls == 1

        atomic_entry = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="TEXT",
            raw_text=raw,
            choices=[],
        )
        checkpoint_calls = 0

        def fail_checkpoint() -> None:
            nonlocal checkpoint_calls
            checkpoint_calls += 1
            raise RuntimeError("synthetic confirmation checkpoint")

        app.state.b07_service._confirmation_checkpoint = fail_checkpoint
        confirmation_id = uuid4()
        failed = _confirm(
            client,
            owner,
            atomic_entry["entry_id"],
            content=_text_content(raw).model_dump(mode="json"),
            mode="MANUAL",
            request_id=confirmation_id,
        )
        assert failed.status_code == 500
        with psycopg.connect(_database_url()) as connection:
            assert connection.execute(
                "select count(*) from baby_data.care_events where source_entry_id = %s",
                (atomic_entry["entry_id"],),
            ).fetchone() == (0,)
            assert connection.execute(
                "select count(*) from baby_data.state_observations where source_entry_id = %s",
                (atomic_entry["entry_id"],),
            ).fetchone() == (0,)
            assert connection.execute(
                "select count(*) from baby_data.label_annotations where entry_id = %s",
                (atomic_entry["entry_id"],),
            ).fetchone() == (0,)
        app.state.b07_service._confirmation_checkpoint = None
        recovered = _confirm(
            client,
            owner,
            atomic_entry["entry_id"],
            content=_text_content(raw).model_dump(mode="json"),
            mode="MANUAL",
            request_id=confirmation_id,
        )
        assert recovered.status_code == 200, recovered.text
        assert checkpoint_calls == 1

        concurrent_entry = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="TEXT",
            raw_text=raw,
            choices=[],
        )

        def confirm_once() -> Any:
            return _confirm(
                client,
                owner,
                concurrent_entry["entry_id"],
                content=_text_content(raw).model_dump(mode="json"),
                mode="MANUAL",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = [
                future.result() for future in [executor.submit(confirm_once) for _ in range(2)]
            ]
        assert sorted(response.status_code for response in responses) == [200, 409]
        with psycopg.connect(_database_url()) as connection:
            assert connection.execute(
                "select count(*) from baby_data.care_events where source_entry_id = %s",
                (concurrent_entry["entry_id"],),
            ).fetchone() == (1,)


def test_correction_updates_existing_resources_and_preserves_lineage() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b07-correction-owner")
    caregiver = auth.create_user("b07-correction-author")
    raw = "🍼 분유 80mL 먹였고 아기가 잠들었어요"
    app = create_app(settings=_settings(auth, enabled=False))

    with TestClient(app) as client:
        baby_id = _create_baby(client, owner, "B07 correction")
        _add_caregiver(baby_id, caregiver)
        original = _create_entry(
            client,
            caregiver,
            baby_id,
            input_mode="TEXT",
            raw_text=raw,
            choices=[],
        )
        first = _confirm(
            client,
            caregiver,
            original["entry_id"],
            content=_text_content(raw).model_dump(mode="json"),
            mode="MANUAL",
        )
        assert first.status_code == 200, first.text
        first_resources = first.json()
        event_id = first_resources["care_event_ids"][0]
        state_id = first_resources["state_observation_ids"][0]
        event_before = client.get(f"/v1/care-events/{event_id}", headers=_headers(owner)).json()
        state_before = client.get(
            f"/v1/state-observations/{state_id}", headers=_headers(owner)
        ).json()
        feed_revision = client.get(
            f"/v1/babies/{baby_id}/changes?since_revision=0",
            headers=_headers(owner),
        ).json()["current_revision"]
        base_versions = [
            {
                "resource_type": "CARE_EVENT",
                "resource_id": event_id,
                "version": event_before["version"],
            },
            {
                "resource_type": "STATE_OBSERVATION",
                "resource_id": state_id,
                "version": state_before["version"],
            },
        ]

        correction = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="TEXT",
            raw_text="분유 100mL, 지금은 깨어 있어요",
            choices=[],
            supersedes_entry_id=original["entry_id"],
            base_record_versions=base_versions,
        )
        edited = _text_content(raw).model_dump(mode="json")
        edited["actions"][0]["amount"] = 100
        edited["actions"][0]["evidence"] = [
            {
                "source": "USER_CORRECTION",
                "choice_id": None,
                "span_start": None,
                "span_end": None,
                "quote": "보호자가 100mL로 수정",
            }
        ]
        edited["states"][0]["state_codes"] = ["AWAKE"]
        edited["states"][0]["evidence"] = [
            {
                "source": "USER_CORRECTION",
                "choice_id": None,
                "span_start": None,
                "span_end": None,
                "quote": "보호자가 깨어 있음으로 수정",
            }
        ]
        corrected = _confirm(
            client,
            owner,
            correction["entry_id"],
            content=edited,
            mode="MANUAL",
            base_record_versions=base_versions,
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["care_event_ids"] == [event_id]
        assert corrected.json()["state_observation_ids"] == [state_id]

        event_after = client.get(f"/v1/care-events/{event_id}", headers=_headers(caregiver)).json()
        state_after = client.get(
            f"/v1/state-observations/{state_id}", headers=_headers(caregiver)
        ).json()
        assert event_after["version"] == event_before["version"] + 1
        assert event_after["event"]["payload"]["amount_ml"] == 100
        assert event_after["created_by_user_id"] == str(caregiver.user_id)
        assert event_after["updated_by_user_id"] == str(owner.user_id)
        assert state_after["version"] == state_before["version"] + 1
        assert state_after["visual_state_code"] == "AWAKE"
        assert state_after["created_by_user_id"] == str(caregiver.user_id)
        assert state_after["updated_by_user_id"] == str(owner.user_id)
        assert state_after["confirmed_by_user_id"] == str(caregiver.user_id)

        saved_correction = client.get(
            f"/v1/care-entries/{correction['entry_id']}", headers=_headers(caregiver)
        ).json()
        assert saved_correction["original_author_user_id"] == str(caregiver.user_id)
        assert saved_correction["confirmed_by_user_id"] == str(owner.user_id)
        changes = client.get(
            f"/v1/babies/{baby_id}/changes?since_revision={feed_revision}",
            headers=_headers(caregiver),
        ).json()["changes"]
        assert {item["resource_type"] for item in changes} == {
            "BABY",
            "CARE_EVENT",
            "STATE_OBSERVATION",
        }

        stale_correction = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="TEXT",
            raw_text="예전 버전으로 다시 수정",
            choices=[],
            supersedes_entry_id=correction["entry_id"],
            base_record_versions=base_versions,
        )
        _assert_error(
            _confirm(
                client,
                owner,
                stale_correction["entry_id"],
                content=edited,
                mode="MANUAL",
                base_record_versions=base_versions,
            ),
            409,
            "VERSION_CONFLICT",
        )

    with psycopg.connect(_database_url()) as connection:
        label_statuses = connection.execute(
            """
            select entry_id::text, status, count(*)
              from baby_data.label_annotations
             where entry_id in (%s, %s)
             group by entry_id, status
             order by entry_id::text, status
            """,
            (original["entry_id"], correction["entry_id"]),
        ).fetchall()
    assert (original["entry_id"], "SUPERSEDED", 4) in label_statuses
    assert (correction["entry_id"], "ACTIVE", 4) in label_statuses


def test_running_normalization_is_fenced_by_manual_confirm_delete_and_revocation() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b07-fence-owner")
    caregiver = auth.create_user("b07-fence-caregiver")
    raw = "🍼 분유 80mL 먹였고 아기가 잠들었어요"

    manual_adapter = BlockingNormalizer(_text_content(raw))
    with TestClient(
        create_app(settings=_settings(auth), normalizer_adapter=manual_adapter)
    ) as client:
        baby_id = _create_baby(client, owner, "B07 completion fences")
        _add_caregiver(baby_id, caregiver)
        manual_entry = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="TEXT",
            raw_text=raw,
            choices=[],
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                _run_normalization, client, owner, manual_entry["entry_id"], 1
            )
            assert manual_adapter.started.wait(5)
            confirmed = _confirm(
                client,
                owner,
                manual_entry["entry_id"],
                content=_text_content(raw).model_dump(mode="json"),
                mode="MANUAL",
            )
            assert confirmed.status_code == 200, confirmed.text
            manual_adapter.release.set()
            late = pending.result(timeout=10)[0]
        assert late.status_code == 200
        assert late.json()["status"] == "STALE"

    delete_adapter = BlockingNormalizer(_text_content(raw))
    with TestClient(
        create_app(settings=_settings(auth), normalizer_adapter=delete_adapter)
    ) as client:
        deleting_entry = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="TEXT",
            raw_text=raw,
            choices=[],
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                _run_normalization, client, owner, deleting_entry["entry_id"], 1
            )
            assert delete_adapter.started.wait(5)
            current = client.get(
                f"/v1/care-entries/{deleting_entry['entry_id']}", headers=_headers(owner)
            ).json()
            delete_id = uuid4()
            deleted = client.delete(
                f"/v1/care-entries/{deleting_entry['entry_id']}?version={current['version']}",
                headers=_headers(owner, request_id=delete_id),
            )
            assert deleted.status_code == 202, deleted.text
            delete_adapter.release.set()
            late = pending.result(timeout=10)[0]
        assert late.status_code == 200
        assert late.json()["status"] == "STALE"
        with psycopg.connect(_database_url()) as connection:
            assert connection.execute(
                "select count(*) from baby_data.care_events where source_entry_id = %s",
                (deleting_entry["entry_id"],),
            ).fetchone() == (0,)

    revoke_adapter = BlockingNormalizer(_text_content(raw))
    with TestClient(
        create_app(settings=_settings(auth), normalizer_adapter=revoke_adapter)
    ) as client:
        revoked_entry = _create_entry(
            client,
            caregiver,
            baby_id,
            input_mode="TEXT",
            raw_text=raw,
            choices=[],
        )
        run_id = uuid4()
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                _run_normalization,
                client,
                caregiver,
                revoked_entry["entry_id"],
                1,
                run_id=run_id,
            )
            assert revoke_adapter.started.wait(5)
            remove_id = uuid4()
            removed = client.delete(
                f"/v1/babies/{baby_id}/members/{caregiver.user_id}?version=1",
                headers=_headers(owner, request_id=remove_id),
            )
            assert removed.status_code == 200, removed.text
            revoke_adapter.release.set()
            late = pending.result(timeout=10)[0]
        _assert_error(late, 404, "RESOURCE_NOT_FOUND")

    with psycopg.connect(_database_url()) as connection:
        assert connection.execute(
            """
            select status::text, failure->>'code'
              from baby_data.normalization_runs where run_id = %s
            """,
            (run_id,),
        ).fetchone() == ("FAILED", "ACCESS_REVOKED")
        assert connection.execute(
            "select status::text, raw_text from baby_data.raw_care_entries where entry_id = %s",
            (revoked_entry["entry_id"],),
        ).fetchone() == ("NEEDS_MANUAL_REVIEW", raw)


def test_expired_lease_is_recovered_on_process_startup() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b07-lease-owner")
    disabled_settings = _settings(auth, enabled=False)
    with TestClient(create_app(settings=disabled_settings)) as client:
        baby_id = _create_baby(client, owner, "B07 lease recovery")
        entry = _create_entry(
            client,
            owner,
            baby_id,
            input_mode="TEXT",
            raw_text="오래 걸린 정규화 원문",
            choices=[],
        )

    run_id = uuid4()
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            insert into baby_data.normalization_runs (
                run_id, baby_id, entry_id, input_revision, status,
                execution_token, lease_expires_at, execution_mode,
                provider_call_executed, provider, model,
                prompt_version, schema_version, ontology_version
            ) values (
                %s, %s, %s, 1, 'RUNNING', %s,
                clock_timestamp() - interval '1 second', 'REAL',
                false, 'openai', 'gpt-5.6-terra',
                'normalization.2026-09-20.v1',
                'openapi-1.2.0.NormalizedContent', 'care-v1'
            )
            """,
            (run_id, baby_id, entry["entry_id"], uuid4()),
        )
        connection.execute(
            """
            update baby_data.raw_care_entries
               set status = 'NORMALIZING', version = version + 1
             where entry_id = %s
            """,
            (entry["entry_id"],),
        )

    with TestClient(create_app(settings=disabled_settings)) as restarted:
        recovered = restarted.get(f"/v1/normalizations/{run_id}", headers=_headers(owner))
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["status"] == "FAILED"
        assert recovered.json()["failure"]["code"] == "NORMALIZATION_LEASE_EXPIRED"
        assert recovered.json()["provider_call_executed"] is False
        saved_entry = restarted.get(
            f"/v1/care-entries/{entry['entry_id']}", headers=_headers(owner)
        ).json()
        assert saved_entry["status"] == "NEEDS_MANUAL_REVIEW"
        assert saved_entry["raw_text"] == "오래 걸린 정규화 원문"

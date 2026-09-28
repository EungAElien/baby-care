from __future__ import annotations

import hashlib
import threading
import time
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_b04_api import AuthSession, LocalAuth, _assert_error, _database_url, _headers
from test_b05_audio_api import (
    SyntheticPcmDecoder,
    _create_episode,
    _create_upload,
    _settings,
    _setup_baby,
    _upload_standard,
    _wav_bytes,
)

from baby_care_api.main import create_app
from baby_care_api.models.audio import AudioCandidate, RecommendedAction, SourceRef
from baby_care_api.services.analysis import (
    AnalysisDecision,
    AnalysisPolicyInfo,
    RecommendationDraft,
)
from baby_care_api.services.model_runtime import ModelRuntimeManager

pytestmark = pytest.mark.integration


@dataclass
class RuntimeStep:
    result: object
    release: threading.Event | None = None


class ScriptedRuntime:
    def __init__(self) -> None:
        self.steps: deque[RuntimeStep] = deque()
        self.started = threading.Event()
        self.completed = threading.Event()
        self.paths: list[Path] = []
        self.pcm_shapes: list[tuple[int, int, int]] = []
        self.closed = False
        self._lock = threading.Lock()

    def infer_audio(self, path: Path) -> object:
        with self._lock:
            step = self.steps.popleft()
            self.paths.append(path)
        with wave.open(str(path), "rb") as audio:
            self.pcm_shapes.append(
                (audio.getframerate(), audio.getnchannels(), audio.getsampwidth())
            )
        self.completed.clear()
        self.started.set()
        try:
            if step.release is not None and not step.release.wait(timeout=5):
                raise TimeoutError("synthetic runtime release was not signalled")
            if isinstance(step.result, Exception):
                raise step.result
            return step.result
        finally:
            self.completed.set()

    def close(self) -> None:
        self.closed = True


class SyntheticAnalysisPolicy:
    @property
    def info(self) -> AnalysisPolicyInfo:
        return AnalysisPolicyInfo(
            product_ready=True,
            inference_mode="STUB",
            model_version="b06-synthetic-model-v1",
            preprocess_version="b06-synthetic-preprocess-v1",
            label_mapping_version="b06-synthetic-labels-v1",
            supported_labels=("HUNGER", "DISCOMFORT"),
            unavailable_reason=None,
        )

    def decide(self, runtime_result: object) -> AnalysisDecision:
        assert isinstance(runtime_result, AnalysisDecision)
        return runtime_result


def _manager(auth: LocalAuth, runtime: ScriptedRuntime) -> ModelRuntimeManager:
    settings = _settings(auth)
    return ModelRuntimeManager(
        enabled=True,
        configured=True,
        settings=settings,
        factory=lambda _: runtime,
    )


def _complete_decision(audio_id: UUID) -> AnalysisDecision:
    return AnalysisDecision(
        status="COMPLETE",
        quality_status="PASS",
        quality_reasons=(),
        cry_detected=True,
        candidates=(AudioCandidate(code="HUNGER", label="배고픔", rank=1),),
        abstain_reason=None,
        recommendation=RecommendationDraft(
            actions=(
                RecommendedAction(
                    action_type="FEEDING",
                    text="수유 필요 여부를 확인해 주세요.",
                    evidence_refs=(SourceRef(kind="AUDIO", resource_id=audio_id, version=None),),
                ),
            ),
            policy_version="b06-synthetic-policy-v1",
        ),
    )


def _abstain_decision() -> AnalysisDecision:
    return AnalysisDecision(
        status="ABSTAIN",
        quality_status="PASS",
        quality_reasons=(),
        cry_detected=True,
        candidates=(),
        abstain_reason="LOW_CONFIDENCE",
        recommendation=None,
    )


def _ready_audio(
    client: TestClient,
    auth: LocalAuth,
    owner: AuthSession,
    baby_id: UUID,
    *,
    payload: bytes | None = None,
) -> tuple[str, UUID, list[str]]:
    audio_payload = payload or _wav_bytes(seconds=2)
    episode_id = _create_episode(client, owner, baby_id)
    upload, _ = _create_upload(client, owner, episode_id, audio_payload)
    grant = upload["upload"]
    assert isinstance(grant, dict)
    assert _upload_standard(
        auth,
        owner,
        str(grant["upload_endpoint"]),
        audio_payload,
        "audio/wav",
    ) in {200, 201}
    request_id = uuid4()
    response = client.post(
        f"/v1/uploads/{grant['upload_id']}/complete",
        headers=_headers(owner, request_id=request_id),
        json={
            "client_request_id": str(request_id),
            "checksum_sha256": hashlib.sha256(audio_payload).hexdigest(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "READY"
    return (
        episode_id,
        UUID(response.json()["audio_id"]),
        response.json()["quality_reasons"],
    )


def _create_analysis(
    client: TestClient,
    owner: AuthSession,
    episode_id: str,
    audio_id: UUID,
    *,
    analysis_id: UUID | None = None,
    request_id: UUID | None = None,
) -> tuple[UUID, UUID, object]:
    active_analysis_id = analysis_id or uuid4()
    active_request_id = request_id or uuid4()
    response = client.post(
        f"/v1/episodes/{episode_id}/analyses",
        headers=_headers(owner, request_id=active_request_id),
        json={
            "client_request_id": str(active_request_id),
            "analysis_id": str(active_analysis_id),
            "audio_id": str(audio_id),
        },
    )
    return active_analysis_id, active_request_id, response


def _wait_until_removed(path: Path) -> None:
    for _ in range(100):
        if not path.exists():
            return
        time.sleep(0.01)
    pytest.fail(f"temporary analysis directory was not removed: {path}")


def test_b06_real_v1_is_product_gated_before_an_analysis_row_is_created() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b06-gate")
    baby_id = _setup_baby(owner)
    runtime = ScriptedRuntime()
    app = create_app(
        settings=_settings(auth),
        audio_decoder=SyntheticPcmDecoder(),
        model_runtime_manager=_manager(auth, runtime),
    )

    with TestClient(app) as client:
        episode_id, audio_id, _ = _ready_audio(client, auth, owner, baby_id)
        capabilities = client.get("/v1/capabilities", headers=_headers(owner))
        assert capabilities.status_code == 200
        assert capabilities.json()["audio_model"] == {
            "available": False,
            "model_version": "m2d-supervised-v1.0.0-final-B",
            "preprocess_version": "m2d-logmel-v1.0.0",
            "label_mapping_version": "donate-cause-labels-v1.0.0",
            "supported_labels": [],
            "inference_mode": "REAL",
        }
        analysis_id, _, response = _create_analysis(client, owner, episode_id, audio_id)
        _assert_error(response, 503, "MODEL_NOT_READY")

    with psycopg.connect(_database_url()) as connection:
        stored = connection.execute(
            "select count(*) from baby_data.analyses where analysis_id = %s",
            (analysis_id,),
        ).fetchone()
    assert stored == (0,)
    assert runtime.paths == []


def test_b06_complete_abstain_idempotency_get_and_change_feed() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b06-results")
    baby_id = _setup_baby(owner)
    runtime = ScriptedRuntime()
    app = create_app(
        settings=_settings(auth),
        audio_decoder=SyntheticPcmDecoder(),
        model_runtime_manager=_manager(auth, runtime),
        analysis_policy=SyntheticAnalysisPolicy(),
    )

    with TestClient(app) as client:
        clipped = _wav_bytes(seconds=2, sample=b"\xff\x7f")
        episode_id, audio_id, source_reasons = _ready_audio(
            client, auth, owner, baby_id, payload=clipped
        )
        assert source_reasons == ["CLIPPING"]
        baseline = client.get(
            f"/v1/babies/{baby_id}/changes?since_revision=0",
            headers=_headers(owner),
        )
        assert baseline.status_code == 200
        baseline_revision = baseline.json()["current_revision"]
        runtime.steps.append(RuntimeStep(_complete_decision(audio_id)))
        analysis_id, request_id, completed = _create_analysis(client, owner, episode_id, audio_id)
        assert completed.status_code == 200, completed.text
        payload = completed.json()
        assert payload["status"] == "COMPLETE"
        assert payload["stage"] == "FINISHED"
        assert payload["attempt_no"] == 1
        assert payload["quality_reasons"] == ["CLIPPING"]
        assert payload["audio_candidates"] == [{"code": "HUNGER", "label": "배고픔", "rank": 1}]
        assert payload["recommendation"]["actions"][0]["evidence_refs"] == [
            {"kind": "AUDIO", "resource_id": str(audio_id), "version": None}
        ]
        assert payload["inference_mode"] == "STUB"
        assert payload["inference_executed"] is False
        assert runtime.pcm_shapes == [(8_000, 1, 2)]

        replay = _create_analysis(
            client,
            owner,
            episode_id,
            audio_id,
            analysis_id=analysis_id,
            request_id=request_id,
        )[2]
        assert replay.status_code == 200
        assert replay.json() == payload
        assert len(runtime.paths) == 1

        terminal_replay = _create_analysis(
            client,
            owner,
            episode_id,
            audio_id,
            analysis_id=analysis_id,
            request_id=uuid4(),
        )[2]
        assert terminal_replay.status_code == 200
        assert terminal_replay.json() == payload
        conflicting = _create_analysis(
            client,
            owner,
            episode_id,
            audio_id,
            analysis_id=uuid4(),
            request_id=uuid4(),
        )[2]
        _assert_error(conflicting, 409, "IDEMPOTENCY_KEY_REUSED")

        fetched = client.get(f"/v1/analyses/{analysis_id}", headers=_headers(owner))
        assert fetched.status_code == 200 and fetched.json() == payload
        changes = client.get(
            f"/v1/babies/{baby_id}/changes?since_revision={baseline_revision}",
            headers=_headers(owner),
        )
        assert changes.status_code == 200
        assert any(
            item["resource_type"] == "ANALYSIS"
            and item["resource_id"] == str(analysis_id)
            and item["version"] == 3
            for item in changes.json()["changes"]
        )

        abstain_episode, abstain_audio, _ = _ready_audio(client, auth, owner, baby_id)
        runtime.steps.append(RuntimeStep(_abstain_decision()))
        _, _, abstained = _create_analysis(client, owner, abstain_episode, abstain_audio)
        assert abstained.status_code == 200
        assert abstained.json()["status"] == "ABSTAIN"
        assert abstained.json()["abstain_reason"] == "LOW_CONFIDENCE"
        assert abstained.json()["audio_candidates"] == []
        assert abstained.json()["recommendation"] is None


def test_b06_timeout_keeps_runtime_slot_and_explicit_retry_reuses_analysis_id() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b06-timeout")
    baby_id = _setup_baby(owner)
    runtime = ScriptedRuntime()
    release = threading.Event()
    app = create_app(
        settings=_settings(auth),
        audio_decoder=SyntheticPcmDecoder(),
        model_runtime_manager=_manager(auth, runtime),
        analysis_policy=SyntheticAnalysisPolicy(),
        analysis_timeout_seconds=0.05,
    )

    with TestClient(app) as client:
        episode_id, audio_id, _ = _ready_audio(client, auth, owner, baby_id)
        runtime.steps.extend(
            [
                RuntimeStep(_complete_decision(audio_id), release),
                RuntimeStep(_complete_decision(audio_id)),
            ]
        )
        analysis_id, _, timed_out = _create_analysis(client, owner, episode_id, audio_id)
        assert timed_out.status_code == 200, timed_out.text
        assert timed_out.json()["status"] == "FAILED"
        assert timed_out.json()["failure"]["code"] == "ANALYSIS_TIMEOUT"
        assert runtime.started.is_set()
        temporary_input = runtime.paths[0]
        assert temporary_input.exists()

        release.set()
        assert runtime.completed.wait(timeout=2)
        _wait_until_removed(temporary_input.parent)

        retry_id = uuid4()
        retried = client.post(
            f"/v1/analyses/{analysis_id}/retry",
            headers=_headers(owner, request_id=retry_id),
            json={"client_request_id": str(retry_id), "expected_attempt": 1},
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["analysis_id"] == str(analysis_id)
        assert retried.json()["attempt_no"] == 2
        assert retried.json()["status"] == "COMPLETE"
        assert len(runtime.paths) == 2

        replay = client.post(
            f"/v1/analyses/{analysis_id}/retry",
            headers=_headers(owner, request_id=retry_id),
            json={"client_request_id": str(retry_id), "expected_attempt": 1},
        )
        assert replay.status_code == 200 and replay.json() == retried.json()
        stale_id = uuid4()
        stale = client.post(
            f"/v1/analyses/{analysis_id}/retry",
            headers=_headers(owner, request_id=stale_id),
            json={"client_request_id": str(stale_id), "expected_attempt": 1},
        )
        _assert_error(stale, 409, "INVALID_STATE")

    with psycopg.connect(_database_url()) as connection:
        attempts = connection.execute(
            """
            select attempt_no, status::text, failure->>'code'
              from baby_data.analysis_execution_attempts
             where analysis_id = %s order by attempt_no
            """,
            (analysis_id,),
        ).fetchall()
    assert attempts == [(1, "FAILED", "ANALYSIS_TIMEOUT"), (2, "COMPLETE", None)]


def test_b06_startup_expires_lease_and_late_execution_cannot_overwrite() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b06-lease")
    baby_id = _setup_baby(owner)
    runtime = ScriptedRuntime()
    release = threading.Event()
    runtime.steps.append(RuntimeStep(_abstain_decision(), release))
    settings = _settings(auth)
    app = create_app(
        settings=settings,
        audio_decoder=SyntheticPcmDecoder(),
        model_runtime_manager=_manager(auth, runtime),
        analysis_policy=SyntheticAnalysisPolicy(),
        analysis_timeout_seconds=5,
    )

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        episode_id, audio_id, _ = _ready_audio(client, auth, owner, baby_id)
        analysis_id = uuid4()
        request_id = uuid4()
        future = executor.submit(
            _create_analysis,
            client,
            owner,
            episode_id,
            audio_id,
            analysis_id=analysis_id,
            request_id=request_id,
        )
        assert runtime.started.wait(timeout=2)
        in_progress = _create_analysis(
            client,
            owner,
            episode_id,
            audio_id,
            analysis_id=analysis_id,
            request_id=uuid4(),
        )[2]
        _assert_error(in_progress, 409, "ANALYSIS_IN_PROGRESS")
        with psycopg.connect(_database_url()) as connection:
            connection.execute(
                """
                update baby_data.analyses
                   set lease_expires_at = clock_timestamp() - interval '1 second'
                 where analysis_id = %s
                """,
                (analysis_id,),
            )
            connection.execute(
                """
                update baby_data.analysis_execution_attempts
                   set lease_expires_at = clock_timestamp() - interval '1 second'
                 where analysis_id = %s and attempt_no = 1
                """,
                (analysis_id,),
            )

        recovery_runtime = ScriptedRuntime()
        recovery_app = create_app(
            settings=settings,
            audio_decoder=SyntheticPcmDecoder(),
            model_runtime_manager=_manager(auth, recovery_runtime),
            analysis_policy=SyntheticAnalysisPolicy(),
        )
        with TestClient(recovery_app) as recovery_client:
            recovered = recovery_client.get(f"/v1/analyses/{analysis_id}", headers=_headers(owner))
            assert recovered.status_code == 200
            assert recovered.json()["status"] == "FAILED"
            assert recovered.json()["failure"]["code"] == "ANALYSIS_LEASE_EXPIRED"

            release.set()
            assert runtime.completed.wait(timeout=2)
            original_response = future.result(timeout=2)[2]
            _assert_error(original_response, 404, "RESOURCE_NOT_FOUND")

            replay = _create_analysis(
                recovery_client,
                owner,
                episode_id,
                audio_id,
                analysis_id=analysis_id,
                request_id=request_id,
            )[2]
            assert replay.status_code == 200
            assert replay.json()["status"] == "FAILED"
            assert replay.json()["failure"]["code"] == "ANALYSIS_LEASE_EXPIRED"

        with psycopg.connect(_database_url()) as connection:
            stored = connection.execute(
                """
                select status::text, failure->>'code'
                  from baby_data.analyses where analysis_id = %s
                """,
                (analysis_id,),
            ).fetchone()
        assert stored == ("FAILED", "ANALYSIS_LEASE_EXPIRED")


def test_b06_consent_race_fences_result_and_no_retention_enqueues_cleanup() -> None:
    auth = LocalAuth()
    owner = auth.create_user("b06-consent")
    baby_id = _setup_baby(owner)
    runtime = ScriptedRuntime()
    release = threading.Event()
    runtime.steps.append(RuntimeStep(_abstain_decision(), release))
    app = create_app(
        settings=_settings(auth),
        audio_decoder=SyntheticPcmDecoder(),
        model_runtime_manager=_manager(auth, runtime),
        analysis_policy=SyntheticAnalysisPolicy(),
    )

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        episode_id, audio_id, _ = _ready_audio(client, auth, owner, baby_id)
        analysis_id = uuid4()
        request_id = uuid4()
        future = executor.submit(
            _create_analysis,
            client,
            owner,
            episode_id,
            audio_id,
            analysis_id=analysis_id,
            request_id=request_id,
        )
        assert runtime.started.wait(timeout=2)
        with psycopg.connect(_database_url()) as connection:
            connection.execute(
                """
                insert into baby_data.consents (
                    baby_id, actor_user_id, scope, status, policy_version,
                    revoked_at, supersedes_consent_id, version
                )
                select baby_id, actor_user_id, scope, 'REVOKED',
                       'b06-test-v1', clock_timestamp(), consent_id, version + 1
                  from baby_data.consents
                 where baby_id = %s and actor_user_id = %s
                   and scope = 'SERVICE_PROCESSING'
                 order by version desc limit 1
                """,
                (baby_id, owner.user_id),
            )
        release.set()
        blocked = future.result(timeout=2)[2]
        _assert_error(blocked, 403, "CONSENT_REQUIRED")

    with psycopg.connect(_database_url()) as connection:
        fenced = connection.execute(
            """
            select n.status::text, n.failure->>'code', a.status::text, j.reason
              from baby_data.analyses n
              join baby_data.audio_assets a on a.audio_id = n.audio_id
              join baby_data.audio_cleanup_jobs j on j.audio_id = n.audio_id
             where n.analysis_id = %s
            """,
            (analysis_id,),
        ).fetchone()
    assert fenced == ("FAILED", "SOURCE_DELETED", "DELETING", "CONSENT_REVOKED")

    retained_owner = auth.create_user("b06-no-retention")
    retained_baby = _setup_baby(retained_owner)
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            insert into baby_data.consents (
                baby_id, actor_user_id, scope, status, policy_version,
                revoked_at, supersedes_consent_id, version
            )
            select baby_id, actor_user_id, scope, 'REVOKED',
                   'b06-test-v1', clock_timestamp(), consent_id, version + 1
              from baby_data.consents
             where baby_id = %s and actor_user_id = %s
               and scope = 'AUDIO_RETENTION'
             order by version desc limit 1
            """,
            (retained_baby, retained_owner.user_id),
        )
    cleanup_runtime = ScriptedRuntime()
    cleanup_app = create_app(
        settings=_settings(auth),
        audio_decoder=SyntheticPcmDecoder(),
        model_runtime_manager=_manager(auth, cleanup_runtime),
        analysis_policy=SyntheticAnalysisPolicy(),
    )
    with TestClient(cleanup_app) as client:
        episode_id, audio_id, _ = _ready_audio(client, auth, retained_owner, retained_baby)
        cleanup_runtime.steps.append(RuntimeStep(_abstain_decision()))
        cleanup_analysis_id, _, analyzed = _create_analysis(
            client, retained_owner, episode_id, audio_id
        )
        assert analyzed.status_code == 200
        assert analyzed.json()["status"] == "ABSTAIN"
        retry_id = uuid4()
        retry = client.post(
            f"/v1/analyses/{cleanup_analysis_id}/retry",
            headers=_headers(retained_owner, request_id=retry_id),
            json={"client_request_id": str(retry_id), "expected_attempt": 1},
        )
        _assert_error(retry, 409, "RESOURCE_DELETING")

    with psycopg.connect(_database_url()) as connection:
        cleanup = connection.execute(
            """
            select a.status::text, j.status::text, j.reason
              from baby_data.audio_assets a
              join baby_data.audio_cleanup_jobs j on j.audio_id = a.audio_id
             where a.audio_id = %s
            """,
            (audio_id,),
        ).fetchone()
    assert cleanup == ("DELETING", "PENDING", "NO_RETENTION_ANALYSIS_FINISHED")

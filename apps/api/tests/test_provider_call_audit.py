from __future__ import annotations

from pathlib import Path

from baby_care_api.services.normalizer import ProviderCallRecord
from baby_care_api.services.provider_call_audit import PersistentProviderCallAudit


def test_provider_call_audit_persists_budget_across_instances(tmp_path: Path) -> None:
    state_path = tmp_path / "provider-calls.json"
    first = PersistentProviderCallAudit(
        state_path,
        max_requests=2,
        session_id="session-one",
    )
    first_id = first.reserve(model="gpt-5.6-terra")
    assert first_id is not None
    first.complete(
        first_id,
        ProviderCallRecord(
            outcome="RESPONSE",
            latency_ms=123,
            response_id="resp_synthetic",
            response_model="gpt-5.6-terra",
            response_status="completed",
            input_tokens=100,
            cached_input_tokens=10,
            output_tokens=20,
            total_tokens=120,
        ),
    )

    restarted = PersistentProviderCallAudit(
        state_path,
        max_requests=2,
        session_id="session-two",
    )
    second_id = restarted.reserve(model="gpt-5.6-terra")
    assert second_id is not None
    assert restarted.reserve(model="gpt-5.6-terra") is None

    snapshot = restarted.snapshot()
    assert snapshot["provider_request_count"] == 2
    assert snapshot["max_requests"] == 2
    assert snapshot["events"][0]["record"]["response_id"] == "resp_synthetic"
    serialized = str(snapshot).lower()
    assert "raw_text" not in serialized
    assert "prompt" not in serialized
    assert "output_text" not in serialized
    assert "authorization" not in serialized
    assert state_path.stat().st_mode & 0o777 == 0o600

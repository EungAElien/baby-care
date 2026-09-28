from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from baby_care_api.services.normalizer import ProviderCallRecord

OperationResult = TypeVar("OperationResult")


class PersistentProviderCallAudit:
    """Conservative cross-process request budget and content-free audit ledger."""

    def __init__(
        self,
        state_path: Path,
        *,
        max_requests: int,
        session_id: str | None = None,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be positive")
        self.state_path = state_path.resolve()
        self.lock_path = self.state_path.with_suffix(f"{self.state_path.suffix}.lock")
        self.max_requests = max_requests
        self.session_id = session_id or str(uuid4())
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._mutate(lambda state: state)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat()

    def _initial_state(self) -> dict[str, Any]:
        return {
            "schema_version": "baby-care.provider-call-audit.v1",
            "max_requests": self.max_requests,
            "provider_request_count": 0,
            "events": [],
        }

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._initial_state()
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("provider call audit state is unreadable") from exc
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != "baby-care.provider-call-audit.v1"
            or state.get("max_requests") != self.max_requests
            or not isinstance(state.get("provider_request_count"), int)
            or not isinstance(state.get("events"), list)
        ):
            raise RuntimeError("provider call audit state is incompatible")
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.state_path.parent,
            prefix=f".{self.state_path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.state_path)
            self.state_path.chmod(0o600)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _mutate(self, operation: Callable[[dict[str, Any]], OperationResult]) -> OperationResult:
        self.lock_path.touch(mode=0o600, exist_ok=True)
        self.lock_path.chmod(0o600)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._read_state()
            result = operation(state)
            self._write_state(state)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return result

    def reserve(self, *, model: str) -> str | None:
        def operation(state: dict[str, Any]) -> str | None:
            if state["provider_request_count"] >= self.max_requests:
                return None
            reservation_id = str(uuid4())
            state["provider_request_count"] += 1
            state["events"].append(
                {
                    "reservation_id": reservation_id,
                    "session_id": self.session_id,
                    "model": model,
                    "status": "RESERVED",
                    "reserved_at": self._timestamp(),
                    "completed_at": None,
                    "record": None,
                }
            )
            return reservation_id

        return self._mutate(operation)

    def complete(self, reservation_id: str, record: ProviderCallRecord) -> None:
        def operation(state: dict[str, Any]) -> None:
            for event in state["events"]:
                if event.get("reservation_id") == reservation_id:
                    if event.get("status") != "RESERVED":
                        raise RuntimeError("provider call reservation was already completed")
                    event["status"] = "COMPLETED"
                    event["completed_at"] = self._timestamp()
                    event["record"] = {
                        "outcome": record.outcome,
                        "latency_ms": record.latency_ms,
                        "response_id": record.response_id,
                        "response_model": record.response_model,
                        "response_status": record.response_status,
                        "input_tokens": record.input_tokens,
                        "cached_input_tokens": record.cached_input_tokens,
                        "output_tokens": record.output_tokens,
                        "total_tokens": record.total_tokens,
                        "error_kind": record.error_kind,
                        "validation_error_types": list(record.validation_error_types),
                        "validation_error_locations": list(record.validation_error_locations),
                    }
                    return
            raise RuntimeError("provider call reservation was not found")

        self._mutate(operation)

    def snapshot(self) -> dict[str, Any]:
        return self._mutate(deepcopy)

    def session_events(self) -> list[dict[str, Any]]:
        return [
            event
            for event in self.snapshot()["events"]
            if event.get("session_id") == self.session_id
        ]

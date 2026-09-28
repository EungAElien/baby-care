"""At-most-once sealed evaluation with a durable claim and fail-closed recovery."""

from __future__ import annotations

from pathlib import Path

from .comparison import checked_artifacts, freeze_json
from .util import atomic_json, object_sha256, read_json, sha256_file, utc_now


def evaluate_once(directory: Path, evaluation_plan, item, operation):
    """Caller holds the training lock. Never silently retry an ambiguous interrupted test."""
    directory.mkdir(parents=True, exist_ok=True)
    identity = {"evaluation_plan_sha256": object_sha256(evaluation_plan), "item": item}
    completion = directory / "completion.json"
    if completion.exists():
        report = read_json(completion)
        if report["identity"] != identity:
            raise RuntimeError("Sealed test identity differs.")
        checked_artifacts(directory, report)
        return read_json(directory / "result.json")
    claim = directory / "claim.json"
    if claim.exists():
        previous = read_json(claim)
        if previous["identity"] != identity:
            raise RuntimeError("Sealed test claim differs.")
        # Result was durably saved but completion publication may have been interrupted.
        if not (directory / "result.json").exists():
            raise RuntimeError(
                "AMBIGUOUS_SEALED_TEST: claimed test has no durable result. "
                "Do not repeat inference; manual incident review required."
            )
        result = read_json(directory / "result.json")
        if result.get("evaluation_identity") != identity:
            raise RuntimeError("Orphan test result identity differs.")
    else:
        freeze_json(claim, {"identity": identity, "claimed_utc": utc_now()})
        result = {"evaluation_identity": identity, **operation()}
        atomic_json(directory / "result.json", result)
    atomic_json(
        completion,
        {
            "identity": identity,
            "status": "completed",
            "completed_utc": utc_now(),
            "test_used": True,
            "artifact_sha256": {
                "result.json": sha256_file(directory / "result.json"),
                "claim.json": sha256_file(claim),
            },
        },
    )
    return result

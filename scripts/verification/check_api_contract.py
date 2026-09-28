from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from baby_care_api.main import create_app


def operations(
    document: dict[str, Any], *, prefix: str = ""
) -> dict[str, tuple[str, str]]:
    return {
        operation["operationId"]: (method.lower(), f"{prefix}{path}")
        for path, path_item in document["paths"].items()
        for method, operation in path_item.items()
        if method.lower() in {"get", "put", "post", "delete", "patch"}
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare implemented FastAPI routes and operation IDs with the canonical contract."
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    canonical = json.loads(
        (ROOT / "contracts" / "openapi계약.json").read_text(encoding="utf-8")
    )
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        runtime = client.get("/openapi.json").json()

    canonical_operations = operations(canonical, prefix="/v1")
    runtime_document = {
        **runtime,
        "paths": {
            path: value
            for path, value in runtime["paths"].items()
            if path.startswith("/v1/")
        },
    }
    runtime_operations = operations(runtime_document)
    declared = runtime["x-business-contract"]
    implemented = set(declared["implemented_operations"])

    errors: list[str] = []
    if declared["version"] != canonical["info"]["version"]:
        errors.append("runtime contract version differs from canonical contract")
    if set(runtime_operations) != implemented:
        errors.append("runtime routes and implemented_operations differ")
    for operation_id, location in runtime_operations.items():
        if canonical_operations.get(operation_id) != location:
            errors.append(
                f"{operation_id} method/path differs from the canonical contract"
            )

    summary = {
        "status": "failed" if errors else "passed",
        "contract_version": canonical["info"]["version"],
        "implemented_count": len(runtime_operations),
        "implemented_operations": sorted(runtime_operations),
        "unimplemented_count": len(set(canonical_operations) - set(runtime_operations)),
        "unimplemented_contract_operations": sorted(
            set(canonical_operations) - set(runtime_operations)
        ),
        "errors": errors,
        "boundary": (
            "Unimplemented contract operations remain future scope and are not exposed as FastAPI routes. "
            "This structural comparison is not a live Auth, RLS, model, or browser test."
        ),
    }
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

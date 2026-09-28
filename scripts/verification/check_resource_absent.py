from __future__ import annotations

import argparse
import json
import subprocess


def names(kind: str) -> set[str]:
    completed = subprocess.run(
        ["docker", kind, "ls", "--format", "{{.Name}}"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return set(completed.stdout.splitlines())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that one isolated Supabase project was cleaned."
    )
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()

    suffix = f"_{args.project_id}"
    containers = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    remaining = {
        "containers": sorted(name for name in containers if name.endswith(suffix)),
        "networks": sorted(name for name in names("network") if name.endswith(suffix)),
        "volumes": sorted(name for name in names("volume") if name.endswith(suffix)),
    }
    failed = any(remaining.values())
    print(
        json.dumps(
            {
                "status": "failed" if failed else "passed",
                "project_id": args.project_id,
                "remaining": remaining,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import ast
import difflib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        print(completed.stdout, file=sys.stderr)
        raise SystemExit(completed.returncode)


def constant_from_python(path: Path, name: str) -> Any:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == name for target in targets
            ):
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} was not found in {path}")


def compare(expected: Path, generated: Path) -> tuple[bool, str | None]:
    expected_text = expected.read_text(encoding="utf-8")
    generated_text = generated.read_text(encoding="utf-8")
    if expected_text == generated_text:
        return True, None
    diff = "".join(
        difflib.unified_diff(
            expected_text.splitlines(keepends=True),
            generated_text.splitlines(keepends=True),
            fromfile=str(expected),
            tofile=f"regenerated:{generated.name}",
            n=3,
        )
    )
    return False, diff[:20000]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate contract and web artifacts in a temporary directory and compare them."
    )
    parser.add_argument("--expected-root", type=Path, default=ROOT)
    parser.add_argument("--tool-root", type=Path, default=ROOT)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    expected_root = args.expected_root.resolve()
    tool_root = args.tool_root.resolve()
    web_cli = (
        tool_root / "apps" / "web" / "node_modules" / ".bin" / "openapi-typescript"
    )
    if not web_cli.is_file():
        print(f"Required generated-type tool is missing: {web_cli}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="baby-care-generated-") as directory:
        temporary = Path(directory)
        generated_contracts = temporary / "contracts"
        generated_web = temporary / "web"
        generated_web.mkdir(parents=True)

        run(
            [
                args.python,
                str(tool_root / "contracts" / "build_contract.py"),
                "--output-dir",
                str(generated_contracts),
            ],
            cwd=tool_root,
        )
        run(
            [
                args.python,
                str(tool_root / "contracts" / "validate_contract.py"),
                "--contract-dir",
                str(generated_contracts),
                "--output",
                str(generated_contracts / "validation.json"),
            ],
            cwd=tool_root,
        )
        run(
            [
                str(web_cli),
                str(generated_contracts / "openapi계약.json"),
                "-o",
                str(generated_web / "generated.d.ts"),
            ],
            cwd=tool_root / "apps" / "web",
        )
        run(
            [
                "node",
                str(tool_root / "apps" / "web" / "scripts" / "generate-fixtures.mjs"),
                "--source",
                str(generated_contracts / "목 응답과 시험 사용자 배치.json"),
                "--output",
                str(generated_web / "fixtures.json"),
            ],
            cwd=tool_root / "apps" / "web",
        )

        pairs = [
            (
                expected_root / "contracts" / "openapi계약.json",
                generated_contracts / "openapi계약.json",
            ),
            (
                expected_root / "contracts" / "목 응답과 시험 사용자 배치.json",
                generated_contracts / "목 응답과 시험 사용자 배치.json",
            ),
            (
                expected_root / "contracts" / "validation.json",
                generated_contracts / "validation.json",
            ),
            (
                expected_root
                / "apps"
                / "web"
                / "src"
                / "lib"
                / "api"
                / "generated.d.ts",
                generated_web / "generated.d.ts",
            ),
            (
                expected_root
                / "apps"
                / "web"
                / "src"
                / "lib"
                / "mock"
                / "fixtures.json",
                generated_web / "fixtures.json",
            ),
        ]
        results: list[dict[str, Any]] = []
        failed = False
        for expected, generated in pairs:
            matched, diff = compare(expected, generated)
            results.append(
                {
                    "path": str(expected.relative_to(expected_root)),
                    "matched": matched,
                }
            )
            if not matched:
                failed = True
                print(diff, file=sys.stderr)

        openapi = json.loads(
            (generated_contracts / "openapi계약.json").read_text(encoding="utf-8")
        )
        fixture = json.loads(
            (generated_contracts / "목 응답과 시험 사용자 배치.json").read_text(
                encoding="utf-8"
            )
        )
        api_version = constant_from_python(
            tool_root / "apps" / "api" / "src" / "baby_care_api" / "core" / "config.py",
            "CONTRACT_VERSION",
        )
        versions = {
            "openapi": openapi["info"]["version"],
            "fixture": fixture["contract_version"],
            "api": api_version,
        }
        if len(set(versions.values())) != 1:
            failed = True
            print(f"Contract version references disagree: {versions}", file=sys.stderr)

        summary = {
            "status": "failed" if failed else "passed",
            "comparison_mode": "temporary-regeneration-no-working-tree-write",
            "contract_versions": versions,
            "artifacts": results,
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
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

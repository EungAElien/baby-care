from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

RULES = {
    "synthetic-secret-canary": re.compile(r"BABY_CARE_SECRET_CANARY_[A-Za-z0-9_-]+"),
    "openai-api-key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "supabase-secret-key": re.compile(r"\bsb_secret_[A-Za-z0-9_-]{8,}\b"),
    "bearer-token": re.compile(
        r"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~-]{16,}"
    ),
    "jwt": re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
    ),
    "database-password": re.compile(
        r"postgres(?:ql)?://[^:\s/]+:(?!\[REDACTED[^\]]*\]@)[^@\s]+@"
    ),
    "labeled-secret": re.compile(
        r"(?i)[\"']?(?:jwt_secret|secret_key|service_role_key|database_password|password|access_token|refresh_token)[\"']?\s*[:=]\s*[\"']?(?!\[REDACTED)[^\"',\s}]{8,}"
    ),
    "labeled-otp": re.compile(
        r"(?i)\b(?:otp|verification[_ -]?code)\b\D{0,12}\d{6,8}\b"
    ),
}

TEXT_SUFFIXES = {
    "",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file() and candidate.suffix.lower() in TEXT_SUFFIXES:
                    yield candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when verification logs or artifacts contain secret-shaped values."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    findings: list[dict[str, object]] = []
    for path in files(args.paths):
        try:
            if path.stat().st_size > 20 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule, pattern in RULES.items():
                if pattern.search(line):
                    findings.append(
                        {"path": str(path.resolve()), "line": line_number, "rule": rule}
                    )

    summary = {
        "status": "failed" if findings else "passed",
        "files_scanned": sum(1 for _ in files(args.paths)),
        "finding_count": len(findings),
        "findings": findings,
        "note": "Detected values are intentionally omitted from output.",
    }
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    stream = sys.stderr if findings else sys.stdout
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=stream)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

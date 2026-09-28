from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from common import npx_command


class IsolatedSupabase:
    """Prepare a unique local Supabase project without touching another checkout's stack."""

    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root.resolve()
        self.project_id = f"baby-care-b13-{uuid4().hex[:10]}"
        self._temporary = tempfile.TemporaryDirectory(prefix="baby-care-b13-supabase-")
        self.workdir = Path(self._temporary.name)
        self.db_container = f"supabase_db_{self.project_id}"
        self.ports = {
            "api": self._free_port(),
            "db": self._free_port(),
            "shadow": self._free_port(),
            "pooler": self._free_port(),
            "studio": self._free_port(),
            "mailpit": self._free_port(),
        }
        shutil.copytree(self.source_root / "supabase", self.workdir / "supabase")
        self._write_config()
        self.assert_unused()

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _write_config(self) -> None:
        path = self.workdir / "supabase" / "config.toml"
        config = path.read_text(encoding="utf-8")
        replacements = {
            r'^project_id = ".*"$': f'project_id = "{self.project_id}"',
            r"^port = 54321$": f"port = {self.ports['api']}",
            r"^port = 54322$": f"port = {self.ports['db']}",
            r"^shadow_port = 54320$": f"shadow_port = {self.ports['shadow']}",
            r"^port = 54329$": f"port = {self.ports['pooler']}",
            r"^port = 54323$": f"port = {self.ports['studio']}",
            r"^port = 54324$": f"port = {self.ports['mailpit']}",
        }
        for pattern, replacement in replacements.items():
            config, count = re.subn(
                pattern, replacement, config, count=1, flags=re.MULTILINE
            )
            if count != 1:
                raise RuntimeError(
                    f"Expected one config value for {pattern}, found {count}"
                )
        path.write_text(config, encoding="utf-8", newline="\n")

    def cli(self, *arguments: str) -> list[str]:
        return [npx_command(), "supabase", "--workdir", str(self.workdir), *arguments]

    def assert_unused(self) -> None:
        inspected = subprocess.run(
            ["docker", "inspect", self.db_container],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if inspected.returncode == 0:
            raise RuntimeError(
                f"Refusing to reuse existing local database {self.db_container}"
            )

    def status(self) -> dict[str, str]:
        completed = subprocess.run(
            self.cli("status", "-o", "json"),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise TypeError("Supabase status did not return an object")
        return {str(key): str(item) for key, item in value.items()}

    def environment(self) -> dict[str, str]:
        return {
            "SUPABASE_WORKDIR": str(self.workdir),
            "SUPABASE_DB_CONTAINER": self.db_container,
        }

    def close(self) -> None:
        self._temporary.cleanup()

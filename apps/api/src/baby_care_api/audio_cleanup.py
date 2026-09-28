from __future__ import annotations

import argparse
import asyncio
import json

from baby_care_api.core.config import get_settings
from baby_care_api.services.audio import AudioCleanupWorker
from baby_care_api.services.storage import SupabaseAudioStorage


async def _run(limit: int) -> dict[str, int]:
    settings = get_settings()
    if (
        settings.database_url is None
        or settings.supabase_url is None
        or settings.supabase_secret_key is None
    ):
        raise SystemExit(
            "DATABASE_URL, SUPABASE_URL, and server-only SUPABASE_SECRET_KEY are required"
        )
    storage = SupabaseAudioStorage(
        supabase_url=settings.supabase_url,
        storage_url=settings.supabase_storage_url,
        service_key=settings.supabase_secret_key.get_secret_value(),
    )
    worker = AudioCleanupWorker(
        settings.database_url.get_secret_value(),
        storage=storage,
    )
    await worker.open()
    try:
        return await worker.run_once(limit=limit)
    finally:
        await worker.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one durable B-05 audio cleanup batch.")
    parser.add_argument("--limit", type=int, default=20, choices=range(1, 101))
    args = parser.parse_args()
    result = asyncio.run(_run(args.limit))
    print(json.dumps(result, sort_keys=True))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

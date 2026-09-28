from __future__ import annotations

import uvicorn

from baby_care_api.core.config import get_settings


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "baby_care_api.main:app",
        host=settings.host,
        port=settings.port,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    run()

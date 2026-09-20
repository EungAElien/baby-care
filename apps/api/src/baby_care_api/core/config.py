from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

API_V1_PREFIX = "/v1"
CONTRACT_VERSION = "1.2.0"
SERVICE_VERSION = "0.4.0"


class RuntimeEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Process configuration.

    Secret values use ``SecretStr`` so accidental repr/serialization does not reveal them.
    A configured value does not make a dependency ready; readiness needs a live probe.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_prefix="BABY_CARE_",
        extra="forbid",
        case_sensitive=False,
    )

    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)

    database_url: SecretStr | None = None
    supabase_jwt_issuer: str | None = None
    supabase_jwt_audience: str | None = None
    supabase_jwks_url: str | None = None
    supabase_jwt_algorithms: str = "ES256,RS256"
    supabase_url: str | None = None
    supabase_publishable_key: SecretStr | None = None
    supabase_secret_key: SecretStr | None = None
    supabase_storage_url: str | None = None
    reauthentication_proof_secret: SecretStr | None = None
    invite_base_url: str = "http://127.0.0.1:3000/invite"
    child_data_production_enabled: bool = False
    external_normalization_enabled: bool = False
    audio_ffmpeg_path: Path = Path("/usr/local/bin/ffmpeg")
    audio_ffprobe_path: Path = Path("/usr/local/bin/ffprobe")
    audio_ffmpeg_version_prefix: str = "ffmpeg version 7.1.1"
    audio_decode_concurrency: int = Field(default=2, ge=1, le=8)
    openai_api_key: SecretStr | None = None
    openai_organization: str | None = None
    openai_project: str | None = None
    normalization_timeout_seconds: float = Field(default=20.0, ge=1.0, le=20.0)
    normalization_lease_seconds: int = Field(default=30, ge=30, le=30)

    # The normal API profile stays lightweight. The dedicated V1 B profile enables
    # this flag and supplies read-only, server-owned paths below.
    m2d_enabled: bool = False
    m2d_allowed_root: Path | None = None
    m2d_bundle_path: Path | None = None
    m2d_source_path: Path | None = None
    m2d_ffmpeg_path: Path = Path("/usr/local/bin/ffmpeg")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from baby_care_api.core.config import (
    API_V1_PREFIX,
    CONTRACT_VERSION,
    SERVICE_VERSION,
    Settings,
    get_settings,
)
from baby_care_api.core.errors import install_exception_handlers
from baby_care_api.core.logging import configure_logging
from baby_care_api.core.request_context import install_request_context
from baby_care_api.routes.audio import router as audio_router
from baby_care_api.routes.b04 import router as b04_router
from baby_care_api.routes.health import router as health_router
from baby_care_api.services.audio import PostgresAudioService
from baby_care_api.services.audio_decoder import AudioDecoder, FfmpegAudioDecoder
from baby_care_api.services.auth_provider import SupabaseSessionRevocationProvider
from baby_care_api.services.idempotency import UnconfiguredIdempotencyPort
from baby_care_api.services.postgres import PostgresAuthorizationPort
from baby_care_api.services.readiness import ComponentName, ReadinessProbe, ReadinessService
from baby_care_api.services.security import (
    SupabaseJwtAuthenticationPort,
    UnconfiguredAuthenticationPort,
    UnconfiguredAuthorizationPort,
)
from baby_care_api.services.storage import (
    AudioStoragePort,
    SupabaseAudioStorage,
    UnconfiguredAudioStorage,
)

IMPLEMENTED_OPERATIONS = [
    "acceptInvite",
    "createAction",
    "createBaby",
    "createCareEntry",
    "createCareEvent",
    "createInvite",
    "createReauthenticationChallenge",
    "createReauthenticationProof",
    "deleteBabyData",
    "deleteCareEntry",
    "deleteCareEvent",
    "deleteMyContributions",
    "getActiveBaby",
    "getCareEntry",
    "getCareEvent",
    "getChanges",
    "getChildDataVerification",
    "getDeletion",
    "getSessionRevocation",
    "getTimeline",
    "listBabies",
    "listConsents",
    "listInvites",
    "listMembers",
    "listMyCareEntries",
    "patchBaby",
    "patchCareEntry",
    "patchCareEvent",
    "patchMyRelationship",
    "reissueInvite",
    "removeMembership",
    "retryDeletion",
    "revokeInvite",
    "revokeSessions",
    "setActiveBaby",
    "setBabyConsent",
    "setMyTrainingConsent",
    "cancelUpload",
    "completeUpload",
    "createEpisode",
    "createUpload",
    "getCapabilities",
    "getEpisode",
    "getPlayback",
    "reissueUpload",
]


def _install_openapi(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema["x-business-contract"] = {
            "source": "contracts/openapi계약.json",
            "version": CONTRACT_VERSION,
            "api_prefix": API_V1_PREFIX,
            "implemented_operations": IMPLEMENTED_OPERATIONS,
        }
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def create_app(
    *,
    settings: Settings | None = None,
    readiness_probes: Mapping[ComponentName, ReadinessProbe] | None = None,
    audio_storage: AudioStoragePort | None = None,
    audio_decoder: AudioDecoder | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    configure_logging(active_settings.log_level)
    database_url = (
        None
        if active_settings.database_url is None
        else active_settings.database_url.get_secret_value()
    )
    proof_secret = (
        None
        if active_settings.reauthentication_proof_secret is None
        else active_settings.reauthentication_proof_secret.get_secret_value()
    )
    configured_storage: AudioStoragePort = audio_storage or (
        SupabaseAudioStorage(
            supabase_url=active_settings.supabase_url,
            storage_url=active_settings.supabase_storage_url,
            service_key=active_settings.supabase_secret_key.get_secret_value(),
        )
        if active_settings.supabase_url is not None
        and active_settings.supabase_secret_key is not None
        else UnconfiguredAudioStorage()
    )
    configured_decoder: AudioDecoder = audio_decoder or FfmpegAudioDecoder(
        ffmpeg_path=active_settings.audio_ffmpeg_path,
        ffprobe_path=active_settings.audio_ffprobe_path,
        expected_version_prefix=active_settings.audio_ffmpeg_version_prefix,
        max_concurrency=active_settings.audio_decode_concurrency,
    )
    b04_service = (
        PostgresAudioService(
            database_url,
            invite_base_url=active_settings.invite_base_url,
            proof_secret=proof_secret,
            child_data_production_enabled=active_settings.child_data_production_enabled,
            storage=configured_storage,
            decoder=configured_decoder,
        )
        if database_url is not None and proof_secret is not None
        else None
    )
    database = (
        b04_service
        if b04_service is not None
        else PostgresAuthorizationPort(database_url)
        if database_url is not None
        else None
    )
    authentication = (
        SupabaseJwtAuthenticationPort(
            issuer=active_settings.supabase_jwt_issuer,
            audience=active_settings.supabase_jwt_audience,
            jwks_url=active_settings.supabase_jwks_url,
            algorithms=tuple(
                item.strip()
                for item in active_settings.supabase_jwt_algorithms.split(",")
                if item.strip()
            ),
        )
        if active_settings.supabase_jwt_issuer
        and active_settings.supabase_jwt_audience
        and active_settings.supabase_jwks_url
        else None
    )
    session_revocation = (
        SupabaseSessionRevocationProvider(
            supabase_url=active_settings.supabase_url,
            publishable_key=active_settings.supabase_publishable_key.get_secret_value(),
        )
        if active_settings.supabase_url is not None
        and active_settings.supabase_publishable_key is not None
        else None
    )
    active_readiness_probes = dict(readiness_probes or {})
    if database is not None:
        active_readiness_probes[ComponentName.DATABASE] = database.probe
    if authentication is not None:
        active_readiness_probes[ComponentName.AUTHENTICATION] = authentication.probe

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if database is not None:
            await database.open()
        try:
            yield
        finally:
            if database is not None:
                await database.close()

    app = FastAPI(
        title="Baby Care API",
        version=SERVICE_VERSION,
        description=(
            "B-04 account/shared-care records, B-05 private audio intake, and the B-09 "
            "durable change feed. "
            "The canonical business contract is version 1.1.1 at "
            "contracts/openapi계약.json."
        ),
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.state.readiness = ReadinessService(active_readiness_probes)
    app.state.authentication = authentication or UnconfiguredAuthenticationPort()
    app.state.authorization = database or UnconfiguredAuthorizationPort()
    app.state.b04_service = b04_service
    app.state.audio_service = b04_service
    app.state.session_revocation = session_revocation
    app.state.idempotency = UnconfiguredIdempotencyPort()

    install_exception_handlers(app)
    install_request_context(app)
    app.include_router(health_router)
    app.include_router(b04_router)
    app.include_router(audio_router)
    _install_openapi(app)
    return app


app = create_app()

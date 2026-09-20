from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
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
from baby_care_api.routes.b07 import router as b07_router
from baby_care_api.routes.health import router as health_router
from baby_care_api.services.audio import PostgresAudioService
from baby_care_api.services.audio_decoder import AudioDecoder, FfmpegAudioDecoder
from baby_care_api.services.auth_provider import SupabaseSessionRevocationProvider
from baby_care_api.services.b07 import PostgresNormalizationService
from baby_care_api.services.idempotency import UnconfiguredIdempotencyPort
from baby_care_api.services.model_runtime import (
    ModelRuntimeFactory,
    ModelRuntimeManager,
    load_configured_m2d_runtime,
    m2d_configuration_complete,
)
from baby_care_api.services.normalizer import (
    NormalizerAdapter,
    OpenAINormalizerAdapter,
    openai_dependency_available,
)
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
    "createNormalization",
    "createReauthenticationChallenge",
    "createReauthenticationProof",
    "deleteBabyData",
    "deleteCareEntry",
    "deleteCareEvent",
    "deleteMyContributions",
    "getActiveBaby",
    "getCareEntry",
    "getCareEvent",
    "getCapabilities",
    "getChanges",
    "getChildDataVerification",
    "getDeletion",
    "getDailySummary",
    "getSessionRevocation",
    "getNormalization",
    "getStateObservation",
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
    "getEpisode",
    "getPlayback",
    "reissueUpload",
    "confirmCareEntry",
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
    model_runtime_factory: ModelRuntimeFactory = load_configured_m2d_runtime,
    normalizer_adapter: NormalizerAdapter | None = None,
    confirmation_checkpoint: Callable[[], None] | None = None,
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
    configured_key = (
        None
        if active_settings.openai_api_key is None
        else active_settings.openai_api_key.get_secret_value()
    )
    normalizer_unavailable_reason: str | None
    active_normalizer: NormalizerAdapter | None = None
    if b04_service is None:
        normalizer_unavailable_reason = "DATABASE_UNAVAILABLE"
    elif not active_settings.external_normalization_enabled:
        normalizer_unavailable_reason = "DISABLED"
    elif configured_key is None or not configured_key.strip():
        normalizer_unavailable_reason = "CREDENTIALS_MISSING"
    elif normalizer_adapter is not None:
        normalizer_unavailable_reason = None
        active_normalizer = normalizer_adapter
    elif not openai_dependency_available():
        normalizer_unavailable_reason = "DEPENDENCY_MISSING"
    else:
        normalizer_unavailable_reason = None
        active_normalizer = OpenAINormalizerAdapter(
            api_key=configured_key,
            organization=active_settings.openai_organization,
            project=active_settings.openai_project,
            timeout_seconds=active_settings.normalization_timeout_seconds,
        )
    b07_service = (
        PostgresNormalizationService(
            b04_service,
            adapter=active_normalizer,
            unavailable_code=(
                None
                if normalizer_unavailable_reason is None
                else f"NORMALIZATION_{normalizer_unavailable_reason}"
                if normalizer_unavailable_reason != "DATABASE_UNAVAILABLE"
                else "NORMALIZATION_DEPENDENCY_MISSING"
            ),
            lease_seconds=active_settings.normalization_lease_seconds,
            confirmation_checkpoint=confirmation_checkpoint,
        )
        if b04_service is not None
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
    model_runtime = ModelRuntimeManager(
        enabled=active_settings.m2d_enabled,
        configured=m2d_configuration_complete(active_settings),
        settings=active_settings,
        factory=model_runtime_factory,
    )
    if model_runtime.enabled:
        # The service-owned model state is authoritative when this profile is
        # enabled; callers cannot replace it with an unrelated readiness probe.
        active_readiness_probes.pop(ComponentName.MODEL, None)
    if model_runtime.enabled and model_runtime.configured:
        active_readiness_probes[ComponentName.MODEL] = model_runtime.probe
    required_components = {
        ComponentName.AUTHENTICATION,
        ComponentName.DATABASE,
    }
    if model_runtime.enabled:
        required_components.add(ComponentName.MODEL)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if database is not None:
            await database.open()
        if b07_service is not None:
            await b07_service.startup_recover()
        # FastAPI does not accept HTTP traffic until lifespan startup completes.
        # A failed model load is retained as readiness=false so liveness can still
        # be served after startup; health requests never trigger a retry.
        await model_runtime.start()
        try:
            yield
        finally:
            await model_runtime.close()
            if b07_service is not None:
                await b07_service.close()
            if database is not None:
                await database.close()

    app = FastAPI(
        title="Baby Care API",
        version=SERVICE_VERSION,
        description=(
            "B-04 account/shared-care records, B-05 private audio intake, B-07 eventless "
            "normalization, and the B-09 durable change feed. "
            "The canonical business contract is version 1.2.0 at "
            "contracts/openapi계약.json."
        ),
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.state.readiness = ReadinessService(
        active_readiness_probes,
        required_components=frozenset(required_components),
    )
    app.state.model_runtime = model_runtime
    app.state.authentication = authentication or UnconfiguredAuthenticationPort()
    app.state.authorization = database or UnconfiguredAuthorizationPort()
    app.state.b04_service = b04_service
    app.state.audio_service = b04_service
    app.state.b07_service = b07_service
    app.state.normalizer_unavailable_reason = normalizer_unavailable_reason
    app.state.session_revocation = session_revocation
    app.state.idempotency = UnconfiguredIdempotencyPort()

    install_exception_handlers(app)
    install_request_context(app)
    app.include_router(health_router)
    app.include_router(b04_router)
    app.include_router(audio_router)
    app.include_router(b07_router)
    _install_openapi(app)
    return app


app = create_app()

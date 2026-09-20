from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, NonNegativeFloat, NonNegativeInt, PositiveInt

from baby_care_api.models.b04 import ActionAttempt, Failure, RecordVersion
from baby_care_api.models.base import ContractModel
from baby_care_api.models.care_events import DataOrigin


class EpisodeSource(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"
    FILE = "FILE"


class TimingStatus(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


class Episode(ContractModel):
    episode_id: UUID
    baby_id: UUID
    created_by_user_id: UUID
    status: Literal["OPEN", "CLOSED"]
    source: EpisodeSource
    timing_status: TimingStatus
    started_at: AwareDatetime | None
    ended_at: AwareDatetime | None
    closed_reason: Literal["NO_CRY", "USER_STOP", "INPUT_GAP", "FILE_IMPORT"] | None
    observation_session_id: UUID | None
    data_origin: DataOrigin
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime


class CreateEpisode(ContractModel):
    client_request_id: UUID
    baby_id: UUID
    source: EpisodeSource
    timing_status: TimingStatus
    started_at: AwareDatetime | None
    observation_session_id: UUID | None
    data_origin: DataOrigin


class AudioStatus(StrEnum):
    ALLOCATED = "ALLOCATED"
    VERIFYING = "VERIFYING"
    READY = "READY"
    REJECTED = "REJECTED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class QualityReason(StrEnum):
    TOO_SHORT = "TOO_SHORT"
    SILENCE = "SILENCE"
    CLIPPING = "CLIPPING"
    HIGH_NOISE = "HIGH_NOISE"
    NO_CRY = "NO_CRY"
    UNSUPPORTED_CODEC = "UNSUPPORTED_CODEC"
    DECODE_ERROR = "DECODE_ERROR"
    TOO_LONG = "TOO_LONG"
    TOO_LARGE = "TOO_LARGE"


class AudioAsset(ContractModel):
    audio_id: UUID
    episode_id: UUID
    baby_id: UUID
    created_by_user_id: UUID
    mime_type: str
    bytes: NonNegativeInt = Field(le=25_000_000)
    duration_seconds: NonNegativeFloat | None = Field(default=None, le=60)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: AudioStatus
    retention_until: AwareDatetime | None
    rejection_code: str | None
    data_origin: DataOrigin
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime
    quality_reasons: list[QualityReason]


class CreateUpload(ContractModel):
    client_request_id: UUID
    mime_type: str = Field(min_length=1, max_length=127)
    bytes: PositiveInt = Field(le=25_000_000)
    duration_seconds: NonNegativeFloat | None = Field(default=None, le=60)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prefer_resumable: bool


class ReissueUpload(ContractModel):
    client_request_id: UUID
    version: PositiveInt


class CompleteUpload(ContractModel):
    client_request_id: UUID
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class CancelUpload(ContractModel):
    client_request_id: UUID


class UploadMethod(StrEnum):
    STANDARD = "STANDARD"
    TUS = "TUS"


class UploadGrant(ContractModel):
    upload_id: UUID
    audio_id: UUID
    bucket: str
    object_key: str
    method: UploadMethod
    upload_endpoint: str
    expires_at: AwareDatetime
    max_bytes: PositiveInt = Field(le=25_000_000)


class AudioUpload(ContractModel):
    audio: AudioAsset
    upload: UploadGrant


class Playback(ContractModel):
    audio_id: UUID
    playback_url: str
    expires_at: AwareDatetime


class AudioCandidate(ContractModel):
    code: str
    label: str
    rank: PositiveInt = Field(le=3)


class ContextValues(ContractModel):
    last_feeding_at: AwareDatetime | None
    last_sleep_started_at: AwareDatetime | None
    last_sleep_ended_at: AwareDatetime | None
    last_diaper_event_at: AwareDatetime | None
    minutes_since_last_feeding: NonNegativeFloat | None
    current_sleep: bool | None


class ContextSnapshot(ContractModel):
    context_snapshot_id: UUID
    baby_id: UUID
    as_of: AwareDatetime | None
    known_at: AwareDatetime
    record_refs: list[RecordVersion]
    values: ContextValues
    missing_fields: list[str]
    reproduction_status: Literal["AVAILABLE", "SOURCE_DELETED"]


class SourceRef(ContractModel):
    kind: Literal["AUDIO", "CARE_EVENT", "ANALYSIS", "OBSERVATION", "PRIOR_CASE"]
    resource_id: UUID
    version: PositiveInt | None


class RecommendedAction(ContractModel):
    action_type: Literal[
        "FEEDING",
        "DIAPER_CHECK",
        "DIAPER_CHANGE",
        "HOLDING",
        "BURPING",
        "SLEEP_PREPARATION",
        "ENVIRONMENT_ADJUSTMENT",
        "OTHER",
    ]
    text: str
    evidence_refs: list[SourceRef]


class ObservationQuestion(ContractModel):
    kind: str
    text: str
    options: list[Literal["YES", "NO", "UNKNOWN"]] = Field(min_length=1)
    policy_version: str


class HelpAction(ContractModel):
    text: str
    action_label: str
    action_url: str
    region: str
    reviewed_template_id: str
    evidence_refs: list[SourceRef] = Field(min_length=1)


class Recommendation(ContractModel):
    recommendation_id: UUID
    analysis_id: UUID
    context_snapshot_id: UUID | None
    status: Literal["READY", "GENERAL_CHECKLIST", "NEEDS_OBSERVATION", "HELP_REQUIRED"]
    actions: list[RecommendedAction] = Field(max_length=3)
    optional_questions: list[ObservationQuestion] = Field(max_length=2)
    help_action: HelpAction | None
    policy_version: str
    supersedes_id: UUID | None
    recorded_at: AwareDatetime


class Analysis(ContractModel):
    analysis_id: UUID
    baby_id: UUID
    episode_id: UUID
    audio_id: UUID
    created_by_user_id: UUID
    status: Literal["READY", "RUNNING", "COMPLETE", "ABSTAIN", "FAILED"]
    stage: Literal["READY", "QUALITY_CHECK", "INFERENCE", "CONTEXT", "PERSISTING", "FINISHED"]
    attempt_no: PositiveInt
    lease_expires_at: AwareDatetime | None
    quality_status: Literal["PENDING", "PASS", "INSUFFICIENT", "INVALID"]
    quality_reasons: list[QualityReason]
    cry_detected: bool | None
    audio_candidates: list[AudioCandidate] = Field(max_length=3)
    abstain_reason: (
        Literal[
            "NO_CRY", "LOW_QUALITY", "INSUFFICIENT_AUDIO", "LOW_CONFIDENCE", "UNSUPPORTED_SCOPE"
        ]
        | None
    )
    failure: Failure | None
    model_version: str | None
    preprocess_version: str | None
    label_mapping_version: str | None
    context_snapshot: ContextSnapshot | None
    recommendation: Recommendation | None
    inference_mode: Literal["REAL", "STUB"]
    inference_executed: bool
    data_origin: DataOrigin
    recorded_at: AwareDatetime
    completed_at: AwareDatetime | None


class ActionGroup(ContractModel):
    action_group_id: UUID
    baby_id: UUID
    episode_id: UUID
    entry_id: UUID
    action_ids: list[UUID] = Field(min_length=2)
    version: PositiveInt


class Outcome(ContractModel):
    outcome_id: UUID
    baby_id: UUID
    action_id: UUID | None
    action_group_id: UUID | None
    observed_at: AwareDatetime | None
    time_precision: Literal["EXACT", "RELATIVE", "UNKNOWN"]
    response: Literal["CALMED", "PARTIALLY_CALMED", "NO_CHANGE", "CRYING_AGAIN", "UNKNOWN"]
    caregiver_interpretation: str | None
    created_by_user_id: UUID
    updated_by_user_id: UUID
    data_origin: DataOrigin
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime


class StateObservation(ContractModel):
    state_observation_id: UUID
    baby_id: UUID
    episode_id: UUID | None
    action_id: UUID | None
    source_entry_id: UUID | None
    phase: Literal["BEFORE", "AFTER", "UNRELATED", "UNKNOWN"]
    observed_at: AwareDatetime | None
    time_precision: Literal["EXACT", "RELATIVE", "UNKNOWN"]
    state_codes: list[
        Literal[
            "CRYING",
            "FUSSING",
            "CALM",
            "SLEEPY_APPEARING",
            "ASLEEP",
            "AWAKE",
            "CHEERFUL_APPEARING",
            "UNKNOWN",
        ]
    ] = Field(min_length=1)
    observation_source: Literal["SELF_REPORTED", "REPORTED_BY_OTHER"]
    confirmation_status: Literal["USER_CONFIRMED", "USER_CORRECTED"]
    visual_state_code: Literal[
        "CRYING",
        "FUSSING",
        "CALM",
        "SLEEPY_APPEARING",
        "ASLEEP",
        "AWAKE",
        "CHEERFUL_APPEARING",
        "NEUTRAL",
    ]
    visual_mapping_version: str
    created_by_user_id: UUID
    data_origin: DataOrigin
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime


class EpisodeDetail(ContractModel):
    episode: Episode
    audio_assets: list[AudioAsset]
    analyses: list[Analysis]
    actions: list[ActionAttempt]
    action_groups: list[ActionGroup]
    outcomes: list[Outcome]
    state_observations: list[StateObservation]


class ModelInfo(ContractModel):
    available: bool
    model_version: str | None
    preprocess_version: str | None
    label_mapping_version: str | None
    supported_labels: list[str]
    inference_mode: Literal["REAL", "STUB"]


class BrowserSupport(ContractModel):
    os: str
    browser: str
    tested_version: str
    automatic_detection_supported: bool
    recording_supported: bool
    verified_at: AwareDatetime
    evidence_ref: str


class DetectorInfo(ContractModel):
    available: bool
    execution_mode: Literal["REAL", "STUB"]
    model_version: str | None
    model_asset_url: str | None
    weights_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_sample_rate_hz: PositiveInt | None
    policy_version: str | None
    supported_clients: list[BrowserSupport]


class Capabilities(ContractModel):
    contract_version: Literal["1.1.1"] = "1.1.1"
    audio_model: ModelInfo
    supported_mime_types: list[str]
    upload_max_bytes: Literal[25_000_000] = 25_000_000
    upload_max_seconds: Literal[60] = 60
    normalizer_available: bool
    automatic_detection_supported: bool
    detector: DetectorInfo

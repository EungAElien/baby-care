from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from baby_care_api.models.base import ContractModel
from baby_care_api.models.care_events import CareEvent, CareEventValue, DataOrigin, LifecycleStatus


class FeedingMode(StrEnum):
    BREAST = "BREAST"
    FORMULA = "FORMULA"
    MIXED = "MIXED"
    UNSPECIFIED = "UNSPECIFIED"


class Relationship(StrEnum):
    MOTHER = "MOTHER"
    FATHER = "FATHER"
    GRANDPARENT = "GRANDPARENT"
    OTHER = "OTHER"


class MembershipRole(StrEnum):
    OWNER = "OWNER"
    CAREGIVER = "CAREGIVER"


class MembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    LEFT = "LEFT"
    REVOKED = "REVOKED"


class InviteStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ConsentScope(StrEnum):
    SERVICE_PROCESSING = "SERVICE_PROCESSING"
    AUDIO_RETENTION = "AUDIO_RETENTION"
    BABY_TRAINING = "BABY_TRAINING"
    CONTRIBUTOR_TRAINING = "CONTRIBUTOR_TRAINING"
    SHARED_USE = "SHARED_USE"


class ConsentStatus(StrEnum):
    NOT_GRANTED = "NOT_GRANTED"
    GRANTED = "GRANTED"
    REVOKED = "REVOKED"


class Baby(ContractModel):
    baby_id: UUID
    owner_user_id: UUID
    alias: str
    birth_date: date
    feeding_mode: FeedingMode
    timezone: str
    status: LifecycleStatus
    context_revision: NonNegativeInt
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime


class Membership(ContractModel):
    membership_id: UUID
    baby_id: UUID
    user_id: UUID
    role: MembershipRole
    relationship: Relationship
    display_name: str
    status: MembershipStatus
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime


class BabyAccess(ContractModel):
    baby: Baby
    membership: Membership


class BabyList(ContractModel):
    items: list[BabyAccess]


class ActiveBaby(ContractModel):
    baby_id: UUID | None


class _BabyInput(ContractModel):
    alias: str = Field(min_length=1, max_length=40)
    birth_date: date
    feeding_mode: FeedingMode
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("birth_date")
    @classmethod
    def birth_date_cannot_be_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("birth_date cannot be in the future")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_iana(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be an IANA time zone") from exc
        return value


class CreateBaby(_BabyInput):
    client_request_id: UUID


class PatchBaby(ContractModel):
    client_request_id: UUID
    version: PositiveInt
    alias: str | None = Field(default=None, min_length=1, max_length=40)
    birth_date: date | None = None
    feeding_mode: FeedingMode | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_patch(self) -> PatchBaby:
        if all(
            value is None
            for value in (self.alias, self.birth_date, self.feeding_mode, self.timezone)
        ):
            raise ValueError("at least one baby field must be supplied")
        if self.birth_date is not None and self.birth_date > date.today():
            raise ValueError("birth_date cannot be in the future")
        if self.timezone is not None:
            try:
                ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("timezone must be an IANA time zone") from exc
        return self


class SetActiveBaby(ContractModel):
    client_request_id: UUID
    baby_id: UUID


class PatchMembership(ContractModel):
    client_request_id: UUID
    version: PositiveInt
    relationship: Relationship


class MembershipPage(ContractModel):
    items: list[Membership]
    next_cursor: str | None = None


class Invite(ContractModel):
    invite_id: UUID
    baby_id: UUID
    inviter_user_id: UUID
    email: str
    status: InviteStatus
    expires_at: AwareDatetime
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime


class IssuedInvite(ContractModel):
    invite: Invite
    invite_url: str | None
    link_reissue_required: bool


class CreateInvite(ContractModel):
    client_request_id: UUID
    email: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        local, separator, domain = normalized.partition("@")
        if separator != "@" or not local or "." not in domain:
            raise ValueError("email must be valid")
        return normalized


class ReissueInvite(ContractModel):
    client_request_id: UUID


class AcceptInvite(ContractModel):
    client_request_id: UUID
    token: str = Field(min_length=32, max_length=512)
    accept_shared_use: Literal[True]
    policy_version: str = Field(min_length=1, max_length=80)
    relationship: Relationship


class InvitePage(ContractModel):
    items: list[Invite]
    next_cursor: str | None = None


class Consent(ContractModel):
    consent_id: UUID
    baby_id: UUID
    actor_user_id: UUID
    scope: ConsentScope
    status: ConsentStatus
    policy_version: str
    granted_at: AwareDatetime | None
    revoked_at: AwareDatetime | None
    version: PositiveInt


class ConsentPage(ContractModel):
    items: list[Consent]
    next_cursor: str | None = None


class SetBabyConsent(ContractModel):
    client_request_id: UUID
    baby_id: UUID
    scope: Literal[
        ConsentScope.SERVICE_PROCESSING,
        ConsentScope.AUDIO_RETENTION,
        ConsentScope.BABY_TRAINING,
    ]
    granted: bool
    policy_version: str = Field(min_length=1, max_length=80)
    version: NonNegativeInt


class SetTrainingConsent(ContractModel):
    client_request_id: UUID
    granted: bool
    policy_version: str = Field(min_length=1, max_length=80)
    version: NonNegativeInt


class ReauthenticationOperation(StrEnum):
    CREATE_INVITE = "CREATE_INVITE"
    DELETE_BABY = "DELETE_BABY"
    ENABLE_BABY_TRAINING = "ENABLE_BABY_TRAINING"


class CreateReauthenticationChallenge(ContractModel):
    client_request_id: UUID
    operation: ReauthenticationOperation
    baby_id: UUID


class ReauthenticationChallenge(ContractModel):
    challenge_id: UUID
    user_id: UUID
    requested_session_id: UUID
    operation: ReauthenticationOperation
    baby_id: UUID
    auth_method: Literal["SUPABASE_OTP"] = "SUPABASE_OTP"
    status: Literal["PENDING", "PROVED", "EXPIRED"]
    created_at: AwareDatetime
    expires_at: AwareDatetime


class CreateReauthenticationProof(ContractModel):
    client_request_id: UUID
    challenge_id: UUID


class ReauthenticationProof(ContractModel):
    proof_id: UUID
    challenge_id: UUID
    user_id: UUID
    session_id: UUID
    operation: ReauthenticationOperation
    baby_id: UUID
    proof_token: str | None
    token_reissue_required: bool
    issued_at: AwareDatetime
    expires_at: AwareDatetime


class SessionRevocationScope(StrEnum):
    CURRENT = "CURRENT"
    OTHERS = "OTHERS"
    ALL = "ALL"


class RevokeSessions(ContractModel):
    client_request_id: UUID
    scope: SessionRevocationScope


class Failure(ContractModel):
    code: str
    message: str
    retryable: bool


class SessionRevocation(ContractModel):
    revocation_id: UUID
    requester_user_id: UUID
    requester_session_id: UUID
    scope: SessionRevocationScope
    status: Literal["PENDING", "COMPLETE", "FAILED"]
    target_session_count: NonNegativeInt
    provider_scope: Literal["local", "others", "global"]
    provider_http_status: NonNegativeInt | None
    failure: Failure | None
    access_blocked: Literal[True]
    requested_at: AwareDatetime
    completed_at: AwareDatetime | None


class ChildDataVerification(ContractModel):
    baby_id: UUID
    subject_user_id: UUID
    status: Literal["UNVERIFIED", "SYNTHETIC_TEST_ONLY", "VERIFIED"]
    method: str | None
    policy_version: str | None
    verified_at: AwareDatetime | None
    production_processing_allowed: bool


class InputMode(StrEnum):
    CHOICE = "CHOICE"
    TEXT = "TEXT"
    MIXED = "MIXED"


class CareEntryStatus(StrEnum):
    DRAFT = "DRAFT"
    NORMALIZING = "NORMALIZING"
    REVIEW_READY = "REVIEW_READY"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
    CONFIRMED = "CONFIRMED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class Choice(ContractModel):
    choice_id: str
    kind: Literal["ACTION", "STATE", "RESPONSE"]
    code: str
    assertion: Literal["PERFORMED", "PLANNED", "NEGATED", "UNCERTAIN"] | None


class RecordVersion(ContractModel):
    resource_type: Literal["CARE_EVENT", "OUTCOME", "STATE_OBSERVATION", "ACTION"]
    resource_id: UUID
    version: PositiveInt


class _CareEntryInput(ContractModel):
    input_mode: InputMode
    raw_text: str | None = Field(default=None, max_length=2000)
    choices: list[Choice]
    occurred_at: AwareDatetime | None
    time_precision: Literal["EXACT", "RELATIVE", "UNKNOWN"]

    @model_validator(mode="after")
    def validate_input_shape(self) -> _CareEntryInput:
        if self.input_mode is InputMode.CHOICE and (self.raw_text is not None or not self.choices):
            raise ValueError("CHOICE requires choices and no raw_text")
        if self.input_mode is InputMode.TEXT and (
            self.raw_text is None or not self.raw_text.strip() or self.choices
        ):
            raise ValueError("TEXT requires raw_text and no choices")
        if self.input_mode is InputMode.MIXED and (
            self.raw_text is None or not self.raw_text.strip() or not self.choices
        ):
            raise ValueError("MIXED requires raw_text and choices")
        return self


class CreateCareEntry(_CareEntryInput):
    client_request_id: UUID
    episode_id: UUID | None
    supersedes_entry_id: UUID | None
    base_record_versions: list[RecordVersion]


class PatchCareEntry(_CareEntryInput):
    client_request_id: UUID
    input_revision: PositiveInt


class CareEntry(ContractModel):
    entry_id: UUID
    baby_id: UUID
    author_user_id: UUID
    original_author_user_id: UUID
    episode_id: UUID | None
    input_mode: InputMode
    raw_text: str | None
    choices: list[Choice]
    occurred_at: AwareDatetime | None
    time_precision: Literal["EXACT", "RELATIVE", "UNKNOWN"]
    input_revision: PositiveInt
    status: CareEntryStatus
    normalization_run_id: UUID | None
    normalized_content: dict[str, Any] | None
    supersedes_entry_id: UUID | None
    base_record_versions: list[RecordVersion]
    confirmed_resources: dict[str, Any] | None
    confirmed_by_user_id: UUID | None
    confirmed_at: AwareDatetime | None
    data_origin: DataOrigin
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime


class CareEntryPage(ContractModel):
    items: list[CareEntry]
    next_cursor: str | None


class CreateAction(ContractModel):
    client_request_id: UUID
    care_event_id: UUID | None
    new_care_event: CareEventValue | None
    recommendation_id: UUID | None
    performed_by_user_id: UUID | None
    sequence: PositiveInt

    @model_validator(mode="after")
    def exactly_one_event_source(self) -> CreateAction:
        if (self.care_event_id is None) == (self.new_care_event is None):
            raise ValueError("exactly one of care_event_id and new_care_event is required")
        return self


class ActionAttempt(ContractModel):
    action_id: UUID
    baby_id: UUID
    episode_id: UUID
    care_event_id: UUID
    recommendation_id: UUID | None
    created_by_user_id: UUID
    performed_by_user_id: UUID | None
    performed_at: AwareDatetime | None
    sequence: PositiveInt
    status: LifecycleStatus
    followup_status: Literal["PENDING", "RECORDED", "UNCONFIRMED"]
    data_origin: DataOrigin
    version: PositiveInt
    recorded_at: AwareDatetime
    updated_at: AwareDatetime


class TimelineItem(ContractModel):
    kind: Literal["CARE_EVENT"]
    resource_id: UUID
    baby_id: UUID
    occurred_at: AwareDatetime | None
    version: PositiveInt
    created_by_user_id: UUID
    data_origin: DataOrigin
    resource: CareEvent


class TimelineItemPage(ContractModel):
    items: list[TimelineItem]
    next_cursor: str | None


class DeletionScope(StrEnum):
    ALL = "ALL"
    CARE_EVENT = "CARE_EVENT"
    CARE_ENTRY = "CARE_ENTRY"
    MY_CONTRIBUTIONS = "MY_CONTRIBUTIONS"


class DeletionJob(ContractModel):
    deletion_job_id: UUID
    requester_user_id: UUID
    baby_id: UUID
    scope: DeletionScope
    resource_id: UUID | None
    status: Literal["PENDING", "RUNNING", "COMPLETE", "FAILED"]
    access_blocked: Literal[True]
    requested_at: AwareDatetime
    completed_at: AwareDatetime | None
    failure: Failure | None
    pending_categories: list[
        Literal[
            "AUDIO",
            "RAW_TEXT",
            "RECORDS",
            "ANALYSES",
            "DERIVED_FEATURES",
            "TRAINING_COPIES",
        ]
    ]
    attempt_no: PositiveInt


class RetryDeletion(ContractModel):
    client_request_id: UUID
    expected_attempt: PositiveInt

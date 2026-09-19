from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, status

from baby_care_api.core.config import API_V1_PREFIX
from baby_care_api.core.errors import ApiException
from baby_care_api.models.b04 import (
    AcceptInvite,
    ActionAttempt,
    ActiveBaby,
    Baby,
    BabyAccess,
    BabyList,
    CareEntry,
    CareEntryPage,
    ChildDataVerification,
    Consent,
    ConsentPage,
    CreateAction,
    CreateBaby,
    CreateCareEntry,
    CreateInvite,
    CreateReauthenticationChallenge,
    CreateReauthenticationProof,
    DeletionJob,
    Invite,
    InvitePage,
    IssuedInvite,
    Membership,
    MembershipPage,
    PatchBaby,
    PatchCareEntry,
    PatchMembership,
    ReauthenticationChallenge,
    ReauthenticationProof,
    ReissueInvite,
    RetryDeletion,
    RevokeSessions,
    SessionRevocation,
    SetActiveBaby,
    SetBabyConsent,
    SetTrainingConsent,
    TimelineItemPage,
)
from baby_care_api.models.care_events import CareEvent, CreateCareEvent, PatchCareEvent
from baby_care_api.models.errors import ErrorCode, ErrorDetails
from baby_care_api.services.auth_provider import SupabaseSessionRevocationProvider
from baby_care_api.services.b04 import PostgresBabyCareService
from baby_care_api.services.idempotency import ensure_idempotency_key_matches
from baby_care_api.services.security import AuthenticatedPrincipal, AuthenticationPort

router = APIRouter(prefix=API_V1_PREFIX)

AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]
IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]
ReauthenticationProofHeader = Annotated[
    str | None,
    Header(alias="X-Reauthentication-Proof"),
]


def _service(request: Request) -> PostgresBabyCareService:
    service = request.app.state.b04_service
    if service is None:
        raise ApiException.service_unavailable()
    return cast(PostgresBabyCareService, service)


async def _principal(request: Request, authorization: str | None) -> AuthenticatedPrincipal:
    authentication = cast(AuthenticationPort, request.app.state.authentication)
    return await authentication.authenticate(authorization)


def _access_token(authorization: str | None) -> str:
    if authorization is None:
        raise ApiException(ErrorCode.AUTH_REQUIRED, "Authentication is required.")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise ApiException(ErrorCode.INVALID_TOKEN, "The access token is invalid.")
    return token.strip()


def _path(request: Request) -> str:
    return request.url.path


@router.get("/babies", response_model=BabyList, operation_id="listBabies")
async def list_babies(request: Request, authorization: AuthorizationHeader = None) -> BabyList:
    principal = await _principal(request, authorization)
    return await _service(request).list_babies(principal)


@router.post(
    "/babies",
    response_model=BabyAccess,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBaby",
)
async def create_baby(
    body: CreateBaby,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> BabyAccess:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_baby(principal, body, path=_path(request))


@router.get("/babies/current", response_model=ActiveBaby, operation_id="getActiveBaby")
async def get_active_baby(
    request: Request, authorization: AuthorizationHeader = None
) -> ActiveBaby:
    principal = await _principal(request, authorization)
    return await _service(request).get_active_baby(principal)


@router.put("/me/active-baby", response_model=ActiveBaby, operation_id="setActiveBaby")
async def set_active_baby(
    body: SetActiveBaby,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> ActiveBaby:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).set_active_baby(
        principal,
        client_request_id=body.client_request_id,
        baby_id=body.baby_id,
        path=_path(request),
    )


@router.patch("/babies/{baby_id}", response_model=Baby, operation_id="patchBaby")
async def patch_baby(
    baby_id: UUID,
    body: PatchBaby,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> Baby:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).patch_baby(principal, baby_id, body, path=_path(request))


@router.get(
    "/babies/{baby_id}/members",
    response_model=MembershipPage,
    operation_id="listMembers",
)
async def list_members(
    baby_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> MembershipPage:
    principal = await _principal(request, authorization)
    return await _service(request).list_members(principal, baby_id)


@router.patch(
    "/babies/{baby_id}/members/me",
    response_model=Membership,
    operation_id="patchMyRelationship",
)
async def patch_my_relationship(
    baby_id: UUID,
    body: PatchMembership,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> Membership:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).patch_my_relationship(
        principal, baby_id, body, path=_path(request)
    )


@router.delete(
    "/babies/{baby_id}/members/{user_id}",
    response_model=Membership,
    operation_id="removeMembership",
)
async def remove_membership(
    baby_id: UUID,
    user_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    version: Annotated[int, Query(ge=1)],
    authorization: AuthorizationHeader = None,
) -> Membership:
    principal = await _principal(request, authorization)
    return await _service(request).remove_membership(
        principal,
        baby_id,
        user_id,
        version=version,
        idempotency_key=idempotency_key,
        path=_path(request),
    )


@router.get(
    "/babies/{baby_id}/invites",
    response_model=InvitePage,
    operation_id="listInvites",
)
async def list_invites(
    baby_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> InvitePage:
    principal = await _principal(request, authorization)
    return await _service(request).list_invites(principal, baby_id)


@router.post(
    "/babies/{baby_id}/invites",
    response_model=IssuedInvite,
    status_code=status.HTTP_201_CREATED,
    operation_id="createInvite",
)
async def create_invite(
    baby_id: UUID,
    body: CreateInvite,
    request: Request,
    idempotency_key: IdempotencyKey,
    proof: ReauthenticationProofHeader = None,
    authorization: AuthorizationHeader = None,
) -> IssuedInvite:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_invite(
        principal, baby_id, body, proof_token=proof, path=_path(request)
    )


@router.post(
    "/invites/{invite_id}/reissue",
    response_model=IssuedInvite,
    status_code=status.HTTP_201_CREATED,
    operation_id="reissueInvite",
)
async def reissue_invite(
    invite_id: UUID,
    body: ReissueInvite,
    request: Request,
    idempotency_key: IdempotencyKey,
    proof: ReauthenticationProofHeader = None,
    authorization: AuthorizationHeader = None,
) -> IssuedInvite:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).reissue_invite(
        principal,
        invite_id,
        client_request_id=body.client_request_id,
        proof_token=proof,
        path=_path(request),
    )


@router.post("/invites/accept", response_model=BabyAccess, operation_id="acceptInvite")
async def accept_invite(
    body: AcceptInvite,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> BabyAccess:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    provider = request.app.state.session_revocation
    if provider is None:
        raise ApiException.service_unavailable()
    verification = await cast(SupabaseSessionRevocationProvider, provider).verify_confirmed_email(
        access_token=_access_token(authorization),
        expected_user_id=principal.user_id,
        expected_email=principal.email,
    )
    if verification.unavailable:
        raise ApiException.service_unavailable()
    if not verification.verified:
        raise ApiException(
            ErrorCode.INVITE_EMAIL_MISMATCH,
            "The invitation requires the currently verified email address.",
        )
    return await _service(request).accept_invite(principal, body, path=_path(request))


@router.delete("/invites/{invite_id}", response_model=Invite, operation_id="revokeInvite")
async def revoke_invite(
    invite_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    version: Annotated[int, Query(ge=1)],
    authorization: AuthorizationHeader = None,
) -> Invite:
    principal = await _principal(request, authorization)
    return await _service(request).revoke_invite(
        principal,
        invite_id,
        version=version,
        idempotency_key=idempotency_key,
        path=_path(request),
    )


@router.get("/consents", response_model=ConsentPage, operation_id="listConsents")
async def list_consents(
    request: Request,
    baby_id: Annotated[UUID, Query()],
    authorization: AuthorizationHeader = None,
) -> ConsentPage:
    principal = await _principal(request, authorization)
    return await _service(request).list_consents(principal, baby_id)


@router.put("/consents", response_model=Consent, operation_id="setBabyConsent")
async def set_baby_consent(
    body: SetBabyConsent,
    request: Request,
    idempotency_key: IdempotencyKey,
    proof: ReauthenticationProofHeader = None,
    authorization: AuthorizationHeader = None,
) -> Consent:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).set_consent(
        principal,
        baby_id=body.baby_id,
        scope=body.scope.value,
        granted=body.granted,
        policy_version=body.policy_version,
        expected_version=body.version,
        client_request_id=body.client_request_id,
        path=_path(request),
        proof_token=proof,
    )


@router.put(
    "/me/training-consents/{baby_id}",
    response_model=Consent,
    operation_id="setMyTrainingConsent",
)
async def set_my_training_consent(
    baby_id: UUID,
    body: SetTrainingConsent,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> Consent:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).set_consent(
        principal,
        baby_id=baby_id,
        scope="CONTRIBUTOR_TRAINING",
        granted=body.granted,
        policy_version=body.policy_version,
        expected_version=body.version,
        client_request_id=body.client_request_id,
        path=_path(request),
        personal=True,
    )


@router.get(
    "/babies/{baby_id}/child-data-verification",
    response_model=ChildDataVerification,
    operation_id="getChildDataVerification",
)
async def child_data_verification(
    baby_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> ChildDataVerification:
    principal = await _principal(request, authorization)
    return await _service(request).child_verification(principal, baby_id)


@router.post(
    "/babies/{baby_id}/care-events",
    response_model=CareEvent,
    status_code=status.HTTP_201_CREATED,
    operation_id="createCareEvent",
)
async def create_care_event(
    baby_id: UUID,
    body: CreateCareEvent,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> CareEvent:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_care_event(principal, baby_id, body, path=_path(request))


@router.get("/care-events/{care_event_id}", response_model=CareEvent, operation_id="getCareEvent")
async def get_care_event(
    care_event_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> CareEvent:
    principal = await _principal(request, authorization)
    return await _service(request).get_care_event(principal, care_event_id)


@router.patch(
    "/care-events/{care_event_id}",
    response_model=CareEvent,
    operation_id="patchCareEvent",
)
async def patch_care_event(
    care_event_id: UUID,
    body: PatchCareEvent,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> CareEvent:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).patch_care_event(
        principal, care_event_id, body, path=_path(request)
    )


@router.delete(
    "/care-events/{care_event_id}",
    response_model=DeletionJob,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="deleteCareEvent",
)
async def delete_care_event(
    care_event_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    version: Annotated[int, Query(ge=1)],
    authorization: AuthorizationHeader = None,
) -> DeletionJob:
    principal = await _principal(request, authorization)
    return await _service(request).delete_care_event(
        principal,
        care_event_id,
        version=version,
        idempotency_key=idempotency_key,
        path=_path(request),
    )


@router.post(
    "/episodes/{episode_id}/actions",
    response_model=ActionAttempt,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAction",
)
async def create_action(
    episode_id: UUID,
    body: CreateAction,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> ActionAttempt:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_action(principal, episode_id, body, path=_path(request))


@router.post(
    "/babies/{baby_id}/care-entries",
    response_model=CareEntry,
    status_code=status.HTTP_201_CREATED,
    operation_id="createCareEntry",
)
async def create_care_entry(
    baby_id: UUID,
    body: CreateCareEntry,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> CareEntry:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_care_entry(principal, baby_id, body, path=_path(request))


@router.get(
    "/babies/{baby_id}/care-entries",
    response_model=CareEntryPage,
    operation_id="listMyCareEntries",
)
async def list_my_care_entries(
    baby_id: UUID,
    request: Request,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    entry_status: Annotated[
        Literal["DRAFT", "NORMALIZING", "REVIEW_READY", "NEEDS_MANUAL_REVIEW"] | None,
        Query(alias="status"),
    ] = None,
    authorization: AuthorizationHeader = None,
) -> CareEntryPage:
    principal = await _principal(request, authorization)
    return await _service(request).list_my_care_entries(
        principal, baby_id, cursor=cursor, limit=limit, status=entry_status
    )


@router.get("/care-entries/{entry_id}", response_model=CareEntry, operation_id="getCareEntry")
async def get_care_entry(
    entry_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> CareEntry:
    principal = await _principal(request, authorization)
    return await _service(request).get_care_entry(principal, entry_id)


@router.patch("/care-entries/{entry_id}", response_model=CareEntry, operation_id="patchCareEntry")
async def patch_care_entry(
    entry_id: UUID,
    body: PatchCareEntry,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> CareEntry:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).patch_care_entry(principal, entry_id, body, path=_path(request))


@router.delete(
    "/care-entries/{entry_id}",
    response_model=DeletionJob,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="deleteCareEntry",
)
async def delete_care_entry(
    entry_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    version: Annotated[int, Query(ge=1)],
    authorization: AuthorizationHeader = None,
) -> DeletionJob:
    principal = await _principal(request, authorization)
    return await _service(request).delete_care_entry(
        principal,
        entry_id,
        version=version,
        idempotency_key=idempotency_key,
        path=_path(request),
    )


@router.get(
    "/babies/{baby_id}/timeline",
    response_model=TimelineItemPage,
    operation_id="getTimeline",
)
async def get_timeline(
    baby_id: UUID,
    request: Request,
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    authorization: AuthorizationHeader = None,
) -> TimelineItemPage:
    principal = await _principal(request, authorization)
    return await _service(request).timeline(
        principal,
        baby_id,
        from_time=from_time,
        to_time=to_time,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/auth/reauthentication/challenges",
    response_model=ReauthenticationChallenge,
    status_code=status.HTTP_201_CREATED,
    operation_id="createReauthenticationChallenge",
)
async def create_reauthentication_challenge(
    body: CreateReauthenticationChallenge,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> ReauthenticationChallenge:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_reauthentication_challenge(
        principal, body, path=_path(request)
    )


@router.post(
    "/auth/reauthentication/proofs",
    response_model=ReauthenticationProof,
    status_code=status.HTTP_201_CREATED,
    operation_id="createReauthenticationProof",
)
async def create_reauthentication_proof(
    body: CreateReauthenticationProof,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> ReauthenticationProof:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).create_reauthentication_proof(
        principal,
        challenge_id=body.challenge_id,
        client_request_id=body.client_request_id,
        path=_path(request),
    )


@router.post(
    "/auth/session-revocations",
    response_model=SessionRevocation,
    operation_id="revokeSessions",
)
async def revoke_sessions(
    body: RevokeSessions,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> SessionRevocation:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    service = _service(request)
    if request.app.state.session_revocation is None:
        raise ApiException.service_unavailable()
    pending = await service.begin_session_revocation(
        principal,
        scope=body.scope,
        client_request_id=body.client_request_id,
        path=_path(request),
    )
    if pending.status == "COMPLETE":
        return pending
    provider = cast(SupabaseSessionRevocationProvider, request.app.state.session_revocation)
    result = await provider.revoke(
        access_token=_access_token(authorization),
        provider_scope=pending.provider_scope,
    )
    completed = await service.finish_session_revocation(
        principal,
        pending.revocation_id,
        provider_http_status=result.http_status,
        failure_code=result.failure_code,
    )
    if completed.status == "FAILED":
        raise ApiException(
            ErrorCode.AUTH_PROVIDER_REVOCATION_FAILED,
            "Local access was blocked, but provider refresh-session revocation failed.",
            retryable=True,
            details=ErrorDetails.empty().model_copy(
                update={
                    "session_revocation_id": completed.revocation_id,
                    "status_url": (
                        f"{API_V1_PREFIX}/auth/session-revocations/{completed.revocation_id}"
                    ),
                }
            ),
        )
    return completed


@router.get(
    "/auth/session-revocations/{revocation_id}",
    response_model=SessionRevocation,
    operation_id="getSessionRevocation",
)
async def get_session_revocation(
    revocation_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> SessionRevocation:
    principal = await _principal(request, authorization)
    return await _service(request).get_session_revocation(principal, revocation_id)


@router.delete(
    "/babies/{baby_id}/data",
    response_model=DeletionJob,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="deleteBabyData",
)
async def delete_baby_data(
    baby_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    version: Annotated[int, Query(ge=1)],
    confirm: Annotated[Literal["DELETE_BABY"], Query()],
    proof: ReauthenticationProofHeader = None,
    authorization: AuthorizationHeader = None,
) -> DeletionJob:
    del confirm
    principal = await _principal(request, authorization)
    return await _service(request).delete_baby_data(
        principal,
        baby_id,
        version=version,
        idempotency_key=idempotency_key,
        proof_token=proof,
        path=_path(request),
    )


@router.delete(
    "/me/contributions/{baby_id}",
    response_model=DeletionJob,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="deleteMyContributions",
)
async def delete_my_contributions(
    baby_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    confirm: Annotated[Literal["DELETE_MY_CONTRIBUTIONS"], Query()],
    authorization: AuthorizationHeader = None,
) -> DeletionJob:
    del confirm
    principal = await _principal(request, authorization)
    return await _service(request).delete_my_contributions(
        principal, baby_id, idempotency_key=idempotency_key, path=_path(request)
    )


@router.get(
    "/deletions/{deletion_job_id}",
    response_model=DeletionJob,
    operation_id="getDeletion",
)
async def get_deletion(
    deletion_job_id: UUID,
    request: Request,
    authorization: AuthorizationHeader = None,
) -> DeletionJob:
    principal = await _principal(request, authorization)
    return await _service(request).get_deletion(principal, deletion_job_id)


@router.post(
    "/deletions/{deletion_job_id}/retry",
    response_model=DeletionJob,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="retryDeletion",
)
async def retry_deletion(
    deletion_job_id: UUID,
    body: RetryDeletion,
    request: Request,
    idempotency_key: IdempotencyKey,
    authorization: AuthorizationHeader = None,
) -> DeletionJob:
    ensure_idempotency_key_matches(
        header_key=idempotency_key, client_request_id=body.client_request_id
    )
    principal = await _principal(request, authorization)
    return await _service(request).retry_deletion(
        principal,
        deletion_job_id,
        expected_attempt=body.expected_attempt,
        client_request_id=body.client_request_id,
        path=_path(request),
    )

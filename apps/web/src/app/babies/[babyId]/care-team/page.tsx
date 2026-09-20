"use client";

// SC10 공동양육 — 구성원·관계·권한·초대 및 참여 상태.
// 실제 세션의 초대 발급·재발급은 작업에 결합된 새 OTP proof를 사용한다.
// 링크 원문은 이 탭 메모리에서 최초 응답에만 보인다.
import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { isForBaby, mockIssuedInvite, mockRoleAssignments, getMockScenario } from "@/lib/mock/fixtures";
import { useMockSessionOptional } from "@/lib/mock/session";
import { useRealSession } from "@/lib/auth/real-session";
import { usePrivateScope } from "@/components/app-providers";
import { useDraft } from "@/lib/mock/draft";
import { useBabiesQuery, findBabyAccess } from "@/lib/api/babies";
import { useInvitesQuery, useMembersQuery, useRemoveMembershipMutation, useRevokeInviteMutation } from "@/lib/api/members";
import { ContractApiError } from "@/lib/api/errors";
import { ScreenSection, ErrorState, LoadingState } from "@/components/screen-state";
import { ReauthenticationPanel } from "@/components/reauthentication-panel";
import { useApiClient } from "@/lib/api/real-client";
import { createInvite, reissueInvite } from "@/lib/api/shared-care";
import type { IssuedInvite } from "@/lib/api/shared-care";
import { newClientRequestId } from "@/lib/api/client";

const roleLabel: Record<string, string> = { OWNER: "관리 보호자", CAREGIVER: "공동 보호자" };
const statusLabel: Record<string, string> = { ACTIVE: "활동 중", REVOKED: "제거됨", LEFT: "나감" };
const inviteStatusLabel: Record<string, string> = {
  PENDING: "대기 중",
  ACCEPTED: "수락됨",
  EXPIRED: "만료됨",
  REVOKED: "취소됨",
};

function errorMessage(error: unknown): string {
  if (error instanceof ContractApiError) return error.envelope.message;
  return "요청을 처리하지 못했어요. 다시 시도해 주세요.";
}

export default function CareTeamPage() {
  const { babyId } = useParams<{ babyId: string }>();
  const real = useRealSession();
  if (real.status === "signed-in") return <RealCareTeam babyId={babyId} />;
  return <MockCareTeam babyId={babyId} />;
}

function RealCareTeam({ babyId }: Readonly<{ babyId: string }>) {
  const router = useRouter();
  const real = useRealSession();
  const scope = usePrivateScope();
  const draft = useDraft();
  const babies = useBabiesQuery(true);
  const members = useMembersQuery(babyId, true);
  const myAccess = findBabyAccess(babies.data, babyId);
  const isOwner = myAccess?.membership.role === "OWNER";
  const invites = useInvitesQuery(babyId, isOwner);
  const removeMembership = useRemoveMembershipMutation(babyId);
  const revokeInvite = useRevokeInviteMutation(babyId);
  const client = useApiClient();
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  const [leaveError, setLeaveError] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteIntent, setInviteIntent] = useState<
    | { kind: "create"; email: string; requestId: string }
    | { kind: "reissue"; inviteId: string; requestId: string }
    | null
  >(null);
  const [issued, setIssued] = useState<IssuedInvite | null>(null);

  if (babies.isLoading || members.isLoading) return <LoadingState label="구성원 정보를 불러오고 있어요" />;
  if (babies.isError || members.isError || !myAccess) {
    return <ErrorState label="구성원 정보를 불러오지 못했어요." retryable />;
  }

  const me = members.data?.items.find((entry) => entry.user_id === real.userId);

  async function leaveOrAttempt() {
    if (!real.userId || !me) return;
    setLeaveError(null);
    try {
      await removeMembership.mutateAsync({ userId: real.userId, version: me.version });
      // Only self-removal loses this browser's access. Removing someone else
      // must not discard the current caretaker's own scope or drafts.
      scope.set(real.userId, null);
      draft.clearBaby(babyId);
      router.push(`/account?baby_id=${babyId}`);
    } catch (error) {
      // OWNER가 시도하면 계약상 409 OWNER_REQUIRED가 그대로 여기로 온다 — 문구를 지어내지 않고 서버 메시지를 보여준다.
      setLeaveError(errorMessage(error));
    }
  }

  if (confirmingLeave) {
    return (
      <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-3 px-6">
        <ScreenSection title="이 아기에서 나갈까요?">
          <p className="text-sm text-muted-foreground">
            공유된 기존 기록은 남아 있지만 새 접근은 차단돼요. 다시 참여하려면 새 초대가 필요해요.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setConfirmingLeave(false)}
              className="min-h-11 rounded-md border border-border px-4 text-sm text-foreground"
            >
              취소
            </button>
            <button
              type="button"
              onClick={leaveOrAttempt}
              disabled={removeMembership.isPending}
              className="min-h-11 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              나가기 확정
            </button>
          </div>
        </ScreenSection>
      </main>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-foreground">공동양육 구성원</h1>

      <ScreenSection title="구성원">
        <ul className="flex flex-col gap-2">
          {members.data?.items.map((member) => (
            <li key={member.membership_id} className="flex items-center justify-between text-sm">
              <span className="text-foreground">
                {roleLabel[member.role]} · {member.display_name}
              </span>
              <div className="flex items-center gap-2">
                <span className={member.status === "ACTIVE" ? "text-primary" : "text-muted-foreground"}>
                  {statusLabel[member.status] ?? member.status}
                </span>
                {isOwner && member.status === "ACTIVE" && member.user_id !== real.userId && (
                  <button
                    type="button"
                    onClick={() => removeMembership.mutate({ userId: member.user_id, version: member.version })}
                    disabled={removeMembership.isPending}
                    className="min-h-11 rounded-md border border-destructive/40 px-2 text-xs text-destructive disabled:opacity-50"
                  >
                    제거
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
        {removeMembership.isError && !confirmingLeave && (
          <ErrorState label={errorMessage(removeMembership.error)} />
        )}
      </ScreenSection>

      {isOwner ? (
        <ScreenSection title="초대">
          {invites.isLoading && <LoadingState label="초대 내역을 불러오고 있어요" />}
          {invites.data?.items.length === 0 && (
            <p className="text-sm text-muted-foreground">이 아기의 초대 내역이 없어요.</p>
          )}
          <ul className="flex flex-col gap-2">
            {invites.data?.items.map((invite) => (
              <li key={invite.invite_id} className="flex items-center justify-between text-sm">
                <span className="text-foreground">
                  {invite.email} · {inviteStatusLabel[invite.status] ?? invite.status}
                </span>
                {invite.status === "PENDING" && (
                  <>
                    <button type="button"
                      onClick={() => {
                        setIssued(null);
                        setInviteIntent({ kind: "reissue", inviteId: invite.invite_id, requestId: newClientRequestId() });
                      }}
                      className="min-h-11 rounded-md border border-border px-2 text-xs text-foreground">
                      링크 재발급
                    </button>
                    <button
                      type="button"
                      onClick={() => revokeInvite.mutate({ inviteId: invite.invite_id, version: invite.version })}
                      disabled={revokeInvite.isPending}
                      className="min-h-11 rounded-md border border-destructive/40 px-2 text-xs text-destructive disabled:opacity-50"
                    >
                      취소
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
          {revokeInvite.isError && <ErrorState label={errorMessage(revokeInvite.error)} />}
          <form onSubmit={(event) => {
            event.preventDefault();
            setIssued(null);
            setInviteIntent({ kind: "create", email: inviteEmail.trim(), requestId: newClientRequestId() });
          }} className="flex flex-col gap-2">
            <label htmlFor="invite-email" className="text-sm text-foreground">초대할 보호자의 이메일</label>
            <input id="invite-email" type="email" required value={inviteEmail}
              onChange={(event) => setInviteEmail(event.target.value)}
              className="min-h-11 rounded-md border border-border bg-background px-3 text-sm" />
            <button type="submit" className="min-h-11 rounded-md border border-border px-4 text-sm">
              새 초대 준비
            </button>
          </form>
          {inviteIntent && (
            <ReauthenticationPanel key={inviteIntent.requestId} babyId={babyId}
              operation="CREATE_INVITE" title="초대 작업 재인증"
              onProof={async (proofToken) => {
                if (!client) throw new Error("Real API client is not configured.");
                const result = inviteIntent.kind === "create"
                  ? await createInvite(client, babyId, inviteIntent.email, proofToken, inviteIntent.requestId)
                  : await reissueInvite(client, babyId, inviteIntent.inviteId, proofToken, inviteIntent.requestId);
                setIssued(result);
                void invites.refetch();
              }} />
          )}
          {issued && (
            <div className="flex flex-col gap-2 rounded-md border border-border p-3">
              {issued.invite_url ? (
                <>
                  <p className="text-sm text-foreground">이 링크는 지금 한 번만 표시돼요. 지정한 이메일의 보호자에게 직접 전달하세요.</p>
                  <input aria-label="초대 링크" readOnly value={issued.invite_url}
                    className="min-h-11 w-full rounded-md border border-border bg-background px-3 text-sm" />
                </>
              ) : (
                <p className="text-sm text-foreground">
                  발급 결과는 복구했지만 링크 원문은 다시 받을 수 없어요. 위 목록에서 링크를 재발급하세요.
                </p>
              )}
              <button type="button" onClick={() => { setIssued(null); setInviteIntent(null); }}
                className="min-h-11 rounded-md border border-border px-3 text-sm">링크 숨기기</button>
            </div>
          )}
        </ScreenSection>
      ) : (
        <ScreenSection title="참여 관리">
          <p className="text-sm text-muted-foreground">공동 보호자는 초대를 발급하거나 구성원을 제거할 수 없어요.</p>
          <button
            type="button"
            onClick={() => setConfirmingLeave(true)}
            className="min-h-11 rounded-md border border-border px-4 text-sm text-foreground"
          >
            이 아기에서 나가기
          </button>
        </ScreenSection>
      )}

      {isOwner && (
        <ScreenSection title="관리 보호자 나가기">
          <button
            type="button"
            onClick={leaveOrAttempt}
            disabled={removeMembership.isPending}
            className="min-h-11 w-fit rounded-md border border-border px-4 text-sm text-foreground disabled:opacity-50"
          >
            이 아기에서 나가기 시도
          </button>
          {leaveError && (
            <>
              <ErrorState label={leaveError} retryable={false} />
              <p className="text-xs text-muted-foreground">대신 설정의 아기 전체 삭제 경로를 사용해요.</p>
            </>
          )}
        </ScreenSection>
      )}
    </div>
  );
}

const invitePreviewLinks = [
  { token: "demo-accept", label: "정상 수락" },
  { token: "demo-wrong-email", label: "이메일 불일치 (403)" },
  { token: "demo-expired", label: "만료된 링크 (410)" },
  { token: "demo-used", label: "이미 사용됨 (409)" },
] as const;

function MockCareTeam({ babyId }: Readonly<{ babyId: string }>) {
  const router = useRouter();
  const draft = useDraft();
  const session = useMockSessionOptional();
  const membership = session?.membershipFor(babyId) ?? null;
  const members = mockRoleAssignments.filter((entry) => entry.baby_id === babyId);
  const issuedInviteFixture = mockIssuedInvite();
  const issuedInvite = isForBaby(babyId, issuedInviteFixture.invite) ? issuedInviteFixture : null;
  const [ownerLeaveBlocked, setOwnerLeaveBlocked] = useState(false);
  const [confirmingLeave, setConfirmingLeave] = useState(false);

  if (!session || !membership) return null;

  // 실제로 멤버십을 지우는 건 확정 클릭 시점뿐이다 — 먼저 지우면 이 화면을
  // 감싼 레이아웃의 실시간 권한 가드가 즉시 404로 바꿔버려 결과를 보여줄 틈이 없다.
  function confirmLeave() {
    session!.leaveBaby(babyId);
    draft.clearBaby(babyId);
    router.push("/login");
  }

  if (confirmingLeave) {
    const leftPreview = getMockScenario("member_left").response.body as { status: string };
    return (
      <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-3 px-6">
        <ScreenSection title="이 아기에서 나갈까요?">
          <p className="text-sm text-foreground">나가면 상태가 {leftPreview.status}(으)로 바뀌어요.</p>
          <p className="text-sm text-muted-foreground">
            공유된 기존 기록은 남아 있지만 새 접근은 차단돼요. 다시 참여하려면 새 초대가 필요해요.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setConfirmingLeave(false)}
              className="min-h-11 rounded-md border border-border px-4 text-sm text-foreground"
            >
              취소
            </button>
            <button
              type="button"
              onClick={confirmLeave}
              className="min-h-11 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
            >
              나가기 확정
            </button>
          </div>
        </ScreenSection>
      </main>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-foreground">공동양육 구성원</h1>

      <ScreenSection title="구성원">
        <ul className="flex flex-col gap-2">
          {members.map((member) => (
            <li key={member.user_id} className="flex items-center justify-between text-sm">
              <span className="text-foreground">
                {roleLabel[member.role]} · {member.user_id.slice(0, 8)}…
              </span>
              <span className={member.status === "ACTIVE" ? "text-primary" : "text-muted-foreground"}>
                {statusLabel[member.status]}
              </span>
            </li>
          ))}
        </ul>
      </ScreenSection>

      {membership.role === "OWNER" ? (
        <ScreenSection title="초대 발급">
          {issuedInvite ? (
            <p className="text-sm text-foreground">{issuedInvite.invite.email}로 24시간 유효한 링크를 만들었어요.</p>
          ) : (
            <p className="text-sm text-muted-foreground">이 아기의 초대 내역이 없어요.</p>
          )}
          <p className="text-xs text-muted-foreground">링크는 최초 한 번만 보여줘요. 잃어버리면 재발급해야 해요.</p>
          <button type="button" disabled className="min-h-11 rounded-md border border-border px-4 text-sm text-muted-foreground">
            새 초대 발급 (실제 API 연동 이후)
          </button>
          <div className="flex flex-col gap-2 border-t border-border pt-3">
            <p className="text-sm font-medium text-foreground">수락 화면 미리보기</p>
            <ul className="flex flex-wrap gap-2">
              {invitePreviewLinks.map((link) => (
                <li key={link.token}>
                  {/* 일반 앵커: 진짜 초대 링크처럼 전체 탐색으로 열어야 마운트 시점에
                      fragment를 안정적으로 읽는다(클라이언트 라우터는 해시를 늦게 반영한다). */}
                  <a
                    href={`/invite/accept#token=${link.token}`}
                    className="flex min-h-11 items-center rounded-md border border-border px-3 text-xs text-foreground hover:border-primary"
                  >
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </ScreenSection>
      ) : (
        <ScreenSection title="참여 관리">
          <p className="text-sm text-muted-foreground">공동 보호자는 초대를 발급하거나 구성원을 제거할 수 없어요.</p>
          <button
            type="button"
            onClick={() => setConfirmingLeave(true)}
            className="min-h-11 rounded-md border border-border px-4 text-sm text-foreground"
          >
            이 아기에서 나가기
          </button>
        </ScreenSection>
      )}

      {membership.role === "OWNER" && (
        <ScreenSection title="관리 보호자 나가기">
          <button
            type="button"
            onClick={() => setOwnerLeaveBlocked(true)}
            className="min-h-11 w-fit rounded-md border border-border px-4 text-sm text-foreground"
          >
            이 아기에서 나가기 시도 (예시)
          </button>
          {ownerLeaveBlocked && (
            <>
              <ErrorState label={getMockScenario("owner_cannot_leave").expected_ui} retryable={false} />
              <p className="text-xs text-muted-foreground">대신 설정의 아기 전체 삭제 경로를 사용해요.</p>
            </>
          )}
        </ScreenSection>
      )}
    </div>
  );
}

"use client";

// SC10 공동양육 — 구성원·관계·권한·초대 및 참여 상태. 실제 발급·제거 API는
// B 연동 후 A-03 후속에서 붙이고, 여기서는 계약 fixture로 상태 전이만
// 보인다: CAREGIVER 나가기(LEFT), OWNER 나가기 차단(409 OWNER_REQUIRED).
import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { mockRoleAssignments, getMockScenario } from "@/lib/mock/fixtures";
import { useMockSession } from "@/lib/mock/session";
import { ScreenSection, ErrorState } from "@/components/screen-state";

const roleLabel: Record<string, string> = { OWNER: "관리 보호자", CAREGIVER: "공동 보호자" };
const statusLabel: Record<string, string> = { ACTIVE: "활동 중", REVOKED: "제거됨" };

const invitePreviewLinks = [
  { token: "demo-accept", label: "정상 수락" },
  { token: "demo-wrong-email", label: "이메일 불일치 (403)" },
  { token: "demo-expired", label: "만료된 링크 (410)" },
  { token: "demo-used", label: "이미 사용됨 (409)" },
] as const;

export default function CareTeamPage() {
  const { babyId } = useParams<{ babyId: string }>();
  const router = useRouter();
  const session = useMockSession();
  const membership = session.membershipFor(babyId);
  const members = mockRoleAssignments.filter((entry) => entry.baby_id === babyId);
  const issuedInvite = getMockScenario("invite_issued").response.body as {
    invite: { email: string; status: string; expires_at: string };
  };
  const [ownerLeaveBlocked, setOwnerLeaveBlocked] = useState(false);
  const [confirmingLeave, setConfirmingLeave] = useState(false);

  if (!membership) return null;

  // 실제로 멤버십을 지우는 건 확정 클릭 시점뿐이다 — 먼저 지우면 이 화면을
  // 감싼 레이아웃의 실시간 권한 가드가 즉시 404로 바꿔버려 결과를 보여줄 틈이 없다.
  function confirmLeave() {
    session.leaveBaby(babyId);
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
          <p className="text-sm text-foreground">{issuedInvite.invite.email}로 24시간 유효한 링크를 만들었어요.</p>
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

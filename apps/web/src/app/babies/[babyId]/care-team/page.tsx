"use client";

// SC10 공동양육 — 구성원·관계·권한·초대 및 참여 상태. 발급·취소·제거의
// 실제 호출은 A-03에서 연결하며, 여기서는 목 응답으로 상태 구분만 보인다.
import { useParams } from "next/navigation";
import { mockRoleAssignments, getMockScenario } from "@/lib/mock/fixtures";
import { useMockSession } from "@/lib/mock/session";
import { ScreenSection } from "@/components/screen-state";

const roleLabel: Record<string, string> = { OWNER: "관리 보호자", CAREGIVER: "공동 보호자" };
const statusLabel: Record<string, string> = { ACTIVE: "활동 중", REVOKED: "제거됨" };

export default function CareTeamPage() {
  const { babyId } = useParams<{ babyId: string }>();
  const session = useMockSession();
  const membership = session.membershipFor(babyId);
  const members = mockRoleAssignments.filter((entry) => entry.baby_id === babyId);
  const issuedInvite = getMockScenario("invite_issued").response.body as {
    invite: { email: string; status: string; expires_at: string };
  };

  if (!membership) return null;

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
        </ScreenSection>
      ) : (
        <ScreenSection title="참여 관리">
          <p className="text-sm text-muted-foreground">공동 보호자는 초대를 발급하거나 구성원을 제거할 수 없어요.</p>
          <button type="button" disabled className="min-h-11 rounded-md border border-border px-4 text-sm text-foreground">
            이 아기에서 나가기
          </button>
        </ScreenSection>
      )}

      {membership.role === "OWNER" && (
        <p className="text-xs text-muted-foreground">
          {getMockScenario("owner_cannot_leave").expected_ui} — 관리 보호자는 대신 아기 전체 삭제 경로를 사용해요.
        </p>
      )}
    </div>
  );
}

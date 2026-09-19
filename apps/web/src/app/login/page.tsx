"use client";

// SC01 시작 — 개발계약 §12 시험 사용자 배치를 사용한 목 화면 이동 셸.
// 실제 Supabase 이메일 OTP 로그인은 A-03에서 연결한다. 여기서는 픽커로
// 고른 시험 사용자의 ACTIVE 멤버십만으로 SC02 홈까지 이동 경로를 보인다.
import { useRouter } from "next/navigation";
import { useMockSession } from "@/lib/mock/session";
import {
  activeMembershipsFor,
  getMockScenario,
  mockNotice,
  mockTestUsers,
  type MockTestUserAlias,
} from "@/lib/mock/fixtures";
import { ScreenSection } from "@/components/screen-state";

// 개발계약 12장 표에서 그대로 가져온 시험 목적 설명. 화면에서 새로 지어내지 않는다.
const purposeByAlias: Record<MockTestUserAlias, string> = {
  owner_a: "관리 권한, 다른 아기로 전환",
  caregiver_a: "공동 입력, 타인 수정·관리 403",
  owner_b: "아기 간 격리·404",
  invited_a: "정상 수락·이메일 불일치·만료",
  removed_a: "권한 회수와 탈퇴 후 철회·삭제",
};

const inviteOutcomeScenarios = ["invite_accepted", "invite_expired", "invite_wrong_email", "invite_used"] as const;

export default function LoginPage() {
  const router = useRouter();
  const session = useMockSession();

  function handleSelect(alias: MockTestUserAlias) {
    const user = mockTestUsers.find((entry) => entry.alias === alias);
    if (!user) return;
    session.signIn(alias);
    const memberships = activeMembershipsFor(user.user_id);
    if (memberships.length > 0) {
      router.push(`/babies/${memberships[0]!.baby_id}`);
    }
  }

  const selectedAlias = session.alias;
  const selectedHasNoBaby = selectedAlias !== null && session.activeMemberships.length === 0;

  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col gap-6 px-6 py-10">
      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium text-primary">아기 돌봄 도우미</p>
        <h1 className="text-2xl font-semibold tracking-tight">시작하기</h1>
        <p className="text-sm text-muted-foreground">
          로그인 아기 정보 분석 동의를 완료하면 홈으로 이동합니다. 지금은 실제 로그인 대신 계약의 시험 사용자로
          화면 이동을 확인합니다.
        </p>
      </div>

      <ScreenSection title="개발용 화면 전환 (실제 로그인 아님)">
        <p className="text-xs text-muted-foreground">{mockNotice}</p>
        <ul className="flex flex-col gap-2">
          {mockTestUsers.map((user) => (
            <li key={user.alias}>
              <button
                type="button"
                onClick={() => handleSelect(user.alias)}
                aria-pressed={selectedAlias === user.alias}
                className="flex min-h-11 w-full flex-col items-start gap-0.5 rounded-md border border-border bg-background px-3 py-2 text-left hover:border-primary"
              >
                <span className="text-sm font-medium text-foreground">{user.alias}</span>
                <span className="text-xs text-muted-foreground">{purposeByAlias[user.alias]}</span>
              </button>
            </li>
          ))}
        </ul>
      </ScreenSection>

      {selectedHasNoBaby && selectedAlias === "invited_a" && (
        <ScreenSection title="참여한 아기가 없어요">
          <p className="text-sm text-muted-foreground">
            초대 링크를 받으면 로그인과 이메일 확인 후 공유 범위를 수락할 수 있어요. 아래는 계약에 정의된 수락
            결과 미리보기입니다.
          </p>
          <ul className="flex flex-col gap-2">
            {inviteOutcomeScenarios.map((name) => {
              const scenario = getMockScenario(name);
              return (
                <li key={name} className="rounded-md border border-border px-3 py-2 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">{scenario.expected_ui}</span>
                </li>
              );
            })}
          </ul>
        </ScreenSection>
      )}

      {selectedHasNoBaby && selectedAlias === "removed_a" && (
        <ScreenSection title="이 아기의 접근 권한이 없어요">
          <p className="text-sm text-muted-foreground">
            공동 기록에는 다시 접근할 수 없지만, 본인의 학습 동의 철회와 본인이 신청한 삭제 작업 조회는 계속할 수
            있어요.
          </p>
          <p className="rounded-md border border-border px-3 py-2 text-xs text-muted-foreground">
            {getMockScenario("consent_revoke_after_leave").expected_ui}
          </p>
        </ScreenSection>
      )}
    </main>
  );
}

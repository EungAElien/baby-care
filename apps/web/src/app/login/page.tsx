"use client";

// SC01 시작. 실제 Supabase 이메일 OTP 로그인이 기본 화면이다(A-03).
// NEXT_PUBLIC_ENABLE_MOCK_NAV가 켜진 빌드에서만 계약 §12 시험 사용자
// 피커를 추가로 보여준다 — 실제 로그인 없이 SC01~SC10 화면 이동만
// 미리 보고 싶을 때 쓰는 개발자 전용 경로다.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRealSession } from "@/lib/auth/real-session";
import { useMockSession } from "@/lib/mock/session";
import { isMockNavEnabled } from "@/lib/mock/config";
import {
  activeMembershipsFor,
  mockNotice,
  mockTestUsers,
  type MockTestUserAlias,
} from "@/lib/mock/fixtures";
import { ScreenSection } from "@/components/screen-state";
import { RealLogin } from "./real-login";

const inviteOutcomeLinks = [
  { token: "demo-accept", label: "정상 수락" },
  { token: "demo-wrong-email", label: "이메일 불일치" },
  { token: "demo-expired", label: "만료된 링크" },
  { token: "demo-used", label: "이미 사용됨" },
] as const;

// 개발계약 12장 표에서 그대로 가져온 시험 목적 설명. 화면에서 새로 지어내지 않는다.
const purposeByAlias: Record<MockTestUserAlias, string> = {
  owner_a: "관리 권한, 다른 아기로 전환",
  caregiver_a: "공동 입력, 타인 수정·관리 403",
  owner_b: "아기 간 격리·404",
  invited_a: "정상 수락·이메일 불일치·만료",
  removed_a: "권한 회수와 탈퇴 후 철회·삭제",
};

export default function LoginPage() {
  const realSession = useRealSession();
  const mockNavEnabled = isMockNavEnabled();

  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col gap-6 px-6 py-10">
      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium text-primary">아기 돌봄 도우미</p>
        <h1 className="text-2xl font-semibold tracking-tight">시작하기</h1>
        <p className="text-sm text-muted-foreground">
          로그인 아기 정보 분석 동의를 완료하면 홈으로 이동합니다.
        </p>
      </div>

      {realSession.configured ? (
        <RealLogin />
      ) : (
        !mockNavEnabled && (
          <ScreenSection title="환경 설정이 필요해요">
            <p className="text-sm text-muted-foreground">
              실제 Supabase 주소·키가 설정되지 않았어요. `.env.local`을 확인하거나, 로컬 화면 확인만 필요하면
              `NEXT_PUBLIC_ENABLE_MOCK_NAV=true`로 목 화면을 켜세요.
            </p>
          </ScreenSection>
        )
      )}

      {mockNavEnabled && <MockLoginPreview />}
    </main>
  );
}

function MockLoginPreview() {
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
    <details className="rounded-lg border border-dashed border-border p-4">
      <summary className="cursor-pointer text-sm font-medium text-muted-foreground">
        개발자용: 목 사용자로 미리보기 (실제 로그인 아님)
      </summary>
      <div className="mt-4 flex flex-col gap-4">
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
              초대 링크를 받으면 로그인과 이메일 확인 후 공유 범위를 수락할 수 있어요. 아래는 링크를 열었을 때의
              결과를 계약 fixture로 미리 보는 화면입니다.
            </p>
            <ul className="flex flex-wrap gap-2">
              {inviteOutcomeLinks.map((link) => (
                <li key={link.token}>
                  {/* 일반 앵커로 전체 탐색 — 클라이언트 라우터는 해시 반영이 늦어
                      마운트 시점에 fragment를 놓칠 수 있다. */}
                  <a
                    href={`/invite/accept#token=${link.token}`}
                    className="flex min-h-11 items-center rounded-md border border-border px-3 text-xs text-foreground hover:border-primary"
                  >
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
            <button type="button" disabled className="min-h-11 w-fit rounded-md border border-border px-4 text-sm text-muted-foreground">
              새 아기 만들기 (실제 API 연동 이후)
            </button>
          </ScreenSection>
        )}

        {selectedHasNoBaby && selectedAlias === "removed_a" && (
          <ScreenSection title="이 아기의 접근 권한이 없어요">
            <p className="text-sm text-muted-foreground">
              공동 기록에는 다시 접근할 수 없지만, 본인의 학습 동의 철회와 본인이 신청한 삭제 작업 조회는 계속할 수
              있어요.
            </p>
            <Link href="/account" className="text-sm font-medium text-primary">
              내 계정 관리로 이동
            </Link>
          </ScreenSection>
        )}
      </div>
    </details>
  );
}

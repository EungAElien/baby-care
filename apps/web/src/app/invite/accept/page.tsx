"use client";

// SC10 진입: "설정 또는 초대 링크". 개발계약 §3 초대 절과 A-03 ②의 토큰
// 취급 요구사항을 따른다 — 토큰은 URL fragment(#token=...)로만 받고, 읽는
// 즉시 주소창에서 지워 referrer·분석 이벤트·서버 로그·로그인 리디렉션에
// 남기지 않는다. 탭 메모리에만 보관하며 sessionStorage 등에 쓰지 않는다.
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMockSessionOptional } from "@/lib/mock/session";
import { getMockScenario, mockAcceptedInvite, mockTestUsers, type MockScenario } from "@/lib/mock/fixtures";
import { ScreenSection, ErrorState, PermissionState } from "@/components/screen-state";

// 실제 토큰은 서버가 발급한 난수다. 여기서는 목 시나리오를 고르기 위한
// 개발용 별칭만 인식하고, 그 밖의 값은 모두 "알 수 없는 링크"로 취급한다.
const demoTokenScenarios = {
  "demo-accept": "invite_accepted",
  "demo-expired": "invite_expired",
  "demo-wrong-email": "invite_wrong_email",
  "demo-used": "invite_used",
} as const;

type DemoToken = keyof typeof demoTokenScenarios;

// Exported only so the prototype-pollution regression can be unit-tested without a DOM renderer.
export function isDemoToken(value: string): value is DemoToken {
  // `in` also matches inherited keys (constructor, toString, __proto__), so
  // #token=constructor would pass and crash getMockScenario downstream.
  // hasOwn only accepts the four demo tokens defined above.
  return Object.hasOwn(demoTokenScenarios, value);
}

export default function AcceptInvitePage() {
  const router = useRouter();
  const session = useMockSessionOptional();
  const [tokenState, setTokenState] = useState<{ read: boolean; token: string | null }>({ read: false, token: null });
  const [accepted, setAccepted] = useState(false);
  const consumed = useRef(false);

  useEffect(() => {
    // 개발 모드 StrictMode는 이 effect를 두 번 실행한다. 해시를 읽고 지우는
    // 동작은 멱등이 아니므로(두 번째 실행 때는 이미 비어 있다) 첫 실행만
    // 반영하도록 가드한다.
    if (consumed.current) return;
    consumed.current = true;
    const hash = window.location.hash.replace(/^#/, "");
    const params = new URLSearchParams(hash);
    const value = params.get("token");
    // 읽자마자 제거 — 뒤로가기·새로고침·공유 링크에도 토큰이 남지 않게 한다.
    window.history.replaceState(null, "", window.location.pathname);
    // window.location은 React 상태가 아닌 브라우저 값이라 마운트 시 한 번
    // 읽어 동기화해야 한다 — 렌더 중에는 SSR에서 window가 없어 읽을 수 없다.
    setTokenState({ read: true, token: value });
  }, []);

  if (!tokenState.read) return null;
  const token = tokenState.token;

  if (!session) {
    return (
      <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-4 px-6">
        <ScreenSection title="초대 수락은 아직 실제 API에 연결되지 않았어요">
          <p className="text-sm text-muted-foreground">
            이 미리보기는 개발용 목 화면 전환(NEXT_PUBLIC_ENABLE_MOCK_NAV)에서만 열 수 있어요.
          </p>
          <Link href="/login" className="text-sm font-medium text-primary">
            로그인 화면으로
          </Link>
        </ScreenSection>
      </main>
    );
  }

  if (!session.alias) {
    return (
      <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-4 px-6">
        <ScreenSection title="로그인이 필요해요">
          <p className="text-sm text-muted-foreground">
            초대를 수락하려면 먼저 로그인해 주세요. 실제 서비스는 이메일 OTP로 로그인하지만, 지금은 계약의 시험
            사용자로 화면 이동을 확인합니다.
          </p>
          <ul className="flex flex-col gap-2">
            {mockTestUsers.map((user) => (
              <li key={user.alias}>
                <button
                  type="button"
                  onClick={() => session.signIn(user.alias)}
                  className="flex min-h-11 w-full items-center rounded-md border border-border bg-background px-3 text-left text-sm text-foreground hover:border-primary"
                >
                  {user.alias}
                </button>
              </li>
            ))}
          </ul>
        </ScreenSection>
      </main>
    );
  }

  if (!token || !isDemoToken(token)) {
    return (
      <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-4 px-6">
        <ErrorState label="유효한 초대 링크가 아니에요." retryable={false} />
        <p className="text-sm text-muted-foreground">관리 보호자에게 새 링크를 요청해 주세요.</p>
      </main>
    );
  }

  const scenario = getMockScenario(demoTokenScenarios[token]) as MockScenario;
  const acceptedFixture = mockAcceptedInvite();
  // 계약: 지정 이메일이 다른 사용자는 수락할 수 없다(invite_wrong_email). 초대의
  // 실제 수신자는 fixture의 membership.user_id(invited_a)이며, 로그인한 시험
  // 계정이 다르면 정상 토큰이라도 수락을 막아야 한다 — PR #11 리뷰 필수 수정.
  const recipientMismatch = token === "demo-accept" && session.userId !== acceptedFixture.membership.user_id;

  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-4 px-6">
      <h1 className="text-lg font-semibold text-foreground">초대 수락</h1>

      {recipientMismatch && (
        <PermissionState label={getMockScenario("invite_wrong_email").expected_ui} />
      )}

      {token === "demo-accept" && !recipientMismatch && !accepted && (
        <ScreenSection title="공유 범위를 수락할까요?">
          <p className="text-sm text-muted-foreground">{scenario.expected_ui}</p>
          <button
            type="button"
            onClick={() => {
              session.acceptInvite(acceptedFixture.baby.baby_id, "CAREGIVER");
              setAccepted(true);
            }}
            className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
          >
            수락하기
          </button>
        </ScreenSection>
      )}

      {token === "demo-accept" && !recipientMismatch && accepted && (
        <ScreenSection title="참여했어요">
          <p className="text-sm text-foreground">공유 범위를 수락했어요. 아기 홈으로 이동할 수 있어요.</p>
          <button
            type="button"
            onClick={() => router.push(`/babies/${acceptedFixture.baby.baby_id}`)}
            className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
          >
            아기 홈으로 이동
          </button>
        </ScreenSection>
      )}

      {token === "demo-wrong-email" && <PermissionState label={scenario.expected_ui} />}

      {(token === "demo-expired" || token === "demo-used") && (
        <ErrorState label={scenario.expected_ui} retryable={false} />
      )}
    </main>
  );
}

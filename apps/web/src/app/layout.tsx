import type { Metadata } from "next";
import { AppProviders } from "@/components/app-providers";
import { RealSessionProvider } from "@/lib/auth/real-session";
import { MockSessionProvider } from "@/lib/mock/session";
import { isMockNavEnabled } from "@/lib/mock/config";
import "./globals.css";

export const metadata: Metadata = {
  title: "아기 돌봄 도우미",
  description: "아기 돌봄 기록을 위한 웹 앱",
};

// 목 세션이 꺼진 빌드에서는 /login·/babies/*를 정적으로 미리 생성하지
// 않는다 — provider 없이 프리렌더하면 실패하고, 프리렌더된 HTML에 목
// 데이터가 남는 것도 개발계약 §11이 금지한다. 미들웨어가 실제 요청은 먼저
// 차단하므로 이 라우트들은 프로덕션에서 렌더링될 일이 없다.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // 개발계약 §11: 목 세션은 명시적 개발/미리보기 설정에서만 연결한다(기본
  // 꺼짐). 미들웨어가 이 값이 꺼졌을 때 /login·/babies/* 직접 접근도 막는다.
  // RealSessionProvider(A-03 실제 Supabase 로그인)는 항상 연결한다 — 실제
  // 환경변수가 없으면 signed-out 상태로만 남고 네트워크를 건드리지 않는다.
  const body = isMockNavEnabled() ? <MockSessionProvider>{children}</MockSessionProvider> : children;

  return (
    <html lang="ko">
      <body>
        <AppProviders>
          <RealSessionProvider>{body}</RealSessionProvider>
        </AppProviders>
      </body>
    </html>
  );
}

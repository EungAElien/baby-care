import type { Metadata, Viewport } from "next";
import { AppProviders } from "@/components/app-providers";
import { RealSessionProvider } from "@/lib/auth/real-session";
import { MockSessionProvider } from "@/lib/mock/session";
import { isMockNavEnabled } from "@/lib/mock/config";
import "./globals.css";

export const metadata: Metadata = {
  title: "아기 돌봄 도우미",
  description: "아기 돌봄 기록을 위한 웹 앱",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#FF9398",
};

// Request-specific CSP nonces and private pages require dynamic rendering.
export const dynamic = "force-dynamic";

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // 개발계약 §11: 목 세션은 명시적 개발/미리보기 설정에서만 연결한다(기본
  // 꺼짐). 각 목 전용 화면도 이 설정을 확인하며 실제 인증과 분리한다.
  // RealSessionProvider(A-03 실제 Supabase 로그인)는 항상 연결한다 — 실제
  // 환경변수가 없으면 signed-out 상태로만 남고 네트워크를 건드리지 않는다.
  const body = isMockNavEnabled() ? (
    <MockSessionProvider>{children}</MockSessionProvider>
  ) : (
    children
  );

  return (
    <html lang="ko" data-seed data-seed-color-mode="light-only">
      <body>
        <AppProviders>
          <RealSessionProvider>{body}</RealSessionProvider>
        </AppProviders>
      </body>
    </html>
  );
}

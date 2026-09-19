import { NextResponse } from "next/server";
import { isMockNavEnabled } from "@/lib/mock/config";

// 목 화면(`/login`, `/babies/*`)은 NEXT_PUBLIC_ENABLE_MOCK_NAV가 켜진
// 빌드에서만 접근 가능해야 한다 — 직접 URL 접근도 예외가 아니다.
export function middleware(): NextResponse {
  if (!isMockNavEnabled()) {
    return new NextResponse(null, { status: 404 });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/login", "/login/:path*", "/babies", "/babies/:path*"],
};

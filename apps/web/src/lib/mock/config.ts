/**
 * SC01~SC10 목 세션·화면(`MockSessionProvider`, `/login`, `/babies/*`)은
 * 개발계약 §11 "개발 환경 설정으로만 목 활성화"에 따라 기본값이 꺼짐이다.
 * 로컬에서 목 화면 이동을 보려면 `.env.local`에
 * `NEXT_PUBLIC_ENABLE_MOCK_NAV=true`를 설정한다. 프로덕션 빌드는 이 값을
 * 설정하지 않으므로 provider도, 해당 라우트로의 직접 URL 접근도 비활성이다.
 */
export function isMockNavEnabled(): boolean {
  return process.env.NEXT_PUBLIC_ENABLE_MOCK_NAV === "true";
}

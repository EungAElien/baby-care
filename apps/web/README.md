# A-01·A-03 웹 기반

이 폴더는 A-01(①·②)과 A-03 범위를 구현합니다. A-01 ①은 Next.js App Router/TypeScript/Tailwind/shadcn 설정, React Hook Form·Zod·TanStack Query 의존성, `openapi계약.json`에서 생성한 타입과 브라우저 사용자 Bearer용 API 클라이언트입니다. A-01 ②는 SC01~SC10 라우트 껍데기·공통 모바일 레이아웃과, 계약의 합성 fixture로 화면 사이를 이동해 보는 목 네비게이션입니다. A-03은 B-04 인계를 받아 **실제** Supabase 이메일 OTP 로그인과 아기 목록/생성/전환을 연결했고, 초대·탈퇴·동의·아기 전환 시 미저장 초안 처리는 아직 목 세션입니다. 배포는 포함하지 않습니다.

## 로컬 검증

```powershell
npm.cmd ci
npm.cmd run generate:api
npm.cmd run generate:fixtures
npm.cmd run typecheck
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

`src/lib/api/generated.d.ts`는 저장소의 `contracts/openapi계약.json`에서 생성합니다. `src/lib/mock/fixtures.json`은 `contracts/목 응답과 시험 사용자 배치.json`을 그대로 복사한 것입니다. 두 계약 파일이 바뀌면 B와 합의 후 각 `generate:*` 스크립트로 다시 생성·검증하세요. 자동 테스트는 `contracts/목 응답과 시험 사용자 배치.json`을 동일 클라이언트의 주입 가능한 `fetch`에 연결하고, 복사본이 원본과 바이트 단위로 같은지도 확인합니다. 합성 fixture는 운영 코드나 오류 대체 경로에 포함되지 않습니다.

## 실제 로그인과 아기 연동 (A-03, B-04)

`/login`은 이제 실제 Supabase 이메일 OTP 폼이 기본 화면입니다. `NEXT_PUBLIC_SUPABASE_URL`·`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`·`NEXT_PUBLIC_API_BASE_URL`이 유효한 형식으로 설정돼 있어야 폼이 동작을 시도합니다(형식이 안 맞으면 "환경 설정이 필요해요"만 보여주고 네트워크를 건드리지 않습니다). B-04 인계(`docs/handoffs/b04-account-shared-care-records.md`)의 로컬 스택을 띄우면:

```bash
# 저장소 루트에서 (B-04 인계 문서 참고)
cd apps/api && python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install --require-hashes --no-deps -r requirements-dev.lock
cd ../.. && npm ci && npm run supabase:start && npm run supabase:reset
./scripts/run-b04-local-api.sh
```

그 뒤 `apps/web/.env.local`에 로컬 FastAPI(`http://127.0.0.1:8080/v1`)와 `npx supabase status`가 알려주는 로컬 Supabase URL·anon key를 넣으면 실제 OTP 로그인 → 아기 목록 조회/생성 → 전환까지 실제로 동작합니다. 로그인 후 아기가 0개면 생성 폼, 1개면 자동 이동, 2개 이상이면 선택 목록을 보여주고 매번 `PUT /v1/me/active-baby`로 다음 로그인 기본값을 저장합니다.

**이 저장소의 Codex 실행 환경에는 Docker·Python 3.12·Supabase CLI가 없어 위 스택을 직접 띄워 실기기 검증을 하지 못했습니다.** `typecheck`/`lint`/`test`/`build`와 가짜 도메인(`*.example.invalid`)으로의 오류 처리 확인까지만 했습니다 — 실제 OTP 왕복·아기 생성·전환은 A가 로컬 스택에서 확인해야 합니다.

초대·탈퇴·동의·아기 전환 시 미저장 초안 처리는 아직 실제 API에 연결하지 않았고, 아래 목 세션으로만 확인할 수 있습니다.

## SC01~SC10 목 화면 이동 (A-01 ②)

목 세션·화면은 기본적으로 꺼져 있습니다(개발계약 §11 "개발 환경 설정으로만 목 활성화"). `.env.local`에 `NEXT_PUBLIC_ENABLE_MOCK_NAV=true`를 설정해야 `npm run dev`의 `/login`에 시험 사용자 피커가 추가로 보입니다. 값이 없으면 루트 레이아웃이 `MockSessionProvider`를 아예 연결하지 않아 목 관련 훅·데이터가 프로덕션 빌드에 전혀 로드되지 않습니다. `/login`·`/babies/*` 자체는 실제 로그인 경로이므로 항상 열려 있습니다(과거에는 미들웨어로 두 경로 전체를 막았지만, 실제 로그인을 그 경로에 붙인 뒤로는 막으면 안 되는 실사용자까지 막게 되어 삭제했습니다).

설정 후 `/login`에서 계약 §12의 시험 사용자(`owner_a`·`caregiver_a`·`owner_b`·`invited_a`·`removed_a`) 중 하나를 고르면 그 사용자의 **ACTIVE 멤버십만으로** 화면이 이동합니다(실제 로그인 폼 아래 접이식 "개발자용: 목 사용자로 미리보기"). 이것은 개발용 화면 전환이며 실제 로그인이 아닙니다 — 새로고침하면 세션이 사라지고 `/login`으로 돌아갑니다(영구 저장하지 않는 것은 의도된 동작입니다). 같은 탭에서 실제로 로그인한 상태라면 `babies/[babyId]`는 항상 실제 세션을 우선합니다.

| 화면 | 경로 | 비고 |
| --- | --- | --- |
| SC01 시작 | `/login` | 시험 사용자 선택, `invited_a`/`removed_a`는 참여 아기가 없는 상태를 보여줌 |
| SC02 홈 | `/babies/[babyId]` | 최근 확인 상태·최근 기록·빠른 진입 |
| SC03 감지와 녹음 | `/babies/[babyId]/detect` | 마이크 상태 셸 + SC04 4개 상태 미리보기 링크 |
| SC04 결과 | `/babies/[babyId]/results/[scenario]` | `scenario` ∈ `analysis_running`\|`analysis_complete_stub`\|`analysis_abstain`\|`analysis_no_cry`\|`analysis_failed` |
| SC05 빠른 기록 | `/babies/[babyId]/quick-record` | 선택지/자연어 입력 셸 |
| SC06 타임라인 | `/babies/[babyId]/timeline` | 저장 성공·버전 충돌 두 기록 예시 |
| SC07 요약과 준비 | `/babies/[babyId]/summary` | `?state=unknown-amount`로 양 모름 상태 전환 |
| SC08 설정 | `/babies/[babyId]/settings` | `?deletion=accepted\|failed\|complete`로 삭제 작업 상태 전환 |
| SC09 내용 확인 | `/babies/[babyId]/entries/[scenario]` | `scenario` ∈ `review`\|`running`\|`stale`\|`failure` |
| SC10 공동양육 | `/babies/[babyId]/care-team` | OWNER만 초대 발급 섹션 노출 |

실제 저장·업로드·분석 실행·상담은 연결하지 않았습니다. 화면에 쓰인 값은 모두 `data_origin=DEMO`/`inference_mode=STUB`이며 각 화면에 `SourceBadge`로 표시합니다.

## 공동양육·초대·개인 초안 (A-03)

| 화면/기능 | 경로 | 비고 |
| --- | --- | --- |
| 초대 수락 | `/invite/accept#token=<demo-accept\|demo-expired\|demo-wrong-email\|demo-used>` | 토큰은 fragment로만 받고 마운트 즉시 주소창에서 지운다. 수락하면 실제로 목 세션에 CAREGIVER 멤버십이 더해진다(탭 메모리, 재로그인 시 초기화). |
| 공동양육 나가기 | `/babies/[babyId]/care-team` | CAREGIVER는 확정 클릭 후 `/login`으로 이동(재접근 차단), OWNER는 409 `OWNER_REQUIRED` 문구를 본다 |
| 내 계정 | `/account` | 아기 멤버십이 없어도(예: 탈퇴한 `removed_a`) 본인 학습 동의·삭제 신청 진입점을 유지한다 |
| 아기 전환 시 미저장 초안 | 헤더의 아기 전환 select | SC05에 입력이 있으면 "계속 작성/개인 초안 저장 후 전환/버리고 전환"을 물어보고, 저장한 초안은 그 아기로 돌아왔을 때 다시 채워진다 |

초대 발급·재발급 버튼은 아직 비활성 상태이며, 아기 생성 폼과 실제 Supabase OTP 로그인은 B의 실제 계정·API 연동 후 붙입니다.

실제 환경을 사용할 때만 `.env.example`을 참고해 공개 설정을 입력하세요. `.env.local`은 버전 관리 대상이 아니며, `service_role`/secret·DB·LLM 자격 증명을 `NEXT_PUBLIC_`에 두면 안 됩니다. 브라우저 토큰 보관 방식(현재 supabase-js 기본값인 지속 저장)은 개발계약이 아직 A의 결정 사항으로 남겨둔 항목입니다 — 잠정값이며 다중 탭·새로고침·XSS 노출 검토 전까지 최종 결정으로 보지 마세요.

API 사용 시 브라우저에서 `createApiClient`에 현재 사용자 세션, 같은 사용자에 대한 1회 갱신 함수, `PrivateScope`를 주입해야 합니다. 모든 POST/PUT/PATCH 요청에는 같은 UUID를 본문 `client_request_id`와 OpenAPI 클라이언트의 `params.header["Idempotency-Key"]`에 넣고 DELETE에는 키와 계약의 `version` 쿼리를 사용합니다. `requireData`로 계약 오류 코드를 분기하며 네트워크 응답 불명은 자동 재전송하지 않습니다. 아기·계정 전환, 탭 복원, 포커스 복귀 때 캐시와 등록된 음원/폼 정리 콜백을 폐기합니다.

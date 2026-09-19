# A-01 ①·② 웹 기반

이 폴더는 A-01의 ①·② 범위를 구현합니다. ①은 Next.js App Router/TypeScript/Tailwind/shadcn 설정, React Hook Form·Zod·TanStack Query 의존성, `openapi계약.json`에서 생성한 타입과 브라우저 사용자 Bearer용 API 클라이언트입니다. ②는 SC01~SC10 라우트 껍데기·공통 모바일 레이아웃과, 계약의 합성 fixture로 화면 사이를 이동해 보는 목 네비게이션입니다. Supabase OTP 연결, 실제 API 연동, 배포는 포함하지 않습니다.

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

## SC01~SC10 목 화면 이동 (A-01 ②)

`npm run dev` 후 `/login`에서 계약 §12의 시험 사용자(`owner_a`·`caregiver_a`·`owner_b`·`invited_a`·`removed_a`) 중 하나를 고르면 그 사용자의 **ACTIVE 멤버십만으로** 화면이 이동합니다. 이것은 개발용 화면 전환이며 실제 로그인이 아닙니다 — 새로고침하면 세션이 사라지고 `/login`으로 돌아갑니다(영구 저장하지 않는 것은 의도된 동작입니다; 실제 세션 저장 방식은 A-03에서 결정합니다).

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

실제 환경을 사용할 때만 `.env.example`을 참고해 공개 설정을 입력하세요. `.env.local`은 버전 관리 대상이 아니며, `service_role`/secret·DB·LLM 자격 증명을 `NEXT_PUBLIC_`에 두면 안 됩니다. 아직 실제 Supabase 클라이언트·계정·FastAPI 주소는 연결하지 않았습니다.

API 사용 시 브라우저에서 `createApiClient`에 현재 사용자 세션, 같은 사용자에 대한 1회 갱신 함수, `PrivateScope`를 주입해야 합니다. 모든 POST/PUT/PATCH 요청에는 같은 UUID를 본문 `client_request_id`와 OpenAPI 클라이언트의 `params.header["Idempotency-Key"]`에 넣고 DELETE에는 키와 계약의 `version` 쿼리를 사용합니다. `requireData`로 계약 오류 코드를 분기하며 네트워크 응답 불명은 자동 재전송하지 않습니다. 아기·계정 전환, 탭 복원, 포커스 복귀 때 캐시와 등록된 음원/폼 정리 콜백을 폐기합니다.

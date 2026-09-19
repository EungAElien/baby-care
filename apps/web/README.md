# A-01 ① 웹 기반

이 폴더는 A-01의 첫 범위만 구현합니다. 공개 시작 화면, Next.js App Router/TypeScript/Tailwind/shadcn 설정, React Hook Form·Zod·TanStack Query 의존성, `openapi계약.json`에서 생성한 타입과 브라우저 사용자 Bearer용 API 클라이언트가 있습니다. SC01~SC10 화면, Supabase OTP 연결, 실제 API 연동, 배포는 포함하지 않습니다.

## 로컬 검증

```powershell
npm.cmd ci
npm.cmd run generate:api
npm.cmd run typecheck
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

`src/lib/api/generated.d.ts`는 저장소의 `contracts/openapi계약.json`에서 생성합니다. 계약이 바뀌면 B와 합의 후 다시 생성·검증하세요. 테스트는 `contracts/목 응답과 시험 사용자 배치.json`을 동일 클라이언트의 주입 가능한 `fetch`에 연결합니다. 합성 fixture는 운영 코드나 오류 대체 경로에 포함되지 않습니다.

실제 환경을 사용할 때만 `.env.example`을 참고해 공개 설정을 입력하세요. `.env.local`은 버전 관리 대상이 아니며, `service_role`/secret·DB·LLM 자격 증명을 `NEXT_PUBLIC_`에 두면 안 됩니다. 아직 실제 Supabase 클라이언트·계정·FastAPI 주소는 연결하지 않았습니다.

API 사용 시 브라우저에서 `createApiClient`에 현재 사용자 세션, 같은 사용자에 대한 1회 갱신 함수, `PrivateScope`를 주입해야 합니다. 모든 POST/PUT/PATCH 요청에는 같은 UUID를 본문 `client_request_id`와 OpenAPI 클라이언트의 `params.header["Idempotency-Key"]`에 넣고 DELETE에는 키와 계약의 `version` 쿼리를 사용합니다. `requireData`로 계약 오류 코드를 분기하며 네트워크 응답 불명은 자동 재전송하지 않습니다. 아기·계정 전환, 탭 복원, 포커스 복귀 때 캐시와 등록된 음원/폼 정리 콜백을 폐기합니다.

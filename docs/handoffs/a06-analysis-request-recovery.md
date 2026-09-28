# A-06 ① 분석 요청·결과·복구 검증표

기준: 계약 1.2.0, `origin/develop=4307e1a` 기준 생성한 작업 브랜치 `feature/web-a06-analysis-request-recovery`. B-06 서버 코드는 PR [#34](https://github.com/EungAElien/baby-care/pull/34)로 `develop`에 이미 병합됐다(병합 커밋 `e251a24`, 기준 인계 [B-06 M2D 분석 파이프라인 인계](./b06-m2d-analysis.md)). 이번 작업은 그 위에서 A-06 ①(분석 요청·READY/RUNNING/COMPLETE/ABSTAIN/FAILED 표시·응답 유실 복구·명시적 FAILED 재시도·출처와 버전 보존·오류 분기)만 다룬다. 대응 행동 입력·추천 클릭과 수행의 구분(A-06 ②)은 이번 범위가 아니다.

## 제품 게이트 현재 상태

B-06 인계에 따르면 고정 V1 B 모델은 macOS 기준 재현 검사(`1e-6`)를 통과하지 못해 `calibration_status=NOT_VALIDATED`, `release_ready=false`이고 운영 정책의 `product_ready=false`다. 따라서 `GET /capabilities`의 `audio_model.available`은 현재 서버에서 `false`를 반환하며, 실제 `createAnalysis` 요청은 분석 행을 만들기 전에 `503 MODEL_NOT_READY`로 거부된다. 이 작업은 이 게이트가 닫혀 있다는 전제로 화면을 구현했다. B-06 코드가 `develop`에 병합된 것과 제품 게이트가 열리는 것은 별개이며, 화면은 코드 병합이 아니라 `capabilities.audio_model.available`만 보고 실제 분석 시작 버튼을 활성화한다.

## 자동 검사와 실제 환경의 경계

| 구분 | 확인 내용 | 상태 |
| --- | --- | --- |
| 웹 합성 자동 시험 | `analysis_id`/`client_request_id`를 Idempotency-Key와 본문에 함께 사용, 응답 유실 시 같은 ID GET 복구, 65초 초기 대기 후 자동 재생성 없이 GET 전환, RUNNING 2초 폴링과 지연 GET의 늦은 결과 폐기, 아기/계정 전환 시 폴링 중지, FAILED만 새 키·`expected_attempt`로 재시도하고 COMPLETE/ABSTAIN은 재시도 API를 쓰지 않음, `ANALYSIS_IN_PROGRESS`/`VERSION_CONFLICT`를 새 분석 생성 신호로 쓰지 않고 GET으로 복구, `IDEMPOTENCY_KEY_REUSED`는 자동 재시도하지 않음, `MODEL_NOT_READY`/`429`는 `Retry-After`를 보여주고 사용자 명시 동작(`resume()`)에서만 계속, 401/403/404/409 분기, COMPLETE 후보를 백분율 없이 표시, ABSTAIN은 후보 없이 서버 사유만 표시 | 통과. `apps/web/tests/analysis-flow.test.ts` 14건, `apps/web/tests/capabilities-api.test.ts` 1건, `apps/web/tests/analysis-screen.test.tsx` 3건. 실제 서버·모델 증거는 아님 |
| B-06 서버 기존 합성·격리 검증 | 요청 내 실행 상태 머신, 45초 요청 제한, 60초 lease, attempt·실행 토큰, 만료 복구, 늦은 결과 차단, 권한·동의·삭제 재검사, B-09 `ANALYSIS` 변경 | [B-06 인계](./b06-m2d-analysis.md) 기록 참조. 이번 A-06 작업에서 재실행하지 않음 |
| A-06과 B-06 실연동(로컬 Postgres·FastAPI·브라우저) | 실제 브라우저 → 로컬 B-06 서버 → 로컬 Postgres/Storage의 `createAnalysis`/`getAnalysis`/`retryAnalysis` 왕복 | 미실행. Docker(OrbStack)는 이 작업 환경에서 사용할 수 있었지만, `apps/api`가 요구하는 Python 3.12(`.python-version`)의 `venv` 생성이 `ensurepip` 단계에서 반복 실패해(exit 1) 로컬 API 서버를 띄우지 못했다. `apps/api/README.md`의 재현 절차(가상환경 생성 → `requirements-dev.lock` 설치 → 로컬 Supabase 기동)를 실행할 별도 Python 3.12 설치가 필요하다 |
| 실제 브라우저·기기, 제품 모델 | 제품 게이트가 열린 뒤 실제 분석 결과 확인 | 미실행. `capabilities.audio_model.available=false`인 동안은 화면상 시작 버튼 자체가 비활성이므로 실제 모델 분석 인수는 게이트가 열린 뒤 별도로 수행한다 |

웹 자동 게이트는 `origin/develop=4307e1a` 위에서 `npm run typecheck`, 전체 ESLint, Vitest 28파일 135건, `NEXT_PUBLIC_ENABLE_MOCK_NAV` 미설정/`=true` 각각의 Next 생산 빌드가 통과했다. 실제 브라우저·서버 통합 경로의 통과로 해석하지 않는다.

## 변경 파일

- `apps/web/src/lib/api/capabilities.ts`: `GET /capabilities`를 읽는 `useCapabilitiesQuery`. A-06의 제품 게이트 판단은 이 값만 사용한다.
- `apps/web/src/lib/analysis/analysis-flow.ts`: `AnalysisFlow` — 요청/응답 유실 복구/RUNNING 폴링/명시적 FAILED 재시도 상태 머신. `lib/audio/intake.ts`의 클래스 기반 구조를 따른다.
- `apps/web/src/lib/analysis/labels.ts`: stage·abstain_reason·failure.code·quality_reasons의 화면 문구. 내부 점수를 문구로 바꾸지 않는다.
- `apps/web/src/components/analysis-screen.tsx`: 실제 분석 화면(`AnalysisScreen`, `ResolvedAnalysis`).
- `apps/web/src/app/babies/[babyId]/episodes/[episodeId]/analysis/page.tsx`: 실제 분석 경로. 기존 `/babies/{babyId}/results/{scenario}` 목 미리보기와 분리했다.
- `apps/web/src/components/audio-intake.tsx`: A-05 READY 단계의 분석 시작 안내를 `capabilities.audio_model.available` 기준으로 갱신(기존 "B-06 API가 아직 준비되지 않았어요" 고정 문구 제거).
- `apps/web/tests/analysis-flow.test.ts`, `apps/web/tests/capabilities-api.test.ts`, `apps/web/tests/analysis-screen.test.tsx`: 신규 시험.

## 재현 준비(격리 로컬 연동이 가능해지면)

1. `apps/api/README.md`의 절차로 Python 3.12 가상환경을 만들고 `requirements-dev.lock`을 설치한 뒤 `npm run supabase:start`로 로컬 Supabase를 띄운다.
2. `scripts/run-b04-local-api.sh`를 참고해 B-06 라우트가 포함된 FastAPI를 `BABY_CARE_ENVIRONMENT=local`로 실행한다. `ENVIRONMENT=TEST` 합성 정책은 코드 검증용이며 이 재현에는 쓰지 않는다.
3. 웹의 `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, publishable key를 로컬 값으로 설정하고 실제 계정으로 로그인한다.
4. A-05로 READY 음원을 만든 뒤 `/babies/{babyId}/detect`에서 `capabilities.audio_model.available`이 이 로컬 정책에서 `true`인지 확인한다(고정 V1은 `product_ready=false`이므로 기본값은 여전히 닫혀 있을 수 있다).
5. 네트워크 패널에서 `POST /episodes/{episode_id}/analyses` → (응답 유실 시) `GET /analyses/{analysis_id}` → (RUNNING이면) 2초 간격 `GET` → 필요 시 `POST /analyses/{analysis_id}/retry` 순서를 확인한다.

## 남은 A-06 범위

- A-06 ②: 여러 대응 행동의 순서·아기 상태/반응·관찰 시각 입력, 추천 클릭과 실제 수행의 구분, 기존 수유 기록 연결 시 중복 생성 방지.
- 제품 게이트(`audio_model.available=true`)가 열린 뒤의 실제 모델 분석·실제 브라우저 인수.
- 로컬 Postgres/FastAPI/브라우저를 모두 연결한 격리 통합 시험(현재 이 작업 환경의 Python 가상환경 문제로 미실행).

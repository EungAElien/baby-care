# B-13 1차 자동 검사·CI·검증 기록 인계

2026년 9월 20일 · 계약 1.1.1 · B-09 병합 후 `develop` 기준

## 결론과 범위

현재 구현된 계약·API·웹·컨테이너·B-03/B-04/B-09 로컬 Supabase 경계를 로컬과 GitHub Actions에서 같은 명령으로 재현한다. 필수 검사가 실패·취소·미실행·예상 밖 skip이면 최종 게이트도 실패한다. 보고서는 실제 checkout commit과 GitHub의 PR head/test merge SHA, 런타임, 계약·migration, 검사별 상태·증거 위치와 후속 인수를 함께 기록한다.

이 작업은 **B-13 전체가 아니라 1차 자동 검사 기반**이다. Cloud Run 배포, 운영 자격 증명·IAM·Secret Manager, 실제 가족 자료, 실제 모델·외부 LLM, TUS, 삭제 실행기·백업 복원·키 회수 리허설, 전체 AC/SEC와 P0 인수는 완료로 판정하지 않는다.

## 전후 비교

| 구분 | 기존에 실행되던 검사 | 이번 보강 | 자동 검사 뒤에도 남는 인수 |
| --- | --- | --- | --- |
| API | Ruff format/lint, mypy, 단위·로컬 통합 pytest, 90% coverage | 루트 진입점, 필수 통합 수집/실행·skip 0 guard, JUnit/coverage 기록 | 운영 Auth·runtime 계정·부하·배포 |
| 계약·생성물 | 계약 validator, 수동 생성, 웹 내부 fixture 비교 | 임시 공간 재생성 후 OpenAPI·목 응답·웹 타입·fixture 바이트 비교, 계약/API operationId 분류 | 후속 미구현 33 operation의 제품 구현·인수 |
| 웹 | typecheck, Vitest, lint, build를 개별 실행 | 빠른 필수 검사와 CI에 모두 연결 | A의 실제 브라우저·기기·두 계정/두 화면 인수 |
| Supabase | 고정 로컬 project에서 migration·pgTAP·Auth/API/Storage | 고유 project id·빈 포트·임시 복사본, 빈 DB reset, 다른 스택 비접촉, 성공/실패 정리 확인 | 운영 migration·TUS·기존 재생 URL·실제 삭제/복원 |
| 컨테이너 | build와 미설정 liveness/readiness | build/실행 분리, 비루트·base digest, 잘못된 JWKS fail-closed, 정상 Auth/DB HTTP, 실패 후 정리 | Cloud Run·동시성·예열·모델 smoke |
| CI·기록 | API/Supabase 경로 필터 workflow, floating action tag | 모든 PR/push/merge queue 실행, SHA 고정 action, 4개 하위 job과 안정적 최종 gate, 마스킹 JSON/Markdown | 보호 규칙 적용은 저장소 관리자가 결정 |

## 설치와 실행 명령

Python API 환경은 3.12.12와 `apps/api/requirements-dev.lock`, 계약 검증은 `contracts/requirements.lock`, Node는 루트와 `apps/web/package-lock.json`을 사용한다. API와 모델 연구 환경은 합치지 않으며 자동 검사에서 모델·데이터셋을 내려받지 않는다.

```bash
cd apps/api
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip==26.2.1
python -m pip install --require-hashes --no-deps -r requirements-dev.lock
python -m pip install --require-hashes --no-deps -r ../../contracts/requirements.lock
cd ../..
npm ci
npm --prefix apps/web ci
```

작업 중에는 빠른 검사를, PR 전에는 네 명령을 모두 실행한다.

```bash
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:quick
npm run verify:container
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:integration
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:failure-detection
```

2026년 9월 20일 M1 Pro 로컬 마지막 기준으로 빠른 검사 약 26초, 컨테이너 약 6초, 격리 통합 약 107초, 실패 감지 약 3초였다. CI·네트워크·Docker cold pull에 따라 달라지며 timeout은 각각 20·15·30·15분이다. 시간과 검사 개수는 고정 성공 조건이 아니다.

## 명령별 판정 경계

- `verify:quick`: 계약 구조·참조·요청/응답 예시·부정 사례, 임시 재생성 일치, 실제 FastAPI operationId 37개와 기준 계약의 후속 미구현 33개 구분, API 정적/단위 검사, 웹 typecheck·Vitest·lint·build를 검사한다. 정적 fixture 성공은 실제 API·RLS·브라우저·모델 성공이 아니다.
- `verify:container`: 고정 digest 기반·비루트 이미지를 빌드하고 미설정 liveness 200, readiness 503, 대표 API 503을 확인한다. 기동 뒤 합성 오류를 발생시켜 컨테이너·network 정리도 확인한다.
- `verify:integration`: 빈 격리 DB에 모든 migration·seed를 적용하고 pgTAP, 연결 문맥, 실제 로컬 Auth JWT의 API 회귀, Data API/Storage 허용·거부를 실행한다. 이어 잘못된 JWKS의 readiness/API 503과 정상 Auth·DB 컨테이너의 readiness 200 및 인증 목록/생성을 실제 HTTP로 확인한다.
- `verify:failure-detection`: 임시 fixture 불일치, 필수 통합 환경 누락, 하위 job 실패, 합성 비밀 표식이 각각 실패 판정되는지만 확인한다. 주 작업 파일에는 실패 주입을 남기지 않는다.

`test:supabase`는 호환성을 위해 `verify:integration`과 같은 안전한 격리 실행을 가리킨다. 개별 `supabase:start/reset/stop`은 고정 project를 직접 다루는 개발 진단용이며 필수 게이트가 아니다.

## 계약·타입·fixture 변경 순서

계약 변경은 A/B 합의와 호환성 검토 뒤 다음 순서로 실행한다.

```bash
apps/api/.venv/bin/python contracts/build_contract.py
apps/api/.venv/bin/python contracts/validate_contract.py
npm --prefix apps/web run generate:api
npm --prefix apps/web run generate:fixtures
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:quick
```

`verify:quick` 자체는 커밋 파일을 수정하지 않는다. 새 계약과 웹 산출물을 임시 디렉터리에 만들고 커밋본과 비교하므로 생성 누락·수동 편집·버전 불일치는 차이와 함께 실패한다. `contracts/requirements.txt`, 생성 코드, 웹 생성 스크립트나 잠금 파일만 바뀐 PR도 전체 workflow가 실행된다.

## CI check와 산출물

workflow 이름은 `B-13 verification`이고 경로 필터를 사용하지 않는다. `pull_request`의 `develop`·`main`, 두 브랜치 push와 merge queue에서 실행한다. 문서-only PR도 workflow가 실행되므로 필수 check가 경로 필터 때문에 Pending으로 남지 않는다.

| check 이름 | 실행 내용 | artifact 이름 |
| --- | --- | --- |
| `test` | 계약·생성 일치, API 빠른 검사, 웹 전체 검사 | `b13-test-<run>-<attempt>` |
| `container` | 이미지 build, 미설정 HTTP, 실패 후 정리 | `b13-container-<run>-<attempt>` |
| `local-supabase` | 격리 DB/Auth/API/Storage와 구성 컨테이너 | `b13-local-supabase-<run>-<attempt>` |
| `failure-detection` | 네 가지 음성 대조군 | `b13-failure-detection-<run>-<attempt>` |
| `B-13 verification gate` | 위 네 job이 모두 `success`인지 판정 | `b13-verification-gate-<run>-<attempt>` |

기존 외부 참조 가능성이 있는 `test`, `container`, `local-supabase` 이름은 유지했다. 2026년 9월 20일 확인 시 저장소 ruleset에는 required status check가 등록돼 있지 않았다. 보호 규칙은 이번 작업에서 바꾸지 않으며 새 필수 check 후보는 안정적인 `B-13 verification gate`다.

모든 공식 action은 확인한 전체 commit SHA와 버전 주석으로 고정했고 기본 권한은 `contents: read`뿐이다. 외부 PR은 운영 비밀·클라우드 자격 증명을 받지 않고 로컬 합성 Auth/DB/Storage만 사용한다. `pull_request_target`은 사용하지 않는다. dependency cache key는 해당 잠금 파일을 사용하며 같은 DB를 쓰는 시험은 `local-supabase` 안에서 순차 실행한다. 오래된 동시 실행은 취소한다.

artifact 보존 기간은 7일이다. 각 suite 폴더에는 다음 중 실제 생성된 파일만 있다.

- `result.json`: 기계 판정, checkout/GitHub/PR SHA, 환경·런타임·계약·migration, 검사 상태·명령·시간·AC/SEC 근거, 미실행 이유
- `summary.md`: 같은 범위의 사람용 요약과 전체 인수 경계
- `*.log`: 값이 마스킹된 검사 출력
- `*-junit.xml`, `api-coverage.xml`: 해당 시험 결과만
- 계약·생성 일치, operation 분류, 민감정보 검사 JSON

`.env`, Supabase status 원문, Auth 응답, DB dump, 브라우저 세션, 토큰·OTP·초대 원문·proof·DB 비밀번호·원문·음원은 수집하지 않는다. suite 안의 스캐너와 업로드 직전 독립 스캐너가 모두 통과한 경우에만 artifact를 업로드한다.

## CI 실패 확인과 복구

| 실패 위치 | 먼저 볼 파일 | 복구 기준 |
| --- | --- | --- |
| `contract-validation` | `contract-validation.json`, 해당 log | 계약 구조·참조·예시·부정 사례 원인을 수정한다. validator를 우회하지 않는다. |
| `generated-artifacts` | `generated-consistency.json`, diff log | 위 재생성 순서를 실행하고 계약/웹 산출물을 함께 검토한다. 검사 중 자동 덮어쓰지 않는다. |
| API·웹 검사 | JUnit과 해당 log | 제품/테스트 원인을 수정한다. coverage 하향·테스트 제외·`continue-on-error`로 통과시키지 않는다. |
| 격리 Supabase start/reset | start/reset log | Docker·포트·CLI·migration 원인을 확인한다. 다른 project를 reset해 우회하지 않는다. |
| 필수 통합 guard | API JUnit/log | 필요한 환경·실행 그룹을 복구한다. 전체 skip은 성공이 아니다. |
| 컨테이너 HTTP | smoke log | build와 실행, liveness/readiness, JWKS·DB 연결, 401/503을 구분한다. |
| 민감정보 scan | `sensitive-scan.json` | 파일·line·rule만 보고 원문을 제거한다. 검출값은 오류 출력에도 다시 표시하지 않는다. artifact는 업로드되지 않는다. |
| 최종 gate | final `result.json` | 실패·취소·skip·missing인 하위 job을 복구한다. final job만 재해석하지 않는다. |

## AC·SEC 증거 연결과 한계

동적 보고서의 `evidence`는 새 시험 번호가 아니라 기존 AC·SEC의 현재 구현 하위 조건을 가리킨다. 구체 연결은 [Supabase 검증 범위 표](../../supabase/README.md#검증-범위와-인수-조건-연결)와 B-04/B-09 통합 시험을 기준으로 한다.

- pgTAP·RLS·문맥·실제 Auth/API/Storage는 AC01·03·13~16·21~22·39~44와 SEC03~24·27·32·48·62 중 표에 명시된 로컬 하위 조건만 증명한다.
- B-09 통합 시험은 공동 변경 원자 저장·중복/동시 쓰기·롤백·재시작, 최초/미래/90일/500개 재동기화 경계, 개인 초안·동의·DeletionJob 비노출을 증명한다.
- 계약 fixture와 unit test는 구조·분기 증거이며 실제 RLS·Storage·브라우저·모델 성공으로 합산하지 않는다.
- `result.json`의 `not_run`, 후속 인수 목록과 실패 이유를 기능 비활성 또는 전체 통과로 바꾸지 않는다.

## A와 운영이 별도로 확인할 일

- A의 실제 브라우저·기기·두 계정/두 화면 공동 인수, CSP·번들·캐시·초대 fragment 제거
- B-09 Realtime과 SEC30·SEC31, 운영 변경 이력 정리 Scheduler 등록
- TUS·음원 처리·실제 모델·외부 LLM과 관련 권리·보관·성능
- 물리 삭제 실행기·파생물/Storage 정리·백업 복원·키 회수 사고 리허설
- 운영 IAM·Secret Manager·개발/시연/실사용 분리·전역 할당량·부하 측정·Cloud Run 배포
- AC01~AC44와 SEC01~SEC62의 실제 환경 공동 판정

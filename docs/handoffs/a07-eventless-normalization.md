# A-07 ① 사건 없는 자연어 확인 화면 인계

구현·검증일: 2026-09-20 KST. 계약 1.2.0. 브랜치: `feature/web-a07-eventless-normalization`.

## 범위와 작업 경계

- 최신 원격을 fetch한 `develop` `5469237`에서 별도 `baby-care-a07-eventless` worktree로 시작했다. 진행 중 병합된 A 인수·B-06·B-07 결과는 `4307e1a`까지 fast-forward로 보존 통합했다.
- `episode_id=null`만 작성·조회·수정·확인한다. 기존 CareEvent 생성·사건 연결 API를 추가 호출하지 않는다.
- 다른 A 인수, A-05, A-12 worktree의 미커밋 파일을 수정·stash·커밋하지 않았다. A 작업표는 이 작업에서 편집하지 않았다. 병합된 인수 기록을 그대로 유지했다.
- A-07은 `care-entry-*` 컴포넌트와 `lib/care-entries/`, `lib/api/care-entries.ts`, `/babies/[babyId]/entries`를 소유한다. 기존 빠른 기록에는 진입 링크, 타임라인에는 서버가 반환한 StateObservation 표시만 연결했다.
- A-12의 캐릭터 자산, `public/characters/`, 모션 컴포넌트와 캐릭터 디자인 문서는 수정하지 않았다. A-07은 애니메이션 없는 텍스트 상태 표시만 제공한다.

## 구현 동작

빠른 기록의 **개인 초안 작성·복구** 또는 타임라인의 **내 개인 초안 작성·복구**로 진입한다.

변경 파일은 다음과 같다(`apps/web/` 기준).

- `src/lib/api/care-entries.ts`: 계약 API와 작성자·아기·리소스 응답 검증
- `src/lib/care-entries/content.ts`, `workspace.ts`: 코드포인트·의미 표시, revision/run·멱등 복구 상태
- `src/components/care-entry-workspace.tsx`, `care-entry-content.tsx`, `care-entry-observation.tsx`: 초안 화면, 확인 카드, 확인된 관찰 텍스트
- `src/app/babies/[babyId]/entries/page.tsx`: 새 진입 화면
- `src/app/babies/[babyId]/quick-record/page.tsx`, `timeline/page.tsx`: 기존 화면 연결
- `tests/care-entries-api.test.ts`, `care-entry-workspace.test.ts`, `care-entry-screen.test.tsx`: 신규 자동 시험
- 이 인계 문서. API·계약·생성 타입·A-12 자산 파일은 이번 기능 diff에 없다.

1. 원문과 선택지의 존재에 따라 TEXT·CHOICE·MIXED로 저장한다. CHOICE의 `raw_text`는 계약대로 null이다. 원문 길이와 TEXT evidence 범위는 Unicode 코드포인트 기준이다.
2. FastAPI의 본인 초안 목록을 페이지 단위로 조회하고, 개별 초안을 현재 권한으로 다시 읽는다. 응답의 아기·작성자·entry/run ID를 확인하며, 개인 초안을 공동 캐시에 넣지 않는다. 상태는 메모리에만 보관하고 계정·아기·private-scope generation 변경 시 폐기한다.
3. 원문 수정은 현재 input_revision을 사용한다. 수정·저장·수동 전환 이후 도착한 이전 run 응답은 적용하지 않는다. 35초 대기 또는 POST 응답 유실은 동일 run의 GET으로 복구한다. GET으로 복구한 COMPLETE 뒤 늦은 POST가 도착해도 사용자 편집을 덮어쓰지 않는다.
4. LLM/RULE/MANUAL을 구분한다. LLM 확인에는 현재 revision의 COMPLETE run만 사용한다. RULE/MANUAL은 `run_id=null`이다. 수정된 RULE 카드는 MANUAL, 성공 LLM 제안의 사용자 수정은 LLM/EDITED 경로를 유지한다.
5. 수행·계획·부정·불확실 행동, 상태 관찰, 보호자 해석, 반응 후보와 미해결 항목을 별도로 표시·편집한다. 사건 없는 outcome과 CONFLICT·UNSUPPORTED_CODE·MISSING_EVIDENCE가 남으면 확인을 차단한다. UNKNOWN_VALUE·UNKNOWN_TIME은 명시 확인 후 유지할 수 있다.
6. 확인 체크는 카드 수정마다 해제된다. 확인 요청의 key와 본문을 함께 메모리에 고정하고, 응답 불명확 상태에서는 편집을 잠근 채 같은 요청만 재전송한다. 서버에 저장된 entry ID는 URL fragment에만 남겨 새로고침 시 동일 entry와 확인 결과를 조회한다. fragment에 원문·토큰·정규화 결과는 넣지 않는다.
7. 422는 오류 필드와 해당 카드에 표시한다. SOURCE_REVISION_CHANGED는 최신 초안과 원문 비교, NORMALIZATION_IN_PROGRESS는 existing_run_id 조회, VERSION_CONFLICT는 최신 연결 기록 조회와 명시적인 수정 초안 재준비, ALREADY_CONFIRMED는 확정 ID 재조회로 복구한다. 수정 초안의 `base_record_versions`는 저장된 기준과 일치시켜 보낸다. 확정 원본을 수정할 때 `supersedes_entry_id`를 유지한다.
8. StateObservation을 서버에서 재조회한 후에만 상태로 표시한다. 코드·매핑 버전·관찰 시각·시각 정밀도·출처·확인 상태·data_origin을 사용한다. UNKNOWN, 배타 상태, 알 수 없는 매핑 버전은 NEUTRAL이며 30분 초과는 오래된 상태로 표시한다. 시각 미상은 현재 상태라고 주장하지 않는다. 수유 행동만으로 CALM을 만들지 않는다.

## 외부 처리 게이트

`normalizer_available`과 서버 차단 사유를 표시한다. 서버 조회 실패·비활성에서도 초안 복구와 RULE/MANUAL 확인을 유지한다.

제품 연결부의 `actualProcessingApproved=false`는 이번 요청의 실제 자료 처리 승인 대기를 반영한다. 서버 capability가 true이거나 화면에 DEMO가 표시된다는 이유로 이 게이트를 우회하지 않는다. 브라우저에는 OpenAI 호출·OpenAI key·공개 환경변수 우회 설정이 없다. 실행 로직은 승인 조건을 주입한 자동 시험으로 검증했으며, 실제 제품 화면의 LLM 버튼은 승인 전 비활성이다. 해제는 B의 운영 처리 조건과 실제 처리 승인에 맞춘 별도 검토 대상이다.

## 실행한 검증

최종 웹 검증은 최신 `develop` `4307e1a`를 통합한 상태에서 수행했다.

| 검증 | 결과 | 증거의 범위 |
|---|---|---|
| `npm test -- --maxWorkers=4` | **28개 파일, 161개 통과** | A-07 신규 44개 포함. 웹 API 대역·jsdom·가상 시계 시험 |
| `npm run typecheck` | 통과 | strict TypeScript |
| `npm run lint` | 통과 | 웹 전체 ESLint |
| `NEXT_PUBLIC_ENABLE_MOCK_NAV=false npm run build` | 통과 | 실제 계정 경로 빌드 |
| `NEXT_PUBLIC_ENABLE_MOCK_NAV=true npm run build` | 통과 | 목 탐색 포함 빌드 |
| `tests/integration/test_b07_normalization.py` | **6개 수집·6개 실행·6개 통과·skip 0** | 실제 격리 Supabase Auth/DB + FastAPI TestClient + 주입한 Normalizer 대역. 최신 develop 통합 뒤 재실행 |
| Chrome + 로컬 HTTP FastAPI | 아래 흐름 확인 | 합성 두 계정 **순차 로그인**, 실제 브라우저 UI. 외부 정규화 비활성 |

전체 웹 테스트 첫 실행은 Docker 초기화와 28개 worker가 겹쳐 새 jsdom 시험 1개가 기본 5초 제한을 넘었다(157/158). 테스트 의미나 timeout을 완화하지 않고 `--maxWorkers=4`로 다시 실행해 통과했다. 이후 추가 회귀까지 포함한 최종 결과는 161/161이다.

새 격리 Supabase 프로젝트 ID·빈 포트와 이번 worktree만 사용했다. Windows API 시작은 Linux 전용 `resource` import 때문에 실패하여, 기존 Linux API 이미지에 이번 worktree 소스를 읽기 전용으로 연결한 별도 컨테이너로 검증했다. 초기 브라우저 왕복 기준은 `5469237`의 B-07이며 로컬 전용 CORS 래퍼를 사용했다. 통합 뒤 B-07 6개 시험은 `4307e1a` API 소스·B-06 추가 마이그레이션으로 재검증했다. 테스트용 설정·키·세션·합성 원문 전체는 Git에 포함하지 않는다.

### 자동 시험에서 확인한 경계

- CHOICE·TEXT·MIXED 생성·수정, 본인 목록·복구, 다른 아기·작성자 응답 거부
- 계획·부정·불확실 assertion 보존, 상태 자동 추정 없음
- 35초 뒤 동일 run 조회, POST 응답 유실 조회, RUNNING·COMPLETE·FAILED·STALE 분리
- 이전 revision과 폐기된 사용자 범위의 늦은 완료 차단, 복구 후 늦은 COMPLETE의 편집 덮어쓰기 차단
- 이모지·한글·결합문자의 코드포인트 변환, surrogate 중간 선택 거부
- 서버 비활성·조회 실패·LLM FAILED의 직접 확인 복귀
- 명시 확인·run/mode/base version, 같은 확인 key/본문 재전송, 확인 완료 후 재저장 차단
- SOURCE_REVISION_CHANGED·VERSION_CONFLICT·ALREADY_CONFIRMED·NORMALIZATION_IN_PROGRESS·422 처리
- UNKNOWN·배타 관찰의 중립 표현, 30분 경계·시각 미상, 화면 읽기용 이름

### 실제 브라우저에서 확인한 흐름

1. 합성 OWNER OTP 로그인. CAREGIVER가 만든 개인 초안이 OWNER 목록에 나오지 않았다.
2. CHOICE로 수유 PLANNED + UNKNOWN 관찰을 작성·저장하고 새로고침 후 목록에서 복구했다.
3. RULE 카드의 명시 확인 후 서버가 **CareEvent 0개, StateObservation 1개**를 반환했다. 관찰 재조회 화면은 NEUTRAL·시각 미상·확인 상태를 표시했다.
4. TEXT(이모지·한글 포함) 생성 후 선택지를 추가해 MIXED로 수정했다. 원문과 선택지가 유지되고 revision 1→2가 표시됐다.
5. 로그아웃·서버 세션 회수 후 CAREGIVER로 순차 로그인했다. OWNER의 MIXED 초안은 보이지 않고 CAREGIVER 자신의 초안만 보였다.
6. CAREGIVER 초안을 수정하고 MANUAL에서 HOLDING/NEGATED와 ASLEEP 관찰을 작성했다. 잘못된 관찰 시각의 실제 HTTP 422가 원문을 유지하며 해당 카드에 표시됐다.
7. 시각을 수정하고 다시 명시 확인해 **CareEvent 0개, StateObservation 1개**를 저장했다. 오래된 상태·관찰 시각·출처·수정 후 확인·서버 매핑 버전이 표시됐다.
8. 동일 entry fragment로 새로고침해 확인된 결과와 관찰을 복구했다. 시험 종료 후 계정을 로그아웃했다.

## LLM 결과의 구분

- **이번 웹 자동 시험:** 계약 fixture와 API 함수 대역. 실제 제공자 실행 증거가 아니다.
- **이번 B-07 통합 시험:** NormalizerAdapter에 주입한 대역 + 실제 로컬 Auth/DB. 실제 Terra 호출이 아니다.
- **이번 실제 브라우저:** 외부 정규화 DISABLED, RULE/MANUAL 왕복. 실제 LLM은 호출하지 않았다.
- **다른 B 작업에서 병합된 실제 Terra 결과:** [B-07 Terra 제품 API 검증](b07-terra-product-verification.md)의 합성 3사례·외부 요청 총 6회 기록. 이번 A-07의 실행 실적이나 운영 자료 처리 승인으로 합산하지 않는다.

## 남은 실제 화면 인수와 후속 의존성

- 브라우저의 실제 35초 네트워크 대기·응답 유실 주입, 늦은 revision 경쟁, HTTP 409 전 분기, 동일 확인 요청 네트워크 재전송의 실제 브라우저 시험. 현재 자동 시험으로 검증한 범위다.
- 두 브라우저의 동시 로그인·동시 수정·실제 권한 회수, 모바일·실기기·스크린리더 인수. 이번 두 계정 시험은 같은 Chrome에서 순차 로그인했다.
- **B-07/B-09:** 확인 직후 `getStateObservation`은 성공했지만 현재 서버 타임라인 목록은 저장된 관찰을 반환하지 않았다. 타임라인에 StateObservation이 반환되면 이번 표시 컴포넌트로 렌더링되며, 지속적인 마지막 확인 상태 조회·홈 연결은 서버 목록/조회 계약과 함께 후속으로 진행한다. 이를 이번 확인 직후 상태 표시와 혼동하지 않는다.
- **B-07:** 실제 처리 승인·운영 조건, 실제 LLM을 거치는 A 화면 왕복, 사건 연결 ActionAttempt·Outcome은 후속이다. 이번에 운영 서버나 실제 아기 원문 처리 게이트를 켜지 않았다.
- **A-12:** 확인된 StateObservation의 의미·시각·출처를 상세 캐릭터에 연결한다. A-07의 표시를 근거로 수유 후 진정이나 분석 후보 표정을 생성하지 않는다. 캐릭터 자산·모션·장면 인수는 이번 범위 밖이다.

이 기록은 A-07 ①의 구현·부분 검증 결과다. 전체 A-07 또는 AC·SEC·CHAR 인수 통과로 표시하지 않는다.

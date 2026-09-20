# B-07 사건 없는 정규화·확인 저장 1차 인계

기준일: 2026-09-20

계약: 1.2.0

연결 작업: B-02, B-04, B-07, B-09, A-07, A-12

## 인계 결론

`episode_id=null`인 CHOICE·TEXT·MIXED 입력을 작성자 전용 초안으로 저장하고,
LLM/RULE/MANUAL 검토를 거쳐 허용된 CareEvent·StateObservation·확인 라벨을 원자적으로
확정하는 흐름을 실제 FastAPI와 격리 로컬 Supabase에서 구현했다. LLM 제품 어댑터는
OpenAI Responses API의 정확한 모델 ID `gpt-5.6-terra`와 strict Structured Outputs를
사용한다. 다만 현재 환경에는 `OPENAI_API_KEY`가 없어 실제 Terra 호출과 품질 판정은
실행하지 않았다. 자동 테스트의 LLM 결과는 제품용 `NormalizerAdapter` 인터페이스에 주입한
대역이며 외부 모델 실행 증거가 아니다.

사건 없는 범위에서 가짜 episode를 만들지 않는다. ActionAttempt·action group·Outcome과
실제 울음 사건 연결, A의 브라우저·캐릭터 재생, 전체 B-07 인수는 남은 작업이다.

## 실제 구현한 operationId

모든 경로의 실행 prefix는 `/v1`이다.

| 역할 | operationId | 경로 | 이번 사용법 |
|---|---|---|---|
| 기능 확인 | `getCapabilities` | `GET /capabilities` | 외부 정규화 가능 여부와 차단 이유 확인 |
| 초안 생성 | `createCareEntry` | `POST /babies/{baby_id}/care-entries` | `episode_id=null`, CHOICE·TEXT·MIXED |
| 내 초안 복구 | `listMyCareEntries` | `GET /babies/{baby_id}/care-entries` | 본인 미확인 초안만 목록 |
| 초안 조회·수정·삭제 | `getCareEntry`, `patchCareEntry`, `deleteCareEntry` | `/care-entries/{entry_id}` | 작성자 전용, 수정 시 `input_revision` 증가 |
| LLM 실행 | `createNormalization` | `POST /care-entries/{entry_id}/normalizations` | 현재 작성자·revision 확인 뒤 run claim |
| run 복구 | `getNormalization` | `GET /normalizations/{run_id}` | 응답 유실·35초 UI 대기 종료 뒤 같은 run 조회 |
| 명시 확인 | `confirmCareEntry` | `POST /care-entries/{entry_id}/confirm` | LLM·RULE·MANUAL 공통 의미 검사와 원자 저장 |
| 공유 결과 재조회 | `getCareEvent`, `getStateObservation` | `GET /care-events/{id}`, `GET /state-observations/{id}` | 현재 ACTIVE 멤버십 재검사 |
| 공동 캐시 복구 | `getChanges` | `GET /babies/{baby_id}/changes` | 확정된 공개 리소스의 type·ID·version만 전달 |

FastAPI가 실제로 제공하는 operationId만 자체 `/openapi.json`의
`x-business-contract.implemented_operations`에 표시한다.

## NormalizedContent 저장 경계

| 입력 내용 | 확정 시 저장 | 공유·재조회 | 저장하지 않는 것 |
|---|---|---|---|
| `PERFORMED` 행동 | FEEDING·DIAPER·SOOTHE 계열 CareEvent와 확인 라벨 | `getCareEvent`, B-09 `CARE_EVENT` | ActionAttempt, action group, 가짜 episode |
| `PLANNED`·`NEGATED`·`UNCERTAIN` 행동 | 확인 라벨만 | 공동 피드에는 없음 | CareEvent와 수행 집계 |
| 명시한 state | StateObservation과 확인 라벨 | `getStateObservation`, B-09 `STATE_OBSERVATION` | 행동만 보고 만든 상태 |
| caregiver interpretation | `CAREGIVER_REPORTED` 확인 라벨 | 공동 피드에는 없음 | 객관적 원인 정답 |
| `UNKNOWN_VALUE`·`UNKNOWN_TIME` | 사용자가 그대로 확인하면 라벨로 보존 | 공동 피드에는 없음 | 0이나 임의 시각으로 보정 |
| outcome | 확인 차단 `EVENT_REQUIRED` | 없음 | 사건 없는 Outcome |

확정된 StateObservation은 `episode_id=null`, `action_id=null`, `source_entry_id=entry_id`다.
표현 필드는 `state_codes`, `visual_state_code`, `visual_mapping_version`, `observed_at`,
`time_precision`, `created_by_user_id`, `updated_by_user_id`, `confirmed_by_user_id`,
`observation_source`, `confirmation_status`, `data_origin`, `version`이다. 매핑 버전은
`care-visual-v1`이다. `UNKNOWN`, CRYING+CALM, ASLEEP+AWAKE는 `NEUTRAL`이다. 수유
CareEvent만 저장됐다는 이유로 CALM 관찰을 만들지 않는다.

모든 action/state/interpretation/unresolved는 학습 동의와 별개의 label annotation으로
남는다. LLM 원안은 `EXTRACTED`, LLM 결과를 사용자가 바꾸면 `EDITED`, 선택지 규칙은
`CHOICE`, 수동 입력은 `MANUAL` 출처다. 확인 라벨 생성이 학습 이용 허용을 뜻하지 않는다.

## A-07 연결 흐름

1. `getCapabilities`를 읽는다. `normalizer_available=false`면 LLM 버튼을 비활성화하되
   RULE/MANUAL 입력과 기존 초안 복구는 유지한다.
2. A가 새 `client_request_id`를 만들고 같은 값을 `Idempotency-Key`에 넣어
   `createCareEntry`를 호출한다. `episode_id`는 null이다.
3. LLM을 선택하면 A가 새 `run_id`와 새 논리 요청 key를 만들고 현재
   `input_revision`으로 `createNormalization`을 한 번 호출한다. 서버는 외부 호출 전에
   run과 30초 lease를 커밋하며 외부 호출 동안 DB transaction/lock을 잡지 않는다.
4. POST 응답이 사라지거나 화면의 최대 대기 35초가 끝나면 새 run을 만들지 않고
   `getNormalization(run_id)`을 호출한다. `RUNNING`이면 같은 ID를 다시 조회한다.
5. `COMPLETE`의 result를 확인 카드에 표시한다. TEXT evidence의 `span_start` 포함,
   `span_end` 제외 값은 JavaScript UTF-16 offset이 아니라 Unicode code-point offset이다.
6. 사용자가 내용과 미상 값을 확인하거나 수정한다. CONFLICT·UNSUPPORTED_CODE·
   MISSING_EVIDENCE, 잘못된 참조·수량·시각·근거, 사건 없는 outcome은 저장 전에 해결한다.
7. LLM 결과를 저장할 때 현재 성공 run을 포함하고 `normalization_mode=LLM`으로
   `confirmCareEntry`를 호출한다. RULE/MANUAL은 반드시 `run_id=null`이다.
8. 성공 응답의 CareEvent/StateObservation ID를 다시 읽고, B-09 revision을 적용한다.
   같은 확인 요청 재전송은 같은 key를 써서 같은 ID 목록을 복구한다.
9. LLM이 FAILED이거나 비활성이면 실패를 성공 카드로 바꾸지 않는다. 원문을 유지하고
   사용자가 RULE/MANUAL로 편집한 뒤 별도의 확인 key로 7단계를 수행한다.

원문 PATCH나 수동 확인이 실행 중 run보다 먼저 끝나면 그 run은 STALE이다. 이미 확정한
리소스를 고칠 때는 기존 확정 원문을 직접 PATCH하지 않고 `supersedes_entry_id`와 모든
현재 `base_record_versions`를 가진 새 수정 초안을 만든다. 1차 수정은 기존 CareEvent와
StateObservation 개수를 유지하면서 같은 ID의 version만 증가시킨다. 이전 label은
SUPERSEDED가 되어 같은 사실을 두 번 집계하지 않는다.

## ID·revision·version 규칙

| 값 | 발급 주체 | 재사용 규칙 |
|---|---|---|
| `entry_id` | B | 초안의 수명 전체에서 유지. 수정 확정은 새 entry가 이전 entry를 supersede |
| `client_request_id` / `Idempotency-Key` | A | 한 변경 작업에서 같은 UUID. 응답 유실 재전송은 재사용, 내용 변경·새 시도는 새 UUID |
| `run_id` | A | 정규화 한 시도에 하나. 같은 요청/복구에서 재사용; 다른 entry/revision에 재사용 금지 |
| `input_revision` | B | 원문·선택 수정 때 증가. A는 조회한 현재 값을 echo |
| 내부 `execution_token` | B | 완료 fencing 전용 비밀. 응답·로그·클라이언트 저장 금지 |
| 리소스 `version` | B | CareEvent/StateObservation optimistic concurrency. 수정 초안은 모든 현재 값 제출 |
| CareEvent·StateObservation·label ID | B | 최초 확인 때 발급. 같은 key replay는 같은 목록, 수정은 공개 리소스 ID 유지 |
| `action_ref` | A/정규화 결과 | 한 NormalizedContent 안의 참조일 뿐 공개 ActionAttempt ID가 아님 |
| B-09 `revision` | B | 공동 변경 피드 경계. 리소스 `version`·`input_revision`과 교환하지 않음 |

서버는 사용자별 LLM run claim을 1분 10회, 24시간 100회로 제한한다. 현재 같은
entry/revision에 RUNNING run은 하나뿐이다.

## 요청·응답 예시

아래 UUID·시각은 합성 예시다. 공통 `Authorization: Bearer …`는 생략했다.

### 정상 LLM 실행과 확인

```http
POST /v1/care-entries/10000000-0000-4000-8000-000000000750/normalizations
Idempotency-Key: 10000000-0000-4000-8000-000000000804
Content-Type: application/json

{"client_request_id":"10000000-0000-4000-8000-000000000804","run_id":"10000000-0000-4000-8000-000000000751","input_revision":1}
```

```json
{
  "run_id": "10000000-0000-4000-8000-000000000751",
  "entry_id": "10000000-0000-4000-8000-000000000750",
  "input_revision": 1,
  "status": "COMPLETE",
  "lease_expires_at": null,
  "execution_mode": "REAL",
  "provider_call_executed": true,
  "provider": "openai",
  "model": "gpt-5.6-terra",
  "prompt_version": "normalization.2026-09-20.v1",
  "schema_version": "openapi-1.2.0.NormalizedContent",
  "ontology_version": "care-v1",
  "result": {
    "actions": [{
      "action_ref": "a1", "action_code": "FEEDING", "assertion": "PERFORMED",
      "performed_by_user_id": null, "occurred_at": null, "relative_time": null,
      "time_precision": "UNKNOWN", "sequence": 1, "amount": 80, "unit": "ML",
      "feeding_mode": "FORMULA",
      "evidence": [{"source":"TEXT","choice_id":null,"span_start":0,"span_end":12,"quote":"분유 80mL 먹였어요"}]
    }],
    "states": [], "outcomes": [], "caregiver_interpretations": [],
    "unresolved": [{"field":"actions.a1.occurred_at","code":"UNKNOWN_TIME","message":"수유 시각 미상"}]
  },
  "failure": null,
  "recorded_at": "2026-09-20T09:00:00Z",
  "completed_at": "2026-09-20T09:00:01Z"
}
```

위 `result`를 사용자가 확인한 뒤 다음처럼 저장한다. `UNKNOWN_TIME`은 허용된다.

```http
POST /v1/care-entries/10000000-0000-4000-8000-000000000750/confirm
Idempotency-Key: 10000000-0000-4000-8000-000000000805
Content-Type: application/json

{"client_request_id":"10000000-0000-4000-8000-000000000805","input_revision":1,"run_id":"10000000-0000-4000-8000-000000000751","normalization_mode":"LLM","content":{"actions":[{"action_ref":"a1","action_code":"FEEDING","assertion":"PERFORMED","performed_by_user_id":null,"occurred_at":null,"relative_time":null,"time_precision":"UNKNOWN","sequence":1,"amount":80,"unit":"ML","feeding_mode":"FORMULA","evidence":[{"source":"TEXT","choice_id":null,"span_start":0,"span_end":12,"quote":"분유 80mL 먹였어요"}]}],"states":[],"outcomes":[],"caregiver_interpretations":[],"unresolved":[{"field":"actions.a1.occurred_at","code":"UNKNOWN_TIME","message":"수유 시각 미상"}]},"base_record_versions":[]}
```

```json
{
  "entry_id": "10000000-0000-4000-8000-000000000750",
  "input_revision": 1,
  "care_event_ids": ["10000000-0000-4000-8000-000000000601"],
  "action_ids": [],
  "action_group_ids": [],
  "state_observation_ids": [],
  "outcome_ids": [],
  "label_annotation_ids": ["10000000-0000-4000-8000-000000000603"]
}
```

RULE 또는 MANUAL은 같은 `content` 검사를 사용하되 `run_id:null`이다. RULE은 제출한
CHOICE evidence만 허용한다. MANUAL/사용자 수정은 좌표 없는 비어 있지 않은
`USER_CORRECTION.quote`를 사용할 수 있다.

### 미해결·의미 오류

CONFLICT·UNSUPPORTED_CODE·MISSING_EVIDENCE가 남은 확인, 사건 없는 outcome, 잘못된
Unicode slice는 HTTP 422다. UI는 `field_errors`를 해당 카드에 연결하고 초안을 유지한다.

```json
{
  "code": "VALIDATION_ERROR",
  "message": "The confirmed content has unresolved semantic errors.",
  "retryable": false,
  "request_id": "10000000-0000-4000-8000-000000000900",
  "field_errors": [
    {"field":"unresolved.0","message":"UNRESOLVED_BLOCKING: CONFLICT must be resolved before confirmation."}
  ],
  "details": {"current_version":null,"current_resource":null,"resource_type":null,"existing_analysis_id":null,"existing_run_id":null,"existing_session_id":null,"deletion_job_id":null,"session_revocation_id":null,"reauthentication_challenge_id":null,"status_url":null,"retry_after_seconds":null}
}
```

### 제공자 실패와 비활성

제공자 오류·거절·불완전·시간 초과·schema 오류는 HTTP 성공으로 조회되는 저장된 FAILED
run이다. HTTP 재시도 성공이나 STUB 결과로 바꾸지 않는다.

```json
{
  "run_id": "10000000-0000-4000-8000-000000000751",
  "entry_id": "10000000-0000-4000-8000-000000000750",
  "input_revision": 1,
  "status": "FAILED",
  "lease_expires_at": null,
  "execution_mode": "REAL",
  "provider_call_executed": true,
  "provider": "openai",
  "model": "gpt-5.6-terra",
  "prompt_version": "normalization.2026-09-20.v1",
  "schema_version": "openapi-1.2.0.NormalizedContent",
  "ontology_version": "care-v1",
  "result": null,
  "failure": {"code":"NORMALIZATION_TIMEOUT","message":"Normalization did not finish within 20 seconds.","retryable":true},
  "recorded_at": "2026-09-20T09:00:00Z",
  "completed_at": "2026-09-20T09:00:20Z"
}
```

비활성 서버는 capability와 실행 결과 모두에서 실제 호출이 없었음을 밝힌다.

```json
{"contract_version":"1.2.0","normalizer_available":false,"normalizer_unavailable_reason":"DISABLED"}
```

```json
{"status":"FAILED","provider_call_executed":false,"failure":{"code":"NORMALIZATION_DISABLED","message":"External normalization is disabled by the server.","retryable":false}}
```

두 조각은 전체 응답의 관련 필드만 표시했다. capability에는 audio/detector 필드가,
NormalizationRun에는 ID·버전·시각 필드가 함께 온다.

### STALE과 409 복구

원문 수정·수동 확인과 늦은 완료가 경쟁하면 저장된 run을 새 내용에 적용하지 않는다.

```json
{"run_id":"10000000-0000-4000-8000-000000000751","entry_id":"10000000-0000-4000-8000-000000000750","input_revision":1,"status":"STALE","lease_expires_at":null,"provider_call_executed":true,"result":null,"failure":null,"completed_at":"2026-09-20T09:00:02Z"}
```

현재 revision과 다른 확인은 HTTP 409다.

```json
{
  "code":"SOURCE_REVISION_CHANGED",
  "message":"The source revision changed before confirmation.",
  "retryable":false,
  "request_id":"10000000-0000-4000-8000-000000000900",
  "field_errors":[],
  "details":{"current_version":2,"current_resource":null,"resource_type":"CARE_ENTRY","existing_analysis_id":null,"existing_run_id":null,"existing_session_id":null,"deletion_job_id":null,"session_revocation_id":null,"reauthentication_challenge_id":null,"status_url":null,"retry_after_seconds":null}
}
```

같은 revision의 다른 run이 실행 중이면 `NORMALIZATION_IN_PROGRESS`와
`details.existing_run_id`, `status_url`, `retry_after_seconds=1`을 반환한다. 같은
Idempotency-Key를 다른 본문에 쓰면 `IDEMPOTENCY_KEY_REUSED`, 수정 대상 version이 바뀌면
`VERSION_CONFLICT`, 이미 다른 확인이 이긴 초안은 `ALREADY_CONFIRMED`다. 이 409들은 새 run을
무조건 만드는 신호가 아니다. 현재 resource/run을 다시 읽고 사용자 의도에 따라 재시도한다.

## 실행 조건과 검증

`.env`의 관련 변수는 다음과 같다.

```dotenv
BABY_CARE_EXTERNAL_NORMALIZATION_ENABLED=false
BABY_CARE_OPENAI_API_KEY=
BABY_CARE_OPENAI_ORGANIZATION=
BABY_CARE_OPENAI_PROJECT=
BABY_CARE_NORMALIZATION_TIMEOUT_SECONDS=20
BABY_CARE_NORMALIZATION_LEASE_SECONDS=30
```

DB·Auth/JWKS와 B-04 필수 설정도 필요하다. 로컬 시작과 전체 환경 목록은
[`apps/api/README.md`](../../apps/api/README.md), 격리 Supabase 방식은
[`supabase/README.md`](../../supabase/README.md)를 따른다.

```bash
cd apps/api
PYTHONPATH=src python -m baby_care_api
```

일반 회귀는 외부 key를 넣지 않은 상태에서 실행한다.

```bash
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:quick
npm run verify:container
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:integration
```

최신 `develop` 재배치 뒤 격리 로컬 Supabase와 실제 FastAPI를 사용한 API 전체 suite는
216개 통과, 필수 integration 19/19 수집, coverage 91.02%였고 pgTAP 96개가 통과했다. Ruff와 mypy도
통과했다. 이 수치는 로컬 DB·Auth·RLS와 주입한 LLM 대역 검증이며 실제 Terra 품질 수치가
아니다.

키가 준비되면 일반 테스트 프로세스와 분리한 합성 전용 서버에서만
`BABY_CARE_EXTERNAL_NORMALIZATION_ENABLED=true`와 `BABY_CARE_OPENAI_API_KEY`를 서버 비밀로
설정한다. `getCapabilities`가 true인지 확인한 뒤 합성 초안 최대 3개만 API 흐름으로
호출하고, 재시도를 포함한 외부 요청이 6회를 넘기 전에 중단한다. key·원문·provider 응답
전문은 로그나 보고서에 남기지 않고 run의 `provider_call_executed`, model/version,
status/failure만 기록한다. 다른 모델로 바꾸거나 CI에서 이 절차를 자동 실행하지 않는다.

## 보안·경쟁 조건

- 초안·run·전체 정규화 결과는 작성자만 읽는다. OWNER도 다른 보호자 초안을 읽을 수 없다.
- 확인·수정·삭제·권한 회수와 외부 완료를 실행 토큰과 DB 조건으로 fence한다.
- 프로세스 시작과 run 조회 때 30초가 지난 RUNNING을 `NORMALIZATION_LEASE_EXPIRED`로 끝낸다.
- 같은 key replay는 기존 run/확정 ID를 반환하고 같은 key의 다른 본문은 차단한다.
- 확정 리소스·초안 CONFIRMED·label·B-09 feed는 한 transaction이다. 주입한 중간 실패
  시험에서 전체 rollback을 확인했다.
- 수정은 이전 공개 리소스 ID를 유지하고 version을 올린다. 최초 작성자, 현재 수정자와
  확인자를 각각 보존한다.
- 원문·정규화 결과·token은 일반 로그와 B-09 공동 feed에 넣지 않는다.

## 남은 범위

- 실제 OpenAI 계정의 `gpt-5.6-terra` 접근, 최대 3개 합성 사례의 호출·품질·사용량 확인
- episode가 있는 ActionAttempt·action group·Outcome, 복수 행동 후 반응과 기존 CareEvent 연결
- A-07 실제 브라우저의 35초 대기·재조회·편집·수동 전환·오류 UI·새로고침 복구
- A-12의 ActionAttempt/분석 후보/관찰 의미 연결, 캐릭터 자산·모션·접근성·오래된 상태 표현
- 운영 Supabase migration·외부 처리 조건·비밀 저장·보관/국외 처리 검토와 운영 배포
- 사건 연결을 포함한 AC/SEC/CHAR 전체 B-07 공동 인수

이번 1차 구현을 위 항목의 완료나 실제 모델 성능 검증으로 확대 해석하지 않는다.

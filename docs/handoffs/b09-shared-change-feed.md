# B-09 공동 기록 변경 조회 → A-08 인계

2026년 9월 20일 · 계약 1.1.1 · 로컬 Supabase 검증 기준

## 결론과 범위

B-04의 실제 JWT·현재 세션·`baby_app` 트랜잭션 위에 `GET /v1/babies/{baby_id}/changes?since_revision=...` (`getChanges`)를 구현했다. 한 보호자의 커밋된 공동 변경을 다른 ACTIVE 보호자가 조회하고, 기존 FastAPI에서 최신 리소스를 다시 받아 복구할 수 있다. 변경 응답에는 본문·보호자 원문·음원 URL·토큰·개인 삭제 작업 ID가 없다.

이번 완료 범위는 **전경 폴링과 누락 복구 서버 경로**다. Supabase Realtime은 비활성 상태이며 SEC30·SEC31은 통과로 표시하지 않는다. A의 실제 브라우저 숨김/복귀, 캐시, 두 화면 공동 인수와 운영 배포도 아직 완료가 아니다.

B-09 브랜치는 미병합 Draft PR #10의 B-04 head를 선행으로 사용한다. 따라서 B-09 PR은 #10에 의존하며 #10을 대신 병합하지 않는다.

## B-04 이후 달라진 점

- 아기마다 내구성 있는 `shared_change_feed_state`와 `shared_changes`를 두고 revision을 트랜잭션 안에서 할당한다. 프로세스 메모리, 응답 후 작업, Realtime 전달 여부에 의존하지 않는다.
- B-04 업무 쓰기와 변경 행·revision 갱신이 같은 DB 트랜잭션이다. 롤백은 둘 다 취소되고, 멱등 재전송은 저장된 기존 성공을 반환하므로 revision을 다시 만들지 않는다.
- 조회는 상태 행을 `FOR SHARE`로 잠가 `current_revision`과 변경 목록을 한 경계로 읽는다. 쓰기는 같은 상태 행의 UPDATE 잠금을 거쳐 아기별 순서를 만든다.
- `resource.version`, `babies.context_revision`, 변경 피드 `current_revision`은 서로 다른 값이다. 리소스 충돌, 파생물 무효화, 전달 경계를 각각 담당한다.
- 기존 아기는 migration 시 상태 revision 1을 만들고, `since_revision=0`에서 전체 동기화를 요구한다. 과거 변경을 추측해 합성하지 않는다.
- 변경 이력은 목적에 맞게 90일 보관하고 응답 가능한 고유 리소스는 최대 500개다. 정리 실행기는 관리자 전용 공통 함수로 제공하지만 운영 스케줄 등록은 B-13 후속이다.

PostgreSQL 17.6의 기본 Read Committed에서는 한 트랜잭션 안의 연속 SELECT도 서로 다른 스냅샷을 볼 수 있다. 그래서 시각이나 단순 시퀀스 조회만 사용하지 않고 아기별 상태 행 잠금으로 경계를 직렬화했다. PostgreSQL sequence도 롤백되지 않아 빈 번호가 생길 수 있으므로 업무 완료 순서의 근거로 사용하지 않았다. 근거: [PostgreSQL 17 Transaction Isolation](https://www.postgresql.org/docs/17/transaction-iso.html), [PostgreSQL 17 Sequence Functions](https://www.postgresql.org/docs/17/functions-sequence.html).

## 계약 1.1.1 의미

응답 필드와 enum은 1.1.0에서 삭제·이름 변경 없이 유지했다. 1.1.1은 부족했던 의미, 상한, 복구 규칙과 예시를 추가한 호환 보완이다.

- 정상 증분 범위는 `since_revision < change_revision <= current_revision`, 즉 `(since_revision, current_revision]`이다.
- 한 범위에서 같은 `(resource_type, resource_id)`가 여러 번 바뀌면 가장 큰 change revision의 최종 상태 하나만 보낸다. 변경 순서 자체를 재생하는 이벤트 스트림이 아니다.
- `current_revision`은 서버가 잠근 경계까지 피드 행을 완전히 판정했다는 뜻이다. A의 재조회나 화면 반영 성공을 뜻하지 않는다.
- 변경이 없으면 `changes=[]`, `resync_required=false`, `current_revision`은 현재 경계다.
- `since_revision=0`, 보관된 경계보다 오래된 값, 서버 현재값보다 큰 값은 `changes=[]`, `resync_required=true`다.
- 범위 안의 고유 변경 리소스가 500개를 넘으면 일부를 자르지 않고 `changes=[]`, `resync_required=true`다. 현재 형식에는 페이지 cursor나 개별 change revision이 없으므로 부분 응답과 최신 revision을 같이 주지 않는다.
- 기본 이력 보관은 90일이다. 정리된 revision은 빈 정상 응답이 아니라 전체 재동기화로 판정한다.
- `version`은 해당 리소스의 서버 버전이다. 한 업무 변경이 여러 공개 리소스를 바꾸면 같은 feed revision에 여러 Change가 생긴다. 예를 들어 CareEvent 변경은 공개 `Baby.context_revision`·`Baby.version`도 올리므로 `CARE_EVENT`와 `BABY`를 함께 보낸다. `deleted=true`는 그 버전에서 일반 조회에서 제외된 tombstone이며 삭제 전 본문을 포함하지 않는다.
- `Cache-Control: private, no-store`, `Pragma: no-cache`, `Vary: Authorization`을 반환한다.

## 연결된 실제 쓰기와 A의 재조회

| 쓰기 operationId | 공유 조회 영향 | Change | A의 재조회/처리 |
| --- | --- | --- | --- |
| `createBaby` | 아기와 OWNER 멤버십 원자 생성 | `BABY`, `MEMBERSHIP`, 각각 version 1, 같은 revision | 초기 전체 동기화에서 `listBabies`, `listMembers` |
| `patchBaby` | 공통 프로필 변경 | `BABY`, baby ID, 새 version | `listBabies` 또는 현재 아기 자료 재조회 |
| `patchMyRelationship` | 멤버 표시 관계 변경 | `MEMBERSHIP`, membership ID, 새 version | `listMembers` |
| `acceptInvite` | 새 ACTIVE 구성원 참여 | `MEMBERSHIP`, 새 membership ID, version 1 | `listMembers` |
| `removeMembership` | 탈퇴/제거로 목록 변경 | `MEMBERSHIP`, membership ID, 새 version, `deleted=true` | 남은 구성원은 `listMembers`; 대상 사용자는 다음 조회 404 후 범위 정리 |
| `createCareEvent` | 공유 생활 기록 생성, Baby 공개 context 갱신 | `CARE_EVENT` version 1 + `BABY` 새 version, 같은 revision | `getCareEvent` + `listBabies` |
| `patchCareEvent` | 수정 또는 진행 중 수면 종료, Baby 공개 context 갱신 | `CARE_EVENT` 같은 ID의 새 version + `BABY` 새 version | `getCareEvent` + `listBabies` |
| `deleteCareEvent` | DELETING부터 일반 조회 제외, Baby 공개 context 갱신 | `CARE_EVENT` tombstone + `BABY` 새 version | 사건 캐시 제거 + `listBabies` |
| `createAction` | 새 CareEvent를 함께 만들 때만 공동 타임라인/context 변경 | 새 `CARE_EVENT` version 1 + `BABY` 새 version | `getCareEvent` + `listBabies`; 기존 CareEvent 연결만 한 경우 피드 변화 없음 |
| `deleteMyContributions` | 여러 공유 CareEvent를 같은 트랜잭션에서 DELETING, Baby context 1회 갱신 | 각 `CARE_EVENT` tombstone + `BABY` 새 version, 모두 같은 feed revision | 각 사건 캐시 제거 + `listBabies`; 개인 `DeletionJob`은 별도 `getDeletion`만 사용 |
| `deleteBabyData` | 전체 삭제 시작과 동시에 일반 접근 차단 | 내부적으로 `BABY` tombstone을 같은 트랜잭션에 기록 | 커밋 뒤 `/changes`도 404. 신청자는 별도 `getDeletion`; 캐시/타이머 전체 정리 |

동의, 개인 학습 동의, 세션 회수 작업, 개인 초안·정규화 초안은 공동 조회 결과 자체를 바꾸지 않으므로 피드에 넣지 않는다. OWNER도 타인의 개인 초안이나 삭제 작업을 볼 수 없다.

계약 enum에는 후속 리소스인 `EPISODE`, `ANALYSIS`, `RECOMMENDATION`, `STATE_OBSERVATION`, `OUTCOME`, `DELETION`이 남아 있다. 이번에 실제 연결된 것은 `BABY`, `MEMBERSHIP`, `CARE_EVENT`뿐이다. 후속 구현은 업무 변경과 같은 트랜잭션에서 `baby_private.record_shared_changes`를 호출하고 실제 리소스 ID·version만 기록해야 한다. `DELETION`을 개인 DeletionJob 노출에 사용하면 안 된다.

## 요청·응답 예시

최초 조회 또는 전체 재동기화:

```http
GET /v1/babies/10000000-0000-4000-8000-000000000101/changes?since_revision=0
Authorization: Bearer <current-access-token>
```

```json
{
  "baby_id": "10000000-0000-4000-8000-000000000101",
  "current_revision": 8,
  "changes": [],
  "resync_required": true,
  "server_time": "2026-09-19T09:00:00Z"
}
```

정상 증분:

```http
GET /v1/babies/10000000-0000-4000-8000-000000000101/changes?since_revision=8
Authorization: Bearer <current-access-token>
```

```json
{
  "baby_id": "10000000-0000-4000-8000-000000000101",
  "current_revision": 10,
  "changes": [
    {
      "resource_type": "CARE_EVENT",
      "resource_id": "10000000-0000-4000-8000-000000000601",
      "version": 3,
      "deleted": false
    },
    {
      "resource_type": "BABY",
      "resource_id": "10000000-0000-4000-8000-000000000101",
      "version": 8,
      "deleted": false
    }
  ],
  "resync_required": false,
  "server_time": "2026-09-19T09:00:00Z"
}
```

변경 없음은 같은 형식에서 `changes=[]`, `resync_required=false`다. 삭제는 해당 리소스의 더 큰 `version`과 `deleted=true`를 반환한다. 보관 경계 이전·미래 revision·500개 초과는 일부 변경 대신 `changes=[]`, `resync_required=true`를 반환한다.

세션 회수는 401, ACTIVE지만 관리 권한이 없는 별도 작업은 403, 다른 아기·비구성원·탈퇴/제거·DELETING 아기는 존재를 숨기는 404다. 특히 권한 실패 응답에는 해당 아기의 revision·변경 ID·건수가 없다.

## A의 안전한 폴링·복구 순서

조회 기준과 캐시는 반드시 `(user_id, baby_id)` 범위로 분리한다.

1. 새 범위 진입 시 `getChanges(since_revision=0)`으로 경계 `R0`을 얻는다. `resync_required=true`가 정상이다.
2. 권한 있는 현재 자료를 FastAPI로 전체 조회한다. 이 전체 조회가 모두 성공한 뒤에만 `R0`을 저장한다.
3. 즉시 `getChanges(since_revision=R0)`을 호출한다. 1~2 사이에 생긴 변경은 이 호출로 회수한다.
4. 전경에서는 5초마다 같은 호출을 하되 이전 호출을 무한 중첩하지 않는다. 숨김에서는 중지하고 복귀 즉시 호출한다. 5초는 화면 반영 SLA가 아니다.
5. 증분 응답의 tombstone을 먼저 적용하고, 나머지는 resource ID로 기존 FastAPI를 재조회한다. 모든 적용이 성공한 뒤에만 응답의 `current_revision`을 저장한다. 하나라도 실패하면 기존 revision을 유지한다.
6. 캐시의 version보다 작은/같은 늦은 재조회 응답은 버린다. tombstone version 이하의 응답도 버려 삭제된 자료를 되살리지 않는다.
7. `resync_required=true`면 해당 사용자·아기 공동 캐시를 비우고 응답의 `current_revision`을 기준 `R`로 기억한 뒤 전체 조회한다. 전체 조회 성공 후 `R`을 저장하고 즉시 `since_revision=R` 증분 조회로 전체 조회 중의 변경을 회수한다.
8. 401은 인증 갱신/로그인 경로로 한정하고, 403은 해당 작업 권한 UI를 닫고, 404·삭제 상태는 해당 아기 요청·타이머·revision·캐시를 정리한다. 429는 `Retry-After`, 503은 제한된 지수 backoff를 사용하며 revision을 올리지 않는다. 무한 즉시 재시도하지 않는다.
9. 계정이나 아기 전환 시 이전 범위의 in-flight 요청을 취소하거나 응답 scope를 대조해 버리고, 타이머·revision·캐시를 모두 폐기한다.

이미 화면/기기에 내려간 자료 자체를 서버가 원격 회수할 수는 없다. 제거·세션 회수와 경쟁할 때 서버가 보장하는 것은 커밋 뒤의 신규 `/changes`와 리소스 재조회 차단이다. A는 404/401을 받은 즉시 로컬 범위를 정리해야 한다.

## 로컬 검증

전용 project id `baby-care-b03-local`만 사용한다. linked·공유·운영 DB에는 reset을 실행하지 않는다.

```bash
cd "/path/to/baby-care"
npm ci
cd apps/api
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --no-deps -r requirements-dev.lock
cd ../..
npm run test:supabase
```

통합 시험은 실행 때마다 `example.test` 합성 계정을 로컬 Auth에 만들고 Mailpit OTP로 로그인한다. 고정 비밀번호나 토큰은 문서/로그에 남기지 않는다. 계약 JSON의 사용자 UUID와 별칭은 fixture이며 실제 로그인 계정이 아니다.

검증한 시나리오는 두 보호자의 생성·반복 조회·수정·수면 종료·삭제, 멱등 재전송, 한 리소스 연속 변경 병합, 여러 리소스 동시 쓰기, 조회 중 쓰기, 강제 롤백, API 인스턴스 재생성, 최초/미래/90일 경계/500개 초과, 전체 재동기화 중 쓰기, 본인 기여자료 다수 삭제, 다른 아기·비구성원·제거 사용자·회수 세션·전체 삭제 차단, 개인 초안/동의/DeletionJob 비노출, 한 물리 DB 연결의 사용자 컨텍스트 교체, 실제 응답과 OpenAPI 일치다.

대표 로컬 TestClient/API 측정값:

| 측정 | 결과 |
| --- | --- |
| 실제 B-04 API CareEvent 쓰기 | 500건, 12.394초 |
| 500개 Changes 응답 JSON | 56,149 bytes |
| 빈 폴링 | p50 13.873ms, p95 30.911ms |
| 단일 CareEvent 재조회 | p50 12.593ms, p95 21.687ms |
| 두 계정 owner 폴링/재조회 | p50 7.888/7.022ms, p95 9.769/7.448ms |
| 두 계정 caregiver 폴링/재조회 | p50 7.890/7.011ms, p95 9.056/7.334ms |
| tail SQL 실행 | 0.141ms, `shared_changes_revision_lookup_idx` 사용 |
| DB | PostgreSQL 17.6 |

500개 응답은 499개 `CARE_EVENT`와 같은 범위에서 최종 상태로 합쳐진 `BABY` 1개다. 500번째 CareEvent를 쓰면 고유 리소스가 501개가 되어 부분 응답 없이 `resync_required=true`가 됐다.

이는 같은 Mac의 로컬 FastAPI TestClient와 로컬 Supabase 측정이다. 네트워크·브라우저 렌더·숨김/복귀를 포함한 화면 반영 시간이나 운영 용량 보장이 아니다.

## 남은 A/운영 인수와 Realtime 조건

- A: 전경 5초, 숨김 중지, 복귀 즉시 조회, 통신 단절, 캐시 version/tombstone, 계정·아기 전환, 두 실제 화면 반영을 브라우저에서 확인하고 화면 지연을 별도 측정한다.
- 운영: migration 적용, runtime 로그인, 이력 정리 스케줄, 모니터링/용량을 배포 환경에서 확인한다.
- Realtime: 멤버십별 비공개 채널, 발행 시점 현재 수신 권한 재검사, 제거 뒤 신규 발행 차단, 재가입 전 과거 채널 재사용 거부, 클라이언트 broadcast·presence 차단을 실제로 시험해야 한다. SEC30·SEC31 통과 전에는 계속 꺼 둔다.

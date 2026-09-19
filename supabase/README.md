# B-03/B-04/B-09 Supabase 데이터·권한·계정·기록·변경 기반

이 디렉터리는 B-03의 로컬 재현 가능한 기반이다. 업무 데이터는 `baby_data`, 정책 보조 함수는 `baby_private`에 두고 Data API가 노출하는 스키마는 `public`만 유지한다. 브라우저의 업무 테이블·뷰·RPC 직접 허용 목록은 비어 있다.

이 구현은 B-03 하위 계층 위에 B-04의 실제 Supabase JWT/JWKS 인증, OTP 재인증, 세션 회수, DB 멱등성, 계정·공동양육·기록 API와 B-09 폴링 변경 피드를 연결한다. 전용 로컬 스택에서는 Auth JWT → FastAPI → `baby_app` RLS와 Auth 로그아웃 → 기존 Storage 업로드 차단까지 검증한다. 운영 프로젝트 적용, B-05 파일 검증·완료·재생 URL, TUS 재개, B-09 Realtime, 삭제 실행기는 완료로 보지 않는다.

## API·기능 엔터티와 저장 구조

API 응답마다 테이블을 만들지 않고, 원본·권한·실행 상태와 재현에 필요한 값을 저장한다. 표시용 조합과 시점 계산은 조회 계층에서 만든다.

| 계약·기능 개념 | 주 저장 구조 | 저장하는 값 | 조회 시 계산·조합하는 값 |
|---|---|---|---|
| Baby, 현재 선택 | `babies`, `user_preferences` | 별칭, 생일, 수유 방식, 시간대, 상태, `context_revision`, 개인별 현재 아기 | 생후 일수, 현재 사용자가 선택 가능한 아기 목록 |
| BabyMembership, Invitation | `baby_memberships`, `invitations` | 현재 역할·상태, 초대 이메일, 토큰 해시, 만료·수락 상태 | 현재 DB 멤버십에 따른 관리 가능 여부 |
| Consent, 삭제 권리 | `consents`, `deletion_jobs` | 목적별 불변 동의 이력, 철회 연결, 삭제 범위·진행·미정리 범주 | 현재 유효 동의, 삭제 진행 표시 |
| Episode, 관측 | `observation_sessions`, `observation_windows`, `episodes` | 전경 관측 구간, 공백 이유, 사건 시작·종료·출처 | 사건 지속 시간, 관측 공백 표시 |
| AudioAsset, 업로드 허가 | `audio_assets`, `audio_upload_grants` | private object key, 바이트·체크섬·품질 상태, 정확한 사용자·세션·15분 허가 | 업로드 가능 여부. 재생 URL은 저장하지 않음 |
| Analysis | `analyses`, `context_snapshots`, `recommendations` | 실행 상태·토큰·모드·품질·후보·버전, 사용한 시점 스냅샷·근거 | API 진행 표현과 근거 표시. 생활 맥락을 모델 결과로 합치지 않음 |
| CareEvent | `care_events` | 수유·수면·기저귀 원본 사건, 작성자·수정자, `version` | 타임라인, 최근 기록, 간격 |
| 행동·관찰·반응 | `caregiver_observations`, `action_attempts`, `action_groups`, `action_group_members`, `outcomes`, `state_observations` | 실제 수행·보호자 관찰·반응·출처를 각각 분리 | 사건별 행동 순서와 전후 상태 |
| 원문·정규화 | `raw_care_entries`, `normalization_runs`, `label_annotations` | 작성자 전용 원문, `input_revision`, 실행 상태·결과, 확인 라벨 | 최신 revision의 확인 가능 초안과 확정 기록 |
| 개인 알림 | `reminder_settings`, `reminders`, `record_coverage` | 수신자별 켜기·미루기·확인 상태, 근거 범위 | 공유 패턴에서 개인별 다음 알림 계산 |
| 세션·재인증 | `observed_auth_sessions`, `session_revocation_rules`, `revoked_sessions`, `session_revocation_jobs`, `reauthentication_challenges`, `reauthentication_proofs` | 관측 세션, 회수 범위·상태, OTP challenge, proof 해시·소비 시각 | 현재 JWT 차단, 다른 세션 범위, 민감 작업 proof 유효성 |
| 중복 방지·보안 시도 | `idempotency_records`, `security_attempts` | 정규화 요청 해시, 결과 참조, 최소 7일 만료, 대상 해시와 성공 여부 | 같은 요청 복구, 다른 본문 409, 초대·재인증 시도 제한 |
| 아동 처리 게이트 | `guardian_verifications` | 확인 상태·수단·정책 버전·시각 | 운영 처리 허용 여부. OWNER·이메일 OTP만으로 VERIFIED가 되지 않음 |
| 공동 변경 조회 | `shared_change_feed_state`, `shared_changes` | 아기별 현재/보관 revision, 리소스 type·ID·version·삭제 여부 | `(since,current]`의 리소스별 최종 상태, 상한·보관 경계의 전체 재동기화 |

상담 대화·개인 기억은 이 스키마에 넣지 않았다. B-14에서 계약과 본인 전용 접근 범위를 먼저 확정한다.

## 제약과 권한 경계

- 모든 식별자는 UUID, 시각은 `timestamptz` UTC instant로 저장한다. 모르는 시각·측정값은 `null`이고 측정값 `0`과 구분한다.
- 사건·음원·분석·행동·관찰·원문 연결에는 `(baby_id, resource_id)` 복합 외래키를 사용한다. 다른 아기의 리소스 연결은 DB가 거부한다.
- 부분 고유 인덱스와 지연 constraint trigger가 아기당 활성 OWNER 1명, 사용자당 활성 소유 아기 1명, 아기·사용자 활성 멤버십 1개, 진행 중 수면·관측 세션 1개를 보장한다.
- 상태 전이는 단방향 allow-list이고 `version`이 있는 수정 레코드는 정확히 1씩 증가해야 한다. `input_revision`은 원문 변경 세대이고 실행 토큰과 섞지 않는다.
- `baby_app` 요청에서는 작성자·수정자·확인자·업로더·요청 세션을 트랜잭션 문맥으로 덮어쓴다. 최초 작성자와 확인자는 이후 변경할 수 없다.
- OWNER는 관리와 다른 작성자의 **확정 공유 기록** 수정이 가능하다. CAREGIVER는 본인 작성 공유 기록만 수정한다. 미확인 원문·정규화 초안과 알림 설정은 OWNER에게도 공개하지 않는다.
- ACTIVE 멤버십과 ACTIVE 아기를 매 문장에서 다시 확인한다. 제거·탈퇴·`DELETING` 이후 일반 접근은 차단한다. 과거 구성원은 회수되지 않은 세션에서 본인 동의와 본인 삭제 진행만 조회할 수 있다.

DB 역할은 다음처럼 분리한다.

| 역할 | 용도 | 권한 |
|---|---|---|
| 배포별 관리 로그인 | 마이그레이션·역할 생성·운영 관리 | 저장소가 자격증명을 만들지 않음. 요청 처리에 사용 금지 |
| `baby_app` | FastAPI 런타임이 `SET LOCAL ROLE`로 사용하는 NOLOGIN 그룹 | `baby_data` SELECT/INSERT/UPDATE와 FORCE RLS, DELETE·DDL·RLS 우회 없음 |
| `baby_policy_owner` | RLS·Storage boolean helper 소유자 | NOLOGIN, 필요한 권한 입력 테이블 SELECT만, BYPASSRLS. 업무 자료 반환 함수 없음 |
| `anon`, `authenticated`, `service_role` | Supabase Data API 역할 | `baby_data`의 GRANT 없음. `authenticated`에는 Storage 정책 평가용 boolean/거부 helper만 EXECUTE |

운영의 runtime 로그인 생성과 비밀번호 주입은 배포 작업이다. runtime 로그인은 `baby_app`을 상속하지 않고 요청 트랜잭션에서만 역할과 검증된 문맥을 설정해야 한다.

```sql
begin;
set local role baby_app;
select set_config('baby.request_user_id', :verified_user_id, true);
select set_config('baby.request_session_id', :verified_session_id, true);
-- 업무 SQL
commit;
```

두 설정 중 하나라도 없거나 `revoked_sessions`에 있으면 RLS가 거부한다. `SET LOCAL`이므로 commit·rollback 뒤 같은 풀 연결에 값이 남지 않는다. 사용자 ID는 요청 본문이나 오래된 JWT 역할에서 가져오지 않고, B-01 `AuthenticationPort`가 검증한 주체와 실제 현재 세션만 전달해야 한다.

## 공동 변경 피드

새 migration은 기존 아기에도 revision 1의 상태 행을 만들며 `since_revision=0`에서 전체 동기화를 요구한다. 과거 변경을 소급 추측하지 않는다. `shared_changes`에는 공동 리소스 본문이 아니라 type·ID·resource version·삭제 여부만 저장한다.

- `baby_private.record_shared_changes`는 현재 세션·ACTIVE 멤버십·ACTIVE 아기를 다시 확인하는 `baby_app`용 함수다. 업무 변경과 같은 트랜잭션에서 호출한다.
- `record_shared_changes_unchecked`는 같은 보안 소유자의 삭제 함수처럼 정책 소유자에게만 허용하며, 일반 runtime·브라우저는 실행할 수 없다.
- 아기별 상태 행 UPDATE가 revision을 하나 할당하고 모든 해당 변경을 같은 revision에 기록한다. PostgreSQL sequence나 시각을 커밋 순서로 가정하지 않는다.
- CareEvent 쓰기가 공개 Baby context/version도 바꾸므로 두 리소스를 같은 revision에 기록한다. resource version, Baby context revision, feed revision은 용도가 다르다.
- 조회자는 상태 행을 `FOR SHARE`로 잠그고 권한을 다시 확인한다. 제거·탈퇴·전체 삭제는 피드 상태를 먼저 갱신한 뒤 접근 상태를 바꾸는 동일 잠금 순서를 사용한다.
- 기본 보관은 90일이고 고유 리소스 응답 상한은 500개다. 관리자 전용 `prune_shared_change_history`가 보관 경계와 삭제를 같은 트랜잭션에서 옮긴다. 운영 스케줄은 후속이다.
- 두 테이블은 FORCE RLS이고 `anon`, `authenticated`, `service_role`에 업무 schema/table/RPC 권한을 주지 않는다. `baby_app`도 임의 피드 INSERT나 직접 state 갱신을 할 수 없다.

## private Storage

`baby-audio` bucket은 private이고 정확히 25,000,000바이트가 상한이다. 일반 업로드 정책은 다음을 모두 만족할 때 INSERT만 허용한다.

- 로컬 Auth가 발급·검증한 JWT의 `sub`와 `session_id`
- ACTIVE 아기와 현재 ACTIVE 멤버십
- `audio_assets`의 `ALLOCATED` 상태
- 같은 업로더·세션·bucket·object key를 가진 미완료·미취소 허가
- `recorded_at` 뒤 최대 15분 이내이고 아직 만료되지 않은 허가
- 회수되지 않은 세션

SELECT·UPDATE·DELETE는 허용하지 않는다. private audio 조회·목록·직접 서명은 명시적 권한 오류로 끝나며, 같은 경로 upsert도 거부한다. B-05가 파일 디코딩·품질 검사 뒤 자산 상태와 업로드 완료를 갱신하고, 현재 권한을 다시 검사해 60초 이하 재생 URL을 서버에서만 발급해야 한다.

## 로컬 재현

Docker Desktop, Node.js, npm, Python 3.12.12가 필요하다. CLI는 전역 설치 대신 잠금된 개발 의존성 `supabase@2.117.0`을 사용한다. 실제 AuthorizationPort 통합 시험도 포함하므로 B-01 API 개발 의존성을 먼저 설치한다.

```bash
cd apps/api
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip==26.2.1
python -m pip install --require-hashes --no-deps -r requirements-dev.lock
cd ../..
npm ci
npm run test:supabase
```

이 명령은 `baby-care-b03-local` 로컬 스택을 시작하고 **해당 로컬 DB만 초기화**한 뒤 migration, 합성 seed, DB·RLS·문맥·Auth/Storage HTTP 시험을 순서대로 실행한다. linked/운영 프로젝트에 `db reset`, `db push`를 실행하지 않는다.

API 단계는 단위·실제 로컬 통합 시험 57개를 함께 실행하고 90% coverage 기준을 적용한다. 2026-09-20 B-09 최종 실행에서는 90.41%였으며, 수치는 코드 변경에 따라 달라져도 기준 미달이면 명령이 실패한다.

개별 실행은 다음과 같다.

```bash
npm run supabase:start
npm run supabase:reset
npm run test:supabase:db
npm run test:supabase:context
npm run test:supabase:api-db
npm run test:supabase:http
npm run supabase:stop
```

`seed.sql`의 `.invalid` 계정은 로그인할 수 없는 합성 DB fixture다. HTTP 시험은 매 실행마다 로컬 Auth API로 로그인 가능한 합성 사용자를 만들고 발급된 JWT로 Storage를 호출한다. B-04 API 시험은 `example.test` 합성 계정과 로컬 Mailpit의 숫자 OTP를 사용한다. `local_smtp`와 전용 템플릿은 실제 메일을 발송하지 않으며 토큰·비밀번호는 출력하거나 저장하지 않는다. 환경 변수 이름과 placeholder는 [.env.example](.env.example)에만 두며 실제 값은 커밋하지 않는다.

## 검증 범위와 인수 조건 연결

| 구분 | 로컬에서 실제 실행한 하위 시험 | 전체 항목 판정 |
|---|---|---|
| 스키마·제약 | 빈 DB reset, 전체 migration+seed, 복합 FK, 중복 멤버십·OWNER, 정확한 15분·25,000,000바이트, 상태 전이 | SEC04 및 AC42의 DB 하위 조건만 통과 |
| RLS·역할 | OWNER·CAREGIVER 읽기/수정, 타 아기, 타인 초안·정규화, 개인 알림, 제거 사용자 권리 경로, 멤버십 회수·아기 삭제 차단, 작성·수정·확인자 보호 | AC01·03·41·43·44와 SEC03·05·06·08·18·21의 DB 하위 조건만 통과 |
| 직접 접근 | anon/authenticated Data API 테이블 404, RPC 404, GRANT·뷰·함수 ACL 검사 | SEC09 로컬 하위 시험 통과 |
| 서버 문맥 | 한 연결에서 사용자 교차 처리, rollback·commit 뒤 문맥 소거, 누락 문맥 차단, B-01 실제 pool과 B-04/B-09 요청 트랜잭션의 현재 사용자·세션·피드 범위 검사 | SEC10의 로컬 DB·실제 adapter/API 하위 시험 통과 |
| JWT·공동양육 API | 실제 로컬 Auth JWT/JWKS, 아기·OWNER 원자 생성, OTP proof, 초대 발급·수락·재발급·재가입·경쟁·만료·불일치, 탈퇴 후 본인 권리, 기록·타임라인·행동 연결, 작성자 초안·revision, 동시 수정·수면·삭제 재시도, 두 계정 변경 폴링·누락 복구·회수 차단 | AC01·03·13~16·21~22·39~44와 SEC05·07~08·12~21·32·48·62의 명시된 로컬 API 하위 조건만 통과. Realtime SEC30~31·브라우저·운영은 미실행 |
| 세션 회수·Storage HTTP | 실제 로컬 Auth JWT의 OWNER·CAREGIVER 성공, 비로그인·비구성원·다른 아기·업로더·세션·경로·만료·취소·초과 크기·덮어쓰기·목록·다운로드·서명·삭제 거부, `OTHERS|ALL` 제공자 로그아웃과 회수 JWT의 API·기존 STANDARD 업로드 차단 | SEC18~24·27의 로컬 STANDARD/API 하위 조건 통과. TUS·기존 재생 URL·브라우저 캐시는 미실행 |

AC02의 비보관 분석 전체 흐름, 삭제 객체 실제 정리와 기존 재생 URL, AC41의 구독, AC43의 실제 학습 export 무효화, 브라우저 캐시를 포함한 AC44 전체는 미실행이다. 대표 변경 경로에서 같은 키 동시 전송·새 API 인스턴스 재전송·세션 회수 응답 유실 복구를 시험했지만 모든 변경 경로의 프로세스 강제 종료·동시 경쟁을 전부 시험한 것은 아니다. SEC24의 TUS 재개·잔여 파일 정리와 SEC27의 서버 발급 60초 URL도 미실행이다.

## B-01·B-04·B-05 연결

- PR #5가 develop에 병합된 뒤 기존 구조를 재사용했다. `services/postgres.py`의 `PostgresAuthorizationPort`가 `baby_app` pool 트랜잭션에서 검증된 주체·session ID를 `SET LOCAL`로 전달하고 현재 멤버십·아기 상태를 조회한다.
- DB URL과 JWT/JWKS가 설정되면 역할 전환과 JWKS 조회를 실제 probe한다. 둘 중 하나가 실패하면 readiness는 503이다.
- 운영에서는 migration 로그인과 별도의 runtime 로그인을 만들고 `baby_app` SET 권한만 줘야 한다. 저장소는 실제 로그인이나 비밀번호를 생성·커밋하지 않는다.
- B-04는 초대 수락·탈퇴·동의·삭제·기록 API의 원자적 트랜잭션, 409 version 비교, DB 멱등성 실행기, OTP 재인증과 세션 회수를 연결했다. B-09는 그 트랜잭션에 공동 변경 이력을 연결하고 현재 권한의 폴링 조회를 제공한다. 아동 정보 실사용 확인은 승인 정책이 없어 fail-closed이며 합성 시험만 했다.
- B-05는 업로드 허가 발급량·TUS, 실제 컨테이너/코덱·길이·체크섬 검사, 완료 전환·고아 정리, 서버 재생 URL 발급을 구현한다.

# B-04 → A-03·A-04 연동 인계

2026년 9월 20일 · 계약 1.1.0 · 로컬 Supabase 검증 기준

## A가 지금 연결할 수 있는 결과

- 첫 OTP 계정이 아기를 만들면 OWNER 멤버십까지 한 트랜잭션으로 생성된다. 계정별 현재 아기 선택은 공유 상태가 아니라 개인 상태다.
- OWNER가 새 OTP 재인증 뒤 지정 이메일 초대를 발급·재발급·취소하고, 해당 이메일 계정이 한 번만 수락해 CAREGIVER가 된다. 탈퇴 뒤 다시 수락하면 이전 행을 되살리지 않고 새 멤버십 ID와 이어진 동의 이력을 만든다.
- 활성 구성원은 동의와 확정 CareEvent를 공유한다. 작성자 전용 CareEntry 초안은 OWNER에게도 보이지 않는다.
- CareEvent CRUD, 진행 중 수면 중복 거부, cursor 타임라인, 기존/신규 CareEvent를 이용한 행동 연결을 사용할 수 있다.
- 나가기·본인 기여자료 삭제·전체 아기 삭제는 서로 다른 동작이다. 삭제 요청은 즉시 일반 조회에서 제외되고 `DeletionJob`으로 조회·재시도한다.
- `CURRENT|OTHERS|ALL` 세션 회수는 로컬 차단 기록과 Supabase refresh-session 종료를 함께 수행한다. 제공자 종료 실패는 성공으로 표시하지 않으며, 현재 JWT가 차단된 뒤 응답이 유실돼도 같은 키로 회수 작업을 복구한다.

## operationId 분류

| 분류 | operationId |
|---|---|
| 기존 계약에서 B-04가 구현 | `listBabies`, `createBaby`, `getActiveBaby`, `setActiveBaby`, `patchBaby`, `listMembers`, `patchMyRelationship`, `removeMembership`, `listInvites`, `createInvite`, `acceptInvite`, `revokeInvite`, `listConsents`, `setBabyConsent`, `setMyTrainingConsent`, `createCareEvent`, `getCareEvent`, `patchCareEvent`, `deleteCareEvent`, `createAction`, `createCareEntry`, `getCareEntry`, `patchCareEntry`, `deleteCareEntry`, `getTimeline`, `deleteBabyData`, `deleteMyContributions`, `getDeletion`, `retryDeletion` |
| 계약 1.1.0에서 추가하고 구현 | `reissueInvite`, `listMyCareEntries`, `createReauthenticationChallenge`, `createReauthenticationProof`, `revokeSessions`, `getSessionRevocation`, `getChildDataVerification` |
| 계약에는 있으나 후속 B 작업 | episode 생성·종료/분석/업로드/정규화 확인/요약·알림 등. 정확한 목록은 OpenAPI를 따르고 FastAPI `/openapi.json`의 `x-business-contract.implemented_operations`에는 넣지 않음 |
| 현재 계약 밖 후속 | B-09 `/changes`·Realtime, B-11 집계 실행기, B-12 학습 사본 정리, B-14 상담 |

`getTimeline`의 이번 구현은 CareEvent 항목만 반환한다. episode·분석·상태 항목은 후속 B-06~B-08이 저장·조회 경계를 구현한 뒤 같은 union에 추가한다. `createAction`은 기존 episode가 있어야 하며 episode 생성 API는 B-06 범위다.

## 로컬 실행 주소와 설정

루트에서 다음을 실행한다. 이 작업은 project id `baby-care-b03-local`인 전용 로컬 스택만 초기화한다. linked·운영 프로젝트에는 실행하지 않는다.

```bash
cd apps/api
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip==26.2.1
python -m pip install --require-hashes --no-deps -r requirements-dev.lock
cd ../..
npm ci
npm run supabase:start
npm run supabase:reset
./scripts/run-b04-local-api.sh
```

| 용도 | 주소 |
|---|---|
| FastAPI | `http://127.0.0.1:8080` |
| FastAPI 실제 라우트 문서 | `http://127.0.0.1:8080/openapi.json` |
| Supabase Auth/Storage | `http://127.0.0.1:54321` |
| 로컬 OTP Mailpit | `http://127.0.0.1:54324` |
| A 초대 진입 기본값 | `http://127.0.0.1:3000/invite#<token>` |

Studio와 Realtime은 이 최소 로컬 스택에서 꺼져 있다. 공개 로컬 anon key는 `npx supabase status`에서 실행 중에만 읽어 A의 로컬 환경변수에 둔다. 저장소·문서·스크린샷·브라우저 로그에 access/refresh token, OTP, 초대 fragment, proof 또는 DB URL을 넣지 않는다. `run-b04-local-api.sh`는 값을 출력하지 않고 프로세스 환경에만 전달하며 종료 시 로컬 `postgres`의 임시 `SET ROLE baby_app` 권한을 되돌린다.

## 합성 계정과 아기 배치

고정 비밀번호나 토큰을 공유하지 않는다. A는 서로 다른 `example.test` 이메일 3개로 Supabase `signInWithOtp`를 호출하고 Mailpit의 숫자 OTP를 `verifyOtp(type: "email")`로 확인한다.

| 별칭 | 만드는 법 | 기대 역할·배치 |
|---|---|---|
| OWNER | 첫 OTP 계정으로 `createBaby` 호출 | 생성한 아기의 OWNER |
| CAREGIVER | 둘째 OTP 계정. OWNER가 그 이메일로 초대한 뒤 fragment 토큰으로 `acceptInvite` | 같은 아기의 CAREGIVER |
| OUTSIDER | 셋째 OTP 계정. 초대하지 않음 | 아기 없음; 대상 ID를 알아도 404 |

각 mutation은 새 UUID를 `Idempotency-Key`와 body의 `client_request_id`에 동일하게 넣는다. 응답이 유실됐을 때만 같은 키·같은 body로 재전송한다. 다른 body에 키를 재사용하면 409다.

## A-03 연결 순서

1. Auth 세션을 얻은 뒤 `GET /v1/babies`; 없으면 `POST /v1/babies`, 선택은 `PUT /v1/me/active-baby`로 저장한다.
2. 중요 작업에서 `REAUTH_REQUIRED`를 받으면 `POST /v1/auth/reauthentication/challenges` → 새 이메일 OTP 세션 획득 → `POST /v1/auth/reauthentication/proofs` 순서로 진행한다. 반환 proof는 `X-Reauthentication-Proof`에 한 번만 보내고 저장하지 않는다.
3. 초대는 OWNER가 `POST /v1/babies/{baby_id}/invites`; 링크 fragment를 서버·분석 로그에 보내지 말고 첫 화면에서 읽은 즉시 주소에서 제거한다. 수락은 로그인한 지정 이메일로 `POST /v1/invites/accept`한다.
4. 최초 링크 응답을 잃었으면 같은 멱등 요청을 재전송해도 원문 링크는 다시 나오지 않는다. `link_reissue_required=true`이면 새 OTP proof를 받은 뒤 `POST /v1/invites/{invite_id}/reissue`하고 이전 링크는 폐기한다.
5. 로그아웃은 기기의 마이크·메모리·캐시를 먼저 정리하고 `POST /v1/auth/session-revocations` 결과를 구분한다. `FAILED`/503이면 로컬 로그아웃과 서버 제공자 회수 완료를 같은 문구로 표시하지 않는다.
6. `GET /v1/babies/{baby_id}/child-data-verification`의 `production_processing_allowed=false`이면 실사용 아동 자료 수집·외부 처리를 열지 않는다. OWNER·OTP 로그인은 법정대리인 확인이 아니다.

## A-04 연결 순서

1. 공유 기록은 `POST /v1/babies/{baby_id}/care-events`, 조회/수정/삭제는 `/v1/care-events/{care_event_id}`를 사용한다. 수정·삭제에는 화면이 읽은 `version`을 보낸다.
2. 409 `VERSION_CONFLICT`이면 `details.current_resource`를 현재 화면 값과 비교해 사용자가 다시 결정하게 한다. 자동 덮어쓰지 않는다. `SLEEP_ALREADY_ACTIVE`는 진행 중 수면으로 이동할 수 있는 상태로 표시한다.
3. 타임라인은 `GET /v1/babies/{baby_id}/timeline`; `next_cursor`를 그대로 전달하고 직접 해석·변조하지 않는다.
4. 개인 초안은 `POST /v1/babies/{baby_id}/care-entries`와 `GET /v1/babies/{baby_id}/care-entries`를 사용한다. 다른 구성원에게 공유됐다고 표시하지 않는다. 수정에는 `input_revision`; 오래된 정규화 결과는 `SOURCE_REVISION_CHANGED` 또는 STALE 처리된다.
5. 아기 전환 전에 초안 저장이 실패하면 전환을 끝내지 않는다. 응답만 유실됐으면 원래 아기·같은 키·같은 body로 복구한다. 새 아기에 대신 저장하지 않는다.
6. 삭제 202 응답은 완료가 아니다. `status_url` 또는 `GET /v1/deletions/{id}`로 `PENDING|RUNNING|FAILED|COMPLETE`를 표시하고 `FAILED`일 때만 계약의 retry 동작을 제공한다.

## 오류 화면 기준

| 상태 | 대표 코드 | A의 표현 |
|---|---|---|
| 401 | `AUTH_REQUIRED`, `TOKEN_EXPIRED`, `INVALID_TOKEN`, `SESSION_REVOKED`, `REAUTH_REQUIRED` | 로그인/갱신/새 OTP를 구분. 회수 세션은 캐시·마이크 정리 후 재로그인 |
| 403 | `OWNER_ONLY`, `AUTHOR_ONLY`, `INVITE_EMAIL_MISMATCH`, `CONSENT_REQUIRED`, `CHILD_DATA_VERIFICATION_REQUIRED` | 현재 구성원이지만 역할·동의·확인이 부족함. 404로 바꾸지 않음 |
| 404 | `RESOURCE_NOT_FOUND` | 비구성원·타 아기·타인 초안의 존재를 추측할 정보를 표시하지 않음 |
| 409 | `VERSION_CONFLICT`, `SOURCE_REVISION_CHANGED`, `IDEMPOTENCY_KEY_REUSED`, `INVITE_ALREADY_USED`, `SLEEP_ALREADY_ACTIVE` | 최신 허용 값과 사용자 입력 비교 또는 안전한 재시도 안내 |
| 410 | `INVITE_EXPIRED`, `INVITE_REVOKED`, `RESOURCE_DELETED` | 새 초대/복구 불가/삭제 완료 상태를 구분 |
| 503 | `AUTH_PROVIDER_REVOCATION_FAILED`, `SERVICE_UNAVAILABLE` | 로컬 정리는 유지하되 서버 회수·의존 서비스 완료로 표시하지 않음 |

## 확인 경계

로컬 서버에서 실제로 확인한 것은 JWT/JWKS, OTP proof의 작업 결합·일회성, 초대·경쟁 수락·재가입, 탈퇴 후 본인 동의 철회·삭제 권리, 같은 키 동시 전송·새 API 인스턴스 복구, CareEvent 동시 수정·수면 경쟁, 초안·타임라인·삭제 재시도, Supabase 세션 회수 뒤 API와 기존 STANDARD Storage 업로드 거부다. A는 위 흐름의 브라우저 URL fragment 제거, 캐시/QueryClient 정리, 계정·아기 전환, 실패·응답 유실 UI, 모바일 접근성을 아직 확인해야 한다. 운영 Supabase 설정, TUS·재생 URL, 삭제 실행기와 Storage/학습/백업 정리, Realtime, 모델·LLM은 후속 B 작업이며 이 인계로 완료된 것으로 취급하지 않는다.

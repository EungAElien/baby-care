# Baby Care API — B-04 계정·기록 + B-05 음원 + B-09 변경 조회 + B-11 날짜 집계

이 폴더는 계약 1.1.1 가운데 B-04 계정·공동양육·기록, B-05 private 음원 수신·품질·보관, B-09 공동 변경 조회, B-11 날짜별 기록 집계를 실행하는 FastAPI 서비스입니다. 로컬 Supabase의 실제 Auth JWT/JWKS, 최소 권한 `baby_app` 트랜잭션, DB 멱등성·RLS·Storage 회수 경계를 함께 검증합니다.

## 현재 포함된 범위

- FastAPI 실행 진입점, 환경 설정, 라우터, 공통 오류 처리, 요청별 `request_id`
- 아기 생성·목록·선택·프로필, 구성원·초대·동의 API
- CareEvent CRUD·타임라인, 사건 행동 연결, 작성자 전용 서버 초안
- 아기 시간대 기준 날짜별 수유·수면·기저귀 요약. 확인 범위가 없으면 기록 부재를 실제 0으로 단정하지 않고, 진행 중 수면은 조회 시각까지만 계산합니다. B-11 준비 알림과 B-07 상태 관찰 연동은 후속입니다.
- 아기·멤버십·CareEvent의 내구성 있는 변경 이력과 `getChanges` 폴링 복구
- MANUAL·FILE 사건 생성, 활성 관측 세션에 연결된 AUTO 사건, 실제 연결 자료 사건 상세
- private STANDARD/TUS 업로드 승인·재발급·취소와 실제 객체 완료 검증
- 고정 FFmpeg의 WAV/PCM·WebM/Opus·MP4/AAC·raw AAC 검사와 source-rate PCM 파생물
- 보관 동의·기한·현재 권한 기반 60초 재생 URL과 재시작 가능한 실제 Storage 정리 worker
- 삭제 요청·진행 조회·재시도와 본인 기여자료 삭제 요청
- 실제 Supabase JWT/JWKS 검증과 이메일 확인, OTP 재인증 proof, 세션 범위별 회수
- 계약의 `ApiError` 형식과 입력 검증 오류 변환
- B-03 `baby_app` 역할과 요청 트랜잭션 `SET LOCAL` 문맥을 사용하는 현재 권한·업무 처리
- `(user_id, method, path, Idempotency-Key)` DB 예약과 7일 이상 결과 참조 보존
- 민감정보를 입력으로 받지 않는 허용 목록 기반 JSON 로그
- liveness/readiness 분리, pytest·Ruff·mypy·컨테이너·GitHub Actions 기반
- 별도 V1 B 프로필의 고정 M2D 레지스트리, 시작 시 1회 적재, 모델 readiness

`/v1`의 기준은 저장소 루트의 `contracts/openapi계약.json`입니다. FastAPI의 `/openapi.json`은 실제 라우트만 만들고 `x-business-contract.implemented_operations`에 구현된 B-04·B-05 operationId와 `getChanges`·`getDailySummary`를 표시합니다. B-06 분석·정규화 확인 등 후속 계약 경로를 구현됐다고 노출하지 않습니다.

## 버전과 재현 설치

- Python: `3.12.12` (`.python-version`)
- FastAPI: `0.141.1`
- Pydantic: `2.13.5`
- Uvicorn: `0.53.0`
- 전체 직접·전이 의존성: 해시가 포함된 `requirements.lock`, `requirements-dev.lock`

macOS에서 다음과 같이 설치합니다.

```bash
cd apps/api
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip==26.2.1
python -m pip install --require-hashes --no-deps -r requirements-dev.lock
cp .env.example .env
```

실제 DB URL·JWT 설정·서버 비밀은 `.env`에만 두고 커밋하지 않습니다. 예시 파일은 비밀값을 포함하지 않습니다. 필요한 B-04 설정은 다음과 같습니다.

- `BABY_CARE_DATABASE_URL`: 요청용 로그인. `baby_app`을 상속하지 않고 `SET ROLE baby_app`만 허용합니다.
- `BABY_CARE_SUPABASE_JWT_ISSUER`, `...AUDIENCE`, `...JWKS_URL`: 토큰 issuer·audience·회전 공개키 검증값.
- `BABY_CARE_SUPABASE_URL`, `...PUBLISHABLE_KEY`: 현재 사용자 확인과 Auth 로그아웃 호출. secret/service-role key를 사용하지 않습니다.
- `BABY_CARE_REAUTHENTICATION_PROOF_SECRET`: 32바이트 이상 서버 비밀. proof 원문은 DB에 저장하지 않습니다.
- `BABY_CARE_INVITE_BASE_URL`: A의 초대 진입 주소. 토큰은 URL fragment에 붙고 최초 발급·재발급 응답에서만 반환됩니다.
- `BABY_CARE_CHILD_DATA_PRODUCTION_ENABLED=false`: 승인된 법정대리인 확인 정책이 준비되기 전에는 그대로 둡니다.
- `BABY_CARE_SUPABASE_SECRET_KEY`: B-05 서버가 원본을 검증하고 서명·삭제할 때만 쓰는 서버 비밀입니다. 브라우저·`NEXT_PUBLIC`에 넣지 않습니다.
- `BABY_CARE_SUPABASE_STORAGE_URL`: hosted TUS의 직접 Storage origin입니다. 생략하면 `SUPABASE_URL`을 사용합니다.
- `BABY_CARE_AUDIO_FFMPEG_PATH`, `...FFPROBE_PATH`, `...VERSION_PREFIX`, `...DECODE_CONCURRENCY`: digest로 고정한 7.1.1 디코더 경계입니다.

잠금 파일을 갱신할 때는 Python 3.12 환경에서 `uv==0.12.17`을 사용합니다.

```bash
uv pip compile pyproject.toml --python-version 3.12 --generate-hashes --output-file requirements.lock
uv pip compile pyproject.toml --python-version 3.12 --extra dev --generate-hashes --output-file requirements-dev.lock
```

## B-02 LLM 선검증

합성 한국어 정규화·상담 자료, 분리 프롬프트, 오프라인 판정기와 명시적으로만
실행되는 `gpt-5.6-terra` Responses API smoke는
[`evals/llm_prevalidation`](evals/llm_prevalidation/README.md)에 있습니다. 이 도구는
현재 FastAPI route에 연결되지 않으며 B-02·B-07·B-14의 완료나 실사용 외부 전송을
의미하지 않습니다.

```bash
PYTHONPATH=src .venv/bin/python -m baby_care_api.llm_eval \
  --mode offline \
  --report evals/llm_prevalidation/reports/offline-baseline.json
```

## V1 B M2D 실행 프로필

일반 API는 `BABY_CARE_M2D_ENABLED=false`가 기본이며 기존 경량 이미지와 잠금 파일을
그대로 사용합니다. 전용 `Dockerfile.m2d`만 다음 고정 실행물을 추가합니다.

- 모델 `m2d-supervised-v1.0.0-final-B`, 학습된 `donate` head와 5개 고정 라벨 순서
- 전처리 `m2d-logmel-v1.0.0`, 라벨 매핑 `donate-cause-labels-v1.0.0`
- Python 3.12.12, CPU PyTorch 2.14.0+cpu, 고정 Python 잠금 파일
- digest로 고정한 FFmpeg 7.1.1과 해시를 확인한 최소 M2D 구조 코드
- CPU float32, Uvicorn worker 1개, 프로세스당 동시 추론 1개

가중치·개발 음원·특징은 Git이나 일반 build context에 넣지 않습니다. M2D 구조 코드도
신뢰한 로컬 사본을 준비 스크립트로 검사한 뒤 별도 named build context로만 전달합니다.
예시는 다음과 같습니다. 각 경로는 서버 운영자가 정하고 요청으로 받지 않습니다.

```bash
python3 scripts/model_runtime/prepare_m2d_source.py \
  --source-root "$M2D_SOURCE_ROOT" \
  --output "$M2D_BUILD_CONTEXT"

docker buildx build \
  --platform linux/amd64 \
  --build-context m2d_source="$M2D_BUILD_CONTEXT" \
  --file apps/api/Dockerfile.m2d \
  --tag baby-care-m2d-v1-b:local \
  apps/api

docker run --rm --platform linux/amd64 \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --publish 127.0.0.1:8080:8080 \
  --mount type=bind,src="$V1_B_BUNDLE",dst=/opt/baby-care-runtime/model/selected,readonly \
  baby-care-m2d-v1-b:local
```

lifespan 시작 과정이 레지스트리·파일·버전·구조를 검사하고 전체 가중치와 전처리 객체를
한 번 적재한 뒤 작은 예열 추론을 수행합니다. 이 과정이 끝날 때까지 HTTP 포트가 열리지
않습니다. 성공한 객체는 요청 사이에 재사용되며 health 조회는 파일을 다시 읽거나 추론하지
않습니다. 실패를 잡고 서버가 열린 뒤에는 liveness만 200이고, 활성화된 모델 check와 전체
readiness는 503입니다. health나 요청으로 자동 재시도하거나 STUB으로 바꾸지 않습니다.
고정 파일·설정을 복구한 후 프로세스를 다시 시작해야 합니다.

현재 운영 입력으로 허용한 형식은 검증된 PCM WAV뿐입니다. CAF·3GP 등 압축 입력은 개발
자료로 Linux 디코더 차이만 측정했으며 지원 완료가 아닙니다. 모델 출력은 고정 순서의
점수일 뿐, 보정된 원인 확률이나 임상 진단이 아닙니다. `calibration_status=NOT_VALIDATED`,
`release_ready=false`를 유지하며 `/capabilities`와 공개 분석 API를 활성화하지 않습니다.
빌드·실측 명령, 실패 결과와 남은 일은
[V1 B 런타임 인계](../../docs/handoffs/b02-b06-v1-model-runtime.md)에 있습니다.

## 로컬 실행과 상태 확인

```bash
PYTHONPATH=src python -m baby_care_api
```

다른 터미널에서 확인합니다.

```bash
curl -i http://127.0.0.1:8080/health/live
curl -i http://127.0.0.1:8080/health/ready
```

- `GET /health/live`: 프로세스가 HTTP 요청을 처리하면 200을 반환합니다.
- `GET /health/ready`: 필수 인증·DB probe가 실제 연결되기 전에는 503입니다. 일반 프로필의 모델은 선택 항목이지만 V1 B 프로필에서는 필수입니다. 활성화 상태의 누락 경로·무결성/버전/구조 오류·적재/예열 실패는 모델 check와 전체 readiness를 503으로 유지합니다. 모델 성공이 인증·DB 실패를 가리지 않습니다.
- 모든 응답은 서버가 새로 만든 UUID `X-Request-ID`를 포함합니다. 오류 본문의 `request_id`와 같습니다.

운영 상태 경로는 정적 최소 응답이며 업무 데이터·자격 증명·내부 예외를 노출하지 않습니다.

## 검사

빠른 로컬 검사와 실제 DB 통합 검사의 공통 진입점은 저장소 루트에 있습니다.

```bash
cd ../..
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:quick
npm run verify:container
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:integration
```

`verify:quick`은 Ruff format·lint, mypy와 DB 없는 단위 테스트를 포함합니다. `verify:integration`은 매번 고유 project id와 빈 포트로 전용 Supabase를 만든 뒤 실제 로컬 Auth JWT/JWKS·DB·Storage 시험과 90% coverage 기준을 실행합니다. 필수 통합 그룹이 수집되지 않거나 환경 누락으로 skip되면 실패합니다. 현재 테스트 개수를 성공 조건으로 고정하지 않습니다.

로컬 통합 시험은 실행 때마다 `example.test` 합성 계정과 OTP를 격리된 Mailpit에 만들며 실제 사람에게 메일을 보내지 않습니다. 토큰·비밀번호와 Supabase 상태 원문은 보고서에 저장하지 않습니다. 성공·실패 모두 고유 컨테이너·network·volume·시험 이미지를 정리하고, 정리 실패도 검사 실패로 기록합니다.

컨테이너는 API 폴더를 build context로 사용합니다.

```bash
docker build --pull -t baby-care-api:b01 .
docker run --rm -p 8080:8080 baby-care-api:b01
```

기반 이미지는 Python `3.12.12-slim-bookworm`의 확인한 multi-architecture digest로 고정했습니다. Docker healthcheck는 liveness만 사용하며, readiness 503을 프로세스 장애로 오인하지 않습니다.

`verify:container`는 비루트 사용자와 Python·FFmpeg digest 고정을 검사하고, 합성 WAV/PCM·WebM/Opus·MP4/AAC·raw AAC의 실제 디코딩과 거부 경계를 실행한 뒤 미설정 상태의 liveness 200·readiness 503을 실제 HTTP로 확인합니다. `verify:integration`은 잘못된 JWKS의 readiness/API 503, 정상 로컬 Auth·DB 연결, B-05 STANDARD/TUS·재생·실제 삭제를 별도로 검사합니다.

## B-05 음원 수신과 정리

브라우저는 FastAPI가 발급한 정확한 private object key로만 파일 바이트를 전송합니다. 서버는
완료 요청에서 원본 크기·SHA-256·컨테이너·코덱·단일 오디오 stream·길이와 파생 PCM을
다시 검사합니다. B-05 파생 PCM은 원본 sample rate/channel을 유지하며, V1 전처리의
downmix·resample·정규화는 B-06만 수행합니다.

내구성 있는 정리 batch는 다음처럼 한 번 실행합니다.

```bash
PYTHONPATH=src .venv/bin/python -m baby_care_api.audio_cleanup --limit 20
```

상태·HTTP 오류·품질 기준, TUS 재개, A의 endpoint 검증과 기기별 미실행 범위는
[B-05 인계](../../docs/handoffs/b05-audio-intake.md)에 있습니다.

## B-03 연결과 B-04 처리 순서

연결 순서는 `JWT 검증 → 회수 세션 확인 → 현재 DB 멤버십·아기 상태·객체 권한 → 멱등성 예약 → 업무 처리 → 저장·응답 전 권한/삭제 상태 재검사`입니다.

- `services/security.py`: Bearer 형식, 허용 알고리즘, JWKS 서명, issuer, audience, 만료, `sub`, `session_id`, 사용자 역할과 `amr`를 검증합니다. JWKS를 읽지 못하면 fail-closed 503입니다.
- `services/b04.py`: 매 요청을 `baby_app` 트랜잭션에서 처리하고 사용자·세션·발급 시각을 `SET LOCAL`로 전달합니다. 업무 변경과 멱등성 완료를 같은 트랜잭션에 저장합니다.
- `services/auth_provider.py`: publishable key와 사용자 JWT로 확인된 이메일을 재검사하고 Supabase `local|others|global` 로그아웃을 호출합니다. 제공자 실패를 성공으로 바꾸지 않습니다.
- `models/care_events.py`, `models/b04.py`: 요청에서 작성자·수정자·출처·내부 실행 토큰 주입을 거부하고 시간·수량·상태 조합을 검증합니다.
- `services/readiness.py`: DB role probe와 JWKS probe가 모두 성공할 때만 필수 구성 요소가 준비 상태입니다.

`Idempotency-Key`와 `client_request_id`는 같은 UUID인지 먼저 확인하지만, 이것만으로 중복 방지가 완료되지는 않습니다. 기록 `version`, 원문 `input_revision`, 서버 내부 execution token은 서로 다른 책임으로 유지하며 실제 충돌·재전송 처리는 DB 연결 단위에서 구현합니다.

## B-09 변경 조회

`GET /v1/babies/{baby_id}/changes?since_revision=R`은 현재 세션·ACTIVE 멤버십·ACTIVE 아기를 검사한 뒤 `(R,current_revision]`에서 리소스별 마지막 상태를 최대 500개 반환합니다. 응답에는 리소스 type·ID·version·삭제 여부만 있고 본문·원문·URL·토큰·개인 작업 ID는 없습니다.

- 최초 `R=0`, 90일 보관 경계 이전, 서버보다 큰 revision, 500개 초과는 빈 changes와 `resync_required=true`입니다.
- 정상 빈 범위는 `changes=[]`, `resync_required=false`입니다.
- CareEvent 쓰기는 실제 CareEvent와 함께 공개 `Baby.context_revision`·`Baby.version`을 바꾸므로 같은 feed revision에 `CARE_EVENT`와 `BABY`를 기록합니다.
- 피드 상태와 목록은 한 DB 트랜잭션의 같은 잠금 경계에서 읽습니다. 업무 쓰기·피드 revision·변경 행도 같은 트랜잭션입니다.
- resource version, Baby `context_revision`, feed revision은 서로 다른 책임입니다.
- 응답은 `private, no-store`이며 Realtime은 비활성입니다.

A의 안전한 전체/증분 복구 순서, 쓰기별 재조회 매핑, 예시와 측정값은 [B-09 → A-08 인계](../../docs/handoffs/b09-shared-change-feed.md)에 있습니다.

## 후속 또는 아직 검증하지 않은 범위

- 운영 Supabase migration 적용, 배포별 runtime/관리 로그인 발급과 운영 Auth 설정 검증
- 승인된 법정대리인 확인 수단·증빙·정책. 현재 운영 아동 정보 처리는 fail-closed입니다.
- 삭제 Job 실행기와 Storage·학습 사본·백업 실제 정리(B-12·B-13). B-04는 차단·요청·조회·재시도만 저장하며 COMPLETE를 만들지 않습니다.
- B-07 LLM 정규화·확인 저장 전체, B-09 Realtime, B-11 집계, B-14 상담
- B-06 M2D 분석 API·보정/유보 정책, hosted TUS 조각·백업 보관 확인, 운영 Scheduler와 Cloud Run 배포
- V1 B의 Linux 실행 기반은 별도 프로필로 구현했지만 MPS 기준 대비 `1e-6` 재현 검사는 미통과입니다. 자세한 플랫폼별 차이와 후속 기준 결정은 V1 B 런타임 인계를 따릅니다.
- A의 실제 브라우저·캐시·두 계정 화면 시험과 운영 계정/기기/외부 서비스 시험

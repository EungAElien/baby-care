# Baby Care API — B-04 계정·공동양육·기록

이 폴더는 계약 1.1.0 가운데 B-04 계정·공동양육·기록 경로를 실행하는 FastAPI 서비스입니다. 로컬 Supabase의 실제 Auth JWT/JWKS, 최소 권한 `baby_app` 트랜잭션, DB 멱등성·RLS·Storage 회수 경계를 함께 검증합니다.

## 현재 포함된 범위

- FastAPI 실행 진입점, 환경 설정, 라우터, 공통 오류 처리, 요청별 `request_id`
- 아기 생성·목록·선택·프로필, 구성원·초대·동의 API
- CareEvent CRUD·타임라인, 사건 행동 연결, 작성자 전용 서버 초안
- 삭제 요청·진행 조회·재시도와 본인 기여자료 삭제 요청
- 실제 Supabase JWT/JWKS 검증과 이메일 확인, OTP 재인증 proof, 세션 범위별 회수
- 계약의 `ApiError` 형식과 입력 검증 오류 변환
- B-03 `baby_app` 역할과 요청 트랜잭션 `SET LOCAL` 문맥을 사용하는 현재 권한·업무 처리
- `(user_id, method, path, Idempotency-Key)` DB 예약과 7일 이상 결과 참조 보존
- 민감정보를 입력으로 받지 않는 허용 목록 기반 JSON 로그
- liveness/readiness 분리, pytest·Ruff·mypy·컨테이너·GitHub Actions 기반

`/v1`의 기준은 저장소 루트의 `contracts/openapi계약.json`입니다. FastAPI의 `/openapi.json`은 실제 라우트만 만들고 `x-business-contract.implemented_operations`에 구현된 B-04 operationId를 표시합니다. 분석·업로드·정규화 확인 등 후속 계약 경로를 구현됐다고 노출하지 않습니다.

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

잠금 파일을 갱신할 때는 Python 3.12 환경에서 `uv==0.12.17`을 사용합니다.

```bash
uv pip compile pyproject.toml --python-version 3.12 --generate-hashes --output-file requirements.lock
uv pip compile pyproject.toml --python-version 3.12 --extra dev --generate-hashes --output-file requirements-dev.lock
```

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
- `GET /health/ready`: 필수 인증·DB probe가 실제 연결되기 전에는 503입니다. 모델과 외부 서비스는 별도 선택 구성으로 표시되며, 설정 문자열의 존재만으로 준비 완료가 되지 않습니다.
- 모든 응답은 서버가 새로 만든 UUID `X-Request-ID`를 포함합니다. 오류 본문의 `request_id`와 같습니다.

운영 상태 경로는 정적 최소 응답이며 업무 데이터·자격 증명·내부 예외를 노출하지 않습니다.

## 검사

```bash
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy src
python -m pytest --ignore=tests/integration --no-cov
```

단위 검사는 합성 fixture만 사용합니다. 90% coverage 기준은 실제 인증·DB 경계를 포함한 전체 suite에 적용하므로, 저장소 루트에서 전용 로컬 Supabase를 시작·초기화하는 다음 명령이 최종 게이트입니다.

```bash
cd ../..
npm ci
npm run test:supabase
npm run supabase:stop
```

로컬 통합 시험은 실행 때마다 `example.test` 합성 계정과 OTP를 Mailpit에 만들며 실제 사람에게 메일을 보내지 않습니다. 토큰·비밀번호는 출력하거나 파일에 저장하지 않습니다. 이 명령은 project id `baby-care-b03-local` 전용 DB만 초기화해야 하며 linked·공유·운영 프로젝트에는 사용하지 않습니다.

컨테이너는 API 폴더를 build context로 사용합니다.

```bash
docker build --pull -t baby-care-api:b01 .
docker run --rm -p 8080:8080 baby-care-api:b01
```

기반 이미지는 Python `3.12.12-slim-bookworm`의 확인한 multi-architecture digest로 고정했습니다. Docker healthcheck는 liveness만 사용하며, readiness 503을 프로세스 장애로 오인하지 않습니다.

## B-03 연결과 B-04 처리 순서

연결 순서는 `JWT 검증 → 회수 세션 확인 → 현재 DB 멤버십·아기 상태·객체 권한 → 멱등성 예약 → 업무 처리 → 저장·응답 전 권한/삭제 상태 재검사`입니다.

- `services/security.py`: Bearer 형식, 허용 알고리즘, JWKS 서명, issuer, audience, 만료, `sub`, `session_id`, 사용자 역할과 `amr`를 검증합니다. JWKS를 읽지 못하면 fail-closed 503입니다.
- `services/b04.py`: 매 요청을 `baby_app` 트랜잭션에서 처리하고 사용자·세션·발급 시각을 `SET LOCAL`로 전달합니다. 업무 변경과 멱등성 완료를 같은 트랜잭션에 저장합니다.
- `services/auth_provider.py`: publishable key와 사용자 JWT로 확인된 이메일을 재검사하고 Supabase `local|others|global` 로그아웃을 호출합니다. 제공자 실패를 성공으로 바꾸지 않습니다.
- `models/care_events.py`, `models/b04.py`: 요청에서 작성자·수정자·출처·내부 실행 토큰 주입을 거부하고 시간·수량·상태 조합을 검증합니다.
- `services/readiness.py`: DB role probe와 JWKS probe가 모두 성공할 때만 필수 구성 요소가 준비 상태입니다.

`Idempotency-Key`와 `client_request_id`는 같은 UUID인지 먼저 확인하지만, 이것만으로 중복 방지가 완료되지는 않습니다. 기록 `version`, 원문 `input_revision`, 서버 내부 execution token은 서로 다른 책임으로 유지하며 실제 충돌·재전송 처리는 DB 연결 단위에서 구현합니다.

## 후속 또는 아직 검증하지 않은 범위

- 운영 Supabase migration 적용, 배포별 runtime/관리 로그인 발급과 운영 Auth 설정 검증
- 승인된 법정대리인 확인 수단·증빙·정책. 현재 운영 아동 정보 처리는 fail-closed입니다.
- 삭제 Job 실행기와 Storage·학습 사본·백업 실제 정리(B-12·B-13). B-04는 차단·요청·조회·재시도만 저장하며 COMPLETE를 만들지 않습니다.
- B-07 LLM 정규화·확인 저장 전체, B-09 `/changes`·Realtime, B-11 집계, B-14 상담
- B-05 TUS·업로드 완료·서버 재생 URL, 실제 M2D 모델, Cloud Run 배포
- A의 실제 브라우저·캐시·두 계정 화면 시험과 운영 계정/기기/외부 서비스 시험

# Baby Care API — B-01 서버 기반

이 폴더는 계약 1.0.0을 실제 업무 API로 확장하기 위한 FastAPI 기반입니다. 현재 구현된 HTTP 경로는 운영 상태 확인뿐이며, 아기·기록·분석 업무 경로가 구현됐다고 표시하지 않습니다.

## 현재 포함된 범위

- FastAPI 실행 진입점, 환경 설정, 라우터, 공통 오류 처리, 요청별 `request_id`
- 계약의 첫 공동 기록 경계인 `CreateCareEvent`, `PatchCareEvent`, `CareEvent`와 관련 enum·payload 모델
- 계약의 `ApiError` 형식과 입력 검증 오류 변환
- 인증·현재 멤버십/아기 상태·멱등성 저장소가 연결되기 전에는 성공하지 않는 fail-closed 포트
- B-03 `baby_app` 역할과 트랜잭션 문맥을 사용하는 현재 멤버십·아기 상태 AuthorizationPort 및 실제 DB probe
- 민감정보를 입력으로 받지 않는 허용 목록 기반 JSON 로그
- liveness/readiness 분리, pytest·Ruff·mypy·컨테이너·GitHub Actions 기반

`/v1`의 기준은 저장소 루트의 `contracts/openapi계약.json`입니다. FastAPI의 `/openapi.json`은 현재 실제로 실행되는 운영 경로만 표시하고 `implemented_operations: []`를 명시합니다. 기존 업무 계약을 새 상태 확인 경로로 바꾸지 않습니다.

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

실제 DB URL·JWT 설정·서버 비밀은 `.env`에만 두고 커밋하지 않습니다. 예시 파일은 비밀값을 포함하지 않습니다.

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
python -m pytest
```

테스트는 계약에 포함된 합성 fixture만 사용합니다. 실제 사용자 원문·음원·토큰은 사용하지 않습니다.

컨테이너는 API 폴더를 build context로 사용합니다.

```bash
docker build --pull -t baby-care-api:b01 .
docker run --rm -p 8080:8080 baby-care-api:b01
```

기반 이미지는 Python `3.12.12-slim-bookworm`의 확인한 multi-architecture digest로 고정했습니다. Docker healthcheck는 liveness만 사용하며, readiness 503을 프로세스 장애로 오인하지 않습니다.

## B-03 연결과 이후 지점

연결 순서는 `JWT 검증 → 회수 세션 확인 → 현재 DB 멤버십·아기 상태·객체 권한 → 멱등성 예약 → 업무 처리 → 저장·응답 전 권한/삭제 상태 재검사`입니다.

- `services/security.py`: JWT AuthenticationPort는 아직 항상 503으로 닫혀 있습니다. `services/postgres.py`의 AuthorizationPort만 검증된 `user_id`·`session_id`를 트랜잭션 `SET LOCAL`로 전달해 현재 ACTIVE 멤버십과 ACTIVE 아기를 조회합니다.
- `services/idempotency.py`: `(user_id, HTTP method, path, UUID key)` 범위의 원자 예약, 정규화 본문 해시, 최소 7일 재전송 결과를 DB에 구현합니다. 기본 구현은 항상 503으로 닫혀 있습니다.
- `services/readiness.py`: DB URL이 설정되면 `baby_app` 역할 전환까지 실제 probe하지만, 인증 probe가 없으므로 전체 readiness는 계속 503입니다. 모델과 외부 서비스는 해당 기능을 붙일 때 각자의 probe를 추가합니다.
- `models/care_events.py`: 첫 공동 기록 요청/응답 경계입니다. `created_by_user_id`, `updated_by_user_id`, `data_origin`, 상태, 내부 실행 토큰은 생성 요청에 없고 추가 필드는 거부됩니다.

`Idempotency-Key`와 `client_request_id`는 같은 UUID인지 먼저 확인하지만, 이것만으로 중복 방지가 완료되지는 않습니다. 기록 `version`, 원문 `input_revision`, 서버 내부 execution token은 서로 다른 책임으로 유지하며 실제 충돌·재전송 처리는 DB 연결 단위에서 구현합니다.

## 아직 구현·검증하지 않은 것

- JWT·JWKS AuthenticationPort와 실제 로그아웃·재인증 API
- 운영 Supabase migration 적용과 배포별 runtime/관리 로그인 발급
- 객체별 권한과 B-04 업무 트랜잭션
- `/v1` 업무 라우트, DB 멱등성·version 충돌 처리
- 실제 M2D·LLM·외부 서비스, Cloud Run 배포
- 실제 권한·기기·모델·외부 서비스 시험

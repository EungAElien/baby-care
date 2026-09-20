# Baby Care

아기 돌봄 기록·분석 서비스를 위한 A/B 공동 개발 저장소입니다. 현재는 v2 기획 문서, API 계약 1.2.0, 웹 앱의 초기 기반과 B-01 FastAPI 기반, B-03 로컬 Supabase 권한 계층, B-04 계정·공동양육·기록 API, B-07 사건 없는 정규화·확인 저장 1차, B-09 공동 변경 조회가 있습니다. 실제 울음 사건 연결·전체 정규화 인수와 운영 배포는 아직 구현 중입니다.

## 작업 기준

작업 전에 [코딩 및 협업 컨벤션](docs/project-rule/coding-conventions.md), [개발 원칙](DEVELOPMENT_PRINCIPLES.md), [저장소 지침](AGENTS.md)을 확인합니다. 일반 작업은 최신 `develop`에서 목적별 브랜치로 시작하고 `develop` 대상 PR로 검토합니다. 제품·API 변경은 [개발계약](contracts/개발계약.md)과 [OpenAPI](contracts/openapi계약.json)를 함께 확인합니다.

최신 업무 분담은 [A 기준본](docs/baby-care-implementation-tasks-A-v2.md)과 [B 기준본](docs/baby-care-implementation-tasks-B-v2.md)의 v2.1입니다. 보안 자료는 [보안 설계](docs/security/보안%20설계서.md), [A·B 연결표](docs/security/AB%20보안%20작업%20연결표.md), [62개 시험표](docs/security/62개%20보안%20시험표.md)에 있습니다. OpenAPI 1.2.0에는 B-04 재인증·세션 회수·아동 처리 게이트, B-09 변경 조회 의미와 B-07 사건 없는 정규화·확인 저장 경계가 반영됐습니다. 상담 API, 사건 연결 행동·반응, 초안 자동 만료 등은 여전히 후속 계약 항목입니다.

기획·구현 계획·보안·모델 연구·이전 버전은 [문서 안내](docs/README.md)에서 찾습니다. [문서 이관 기록](docs/migrations/2026-09-19-documents.md)에 가져온 자료, 기존 기준본과의 대응, 검증 범위를 남겼습니다.

## 폴더

- `apps/web/`: Next.js 웹 앱(A 담당)
- `apps/api/`: FastAPI 실행 기반과 B-04 계정·공동양육·기록, B-07 사건 없는 정규화·확인, B-09 변경 조회 API(B 담당)
- `contracts/`: OpenAPI, 합성 목 응답, 계약 생성·검증 코드
- `docs/`: PRD, 기능명세, A/B 작업표, 보안 문서
- `supabase/`: B-03/B-04 로컬 설정, migration, 합성 seed, DB·RLS·Auth/Storage/API 통합 시험. [재현 안내](supabase/README.md)
- `.github/workflows/`: 계약·API·웹·컨테이너·Supabase를 같은 진입점으로 검증하는 B-13 워크플로

## 로컬 시작

웹 앱은 `apps/web/`에서 실행합니다. Node.js와 npm이 필요합니다.

```powershell
cd apps/web
npm ci
npm run generate:api
npm test
npm run typecheck
npm run lint
npm run build
```

계약 JSON은 `contracts/build_contract.py`로 재생성하고, `contracts/validate_contract.py`로 검증합니다. Python 3.10 이상이 필요합니다. 아래 명령은 저장소 루트에서 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r contracts/requirements.txt
.\.venv\Scripts\python.exe contracts/build_contract.py
.\.venv\Scripts\python.exe contracts/validate_contract.py
```

계약을 변경할 때는 A/B 간 합의 후 OpenAPI와 목 응답을 재생성·검증하고 웹 타입도 다시 생성하세요. 목 응답과 시험 사용자는 합성 데이터이며 실제 서비스·모델의 검증 결과가 아닙니다.

환경 변수는 `apps/web/.env.example`을 참고해 로컬에만 설정합니다. 실제 토큰, `service_role`/secret 키, 데이터베이스 비밀번호와 사용자 데이터는 커밋하지 마세요.

API 기반은 `apps/api/`에서 Python 3.12.12로 실행합니다. 자세한 설치·상태 확인·검사·컨테이너 명령은 [API README](apps/api/README.md)를 따릅니다. 현재 `/health/live`는 프로세스 생존만 확인하며 `/health/ready`는 인증·DB를 실제 연결하기 전 503을 반환합니다.

## 자동 검사

Python 3.12.12 API 개발 의존성, `contracts/requirements.lock`, 루트와 웹의 npm 잠금 의존성을 설치한 뒤 저장소 루트에서 실행합니다.

```bash
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:quick
npm run verify:container
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:integration
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:failure-detection
```

- `verify:quick`: 계약을 임시 공간에서 재생성·검증하고 커밋 산출물과 비교한 뒤 API 정적/단위 검사와 웹 typecheck·Vitest·lint·production build를 실행합니다. DB·실제 컨테이너 연결은 실행하지 않습니다.
- `verify:container`: 이미지를 빌드한 뒤 미설정 HTTP fail-closed와 실패 후 자원 정리를 검사합니다.
- `verify:integration` 또는 `test:supabase`: 임시 디렉터리·고유 project id·빈 포트의 로컬 Supabase를 만들고 migration·seed·pgTAP·Auth/API/Storage와 구성된 컨테이너 HTTP를 검사한 뒤 자신이 만든 자원만 제거합니다. 기존 `baby-care-b03-local`이나 linked/공유/운영 프로젝트를 reset·stop하지 않습니다.
- `verify:failure-detection`: 생성물 불일치, 필수 통합 환경 누락, 하위 job 실패, 합성 비밀 표식이 실제로 실패 판정되는지 임시 복사본에서 확인합니다.

각 명령은 `.artifacts/verification/`에 마스킹된 `result.json`, `summary.md`, 로그와 필요한 JUnit/coverage만 만듭니다. 이 디렉터리는 커밋하지 않습니다. CI와 실패 복구, 계약 재생성 순서는 [B-13 자동 검사 인계](docs/handoffs/b13-automatic-verification.md)를 따릅니다.

Supabase B-03 하위 계층은 [API README](apps/api/README.md)의 Python 개발 의존성을 설치한 뒤 저장소 루트에서 Docker로 재현합니다.

```bash
npm ci
npm run test:supabase
```

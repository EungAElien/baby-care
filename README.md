# Baby Care

아기 돌봄 기록·분석 서비스를 위한 A/B 공동 개발 저장소입니다. 현재는 v2 기획 문서, 확정 API 계약, 웹 앱의 초기 기반이 있으며 실제 백엔드와 Supabase 설정은 아직 구현 중입니다.

## 작업 기준

작업 전에 [코딩 및 협업 컨벤션](docs/project-rule/coding-conventions.md), [개발 원칙](DEVELOPMENT_PRINCIPLES.md), [저장소 지침](AGENTS.md)을 확인합니다. 일반 작업은 최신 `develop`에서 목적별 브랜치로 시작하고 `develop` 대상 PR로 검토합니다. 제품·API 변경은 [개발계약](contracts/개발계약.md)과 [OpenAPI](contracts/openapi계약.json)를 함께 확인합니다.

## 폴더

- `apps/web/`: Next.js 웹 앱(A 담당)
- `apps/api/`: 백엔드 구현 위치(B 담당, 현재 자리 표시자)
- `contracts/`: OpenAPI, 합성 목 응답, 계약 생성·검증 코드
- `docs/`: PRD, 기능명세, A/B 작업표, 보안 문서
- `supabase/`: Supabase 설정·마이그레이션 위치(현재 자리 표시자)
- `.github/workflows/`: 향후 CI 워크플로 위치

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

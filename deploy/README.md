# 제출용 배포 환경

## 확인된 상태 (2026-09-20 KST)

- 웹 고정 주소: <https://baby-care-demo.vercel.app>
- Vercel 프로젝트: `jyk3716715-1972s-projects/baby-care-demo` (Hobby)
- 웹 소스: `a546bd8` (`develop` 기준 `ed87d29`), API 연결 후 READY 배포 `dpl_BFmoDPNmfKv6r4zB2EEbkaM5pykh`. 브라우저에서 환경 미설정 경고 없이 이메일 로그인 화면이 표시되는 것을 확인했다. 이전 READY 배포는 `dpl_6W6G8Mi5eAWupFqha7xUpdtQrjaJ`다.
- 인증 없는 `/`, `/login` 요청에서 HTML 200을 확인했다. 새 Supabase 공개 URL·publishable key와 Cloud Run `/v1` 주소를 Vercel Production 환경에 등록했다.
- API: <https://baby-care-api-891675703053.asia-northeast3.run.app>, 서울 `asia-northeast3`, revision `baby-care-api-00001-d4j`, 트래픽 100%. 1 CPU, 512 MiB, concurrency 8, 최소 0/최대 2 인스턴스, timeout 60초.
- Google Cloud 프로젝트: `baby-care-demo-20260920` (번호 `891675703053`). 결제 연결과 필수 서비스 활성화, Cloud Build `1c2ec20d-d51e-4c32-8ea1-bbb0dbd8d002` 성공을 확인했다.
- Supabase: `juny030507's Org`의 `baby-care-demo`, ref `tnsqrizismtkzcwzzymq`, 서울 `ap-northeast-2`, FREE, ACTIVE_HEALTHY, Data API 비활성. 기존 로또 프로젝트 `ccnuctvzdsfinqmpyjtm`에는 변경하지 않았다.
- 기존 migration 7개를 seed 없이 적용했다. `baby_data` 업무 테이블 44개 모두 RLS/FORCE RLS이며 `baby-audio` bucket은 private/25,000,000 bytes다. 보안 advisor 경고·오류 0건.
- `baby_care_runtime`은 LOGIN/NOINHERIT/NOBYPASSRLS이며 관리자·역할 생성·DB 생성 권한이 없다. `baby_app` SET 가능/상속 불가와 session pooler TLS 접속을 확인했다.
- Secret Manager의 지정된 비밀값 4개(version 1)에만 `baby-care-api` 실행 계정의 accessor를 부여했다. 기본 빌드 계정에는 `roles/run.builder`를 부여하고 자동 생성된 `roles/editor`는 제거했다.
- 실제 `/health/live`, `/health/ready` 200, 정확한 웹 CORS, 미인증 `/v1/babies` 401, 미등록 Origin 거부를 확인했다.
- 이 주소의 공개 접속과 로그인 이후 기능 인수는 별개다. 대회 제출 자체, 두 계정 흐름, 실제 모델 동작은 아직 확인하지 않았다.
- 배포 준비 기준 `ed87d29`: 웹 Vitest 31개 파일/179개 테스트, 프로덕션 빌드(타입 검사 포함), ESLint 통과. Vercel JSON 파싱, Cloud Run 업로드 허용 목록, 실제 Docker 기반 Cloud Build·API/DB 연결도 통과했다. PR #39의 GitHub 검사 5개가 성공했다.

### 남은 제품 연결 조건

- 이메일 OTP 로그인: 무료 기본 메일 제공자가 템플릿 수정을 HTTP 400으로 거부했다. 현재 웹의 숫자 OTP 흐름에는 별도 SMTP와 OTP 템플릿 적용이 필요하다. 사용자는 SMTP 없이 배포 기반부터 완료하도록 지시했다. 이메일 발송·두 계정 로그인은 시험하지 않았다.
- Auth Site URL과 정확한 `/login` redirect는 배포 주소로 적용했다. 기본 8자리 OTP·이메일 확인·MFA 등 선언하지 않은 원격 설정은 유지했다.
- 승인된 동의 문구·버전, 보호자 확인 조건, 실제 모델 묶음과 운영 검증은 후속이다. 아동 자료·외부 정규화·M2D 실행 게이트는 모두 `false`다. readiness를 전체 제품 시연 성공으로 해석하지 않는다.

## 웹 재배포

현재 프로젝트는 `apps/web` 폴더를 직접 업로드하는 CLI 배포다. Git 자동 배포는 Vercel의 GitHub Login Connection이 없어 연결되지 않았다. 아래 명령으로 같은 주소에 재배포할 수 있다.

```powershell
cd apps/web
npm ci
npm test
npm run typecheck
npm run lint
npm run build
npx vercel@59.23.2 link --yes --project baby-care-demo --scope jyk3716715-1972s-projects
npx vercel@59.23.2 deploy --dry --json
npx vercel@59.23.2 deploy --prod --yes --scope jyk3716715-1972s-projects
```

`vercel.json`은 Next.js·`npm ci`·빌드 명령을 고정한다. `.vercelignore`는 `.env*`, 로컬 빌드, 의존성, 테스트를 업로드에서 제외한다. `.vercel` 프로젝트 연결 파일과 CLI가 내려받은 OIDC 토큰이 포함된 `.env.local`은 커밋하지 않는다. CLI의 `link`가 내려받는 환경 파일을 공유하지 않는다.

Git 자동 배포를 추가한다면 같은 저장소를 연결하고 Root Directory를 `apps/web`로 지정한다. 저장소 기본 브랜치 `main`은 현재 배포한 `develop`보다 오래되므로 연결 직후 옛 코드가 운영 주소를 덮어쓰지 않도록 배포 브랜치와 버전을 확인한다. 기존 팀의 `develop` → `main` 배포 PR 절차는 유지한다.

Vercel 프로젝트의 **Production** 환경에는 다음 공개 값만 넣는다. `NEXT_PUBLIC_` 값은 빌드 시 브라우저 번들에 들어가므로 변경 후 재배포한다.

| 변수 | 값 |
| --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `https://baby-care-api-891675703053.asia-northeast3.run.app/v1` |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://tnsqrizismtkzcwzzymq.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | 새 프로젝트의 publishable key |
| `NEXT_PUBLIC_SUPABASE_STORAGE_URL` | `https://tnsqrizismtkzcwzzymq.storage.supabase.co` |

운영 주소에서 `NEXT_PUBLIC_ENABLE_MOCK_NAV`, `NEXT_PUBLIC_POLICY_PROFILE=LOCAL_SYNTHETIC_V1`은 켜지 않는다. 승인된 동의 정책 문구·버전과 서버의 아동 자료 처리 조건은 별도 준비가 필요하다. 화면을 열기 위해 해당 게이트를 임의 해제하지 않는다.

## Supabase 연결

1. 전용 프로젝트 생성 완료와 project ref를 확인한다. 기존 프로젝트를 재사용하거나 초기화하지 않는다.
2. 저장소 고정 버전 CLI로 `supabase login` 후 전용 프로젝트를 연결한다. `link --help`, `db push --help`로 현재 옵션을 먼저 확인한다.
3. `db push --dry-run --project-ref <새 ref>`로 예정된 migration을 확인하고, 같은 ref에 `db push`를 실행한다. `--include-seed`는 사용하지 않는다. 로컬 seed는 공개 시연 계정 준비를 대신하지 않는다.
4. [Supabase README](../supabase/README.md)에 따라 migration 계정과 별도의 `LOGIN NOINHERIT NOBYPASSRLS` 런타임 계정을 준비한다. `baby_app` 역할 전환만 허용하고 `postgres` 관리자 연결을 API에 넣지 않는다.
5. Cloud Run에서 접속 가능한 TLS DB 연결을 사용한다. 현재 psycopg 코드는 prepared statement 설정을 바꾸지 않으므로 **session pooler**를 사용하고 transaction pooler로 임의 변경하지 않는다.
6. Auth Site URL은 `https://baby-care-demo.vercel.app`, 허용 redirect는 실제 웹 로그인 경로에 맞춘다. 이메일 OTP 템플릿·SMTP/전송 제한·두 계정 로그인을 별도로 확인한다.
7. API가 사용하는 비대칭 JWT 알고리즘과 JWKS를 확인한다. Data API 비활성은 Auth·Storage 사용과 별개이며 업무 테이블은 FastAPI만 거친다.

배포용 최소 Auth 설정은 [supabase/config.toml](supabase/config.toml)에 분리했다. 저장소 루트의 로컬 개발 config 전체를 원격에 push하지 않는다. `supabase config diff --workdir deploy --project-ref tnsqrizismtkzcwzzymq`로 검토한 뒤 같은 인자의 `config push`로 URL 2개만 적용한다. SMTP가 준비되면 메일 템플릿에 `{{ .Token }}`을 포함하고 별도로 발송을 확인한다.

## Cloud Run 준비

사용자가 프로젝트 결제 계정/무료 체험을 연결한 뒤 진행한다. 최소 인스턴스 0은 유휴 실행 비용을 줄이는 설정이고, 빌드·이미지 저장·네트워크 등 모든 비용을 0으로 보장하지 않는다. 모델 포함 이미지는 이 기본 API 배포에 포함하지 않는다.

저장소 루트에서 `cloud-run.env.example.yaml`을 무시되는 `.artifacts/deployment/cloud-run.env.yaml`로 복사하고 실제 공개 주소·issuer·JWKS를 채운다. 비밀값은 Secret Manager에 보관한다.

```bash
# Cloud Shell 또는 gcloud가 설치된 환경, 저장소 루트에서 실행
PROJECT_ID=baby-care-demo-20260920
REGION=asia-northeast3
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com --project "$PROJECT_ID"
gcloud iam service-accounts create baby-care-api --project "$PROJECT_ID" --display-name "Baby Care API runtime"

# 위 런타임 계정에는 필요한 개별 secret의 accessor만 부여한다.
# 소스 빌드 계정/IAM은 아래 공식 source deployment 문서에 맞춰 확인한다.
# secret 이름과 버전 1은 실제 등록한 값으로 확인 후 사용한다.
gcloud run deploy baby-care-api \
  --project "$PROJECT_ID" --region "$REGION" \
  --source apps/api --port 8080 \
  --service-account "baby-care-api@$PROJECT_ID.iam.gserviceaccount.com" \
  --cpu 1 --memory 512Mi --concurrency 8 \
  --min 0 --max 2 --timeout 60 \
  --env-vars-file .artifacts/deployment/cloud-run.env.yaml \
  --set-secrets BABY_CARE_DATABASE_URL=baby-care-database-url:1,BABY_CARE_SUPABASE_PUBLISHABLE_KEY=baby-care-supabase-publishable-key:1,BABY_CARE_SUPABASE_SECRET_KEY=baby-care-supabase-secret-key:1,BABY_CARE_REAUTHENTICATION_PROOF_SECRET=baby-care-reauthentication-proof-secret:1 \
  --allow-unauthenticated
```

공개 HTTPS 진입점은 Supabase 사용자 JWT를 전달하기 위한 것이다. 업무 API의 Bearer 검증과 DB 권한은 계속 적용한다. `.gcloudignore`는 `Dockerfile`, 잠금 의존성, `src`만 전송한다. 기존 Dockerfile은 8080 포트와 고정 FFmpeg/Python 이미지를 사용한다.

기본 API 설정의 모델·실사용 아동 자료·외부 정규화 게이트는 닫힌 상태다. REAL 모델은 별도 `Dockerfile.m2d`, 승인된 모델 묶음·해시·실행 검증 후 연결한다. `health/ready` 성공도 모델과 로그인 후 업무 흐름 전체의 통과를 의미하지 않는다.

## 접속 검사와 롤백

```bash
# 웹만 준비됐을 때: API 미검증을 명시하고 공개 접속을 검사
node scripts/deployment/check-endpoints.mjs https://baby-care-demo.vercel.app

# API 설정 완료 후: /v1이 없는 Cloud Run origin을 두 번째 인자로 전달
node scripts/deployment/check-endpoints.mjs https://baby-care-demo.vercel.app https://YOUR_SERVICE.run.app
```

두 번째 명령은 liveness, Auth/DB readiness, 정확한 CORS 출처, 미인증 업무 API 401, 잘못된 출처 거부를 검사한다. 토큰을 보내거나 업무 데이터를 쓰지 않는다. 이후 별도 두 계정·모바일 HTTPS·업로드·모델·기록 저장 인수가 필요하다. 최종 제출은 고정 웹 주소를 사용하며 배포별 미리보기 URL이나 로컬 주소를 사용하지 않는다.

재배포 전에 Vercel의 이전 READY deployment와 Cloud Run의 이전 revision 및 트래픽을 기록한다. 장애 시 검증된 이전 웹 deployment를 Promote하고 Cloud Run 트래픽을 이전 revision으로 되돌린다. DB migration은 서비스 rollback과 별개이며 임의 reset하지 않는다.

공식 문서: [Vercel CLI 배포](https://vercel.com/docs/cli/deploy), [Vercel 모노레포](https://vercel.com/docs/monorepos), [Cloud Run source 배포와 IAM](https://docs.cloud.google.com/run/docs/deploying-source-code), [gcloud run deploy](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy), [Supabase Data API 설정](https://supabase.com/docs/guides/api/securing-your-api).

# B-07 제품 API 실제 Terra 통합 검증

검증일: 2026-09-20 KST

기준 계약: 1.2.0

실제 Terra 호출 기준 `develop`: `1b8611265ffd9389e7ceb35dd8b2fc3a04efc93a`

최종 회귀 기준 `develop`: `e251a247aaa4a67961653f3cc135b0e78cdc266b`

## 결론

동결한 합성 사례 3개를 실제 FastAPI HTTP 경로와 격리 로컬 Supabase Auth·DB에서
`gpt-5.6-terra`로 정규화하고, 사용자의 명시 확인, 저장, 같은 key 재전송, ID 재조회,
비구성원·멤버십 회수 차단까지 검증했다. 최종 판정은 다음 세 축 모두 `PASS`다.

- 실제 Terra 연결과 정확한 모델 ID
- 세 사례의 지정 의미 판정
- 확인 저장·권한·응답 유실 복구

외부 요청은 실패 진단과 수정 후 재검증을 포함해 총 6회로 종료했다. 키, Authorization,
JWT, 전체 프롬프트, 원문 provider 응답은 로그·검증 결과·Git에 저장하지 않았다.

## 실제 호출과 사례

| 사례 | 실제 모델 결과 | 확인 방식 | 저장·재조회 |
|---|---|---|---|
| `B07-LIVE-001` | 분유 80mL `PERFORMED`, `FORMULA`, `AWAKE`, 미상 시각 유지 | 모델 원안 | CareEvent 1, StateObservation 1, `USER`, PASS |
| `B07-LIVE-002` | 기저귀 교체만 `PERFORMED`; 수유 `NEGATED`, 안기 `PLANNED`, 트림 `UNCERTAIN` | 모델 원안 | CareEvent 1, StateObservation 0, `USER`, PASS |
| `B07-LIVE-003` | 수유량·시각 미상과 기저귀 수행/부정 충돌 유지 | 차단 확인 후 합성 시험 사용자가 수정 | CareEvent 1, StateObservation 0, `USER`, PASS |

세 사례 모두 POST 응답 본문을 버린 뒤 같은 `run_id`의 GET으로 결과를 복구했다. 같은
확인 key 재전송은 같은 ID와 같은 DB 개수를 반환했다. 작성자 전용 초안·run은 OWNER와
비구성원에게 404였고, 확정 리소스는 활성 OWNER만 재조회할 수 있었다. 구성원 상태를
`REVOKED`로 바꾼 뒤 기존 JWT의 목록·확정 리소스 접근도 모두 404였다.

사건 없는 outcome 저장 시도는 외부 호출 없이 HTTP 422 `EVENT_REQUIRED`였고 리소스를
남기지 않았다. B-09 변경 조회에는 확정 리소스 ID·version만 나타났으며 원문과 정규화
내용은 없었다.

## 검증 중 발견해 고친 문제

1. 이모지가 있는 TEXT evidence에서 모델 offset을 그대로 믿으면 Unicode 코드포인트
   slice가 어긋날 수 있었다. 인용문이 원문에 정확히 한 번만 나타날 때만 서버가 offset을
   코드포인트 기준으로 보정한다. 없거나 여러 번 나타나는 인용문은 보정하지 않고 기존
   의미 검증에서 실패한다.
2. “10분 뒤 안아주기”를 HOLDING의 `amount=10`, `unit=MINUTES`로 출력한 사례가 있었다.
   비수유 행동의 `amount`, `unit`, `feeding_mode`는 null이고 시점 표현은
   `relative_time`/`RELATIVE`로만 남기도록 제품 프롬프트를 보강했다.
3. smoke의 멤버십 회수 probe가 실제 enum `REVOKED` 대신 `REMOVED`를 사용했다. 실제
   스키마 값으로 수정하고 기존 JWT 차단을 확인했다.
4. 프로세스가 중단돼도 6회 상한을 넘기지 않도록 요청 직전 예약하는 0600 영속 원장을
   추가했다. 원장에는 응답 ID·모델·상태·지연·토큰 수와 오류 위치/유형만 기록한다.

첫 두 요청의 실패 원문은 보존하지 않았으므로 그 전체 출력은 사후 재구성하지 않았다.
첫 사례는 schema/의미 거부 뒤 Unicode 보정과 재실행으로 통과했고, 두 번째 사례의
`QUANTITY_NOT_ALLOWED`는 내용 없는 진단 코드로 확인한 뒤 프롬프트를 수정했다. 기대값을
실제 출력에 맞춰 낮추지는 않았다.

## 재현 절차

권장 서버 설정은 Git에서 제외된 `apps/api/.env`의 다음 형식이다.

```dotenv
BABY_CARE_OPENAI_API_KEY=실제_키
```

파일 권한은 `chmod 600 apps/api/.env`로 제한한다. 전용 smoke는 사용자가 이번에 저장한
원문 키 한 줄 파일도 호환 입력으로 읽지만, 일반 FastAPI `Settings`는 표준
`BABY_CARE_OPENAI_API_KEY=...` 형식을 사용한다.

```bash
BABY_CARE_B07_LIVE_SMOKE=1 \
PYTHONPATH=apps/api/src \
apps/api/.venv/bin/python scripts/verification/b07_live_terra_smoke.py \
  --allow-provider-calls
```

도구는 다음 조건을 모두 강제한다.

- fixture가 호출 전에 동결된 합성 3사례인지 확인
- `--allow-provider-calls`와 `BABY_CARE_B07_LIVE_SMOKE=1`을 동시에 요구
- 재시도를 포함한 누적 provider 요청을 6회로 제한
- 고유 포트·project id의 격리 Supabase와 실제 로컬 Auth 계정 사용
- 실제 uvicorn TCP 서버를 통한 FastAPI 요청
- 성공·실패 모두 자신이 만든 서버·컨테이너·network·volume만 정리
- 결과 파일과 호출 원장을 0600으로 저장

로컬 원본 증거는 Git에서 제외된 `.artifacts/b07-terra-product-smoke/`에 있다. 호출 예산은
소진됐으므로 이 원장을 지우거나 우회해 같은 검증을 다시 실행하지 않는다.

최신 `develop` 재배치 후 일반 빠른 회귀는 API 단위 234개와 계약·생성물·Ruff·mypy·웹
타입/테스트/빌드를 통과했다. 격리 통합 회귀는 259개 전체 테스트, 필수 integration
25/25, coverage 90.52%, pgTAP 97개, Auth/Data API/Storage HTTP 23개를 통과했다. 두
회귀의 산출물과 B-07 실제 호출 산출물 모두 민감정보 shape scan 결과 0건이었다.

## 검증 경계

이 결과는 합성 한국어 3사례와 로컬 격리 환경에 한정된다. 운영 Supabase·배포 비밀 저장,
실제 보호자/아기 자료, 실제 브라우저 A-07, 기기, 사건 연결 ActionAttempt·Outcome, 부하·비용,
의료적 정확도와 사용자 일반화는 검증하지 않았다. `data_origin=USER`는 제품 API가 만든
업무 자료 출처이고, fixture가 합성이라는 사실은 검증 보고서의 `synthetic_only=true`로
별도 보존했다.

기계 판독 가능한 요약은 [검증 결과 JSON](b07-terra-product-verification.json)에 있다.

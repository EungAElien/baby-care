# B-06 M2D 분석 파이프라인 인계

기준일: 2026-09-20
기준 계약: 개발계약·OpenAPI 1.2.0(분석 경로는 1.1.1 의미 유지)
범위: B-05 READY 음원부터 내구성 있는 분석 결과와 B-09 변경 알림까지

## 판정

B-06의 요청 내 실행 상태 머신과 복구 경계를 실제 FastAPI·격리 PostgreSQL·Supabase
Storage로 구현했다. 생성·조회·명시적 재시도, 45초 요청 제한, 60초 lease, attempt와 실행
토큰, 시작 시 만료 복구, 이전 실행의 늦은 결과 차단, 권한·동의·삭제 재검사, 입력 무결성,
임시 파일 수명, 제한된 실행 대기열과 B-09 `ANALYSIS` 변경을 자동 검증한다.

제품 분석은 아직 활성화하지 않았다. 고정 V1 B는 실제 Linux CPU에서 적재·추론됐지만
macOS 기준과의 `1e-6` 재현 검사를 통과하지 못했고 `calibration_status=NOT_VALIDATED`,
`release_ready=false`다. 검증된 cry detector, 제품 라벨 의미, 보정, 유보 임계값과 추천
정책도 없다. 따라서 운영 정책의 `product_ready`는 false이며 실제 생성 요청은 분석 행을
만들기 전에 `503 MODEL_NOT_READY`로 거부한다. 가짜 후보나 STUB으로 대체하지 않는다.

COMPLETE·ABSTAIN·FAILED 완주 검증에는 `ENVIRONMENT=TEST`에서만 주입할 수 있는 명시적
합성 정책과 런타임을 사용했다. 이는 구현 검증이지 모델 성능·제품 판단 검증이 아니다.

## A-06 요청과 복구 흐름

1. B-05 완료 응답에서 `status=READY`인 AudioAsset과 같은 Episode를 사용한다.
2. A가 새 `analysis_id`와 `client_request_id`를 만들고 같은 UUID를
   `Idempotency-Key`에도 넣어 `POST /v1/episodes/{episode_id}/analyses`를 보낸다.
3. B는 현재 권한과 입력을 검사한 뒤 분석 행·attempt 1·60초 lease·실행 토큰을 먼저
   저장하고 요청 안에서 최대 45초 처리한다.
4. 정상 응답은 HTTP 200의 `Analysis`다. `status=FAILED`와 `status=ABSTAIN`도 저장된
   최종 자원이므로 HTTP 200이다.
5. 응답을 받지 못하면 같은 요청을 같은 키·본문으로 재전송하거나
   `GET /v1/analyses/{analysis_id}`로 복구한다. 새 analysis_id를 자동 생성하지 않는다.
6. 조회가 RUNNING이면 2초 간격으로 조회한다. GET은 만료된 lease를 먼저 FAILED로
   확정한다.
7. FAILED만 사용자의 명시적 선택으로 같은 analysis_id에 재시도한다. 새
   `client_request_id`/`Idempotency-Key`와 현재 `expected_attempt`를
   `POST /v1/analyses/{analysis_id}/retry`에 보낸다.
8. ABSTAIN 뒤 재녹음은 새 audio_id와 새 analysis_id다. COMPLETE·ABSTAIN을 retry API로
   다시 실행하지 않는다.

동일 생성 키의 완료 재전송은 저장된 자원을 돌려준다. 같은 analysis_id나 audio_id를 다른
입력에 묶으면 409 `IDEMPOTENCY_KEY_REUSED`, 실행 중 중복은 기존 ID와 조회 경로를 담은
409 `ANALYSIS_IN_PROGRESS`다. FAILED의 오래된 `expected_attempt`는 409
`VERSION_CONFLICT`다.

## 상태와 실행 소유권

분석은 `READY → RUNNING → COMPLETE | ABSTAIN | FAILED`이고 stage는 별도로
`READY → QUALITY_CHECK → INFERENCE → PERSISTING → FINISHED`를 기록한다. 각 시도는
`analysis_execution_attempts`에 요청자·세션·발급 시각, idempotency 참조, attempt,
lease와 무작위 실행 토큰만 저장한다. 음원·모델 점수·전체 응답을 복제하지 않는다.

최종 UPDATE는 현재 상태가 RUNNING이고 attempt와 실행 토큰이 모두 일치할 때만 성공한다.
재시작 시와 GET 전에 만료 lease를 FAILED `ANALYSIS_LEASE_EXPIRED`로 확정한다. 이전 CPU
작업이 뒤늦게 끝나더라도 새 attempt 또는 만료된 행을 덮어쓸 수 없다.

모델 실행기는 CPU 작업 1개와 대기 1개만 허용한다. 요청 timeout은 Python thread를
강제로 종료할 수 없으므로 실행 중 slot과 로컬 입력은 실제 thread가 끝날 때까지 유지한다.
그동안 추가 요청은 제한된 대기열 밖에서 실패해 무제한 메모리·작업 누적을 만들지 않는다.

## B-05 입력과 자원 수명

B-06은 B-05가 READY로 확정한 private `baby-audio` 객체의
`PCM_S16LE_SOURCE_RATE`/`audio/wav` 파생물만 사용한다. 저장된 bytes·SHA-256·객체 경로,
PCM 16-bit, source sample rate·channels와 전처리 경계 버전을 다운로드 뒤 다시 검사한다.
B-05는 source-rate PCM까지만 만들며 V1 런타임이 downmix·resample·정규화를 정확히 한
번 수행한다.

입력은 분석별 임시 디렉터리에만 내려받는다. 추론 제출 전 실패면 요청 종료 시 제거하고,
timeout 뒤 실제 CPU thread가 남아 있으면 완료 callback에서 제거한다. 저장되는 로그·오류에
로컬 경로나 음원 내용은 넣지 않는다.

최종 COMPLETE·ABSTAIN·FAILED는 B-05 보관 정책의 분석 완료 cleanup을 enqueue한다.
보관 동의가 없는 음원은 DELETING으로 전환되어 원본과 파생물을 정리한다. 이 경우 FAILED
재시도는 `RESOURCE_DELETING` 또는 `RESOURCE_DELETED`이며 새 업로드가 필요하다.

## 최종 저장 전 차단

처음 수락할 때와 추론 직전, 최종 저장 직전에 다음을 다시 검사한다.

- 요청자의 ACTIVE 멤버십과 세션 회수 상태, ACTIVE 아기
- USER 자료의 현재 OWNER 보호자 확인
- 최신 `SERVICE_PROCESSING=GRANTED` 동의
- AudioAsset과 PCM 파생물의 READY 상태·동일 checksum/경로
- 현재 analysis status, attempt, 실행 토큰과 lease

권한·동의가 사라지면 FAILED `ACCESS_REVOKED`, 입력이 삭제 중/삭제됨이면 FAILED
`SOURCE_DELETED`를 저장하고 성공 결과는 폐기한다. 클라이언트에는 현재 경계에 맞는
403/409/410 오류가 반환될 수 있지만 GET으로 저장된 FAILED를 복구할 수 있다. 보안
트랜잭션이 현재 권한 때문에 열리지 않는 경우에도 제한된 security-definer 함수가 원래
attempt 소유권을 확인해 해당 실행만 fence한다.

## 결과 의미와 B-07/B-08 경계

`inference_mode`, `inference_executed`, `data_origin`, model/preprocess/label-mapping version을
따로 보존한다. REAL/STUB은 실행 방식이고 USER/DEMO는 데이터 출처라 서로 대신하지 않는다.
B-05의 측정 가능한 품질 경고는 결과에 보존되지만 `HIGH_NOISE`·`NO_CRY`는 검증된 판정기
없이 생성하지 않는다. 내부 점수는 원인 확률이나 확인된 감정·상태가 아니다.

B-07은 이 응답의 `episode_id`/`analysis_id`를 참조해 사용자가 확인한 행동·관찰을 저장할
수 있지만 분석 후보를 확정 기록으로 자동 변환하면 안 된다. B-06은 B-07 구현을 import하거나
대기하지 않는다. B-08이 나중에 현재 기록을 이용한 context snapshot·추천 재계산을 소유한다.
테스트 정책의 recommendation은 B-06 저장 계약과 원자성을 검증하기 위한 합성 fixture다.

## B-09 변경 조회

생성 RUNNING, INFERENCE 전환, 최종 상태와 retry 전환마다 같은 트랜잭션에서
`resource_type=ANALYSIS`, `resource_id=analysis_id`, 증가한 `change_version`을 공동 변경
피드에 남긴다. 피드에는 후보·실패 본문·토큰·음원 위치를 넣지 않는다. A는 변경을 받은 뒤
권한 있는 Analysis GET으로 본문을 다시 읽는다.

## 검증 범위와 남은 일

격리 통합 검사는 실제 migration·RLS·Auth JWT/JWKS·Storage를 사용해 생성/조회/재전송,
COMPLETE/ABSTAIN/FAILED, 시간 초과, restart lease 복구, explicit retry, 늦은 결과 fencing,
동시 중복, 동의 회수, 삭제·no-retention cleanup, B-09 변경을 확인한다. 단위 검사는 큐 제한,
timeout 뒤 slot·임시 파일 보존, 계약에 맞지 않는 정책 출력의 fail-closed 동작을 확인한다.

아직 완료로 세지 않는 항목은 다음과 같다.

- `1e-6` 재현 기준 또는 별도 승인 기준과 기준 장치 확정
- 검증된 cry detector, 제품 라벨 의미, calibration, COMPLETE/ABSTAIN threshold
- 실제 사용자에게 보여 줄 추천 정책과 B-08 context snapshot
- 운영 Supabase/Storage·실제 계정·브라우저·기기와 모델을 함께 둔 부하·cold start 시험
- Cloud Run 배포, 운영 Scheduler, hosted TUS 조각과 백업 보관 검증

위 항목이 끝나기 전 `/capabilities.audio_model.available`은 false이며 제품 분석 생성은
`MODEL_NOT_READY`다. 고정 V1 런타임의 실제 적재·추론과 실패 증거는
[V1 B 런타임 인계](./b02-b06-v1-model-runtime.md)를 따른다.

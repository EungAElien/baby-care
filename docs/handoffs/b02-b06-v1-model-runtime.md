# B-02/B-06 V1 B 서비스 실행 기반 인계

기준일: 2026-09-20
범위: B-02의 Linux 컨테이너 추론 재현과 B-06의 시작 시 1회 모델 적재·모델 readiness

## 판정

실행 기반은 구현됐고 고정 V1 B 전체 가중치가 네트워크 차단 Linux CPU 컨테이너에서
실제로 적재·추론됐다. 모델·metadata·config, M2D 구조 코드, 실행 버전, 라벨 순서,
전처리와 전체 state digest 검증도 통과했다. 그러나 PR #18의 macOS 기준 산출물과 비교한
최대 절대 오차가 사전에 정한 `1e-6`을 넘었으므로 **B-02 컨테이너 재현 항목은 미통과**다.
기준을 완화하거나 기준 출력을 다시 만들지 않았다.

B-06에서는 일반 API를 바꾸지 않는 별도 V1 B 프로필, lifespan 1회 적재·재사용,
fail-closed 모델 readiness, 실패 후 무재시도, 무-STUB, 단일 동시 추론 경계를 구현했다.
업로드/분석 API와 작업 상태·저장·복구는 범위 밖이므로 B-06 전체 완료가 아니다.

`calibration_status=NOT_VALIDATED`, `release_ready=false`이며 이 결과는 성능 개선이나 제품
사용 승인, 원인 확률 또는 임상 진단 검증이 아니다.

## 선행 코드와 고정 대상

PR [#18](https://github.com/EungAElien/baby-care/pull/18)의 head
`fa271ba161617a140ed3d77ac382719ec9be1ec7`은 확인 당시 계속 OPEN이었다. 공유 브랜치나
PR #18/#19를 병합하지 않았다. 이 변경은 #18의 추론 전용 구조 생성·전처리·전체 가중치
적재 방식을 재사용한 독립 서비스 어댑터를 최신 `develop` 위에 두며, 실제 V1 B 묶음의
출처는 #18이다. #18이 바뀌거나 대체되면 레지스트리와 증거를 자동 갱신하지 말고 별도
검토한다. V2 PR #19의 학습 코드는 사용하지 않았다.

고정 식별자는 다음과 같다.

- model: `m2d-supervised-v1.0.0-final-B`
- product/trained head: `donate` / `['donate']`
- labels: `belly_pain`, `burping`, `discomfort`, `hungry`, `tired` 순서
- preprocessing: `m2d-logmel-v1.0.0`
- label mapping: `donate-cause-labels-v1.0.0`
- M2D source commit: `3d0c4de9447c404a8d3f9f37e04f53bc902e09b3`
- model SHA-256: `016bd1e048de7d6ddea010a4bcbe269f2ee4adf76bbc2fd29547aeb808bc97c5`
- metadata SHA-256: `c4a01b96e31fa5794bdc315f5b9c086e832bc9bc4cec5b4fadec601e3e854597`
- config SHA-256: `a59e39420242a6c186522acc8678498b17f2e70f859ebb690c985e6d20551283`
- full state SHA-256: `b3eb02136a4669330ced2d56eeab90a305b2d6cc875fb5e2d0704be7f55c9015`

저장소 레지스트리는 묶음의 자기 신고만 믿지 않고 위 값, 세부 전처리, 출력 차원과 구조,
M2D/MAE에서 실제 필요한 네 파일의 해시를 독립적으로 검사한다. 중첩 artifact 이름,
허용 루트 밖 경로와 심볼릭 링크도 거부한다. probe와 개발 manifest는 운영 적재에 필요하지
않다.

## 구현 경계

- `baby_care_m2d.registry`: 신뢰 레지스트리, 고정 파일·의미·경로 검증
- `baby_care_m2d.model/runtime`: CPU float32, `weights_only=True`, strict load, finite/state
  digest 검사, `eval()`·`inference_mode()`, Donate 5개 점수와 식별자만 반환
- `baby_care_m2d.audio`: 로컬 파일만 받는 고정 FFmpeg probe/decode, 채널 평균과 soxr HQ,
  고정 nnAudio frontend, 중앙 crop. 전처리 객체는 시작 시 한 번 만들고 재사용
- `ModelRuntimeManager`: event loop 밖 CPU 실행, 프로세스당 적재 시도 1회, semaphore 1개
- FastAPI lifespan/readiness: 활성 프로필의 모델을 필수로 만들고 성공/실패 상태만 조회
- `Dockerfile.m2d`/`requirements-m2d.lock`: 일반 API 이미지와 분리한 재현 실행물

HTTP는 lifespan 적재·검사·예열이 끝나기 전에는 열리지 않는다. 적재 실패를 잡은 뒤에는
liveness 200과 모델/전체 readiness 503을 제공한다. health 조회는 재적재나 추론을 하지
않는다. 오류 응답에는 경로·파일 내용·traceback이 없고 로그에는 안전한 결과 코드와 예외
종류만 기록한다.

## 재현 절차

원본 구조와 모델 묶음은 읽기 전용으로 다룬다. 준비 출력과 개발 probe는 Git 저장소 밖의
빈 디렉터리를 사용한다.

```bash
python3 scripts/model_runtime/prepare_m2d_source.py \
  --source-root "$M2D_SOURCE_ROOT" \
  --output "$M2D_BUILD_CONTEXT"

python3 scripts/model_runtime/prepare_development_probes.py \
  --research-root "$M2D_RESEARCH_ROOT" \
  --bundle "$V1_B_BUNDLE" \
  --output "$M2D_PROBE_ROOT"

docker buildx build \
  --platform linux/amd64 --load \
  --build-context m2d_source="$M2D_BUILD_CONTEXT" \
  --file apps/api/Dockerfile.m2d \
  --tag baby-care-m2d-v1-b:local \
  apps/api

docker run --rm --platform linux/amd64 \
  --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --mount type=bind,src="$V1_B_BUNDLE",dst=/opt/baby-care-runtime/model/selected,readonly \
  --mount type=bind,src="$M2D_PROBE_ROOT",dst=/probes,readonly \
  --mount type=bind,src="$M2D_REPORT_ROOT",dst=/report \
  --entrypoint python baby-care-m2d-v1-b:local \
  -m baby_care_m2d.verify \
  --allowed-root /opt/baby-care-runtime \
  --bundle /opt/baby-care-runtime/model/selected \
  --source /opt/baby-care-runtime/m2d-source \
  --audio-probe-root /probes \
  --audio-probe-manifest /probes/manifest.json \
  --output /report/verification.json
```

검증 CLI의 비정상 종료를 성공으로 바꾸지 않는다. 보고서의 `status=failed`이면 오차가
기준을 초과한 것이다. 운영 서버는 같은 모델 mount를 `readonly`로 연결하고 기본 CMD를
사용한다. 실제 인증·DB 설정이 없으면 모델이 준비돼도 전체 readiness는 의도대로 503이다.

## 실제 Linux 결과

같은 고정 파일, Python 3.12.12, PyTorch 2.14.0+cpu, timm 1.0.29, nnAudio 0.3.4,
NumPy 2.4.6, soundfile 0.13.1, soxr 1.0.0, FFmpeg 7.1.1을 사용했다. 두 실행 모두
`--network none`, read-only 루트, read-only 원본 모델 mount였고 네트워크 인터페이스는
loopback만 관측됐다. 봉인 Donate test 90개, 초기/부분 checkpoint, 전체 학습 자료는 쓰지
않았다.

| 실행 | 상태 | 적재 | tensor probe 최대 점수 차이 | PCM 최대 입력/점수 차이 | 중앙 추론 |
|---|---:|---:|---:|---:|---:|
| Linux x86_64, Apple Silicon 위 에뮬레이션 | 실패 | 8.334초 | `1.5795231e-6` | `6.6518784e-5` / `1.6689301e-6` | 0.956초 |
| Linux aarch64, 네이티브 | 실패 | 6.827초 | `2.0265579e-6` | `6.0439110e-5` / `1.8477440e-6` | 0.453초 |

관측 peak RSS는 x86_64 약 1072 MiB, aarch64 약 1110 MiB였다. 이는 단일 로컬 실행의
`ru_maxrss`이고 컨테이너 메모리 한도나 Cloud Run sizing이 아니다. x86_64 시간은
에뮬레이션 값이므로 운영 지연이나 p95 근거로 사용하지 않는다.

차이 분석:

- `probes.pt`의 기대 점수는 #18에서 macOS MPS로 생성됐다. 동일 고정 tensor를 Linux
  CPU에 넣었을 때 backend 수치 차이가 `1e-6`을 소폭 넘었다.
- 개발 전처리 기준은 macOS에서 생성됐다. Linux aarch64의 16 kHz PCM 두 개는 입력이
  정확히 일치했지만 8→16 kHz soxr 세 개는 달랐다. Linux x86_64 에뮬레이션에서는 FFT
  경로까지 미세하게 달랐다.
- oneDNN을 끈 진단 실행은 x86_64 tensor 차이를 `1.0132790e-6`까지 줄였지만 여전히
  실패였고, 검사를 통과시키기 위한 backend 변경으로 채택하지 않았다.
- CAF 두 개는 aarch64에서 기준과 일치했지만, AMR-NB 3GP 세 개는 입력 최대
  `1.1911182`, 점수 최대 `0.00726557` 차이를 보였다. 압축 형식은 모두 미지원으로 둔다.

정확한 기계 판독 결과는
[`b02-b06-v1-model-runtime-verification.json`](./b02-b06-v1-model-runtime-verification.json)에
있다. 원본 음원·특징·가중치·절대 로컬 경로는 포함하지 않았다.

## readiness와 실패 복구

합성 단위 시험에서 다음을 확인했다.

- 모델 비활성 기본 프로필은 기존 인증·DB readiness만 요구한다.
- 활성 모델은 필수이며 정상 factory는 health 반복 조회와 두 번의 추론에도 한 번만 적재된다.
- 누락 설정, 누락 파일, 해시·라벨·전처리·구조·source/path 오류, factory 예외는 실패한다.
- 모델 실패 후 liveness는 200, readiness는 503이며 health 반복 조회가 재시도하지 않는다.
- 모델 성공도 합성 인증 실패를 가리지 않는다.
- 개별 입력 오류는 준비된 모델 상태를 실패로 바꾸지 않는다.
- CPU 추론은 event loop 밖에서 실행되고 동시에 하나만 실행된다.

실제 전체 가중치 서버에서도 적재 후 liveness 200과 `checks.model`의
`required/configured/ready=true`를 확인했다. 실제 인증·DB는 연결하지 않았으므로 두 check와
전체 readiness는 503이었다. 별도 임시 묶음의 누락 모델 시험에서는 liveness 200,
모델 ready false, 전체 readiness 503이었고 원본은 수정하지 않았다. 합성 인증·DB probe로
얻은 readiness 200은 실제 Supabase 연결 증거가 아니다.

복구는 고정 레지스트리에 맞는 파일·경로를 다시 준비하고 프로세스를 재시작하는 방식뿐이다.
실행 중 다운로드, 자동 교체, 재학습, health 기반 재시도, 다른 가중치나 STUB 대체를 하지
않는다.

## 회귀 검사

- `verify:quick`: 계약·생성물 일치, Ruff, mypy 40개 source, API 단위 시험 105개,
  웹 typecheck·Vitest·lint·production build, 증거 민감정보 검사가 모두 통과했다.
- `verify:container`: 기존 경량 API 이미지의 digest 고정·non-root, 미구성 liveness 200 /
  readiness 503, 실패 후 자원 정리가 통과했다.
- 실제 M2D 의존성이 설치된 Linux 이미지에서도 mypy가 통과했다. 위 두 아키텍처 검증은
  전체 가중치 적재·예열·추론 자체는 성공했지만 고정 수치 기준 때문에 의도적으로 종료
  코드 1과 `status=failed`를 반환했다.

## 미실행·다음 작업

- x86_64 실제 머신의 비에뮬레이션 재측정과 `1e-6` 기준/기준 장치의 별도 승인
- 브라우저·실기기 PCM 생성, 기기·음질·잡음·비울음 및 지원 형식 검증
- 라벨의 제품 의미 검토, 보정과 판단 유보 정책. 현재 점수에 확률 의미를 붙이지 않음
- B-05 업로드·권한·격리·보관과 B-06 분석 상태/lease/저장/재시도 API 연결
- 실제 Supabase 인증·DB와 모델을 함께 둔 readiness, Cloud Run 부하·cold start·메모리 검증
- 공개 이미지/모델 업로드와 웹 `/capabilities` 활성화
- Donate 봉인 test 90개 재평가. 이 작업의 오차나 설정을 이용해 튜닝하지 않음

위 항목 전에는 이 실행 기반을 B-02/B-06 전체 완료나 제품 출시 승인으로 표시하지 않는다.

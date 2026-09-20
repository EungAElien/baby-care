# B-05 음원 수신·품질·보관 API 인계

기준: 계약 1.1.1 · 작성일: 2026-09-20 · 서버 구현: B · 브라우저 후속: A-02/A-05

## 완료 범위와 완료가 아닌 범위

B-05는 보호자가 선택한 원본 파일을 FastAPI가 발급한 정확한 private Storage 경로로
직접 전송하고, 서버가 원본 객체를 다시 읽어 검증한 뒤 `READY` 또는 `REJECTED`로
확정하는 경계를 구현한다. 업로드에 필요한 최소 `createEpisode`·`getEpisode`, STANDARD와
TUS 승인, 만료 후 재발급, 완료, 취소, 조건부 재생, 원본·파생 PCM의 내구성 있는 삭제가
포함된다. 사건 상세는 연결된 음원·분석·행동·행동 묶음·반응·상태 관찰을 실제 DB에서
조회한다.

다음은 이 작업의 완료 범위가 아니다.

- B-06 분석 생성·재시도·후보·ABSTAIN 저장. 병합된 V1 B 런타임은 적재 기반일 뿐
  B-05 완료가 모델 분석 허용을 뜻하지 않는다.
- HIGH_NOISE·NO_CRY 판정. 검증된 판정기가 없어 B-05가 이 값을 만들지 않는다.
- B-10 자동 감지 전체. `AUTO` 사건은 이미 활성인 동일 아기·동일 사용자의 관측 세션을
  확인할 뿐 세션을 대신 만들지 않는다.
- 운영 Scheduler·Cloud Run·백업 삭제 확인과 실제 가족 자료 처리.
- Mac/Windows Chrome, Android Chrome, iPhone Safari의 실제 녹음. 서버 합성 파일 시험과
  실제 기기 시험은 아래 표에서 분리한다.

계약 필드·경로·enum은 바꾸지 않았고 OpenAPI 버전은 1.1.1을 유지한다.

## 새로 가능한 API 흐름

1. `POST /v1/episodes`로 `MANUAL` 또는 `FILE` 사건을 만든다. `AUTO`이면 유효한
   `observation_session_id`가 필수다.
2. `POST /v1/episodes/{episode_id}/uploads`에 **완성된 원본 Blob**의 실제 MIME,
   바이트 수, 선택적 SHA-256과 길이를 보낸다. `Idempotency-Key`는
   `client_request_id`와 같은 UUID다.
3. 응답의 `bucket`, 난수 `object_key`, `method`, `upload_endpoint`, `expires_at`만 사용한다.
   파일명·기기명은 경로 또는 TUS metadata에 넣지 않는다.
4. 브라우저는 publishable key와 현재 사용자 access token으로 Storage에 직접 전송한다.
   서버 secret/service-role key는 브라우저에 전달하지 않는다.
5. `POST /v1/uploads/{upload_id}/complete`를 호출한다. 서버가 Storage 원본을 다시 내려받아
   실제 크기·SHA-256·컨테이너·코덱·스트림·길이·PCM 출력을 확인한다.
6. 성공한 `AudioAsset.status=READY`만 B-06 분석 입력 후보가 된다. `READY`는 분석 완료가
   아니다.
7. 보관 동의와 현재 권한·기한이 모두 유효할 때만
   `GET /v1/audio-assets/{audio_id}/playback`이 최대 60초 재생 URL을 발급한다.

취소는 전송을 먼저 중단한 뒤 `POST /v1/uploads/{upload_id}/cancel`을 호출한다. 취소된
음원을 다시 보내려면 새 `audio_id`로 2단계부터 시작한다. 만료된 **미완료** 음원만
`POST /v1/audio-assets/{audio_id}/uploads`로 새 15분 승인을 받을 수 있으며, 이때 object
key는 바뀌지 않는다.

## 승인·할당량·TUS 경계

| 항목 | 서버 규칙 |
|---|---|
| 원본 상한 | 25,000,000 bytes(십진), 빈 객체 거부 |
| 사건별 실제 길이 | `AUTO` 20초, `MANUAL` 30초, `FILE` 60초 |
| STANDARD/TUS 선택 | `bytes > 6 × 1024 × 1024` 또는 `prefer_resumable=true`이면 TUS |
| TUS 청크 | 마지막 청크를 제외하고 6 MiB(`6 × 1024 × 1024`) 고정 |
| 업무 승인 | 정확한 사용자·세션·아기·사건·음원·bucket·object key에 15분 |
| 미완료 한도 | 사용자별 활성 승인 최대 3개, 일일 할당 250,000,000 bytes |
| 난수 경로 | `{baby_id}/{audio_id}/{random_hex}`; 확장자·원본명·기기 정보 없음 |
| 덮어쓰기 | `x-upsert=false`; 확정 객체 UPDATE와 클라이언트 DELETE 금지 |

Supabase TUS의 직접 Storage hostname과 6 MiB 청크 권고를 사용한다. 제공자 TUS upload URL은
최대 24시간 살아 있을 수 있지만 프로젝트 승인은 15분이다. 기존 URL만으로 계속할 수
없으며 현재 승인·세션·멤버십이 다시 성립해야 한다. 만료 뒤 같은 object key로 재발급하면
탭 메모리에 남은 기존 TUS URL을 이어 쓸 수 있다. 재발급 전, 취소 후, 세션 회수 후에는
같은 URL의 PATCH가 거부됨을 로컬 Storage에서 확인했다.

일반 업로드에 Supabase signed upload URL을 사용하지 않는다. 제공자 signed upload URL의
기본 2시간 수명은 15분 업무 승인과 맞지 않기 때문이다. STANDARD도 사용자 JWT와 Storage
RLS가 정확한 활성 grant를 검사한다.

참고 문서:

- [Supabase resumable uploads](https://supabase.com/docs/guides/storage/uploads/resumable-uploads)
- [Supabase create signed upload URL](https://supabase.com/docs/reference/javascript/file-buckets-createsigneduploadurl)
- [Supabase signed downloads](https://supabase.com/docs/guides/storage/serving/downloads)
- [Supabase Storage access control](https://supabase.com/docs/guides/storage/security/access-control)

### A의 endpoint 검증

JWT를 붙이기 전에 URL을 문자열 prefix가 아니라 `URL` 객체로 파싱한다.

- STANDARD: 구성된 Supabase API origin과 정확히 같고 경로가
  `/storage/v1/object/baby-audio/`로 시작해야 한다.
- TUS: 구성된 직접 Storage origin과 정확히 같고 경로가
  `/storage/v1/upload/resumable`과 정확히 같아야 한다.
- 사용자 정보, access token, TUS URL은 로그·오류 수집·영구 저장소에 남기지 않는다.
- 재개 정보는 탭 메모리에서 `(user_id, baby_id, audio_id, upload_id)`와 함께 보관하고
  계정·아기 전환, 로그아웃, 멤버십 해제, 취소 때 폐기한다.

## 원본에서 V1 입력까지의 소유권

| 단계 | 저장·검증 값 | 변환 책임 |
|---|---|---|
| 브라우저 원본 | 최종 Blob의 실제 `mimeType`, bytes, 선택적 SHA-256 | A는 모든 `dataavailable` 조각과 정상 stop의 마지막 조각을 한 파일로 완성 |
| Storage 원본 | 별도 `audio_id`, 원본 object key, 실제 bytes·SHA-256, 검증한 container·codec | B-05가 원본을 덮어쓰지 않고 읽기 전용 검증 |
| 파생 PCM | 별도 `derivative_id`·object key·bytes·SHA-256·decoder/preprocessing-boundary version | B-05가 signed 16-bit WAV로 디코딩하되 **원본 샘플레이트·채널 수를 유지** |
| V1 모델 입력 | 16 kHz mono와 V1 고정 전처리 | B-06만 downmix·resample·정규화. B-05 PCM에 다시 같은 처리를 저장하지 않음 |

파생 종류는 `PCM_S16LE_SOURCE_RATE`, 경계 버전은 `source-rate-pcm-s16le-v1`이다. 원본
`AudioAsset.bytes/checksum_sha256/mime_type`을 파생 PCM 값으로 덮어쓰지 않는다. B-06은
`audio_derivatives`의 READY 행을 입력으로 받고, 기존 V1 전처리의 샘플레이트 변환과
정규화를 정확히 한 번 적용해야 한다. V1 가중치·라벨·봉인 결과와
`calibration_status=NOT_VALIDATED`, `release_ready=false`는 바꾸지 않았다.

## 실제 바이트 검사와 품질 판정

FFmpeg/ffprobe 7.1.1 이미지는 digest로 고정했다. 입력은 서버가 만든 임시 디렉터리의 로컬
파일 하나이고, 인자는 배열로 전달하며 shell을 사용하지 않는다. `file,pipe` 외 프로토콜,
외부 URL, 영상·자막·데이터 스트림, 둘 이상의 오디오 스트림을 거부한다. 실행은 30초,
프로세스당 CPU 30초, 주소 공간 512 MiB, 출력 25,000,000 bytes, 동시 디코딩 기본 2개로
제한한다. 임시 디렉터리는 성공·실패 뒤 문맥 종료와 함께 삭제된다.

| 결과 | 측정·조건 | AudioAsset | HTTP/재시도 |
|---|---|---|---|
| `TOO_LARGE` | 원본 또는 파생 PCM이 25,000,000 bytes 초과 | `REJECTED`, 정리 job | 413, 새 `audio_id` 필요 |
| `UNSUPPORTED_CODEC` | MIME/실제 bytes 불일치, 허용하지 않은 container·codec·stream | `REJECTED`, 정리 job | 415, 다른 실제 형식으로 새 업로드 |
| `TOO_LONG` | 디코딩 길이가 사건 source 상한 초과 | `REJECTED`, 정리 job | 422, 새 `audio_id` 필요 |
| `TOO_SHORT` | PCM 길이 `< 1.0 s` | `REJECTED`, 정리 job | 422, 다시 녹음 |
| `SILENCE` | 전체 signed PCM RMS `< -50 dBFS` | `REJECTED`, 정리 job | 422, 입력 장치 확인 |
| `CLIPPING` | `abs(sample) >= 32734`인 샘플이 전체의 `>= 1%` | `READY`, warning 전달 | 200, B-06이 품질 정보와 함께 판단 |
| `DECODE_ERROR` | 손상·잘림·유효하지 않은 PCM 출력 | `REJECTED`, 정리 job | 422, 새 업로드 |
| checksum/byte 불일치 | 예약값·완료값·실제 객체가 다름 | `REJECTED`, 정리 job | 422, 같은 객체 승격 금지 |
| Storage/decoder 일시 장애 | timeout·5xx·고정 바이너리 일시 불가 | `ALLOCATED` 복구, run `FAILED` | 503, 새 idempotency key로 재시도 |

`HIGH_NOISE`와 `NO_CRY`는 계약 enum에 있지만 B-05가 만들지 않는다. 검증된 음향 잡음
측정기와 울음 감지기가 없으므로 임의 에너지 휴리스틱이나 원인 분류 점수를 대신 쓰지
않는다. B-06은 입력 가능 여부와 모델 `ABSTAIN`을 별도로 저장한다. 시각 정보 부재도 음질
오류가 아니다.

검증은 짧은 DB 트랜잭션에서 실행 token과 2분 lease를 먼저 확보하고, 다운로드·디코딩은
트랜잭션 밖에서 실행한다. 저장 직전 grant·동의·상태·token을 다시 잠가 확인한다. 만료된
VERIFYING lease는 다음 완료 시 `STALE`로 닫고 새 실행이 인계받을 수 있으며, 취소·동의
철회된 옛 실행은 READY를 되살릴 수 없다.

## 보관·재생·삭제

| 조건 | 동작 |
|---|---|
| `AUDIO_RETENTION=GRANTED` | READY 시점부터 기본 7일 보관 |
| 보관 미동의 | `delete_after=received_at+1h`; B-06 terminal 저장 직후 내부 hook으로 즉시 정리 예약 |
| 보관 철회/처리 동의 철회 | 원본·파생을 즉시 `DELETING`, 재생 신규 발급 차단, 내구성 cleanup job |
| 거부·취소·만료·고아 | 접근 차단 뒤 cleanup worker가 실제 Storage DELETE |
| Storage DELETE 실패 | job `FAILED`, 지수 backoff, DB는 `DELETING` 유지 |
| 실제 Storage DELETE 성공 | 그 뒤에만 원본·파생 metadata를 `DELETED`, job을 `COMPLETE` |

정리 실행기는 재시작 가능한 DB lease/token을 사용한다.

```bash
cd apps/api
PYTHONPATH=src .venv/bin/python -m baby_care_api.audio_cleanup --limit 20
```

필요 환경은 `BABY_CARE_DATABASE_URL`, `BABY_CARE_SUPABASE_URL`, 서버 전용
`BABY_CARE_SUPABASE_SECRET_KEY`다. 한 번 발급한 재생 URL은 보관 철회 후에도 남은 최대
60초 동안 유효할 수 있고, 이미 내려받은 파일을 회수할 수는 없다. 새 URL만 즉시
차단한다.

TUS 미완료 조각은 Storage provider가 관리하며 객체 목록에 완성 객체로 나타나지 않는다.
로컬에서는 만료·취소 후 PATCH와 완료 승격 차단을 확인했지만, hosted Supabase 내부 조각의
실제 제거 시각과 백업 보관은 확인하지 않았다. 운영 정책·provider 보관 확인은 B-12/B-13
배포 인수에 남는다.

## 자동 검증과 실제 기기 결과

| 입력/환경 | 서버 생성·디코딩 | 실제 브라우저 녹음 | 현재 판정 |
|---|---:|---:|---|
| WAV / PCM s16le | 통과 | 미실행 | 서버 지원, 기기 지원 주장 안 함 |
| WebM / Opus | 통과 | 미실행 | 서버 지원, Chrome 실제 Blob 확인 필요 |
| MP4/M4A / AAC | 통과 | 미실행 | 서버 지원, Safari 실제 Blob 확인 필요 |
| raw AAC / AAC | 통과 | 미실행 | 서버 지원, 브라우저 생성 여부 미확인 |
| video 포함 MP4 | 거부 | 해당 없음 | 지원 안 함 |
| 다중 오디오 track | 거부 | 해당 없음 | 지원 안 함 |
| FLAC·그 밖의 codec | 거부 | 미실행 | 지원 안 함 |
| Mac Chrome | 해당 없음 | 미실행 | A-02 후속 |
| Windows Chrome | 해당 없음 | 미실행 | A-02 후속 |
| Android Chrome | 해당 없음 | 미실행 | A-02 후속 |
| iPhone Safari | 해당 없음 | 미실행 | A-02 후속 |

컨테이너 smoke는 합성 tone으로 위 4개 허용 형식을 실제 고정 FFmpeg에 통과시키고,
MIME 위조·영상·다중 track·미지원 codec·손상 파일·크기·60초 PCM 출력 경계를 검사한다.
합성 파형은 실제 영아 울음/비울음 성능 근거가 아니다.

```bash
docker build -t baby-care-api:b05 apps/api
docker run --rm --entrypoint python baby-care-api:b05 \
  -m baby_care_api.audio_decoder_smoke
```

전체 자동 검사는 다음 공통 진입점을 사용한다.

```bash
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:quick
npm run verify:container
API_PYTHON="$PWD/apps/api/.venv/bin/python" npm run verify:integration
npm run verify:failure-detection
```

2026-09-20 최종 로컬 실행 증거는 다음과 같다.

| 게이트 | 결과 |
|---|---|
| `verify:quick` | API 단위 153건, 계약·생성물 일치, Ruff·mypy, 웹 typecheck·Vitest·lint·production build 통과 |
| `verify:container` | digest 고정 Python·FFmpeg, 비루트 runtime, 4개 허용 포맷과 7개 거부/경계 묶음, 미구성 fail-closed·정리 probe 통과 |
| `verify:integration` | 전체 pytest 166건 중 필수 통합 13건·skip 0, branch coverage 90.28%, pgtap 96건, 직접 Storage HTTP 23건, 구성 컨테이너·자원 정리 통과 |
| `verify:failure-detection` | 생성물 불일치·통합 환경 누락·하위 job 실패·민감 marker의 4개 음성 대조군 통과 |

이는 격리 로컬 합성 자료의 결과다. hosted Supabase, 운영 데이터, 실제 기기 녹음 또는 실제
영아 울음 성능 결과로 확대 해석하지 않는다.

`verify:integration`은 매번 별도 project id·빈 포트·임시 Supabase 복사본을 만들고 자신이
만든 자원만 중지한다. B-05 시험은 실제 로컬 Auth JWT, STANDARD와 TUS Storage, 완료,
재발급·취소·세션 회수, 서명 재생, 동의 철회, 삭제 실패/재시도와 실제 객체 삭제를 사용한다.
운영 Supabase와 실제 가족 자료는 사용하지 않는다.

## A-05 구현·시험 체크리스트

이번에 새로 가능한 동작은 사건 생성부터 실제 Storage 전송, 완료 상태, 조건부 재생까지의
서버 전체 경로다. 기존 화면 목에서 바뀌는 점은 다음과 같다.

- 서버가 준 `method`를 따라 STANDARD/TUS를 선택하고 임의 경로를 만들지 않는다.
- `MediaRecorder.isTypeSupported()`는 후보 탐색일 뿐이다. 실제 recorder `mimeType`과 stop
  뒤 완성 Blob의 bytes를 사용하고 확장자 또는 MIME 문자열만 WAV로 바꾸지 않는다.
- `dataavailable` 조각 각각을 업로드하지 않는다. 정상 stop이 낸 마지막 데이터까지 모은
  최종 파일 하나를 보낸다.
- 413·415·422를 모두 “녹음 실패” 하나로 합치지 않고 표의 재시도 안내를 표시한다.
- `READY`를 분석 완료로 표시하지 않는다. B-06이 없으면 분석 시작 동작을 비활성으로 둔다.
- 취소·계정/아기 전환 때 fetch/TUS 전송을 중지하고 탭 재개 정보·원본 Blob·재생 URL을
  폐기한다.
- 재생 URL을 캐시·로그·공유하지 않고 `expires_at` 또는 전환 시 제거한다.

A가 추가로 해야 할 실제 시험은 네 대상 기기에서 후보 탐지값, recorder의 최종
`mimeType`, 완성 Blob의 ffprobe 결과, stop/마지막 chunk 포함 여부, 6 MiB TUS 중단·재개,
15분 만료·취소·로그아웃·아기 전환을 기록하는 것이다. 관련 규칙은
[MDN `isTypeSupported`](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/isTypeSupported_static),
[MDN `mimeType`](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/mimeType),
[W3C recording data handling](https://www.w3.org/TR/mediastream-recording/#data-handling)을
따른다. 미실행 기기는 통과로 표시하지 않는다.

## B-06·운영 후속

- B-06은 READY PCM derivative를 읽어 V1 전처리를 한 번만 적용하고 Analysis terminal 상태를
  저장한 **뒤** `enqueue_post_analysis_cleanup(audio_id)`를 호출한다.
- B-06은 B-05 quality warning과 모델 ABSTAIN/NO_CRY를 분리한다. 원인 후보·보정·유보
  임계값은 이 작업에서 바꾸지 않았다.
- A-05는 위 실제 기기 표를 채우고 서버 지원 MIME과 제품 지원 기기를 따로 표시한다.
- B-12/B-13은 cleanup CLI를 Scheduler/Job에 등록하고 hosted TUS 조각·백업 보관·지연 알림,
  운영 IAM/secret, 전역 부하·할당량을 검증한다.
- Cloud Run 배포와 운영 migration 적용은 수행하지 않았다.

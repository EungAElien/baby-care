# A-05 브라우저 음원 입력 검증표

기준: 계약 1.1.1, B-05 구현 커밋 `efa2d18`, B-05 브랜치 확인 커밋 `21f5d42dbb81c7a0f4b4d183cec17007f3c5abb6`. 이 브랜치 내용은 `develop` 이력에 포함된다. A-02 개발용 측정 화면은 실제 음원 결과가 아니며 [A-02 기기 시험표](../a02-audio-device-check.md)의 실기기 시험은 아직 완료되지 않았다.

## 자동 검사와 실제 환경의 경계

| 구분 | 확인 내용 | 상태 |
| --- | --- | --- |
| 웹 합성 자동 시험 | 실제 Blob MIME·크기, 정상 stop의 마지막 조각, 6 MiB TUS 경계·청크·재개, URL 출처·경로 거부, 취소 순서, 만료 재승인, complete 응답 유실, 아기/계정 전환 시 전송·재생 정리, 품질 문구 | 통과. 실제 마이크·Storage 증거는 아님 |
| B-05 서버 기존 합성 검증 | B-05 인계의 로컬 Auth·DB·Storage·FFmpeg STANDARD/TUS·품질·재생 시험 | B-05 인계 기록 참조. 이 A-05 작업에서 재실행한 결과로 표기하지 않음 |
| A-05와 B-05 실연동 | 실제 브라우저 → 로컬 B-05 서버 → 로컬 Storage 전송·완료·재생 | 미실행. 이 작업 PC에 Docker CLI가 없고 WSL 배포 열거가 접근 거부되어 로컬 Supabase를 시작하지 못함 |
| 실제 브라우저·기기 | 아래 네 대상에서 직접 녹음·파일·권한·연결 중단 | 미실행 |

웹 자동 게이트는 최신 `develop` 재적용 후 `typecheck`, 전체 ESLint, Vitest 25파일 115건, `NEXT_PUBLIC_ENABLE_MOCK_NAV=true/false` 각각의 Next 생산 빌드가 통과했다. 실제 브라우저와 서버 통합 경로의 통과로 해석하지 않는다.

## 재현 준비

1. B-05 서버와 로컬 Supabase를 별도 B-05 기준 체크아웃에서 띄우고, 합성 시험 계정에 현재 아기의 `ACTIVE` 멤버십을 준다. 개인 음원 대신 합성 WAV·WebM/Opus·MP4/AAC 파일을 사용한다.
2. 웹의 `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, publishable key를 로컬 값으로 설정한다. B-05 grant가 별도 직접 Storage origin을 반환하면 `NEXT_PUBLIC_SUPABASE_STORAGE_URL`도 정확히 설정한다. secret/service-role key는 웹에 넣지 않는다.
3. 실제 계정으로 로그인하고 `/babies/{baby_id}/detect`를 연다. 개발용 목 계정 화면에서는 업로드를 시작할 수 없어야 한다.
4. 네트워크 패널에서 `POST /episodes` → `POST /episodes/{id}/uploads` → Storage 전송 → `POST /uploads/{id}/complete` → `GET /episodes/{id}` 순서를 확인한다. 요청/응답 캡처에는 JWT, signed URL, 원음, 원본 파일명을 남기지 않는다.

## 기기별 직접 시험표

각 칸은 해당 기기에서 실행한 뒤 결과, 브라우저 버전, 실제 `recorder.mimeType`, 완성 Blob MIME·bytes, 서버가 검증한 container/codec, 문제가 났다면 request ID만 기록한다. 후보 `isTypeSupported()` 값만으로 지원 판정을 내리지 않는다.

| 기기 | 후보·실제 형식·마지막 조각·30초 자동 stop | 60초 파일 입력·6 MiB 경계 | TUS 중단·같은 탭 재개 | 15분 만료·같은 key 재승인 | 취소·로그아웃·아기 전환·재생 정리 |
| --- | --- | --- | --- | --- | --- |
| Mac Chrome | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 |
| Windows Chrome | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 |
| Android Chrome | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 |
| iPhone Safari | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 |

### 각 기기에서 따를 절차

- **녹음:** 사용자가 시작하기 전 권한 요청·트랙이 없는지 확인한다. 짧게 시작/정지하고 마지막 `dataavailable` 조각이 완성 Blob에 포함되는지 서버 검증 바이트와 비교한다. 30초 타이머로 정지하고 실제 MIME·길이를 기록한다. 권한 거부, 화면 숨김/잠금, 통화 개입도 확인한다.
- **파일:** 1초 미만, 무음, clipping, 30초 초과 직접 녹음, 60초 초과 파일, 25,000,000바이트 초과, 손상·미지원 코덱을 각각 시도한다. `CLIPPING`은 READY 경고, 다른 서버 거부는 사유와 다음 행동으로 보여야 한다. 파일 시각 불명은 맥락 제한이어야 한다.
- **전송:** 정확히 6 MiB는 서버 선택 STANDARD, 그보다 1바이트 큰 파일 또는 불안정 연결 선택은 TUS여야 한다. 마지막 외 모든 TUS PATCH는 6 MiB인지 확인한다. 전송 중 네트워크를 끊고 같은 탭·같은 사용자/아기/음원/승인에서만 재개한다. 새 탭·전환 뒤 기존 재개 정보는 사용되지 않아야 한다.
- **만료/취소:** 미완료 승인이 15분 지난 뒤 전송이 거부되고, 같은 audio ID·object key로만 재승인되는지 확인한다. 전송 중 취소는 네트워크 중단 후 서버 cancel이며, 이후 재업로드는 새 audio ID여야 한다. complete 응답을 차단하고 사건 GET으로 READY/REJECTED를 회복하는지 확인한다.
- **재생·전환:** `AUDIO_RETENTION` 동의가 없거나 현재 권한이 사라지면 새 재생 URL을 받지 못해야 한다. 동의 후 READY 원본만 최대 60초 링크를 받고, 종료·만료·아기/계정 전환·로그아웃에서 플레이어와 Blob URL을 정리한다. 이미 발급된 URL과 내려받은 파일의 즉시 회수를 기대하지 않는다.

실기기 및 로컬 B-05 통합 시험이 끝나기 전에는 모바일 지원, A-02 전체 또는 A-05 전체 인수를 완료로 표시하지 않는다. `READY`는 업로드·검증 완료이며 B-06 분석 완료가 아니다.

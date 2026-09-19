# M2D 연구 문서와 관측 기록

이 폴더는 기존 프로젝트에서 작성한 연구 문서와 작은 JSON 기록을 보존한다. 학습 코드·원시 음원·데이터셋·가중치·체크포인트는 포함하지 않는다. 문서 안의 `data/`, `runs/`, `reports/`, `.venv`와 학습 명령은 별도 학습 프로젝트의 상대 경로다. `baby-care` 서비스 저장소에서 그대로 실행하는 명령으로 해석하지 않는다.

## 읽는 순서와 검증 범위

| 자료 | 의미와 한계 |
|---|---|
| [2026-09-15 로컬 학습 설계](m2d-local-training-plan-2026-09-15.md) | 당시 데이터·모델·저장공간·평가 설계. 이후 실행의 완료 증거가 아님 |
| [입력 준비](STAGE3.md) | 자료 정리·분할·전처리와 완료 조건 |
| [예비학습](STAGE4.md) | 제한된 입력으로 수행하는 예비 실험의 설정 |
| [전체 자기지도 추가학습](STAGE4_FULL.md) | 전체 실행 범위와 완료 확인 조건. 원인 분류 성능과 구분 |
| [B 계획의 현황 기록](../../planning/b-implementation-plan-2026-09-19/progress-snapshot.json) | 2026-09-19 19:36 KST 관측. 현재 실행 상태로 표시하지 않음 |

## 보존한 점검 기록

| 기록 | 확인할 범위 |
|---|---|
| [임의 초기화 모델 점검](snapshots/mps-stage2/random_model_check.json) | 합성 입력의 MPS 연산·갱신 점검. 학습된 가중치 저장이나 실음원 성능 검증이 아님 |
| [사전학습 모델 점검](snapshots/mps-stage2/pretrained_model_check.json) | 합성 입력의 적재·갱신 및 다운로드 무결성 기록. 단일 초기 실행 시간은 학습 벤치마크가 아님 |
| [군집 추출 변경 전 기록](snapshots/cluster-cpu-tuning/extraction_before.json) | 저장 당시 중단 상태와 처리량 |
| [파이프라인 변경 전 기록](snapshots/cluster-cpu-tuning/pipeline_before.json) | 저장 당시 관리 프로세스 상태 |
| [CPU 스레드 1개 부분 측정](snapshots/cluster-cpu-tuning/threads-1.json) | 같은 입력 일부에 대한 추론 측정. 전체 입출력과 파이프라인 성능을 포함하지 않음 |

JSON의 값·시각·경로·해시는 원본 그대로다. 과거 `/Users/`·`/private/tmp/` 경로와 프로세스 ID는 관측 환경의 기록이며 현재 사용 가능한 실행 위치가 아니다. 이번 이관에서는 모델을 다시 적재하거나 학습·평가하지 않았다.

## 별도 학습 프로젝트의 참조 자료

B 구현 계획이 참조한 아래 자료는 기존 ChatGPT 작업 폴더 밖의 별도 연구 프로젝트에 속한다. 여기서는 문서의 역할과 연구 프로젝트 기준 경로를 기록한다. 이 절은 해당 파일의 사본이나 새 검증 결과가 아니다. 최신 파일이 필요하면 별도 연구 프로젝트에서 내용·관측 시각·해시를 다시 확인한다.

### cluster-aux

`CLUSTER_AUX.md`: 군집 보조 학습의 설정과 상태를 설명한 별도 연구 문서. B 계획의 “현재 32군집”은 계획 작성 시점의 표현이다.

### stage4-completion

`reports/stage4_full_completion_check.json`: 기본 자기지도 추가학습의 당시 완료 점검 원본. B 계획의 스냅샷에 일부 관측값과 출처가 남아 있다.

### cluster-verification

`data/babycry_clusters_v1/verification.json`: 군집 목표 생성의 당시 검증 원본. 군집 ID가 원인 정답이나 실제 원인 분류 성능을 뜻하지 않는다.

### stage5-dual

`STAGE5_DUAL.md`: 후속 지도학습의 준비와 제한을 설명한 별도 연구 문서.

### stage5-runner

`train_stage5_dual.py`: 별도 연구 프로젝트의 지도학습 실행 코드. 문서 이관에 포함하지 않았으며 실행하거나 수정하지 않았다.

초기 설계에 남아 있는 `thread://`와 `chatgpt-conversation://` 링크는 당시 대화 식별자다. 팀원이 접근할 수 있는 공개 근거나 파일 경로로 간주하지 않는다. 공식 자료의 웹 링크는 원문에 보존했다.

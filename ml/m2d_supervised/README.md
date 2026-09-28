# M2D 지도학습

고정된 A/B/C 설계로 3개 시드 × 3개 폴드의 개발 학습 27회를 순차 실행한다. 학습기는
원본 인코더를 해시로 참조하고 마지막 두 블록·최종 norm·head와 옵티마이저·RNG를
부분 체크포인트로 저장한다. 완료한 실행은 최선 가중치와 결과를 검증한 후 재개 파일을
정리한다. 27회 개발 실행 뒤에는 별도 `launch-post` 명령으로 비교·최종 재학습·봉인
테스트·로컬 전체 모델 내보내기를 수행한다. [후속 실행 안내](docs/POST_TRAINING.md)의
완료 조건과 고정 선택 규칙을 따른다.

## 준비된 범위

- A/B/C 실험의 초기 체크포인트와 해시
- 2022 M2D 체크포인트의 공식 하위호환 기본 정규화값 `[-7.1, 4.2]`
- Donate-a-Cry 및 EnesBabyCries1의 고정 분할과 라벨 체계
- BABYCRY 사전학습 완료 증거와 M2D 소스 리비전
- 전처리, 입력 자르기, 계층 균형 표본추출 및 클래스 가중치 규칙
- 데이터셋별 분리 헤드와 5×768 풀링 계약
- 27개 개발 실행의 하이퍼파라미터, 비교 및 선택 규칙
- 원본 전체 특징 파일의 해시·형상·유한값, 교차 데이터셋 정확 중복, 저장공간·전원·락 검사

Enes는 별도 3분류 보조 헤드로만 사용한다. 제품 배포 헤드는 Donate 5분류이며, 두 라벨
체계를 하나의 원인 코드처럼 합치지 않는다.

## 사전검증과 실행

프로젝트 밖의 데이터와 가중치 위치는 실행 시 주입한다. 절대경로, 원본 음원, 모델 가중치는
저장소에 기록하지 않는다.

```sh
cd "$REPOSITORY_ROOT/ml/m2d_supervised"
"$RESEARCH_ROOT/.venv/bin/python" -m m2d_supervised preflight \
  --research-root "$RESEARCH_ROOT" \
  --output-root "$RESEARCH_ROOT/runs/supervised_v1" \
  --m2d-source-root "$RESEARCH_ROOT/m2d" \
  --repository-root "$REPOSITORY_ROOT"
```

성공 보고서는 `$RESEARCH_ROOT/runs/supervised_v1/preflight/preflight.json`에 원자적으로
기록된다. 전체 특징 검사를 생략하는 `--bounded-check`는 개발용이며
`ready_for_training=false`를 유지한다.

같은 인자로 `pilot`을 실행하면 별도 예비 실행에서 실제 MPS 처리량, 부분 저장·복원,
다음 업데이트 일치를 확인한다. 이어서 `launch`를 실행하면 검증된 코드의 해시별 복사본에서
백그라운드 학습을 시작한다. 이미 시작한 작업은 동일 명령으로 체크포인트부터 재개하며,
동시 실행·코드/데이터/초기값이 바뀐 재개는 거부한다.

출력 위치, 중단·재개 방법과 학습 후 할 일은 [운영 안내](docs/TRAINING_RUNBOOK.md)에 있다.
2026-09-20 실제 시작 증거와 예상 시간은 [학습 시작 기록](docs/TRAINING_START.md)에 있다.

## 검증

```sh
python -m unittest discover -s tests -v
python -m compileall m2d_supervised tests
```

고정 계약과 후속 구현 범위는 [docs/HANDOFF.md](docs/HANDOFF.md)에 있다.

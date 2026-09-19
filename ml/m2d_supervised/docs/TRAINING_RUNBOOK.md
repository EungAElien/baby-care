# M2D 학습 실행 안내

## 실행 범위

`launch`는 A/B/C × seed 42·43·44 × fold 0·1·2를 순차 실행한다. 각 시드·폴드에서
B → C → A 순서다. 매 실행은 원래 초기 인코더와 새 head/optimizer에서 시작하며,
3 epoch head 학습 뒤 마지막 두 블록·최종 norm을 최대 20 epoch 미세조정한다.
Donate 검증 Macro-F1 개선이 5 epoch 없으면 해당 실행을 끝낸다. warm-up 결과도
최선 후보에 포함하며 동률은 먼저 선택된 가중치를 유지한다.

C의 Enes는 Donate와 같은 수만 계층 표본추출하며 0.25 가중치의 보조 손실로 사용한다.
Enes 전체 검증은 C의 Donate 기준 최선 가중치가 확정된 뒤 실행별 한 번 수행한다.
27회 개발 학습이 끝나면 비교·테스트·내보내기를 자동 수행하지 않고 종료한다.

## 시작과 재개

실행 전 `RESEARCH_ROOT`와 `REPOSITORY_ROOT`에 로컬 경로를 설정한다. 기존 연구 폴더의
Python 3.11 환경(torch 2.14.0, numpy 2.4.6, timm 1.0.29)을 사용한다.

```sh
cd "$REPOSITORY_ROOT/ml/m2d_supervised"
"$RESEARCH_ROOT/.venv/bin/python" -m m2d_supervised pilot \
  --research-root "$RESEARCH_ROOT" \
  --output-root "$RESEARCH_ROOT/runs/supervised_v1" \
  --m2d-source-root "$RESEARCH_ROOT/m2d" \
  --repository-root "$REPOSITORY_ROOT"

"$RESEARCH_ROOT/.venv/bin/python" -m m2d_supervised launch \
  --research-root "$RESEARCH_ROOT" \
  --output-root "$RESEARCH_ROOT/runs/supervised_v1" \
  --m2d-source-root "$RESEARCH_ROOT/m2d" \
  --repository-root "$REPOSITORY_ROOT"
```

처음 실행 시 예비 실행을 통과해야 한다. 재개 시에는 같은 코드·설정·환경을 사용한다.
작업용 저장소가 바뀌었다면 `launch.json`의 `working_directory`에 보존된 코드 복사본에서
`launch`를 다시 실행한다. `command`는 동일한 `train-matrix` 재현 명령이다.
실행 중인 코드 복사본과 가상환경을 수정하지 않는다.

`launch`는 별도 프로세스를 만들며 절전 방지 `caffeinate`를 함께 사용한다. 전원 연결을
유지한다. 덮개 닫기·시스템 재시작으로 중단되면 마지막 완전한 체크포인트부터 재개한다.

## 진행 상태와 중단

- `matrix_progress.json`: 완료 실행 수, 현재 실험·시드·폴드·epoch, 처리량, 예상 잔여 시간
- `training.log`: epoch별 로그 및 예외 원인
- `launch.json`: 시작 프로세스, 실제 명령, 실행 코드·커밋
- `pilot/report.json`: 예비 실행 시간·메모리·저장 크기와 전체 시간 시나리오
- `development/<run_id>/progress.json`: 해당 실행의 마지막 상태

정상 중단은 `matrix_progress.json`의 `pid`에 TERM 신호를 보내거나 출력 폴더에
`STOP_TRAINING` 파일을 만들어 요청한다. 현재 업데이트가 적용되기 전 중단되면 그
업데이트를 통째로 재실행한다. 직접 종료할 때 `launch.json`의 caffeinate PID와 실제
학습 PID를 혼동하지 않는다. STOP 파일을 사용했다면 재개 전에 해당 파일을 치운다.

전원 분리·여유 공간 경계는 진행 상태를 저장하고 종료한다. 비정상 손실·가중치 또는
예기치 않은 오류는 기존 디스크 체크포인트를 보존하고 실패 이유를 기록한다. 실패를
무한 재시도하지 않으며 원인을 고친 뒤 같은 조건으로 재개할 수 있는지 확인한다.

## 보존 정책

`last.pt`에는 부분 가중치, AdamW 상태, phase, epoch·다음 update, best·patience,
Python/NumPy/Torch/MPS RNG, 누적 노출 기록과 코드·데이터·초기값 식별자가 들어 있다.
첫 업데이트, 매 25 update, epoch·단계 경계와 정상 중단에 원자적으로 저장한다.
예상 임시 저장량을 빼고 최소 8GiB를 남길 수 없으면 기록을 진행하지 않는다.

각 실행은 `best.pt`, 검증 예측, history, 설정, identity 및 완료 증거를 보존한다.
최선 모델 재적재와 예측 재현·파일 해시 검증이 끝난 후 해당 실행의 `last.pt`만 삭제한다.
전체 초기 인코더와 기존 연구 산출물은 변경하지 않는다. 최종 모델 내보내기는 후속이다.

완료 후 업무는 [인계 문서](HANDOFF.md)의 순서를 따른다.

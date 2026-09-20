# M2D 불균형 개선 개발 실험 v2

## 승인 범위와 기존 결과 보호

사용자가 2026-09-20 승인한 후속 범위는 작은 균형 자료 진단, B 분류기의 균형 학습,
마지막 두 인코더 블록 미세조정 비교다. 신규 자료 수집, 최종 전체 재학습, 테스트 평가,
제품 모델 교체·내보내기·배포는 이 실험에 포함하지 않는다.

기존 `supervised_v1` 모델·코드 복사본·결과는 읽기 전용으로 보존한다. Donate 개발 367개와
기존 3개 녹음/출처 그룹 분할만 사용한다. 테스트 90개와 Enes는 로더에서 접근을 거부한다.
Enes를 사용했던 v1 실험 C를 취소하거나 그 결과를 수정하지 않는다.

새 작업은 원격 `develop`의 `67ccba93851d4a11921c80004d26bc2cbf1d02da`에서 시작했다.
기존 로컬 저장소는 `refs/heads/develop 2` 참조 오류로 fetch에 실패했으므로 수정하지
않았다. 같은 원격의 별도 로컬 clone을 사용하며 원래 미추적 복사본·브랜치를 보존했다.

## 결과 확인 전에 고정한 계획

단일 기준은 [balanced_v2.json](../config/balanced_v2.json)이다. 원래 초기 인코더,
전처리, 특징 및 manifest 해시는 `experiment_v1.json`의 해시로 연결한다.

1. **학습 가능성 진단:** fold 0의 훈련 부분에서 seed 42로 라벨당 2개, 서로 다른 출처
   그룹의 실제 음원 총 10개를 고른다. B 인코더는 고정하고 중앙 crop의 특징을 한 번
   계산한다. 실제 분류기를 일반 교차엔트로피로 최대 500 update 학습한다. 학습 자료
   accuracy 1.0, 평균 손실 0.1 이하, 모든 출력의 유한한 비영 gradient, 인코더 불변 및
   실제 입력과 캐시 특징의 출력 차이 1e-6 이하여야 통과한다. 이는 암기 진단이며 일반화
   성능이 아니다. 실패하면 다음 단계는 시작하지 않고 원인을 검토한다.
2. **실제 MPS 재개 진단:** 별도 초기 B에서 마지막 두 블록·최종 norm과 분류기를 학습한다.
   실제 개발 입력 2 update 연속 실행과 1 update 저장·새 모델 복원·다음 update의 전체
   가중치 해시가 같아야 한다. 마지막 블록 변경과 고정된 나머지 인코더의 불변도 확인한다.
3. **개발 비교:** 아래 세 방법 × seed 42/43/44 × 기존 fold 0/1/2 = 27회를 순차 실행한다.

| 방법 | 표본 노출 | 손실 | 인코더 |
|---|---|---|---|
| natural_head | epoch마다 훈련 음원 각 1회 | 기존 역제곱근 빈도 가중치 | 계속 고정 |
| balanced_head | 라벨 균형 → 출처 그룹 순환 → 음원 순환 | 일반 교차엔트로피 | 계속 고정 |
| balanced_finetune | balanced_head와 같은 추출 순서 | 일반 교차엔트로피 | 3 epoch 뒤 블록 10·11과 최종 norm 조정 |

모든 방법은 새 **자기지도학습 B 인코더**와 동일 seed의 새 분류기에서 시작한다. 개발
367개 전체로 학습했던 v1 최종 분류기를 가져오면 fold 검증 누출이 되므로 사용하지 않는다.
진단에 사용한 분류기도 본 비교에 넘기지 않는다.

한 epoch의 총 노출은 해당 fold의 원래 훈련 음원 수 N으로 같다. 균형 추출은 라벨별
노출 차이를 최대 1로 유지하며 출처 그룹을 모두 순환하기 전에 같은 그룹만 반복하지
않는다. 각 그룹 안의 음원도 순환한다. 이를 독립 표본 수 증가로 세지 않는다.
자연 분포 방식과 균형 방식은 추출과 손실을 함께 바꾼 **레시피 비교**다. 추출만의
독립 효과를 주장하지 않는다.

모든 방법의 effective batch=16, micro batch<=2, head LR=1e-3, encoder LR=1e-5,
AdamW weight decay=0.01, gradient clip=1을 고정한다. 공통 3 epoch 준비 학습 뒤 최대
17 epoch를 더 실행하며, 검증 Macro-F1 개선 0.001 초과·patience 5의 같은 규칙을 쓴다.
따라서 최대 노출·업데이트 예산은 같지만 조기 종료에 따른 **실제** 학습량은 다를 수 있다.
기존 v1 B는 학습 방식·예산이 다른 역사적 참고값이며 위의 통제 비교군과 동일시하지 않는다.

선택은 준비 학습을 포함한 최고 검증 checkpoint로 한다. 미세조정 방법도 선택 checkpoint가
3 epoch 이내일 수 있으며 이 경우 `selected_encoder_finetuned=false`로 명시한다.
미세조정을 해봤다는 사실을 선택된 모델에 미세조정이 반영됐다는 뜻으로 바꾸지 않는다.

## 비교와 해석

각 seed에서 세 fold의 OOF 검증 예측을 합쳐 367개를 정확히 한 번씩 포함하는지 검사한다.
Macro-F1 평균·seed 표준편차, 라벨별 precision/recall/F1, 혼동행렬·예측하지 않은 라벨,
실제 epoch·update·클립 및 출처별 노출량과 훈련 적합도를 보존한다.

balanced_head−natural_head, balanced_finetune−balanced_head를 동일 출처 그룹의 2,000회
짝지은 bootstrap(seed 42)으로 비교한다. 이는 고정 seed 및 검증 checkpoint 선택에
조건부인 탐색적 불확실성이다. 새로운 독립 테스트 성능이나 임상적 원인 판별 성능이 아니다.
모든 라벨을 출력했다는 사실만으로 개선이나 서비스 적합성을 선언하지 않는다.

`test_used=false`, `enes_used=false`, `calibration_status=NOT_VALIDATED`,
`release_ready=false`, `service_model_selected=false`를 유지한다.

## 실행과 안전한 재개

실행 전 관련 단위·통합 시험과 정적 검사를 통과시킨다. 런처는 전체 Python 코드와 두 설정을
해시별 외부 복사본으로 고정한다. 실행 중인 복사본과 환경은 바꾸지 않는다.

```sh
cd "$REPOSITORY_ROOT/ml/m2d_supervised"
"$RESEARCH_ROOT/.venv/bin/python" -m m2d_supervised.balanced_runtime launch \
  --base-config config/experiment_v1.json --config config/balanced_v2.json \
  --research-root "$RESEARCH_ROOT" \
  --output-root "$RESEARCH_ROOT/runs/supervised_v2_balanced" \
  --m2d-source-root "$RESEARCH_ROOT/m2d" --repository-root "$REPOSITORY_ROOT"
```

`state.json`, `training.log`, 각 방법의 `arms/*/development/*/progress.json`으로 상태를
확인한다. 전원 분리, 최소 8 GiB와 원자 저장용 여유분 부족, SIGINT/SIGTERM 또는 출력
루트의 `STOP_TRAINING` 파일은 업데이트 경계에서 저장하고 중단한다. 동시 MPS 학습은
기존 전역 잠금과 새 실행 잠금으로 금지한다. 파일을 임의 삭제하지 않는다.

실패 원인을 해결하고 설정·코드·환경·원본 해시가 같음을 확인한 후 동일 복사본의 런처로
재개한다. 완료한 실행은 결과 해시를 검증하고 건너뛴다. 고정 설정을 바꾸어야 하면 기존
결과와 혼합하지 말고 새 버전의 설계를 먼저 명시한다. sanity의 실패 기록을 지우고 반복해
통과시키지 않는다. 완료된 실행의 중복 `last.pt`만 best·결과의 해시 검증 후 정리하며,
best와 그 외 결과는 보존한다.

## 근거와 인계

- [Kang et al., ICLR 2020](https://arxiv.org/abs/1910.09217): 표현과 분류기 학습의 분리 및
  분류기 재균형을 비교할 근거. 이미지 연구이며 아기 울음에서의 효과는 이 실험으로 확인한다.
- B-02·B-06 및 SEC54·SEC55의 모델·출처·평가 경계만 다룬다. API·권한·실기기·운영 시험은
  이 연구 시험으로 대체하지 않는다. 제품 계약·서비스 모델 레지스트리는 변경하지 않는다.
- 선행 학습기 PR #15는 병합됨. 후속 최종 평가·내보내기 PR #18은 이 작업 시작 시 열려
  있으며 별도 결과다. v2는 해당 PR의 신규 비교기·평가기·내보내기를 사용하지 않는다.

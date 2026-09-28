# M2D 지도학습 준비 사전검증 결과

검사 시각은 2026-09-20 04:46 KST이며, 실행 코드 기준 커밋은
`1f410fa71a30416dbe5e08b38b59b754293334fb`이다.

## 판정

- `status=passed`
- `ready_for_training=true`
- `trainer_implemented=false`
- `optimizer_step_executed=false`
- `training_or_evaluation_executed=false`
- 고정 개발 실행 계획 27개: `B-s42-f0`부터 `A-s44-f2`까지

이 판정은 데이터와 런타임이 이후 학습기 구현을 시작할 준비가 됐다는 뜻이다. 모델 성능,
보정, 제품 출시 준비를 검증한 결과가 아니다.

## 확인된 입력

- Donate-a-Cry: 전체 457, 개발 367, 테스트 90, 전체 221개 그룹
- EnesBabyCries1: 전체 39,201, 개발 31,578/19명, 테스트 7,623/5명
- 모든 개발 표본은 세 폴드에서 검증 표본으로 정확히 한 번씩 사용되며 그룹 누수는 0건
- Donate 특징 파일 457개, 101,377,856바이트 전체 확인
- Enes 특징 파일 39,201개, 1,165,433,088바이트 전체 확인
- 각 특징 파일의 고정 해시, `float32`, `[1, 80, frames]` 형상과 유한값 확인
- BABYCRY 준비 DB의 정상 레코드 107,312개 확인
- Donate/BABYCRY, Enes/BABYCRY, Donate/Enes 사이 스펙트로그램·PCM·원본 식별자
  정확 해시 중복은 모두 0건
- 원본 M2D와 BABYCRY SSL 최적 인코더의 파일 해시·151개 인코더 키·형상 확인
- BABYCRY SSL 완료 보고서와 선택 epoch 1 확인
- 전처리와 모델 정규화 `[-7.1, 4.2]` 일치 확인
- MPS 사용 가능, AC 전원 연결, 공통 학습 락 세 종류 사용 가능, 최소 여유 공간 게이트 통과

원본 M2D 2022 체크포인트에는 `norm_stats`가 없으므로 M2D의 명시적 하위호환 기본값을
사용했다. 이는 무작위 가중치 대체가 아니다. BABYCRY 체크포인트에는 같은 값이 포함돼 있다.

## 재현 식별자

- 설정 파일 SHA-256: `b05c45828a39d00c925d6fc384b49a6635372b151902ff149a0a628523bcadd0`
- 준비 코드 SHA-256: `8b666519ccb719196618b5d7d53b4d345ee49b59458285d2dc9c60eeab4b6de9`
- 전체 사전검증 보고서 SHA-256:
  `de589ada78f616b022a13efaa1a902ecddf01e0b597cbb5f7f1fcb9deda69e69`
- 로컬 전체 보고서: `$RESEARCH_ROOT/runs/supervised_v1/preflight/preflight.json`

## 확인하지 않은 범위

- 변환·크롭에 강인한 유사 중복 탐지
- 서로 다른 데이터셋 사이 영아 신원 분리
- 실제 지도학습, 재개, 27회 비교, 최종 테스트 평가
- 확률 보정, 제품 임곗값, 실제 서비스 모델 실행 및 기기 검증

따라서 calibration은 `NOT_VALIDATED`, `release_ready=false`를 유지한다.

# M2D 지도학습 준비

이 패키지는 아기 울음 M2D 지도학습의 **학습 직전 상태**만 고정하고 검증한다. 학습 루프,
옵티마이저, 체크포인트 재개, 모델 선택, 최종 평가와 내보내기는 아직 구현하지 않았다.

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

## 전체 사전검증

프로젝트 밖의 데이터와 가중치 위치는 실행 시 주입한다. 절대경로, 원본 음원, 모델 가중치는
저장소에 기록하지 않는다.

```sh
python -m m2d_supervised preflight \
  --research-root "$RESEARCH_ROOT" \
  --output-root "$RESEARCH_ROOT/runs/supervised_v1" \
  --m2d-source-root "$RESEARCH_ROOT/m2d" \
  --repository-root "$REPOSITORY_ROOT"
```

성공 보고서는 `$RESEARCH_ROOT/runs/supervised_v1/preflight/preflight.json`에 원자적으로
기록된다. 전체 특징 검사를 생략하는 `--bounded-check`는 개발용이며
`ready_for_training=false`를 유지한다.

## 검증

```sh
python -m unittest discover -s tests -v
python -m compileall m2d_supervised tests
```

다음 구현자가 따라야 할 고정 계약과 미완료 범위는 [docs/HANDOFF.md](docs/HANDOFF.md)에 있다.

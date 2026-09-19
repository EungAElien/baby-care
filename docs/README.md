# 문서 안내

현재 제품·API 기준, 구현 계획, 연구 기록과 이전 버전을 아래에서 구분해 찾을 수 있다. 기존 프로젝트에서 가져온 문서는 [이관 기록](migrations/2026-09-19-documents.md)에 원본 위치·해시·변경 범위를 남겼다.

## 작업 기준과 현재 계약

| 목적 | 문서 | 적용 범위 |
|---|---|---|
| 작업 방식 | [개발 원칙](../DEVELOPMENT_PRINCIPLES.md), [코딩 및 협업 컨벤션](project-rule/coding-conventions.md), [저장소 지침](../AGENTS.md) | 작업 시작·변경·검증·커밋·PR |
| 제품과 기능 | [PRD v2](baby-care-prd-v2.docx), [기능명세 v2](baby-care-functional-spec-v2.docx) | 제품 범위·기능·인수 조건 |
| API 접점 | [개발계약](../contracts/개발계약.md), [OpenAPI](../contracts/openapi계약.json), [목 응답](../contracts/목%20응답과%20시험%20사용자%20배치.json) | 현재 요청·응답·상태·권한·오류 |
| 계약 구조 검증 기록 | [validation.json](../contracts/validation.json) | 저장된 구조 검증 결과. 실제 API·권한·모델 검증과 구분 |
| 기본 업무 분담 v2 | [A 작업표](baby-care-implementation-tasks-A-v2.md), [B 작업표](baby-care-implementation-tasks-B-v2.md) | 기본 작업 번호와 인수 조건 |

## 구현·제출 계획과 제품 기획

| 문서 | 읽을 때 확인할 점 |
|---|---|
| [B 담당 구현 계획](planning/b-implementation-plan-2026-09-19/README.md) | B-01~B-13 실행 순서와 종료 조건. 진행 현황은 2026-09-19 19:36 KST 관측 기록 |
| [9월 20일 링크 제출 계획](planning/b-implementation-plan-2026-09-19/SUBMISSION-PLAN.md) | 당시 제출 우선순위와 내부 목표. 공식 세부 마감·현재 배포 상태를 새로 확인한 문서가 아님 |
| [진행 현황 원본 기록](planning/b-implementation-plan-2026-09-19/progress-snapshot.json), [당시 계획 문서 검사](planning/b-implementation-plan-2026-09-19/plan-validation.json) | 시각·범위·미검증 항목을 유지한 과거 기록. 원본 JSON의 절대 경로는 당시 환경 기록 |
| [아기 상태·완료 조치·기록 그래프 범주 v2](product/baby-state-care-action-categories-v2.md) | 사용자 선택을 반영한 기획. 계약에 반영할 차이는 8절에 명시. API enum이나 구현을 이미 바꾼 것으로 취급하지 않음 |

## 보안 설계와 시험 항목

- [보안 설계서](security/보안%20설계서.md)
- [62개 보안 시험표](security/62개%20보안%20시험표.md)
- [A/B 보안 작업 연결표](security/AB%20보안%20작업%20연결표.md)

기존 계약 조건, 추가 제안, 미실행 시험을 구분한다. 이관 과정에서는 문서 간 연결만 정리했으며 보안 기능이나 시험을 실행하지 않았다.

## 연구와 이전 버전

- [M2D 학습 문서와 관측 기록](research/m2d/README.md): 로컬 설계, 단계별 실행 범위, 합성 입력 점검, CPU 부분 측정, 별도 연구 프로젝트 참조.
- [울음 특징과 의미의 연구 조사](research/infant-cry-research-2026-09-15/report.ko.md)와 [검색 기록](research/infant-cry-research-2026-09-15/search-log.json): 2026-09-15 조사본. 외부 근거를 이번 이관에서 재조사하지 않았다.
- [이전 제품 문서](archive/2026-09-product/README.md): PRD·기능명세 v1의 Markdown/DOCX, 초기 MVP 기획, 상태·조치 범주 v1.

옛 문서에 있는 명령은 해당 문서의 작업 환경과 요청 범위를 확인한 뒤 사용한다. 문서를 가져왔다는 사실은 학습 완료·성능 검증·배포 또는 과거 진행 상태의 현재 유효성을 증명하지 않는다.

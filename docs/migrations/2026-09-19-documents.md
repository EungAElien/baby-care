# 기존 프로젝트 문서 이관 기록

대상: `EungAElien/baby-care` · 이관일: 2026-09-19

기존 ChatGPT 프로젝트 로컬 미러의 정식 문서·연구 문서·작은 관측 기록을 전수 대조했다. 검토한 문서 후보 32개 중 22개를 복사하고, 8개는 현재 저장소의 기준본으로 연결했으며, 미러 전용 지침 2개는 원래 위치에 유지했다. 원본 파일은 수정·이동·삭제하지 않았다.

현재 자료는 [문서 안내](../README.md)에서 찾는다. [기계 판독용 목록](2026-09-19-documents.json)에 원본 상대 경로, 원본·대상 SHA-256, 바이트 일치 여부, 문서별 조정을 기록했다. 목록의 해시는 이관 시점의 값이다.

## 처리 원칙

- PRD·기능명세 v2, 현재 OpenAPI·목 응답과 기존 A/B 작업표를 보존한다. 이전 PRD·명세·범주는 archive에서 구분한다.
- Markdown의 본문과 작성 시점은 유지한다. 보관·관측 범위 안내, 문서 링크, 표시 의미를 유지하는 줄바꿈 표기만 정리했다. JSON 8개와 DOCX 2개는 원본 그대로 복사했다.
- 보안 문서 3개는 이미 동일한 내용이 있어 새 사본을 만들지 않고 실제 파일명에 맞게 상호 링크만 고쳤다. 계약 설명서는 원본의 채택 안내와 실제 OpenAPI 파일명을 반영했다. 요청·응답·권한·상태·enum은 변경하지 않았다.
- 기존 A 작업표에 남아 있던 옛 계약·보안 폴더 링크 9곳을 현재 위치로 정리했다. 작업 내용·담당·인수 조건은 변경하지 않았다.
- 학습 단계 문서와 JSON의 완료·진행 중·중단 표시는 각 기록의 관측 시점에만 해당한다. 이번 이관은 모델 실행이나 현재 상태 재검증이 아니다.
- 범주 v2에서 사용자 선택으로 정한 표시 기획과 아직 계약에 반영하지 않은 접점을 그대로 구분한다. 복사만으로 API 변경을 확정하지 않는다.

## 복사한 문서

| 기존 위치 | 현재 위치 | 처리 |
|---|---|---|
| `deliverables/b-implementation-plan-2026-09-19/README.md` | [docs/planning/b-implementation-plan-2026-09-19/README.md](../planning/b-implementation-plan-2026-09-19/README.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `deliverables/b-implementation-plan-2026-09-19/SUBMISSION-PLAN.md` | [docs/planning/b-implementation-plan-2026-09-19/SUBMISSION-PLAN.md](../planning/b-implementation-plan-2026-09-19/SUBMISSION-PLAN.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `deliverables/b-implementation-plan-2026-09-19/progress-snapshot.json` | [docs/planning/b-implementation-plan-2026-09-19/progress-snapshot.json](../planning/b-implementation-plan-2026-09-19/progress-snapshot.json) | 원본 바이트 보존 |
| `deliverables/b-implementation-plan-2026-09-19/plan-validation.json` | [docs/planning/b-implementation-plan-2026-09-19/plan-validation.json](../planning/b-implementation-plan-2026-09-19/plan-validation.json) | 원본 바이트 보존 |
| `docs/baby-state-care-action-categories-v2.md` | [docs/product/baby-state-care-action-categories-v2.md](../product/baby-state-care-action-categories-v2.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `docs/baby-state-care-action-categories-v1.md` | [docs/archive/2026-09-product/baby-state-care-action-categories-v1.md](../archive/2026-09-product/baby-state-care-action-categories-v1.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `docs/baby-care-prd-v1.md` | [docs/archive/2026-09-product/baby-care-prd-v1.md](../archive/2026-09-product/baby-care-prd-v1.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `docs/baby-care-functional-spec-v1.md` | [docs/archive/2026-09-product/baby-care-functional-spec-v1.md](../archive/2026-09-product/baby-care-functional-spec-v1.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `docs/baby-care-mvp-spec-2026-09-15.md` | [docs/archive/2026-09-product/baby-care-mvp-spec-2026-09-15.md](../archive/2026-09-product/baby-care-mvp-spec-2026-09-15.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `deliverables/baby-care-prd-v1.docx` | [docs/archive/2026-09-product/baby-care-prd-v1.docx](../archive/2026-09-product/baby-care-prd-v1.docx) | 원본 바이트 보존 |
| `deliverables/baby-care-functional-spec-v1.docx` | [docs/archive/2026-09-product/baby-care-functional-spec-v1.docx](../archive/2026-09-product/baby-care-functional-spec-v1.docx) | 원본 바이트 보존 |
| `docs/m2d-local-training-plan-2026-09-15.md` | [docs/research/m2d/m2d-local-training-plan-2026-09-15.md](../research/m2d/m2d-local-training-plan-2026-09-15.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `work/m2d-stage3/STAGE3.md` | [docs/research/m2d/STAGE3.md](../research/m2d/STAGE3.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `work/m2d-stage4/STAGE4.md` | [docs/research/m2d/STAGE4.md](../research/m2d/STAGE4.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `work/m2d-stage4/STAGE4_FULL.md` | [docs/research/m2d/STAGE4_FULL.md](../research/m2d/STAGE4_FULL.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `work/m2d-stage2/random_model_check.json` | [docs/research/m2d/snapshots/mps-stage2/random_model_check.json](../research/m2d/snapshots/mps-stage2/random_model_check.json) | 원본 바이트 보존 |
| `work/m2d-stage2/pretrained_model_check.json` | [docs/research/m2d/snapshots/mps-stage2/pretrained_model_check.json](../research/m2d/snapshots/mps-stage2/pretrained_model_check.json) | 원본 바이트 보존 |
| `work/cluster-cpu-tuning/extraction_before.json` | [docs/research/m2d/snapshots/cluster-cpu-tuning/extraction_before.json](../research/m2d/snapshots/cluster-cpu-tuning/extraction_before.json) | 원본 바이트 보존 |
| `work/cluster-cpu-tuning/pipeline_before.json` | [docs/research/m2d/snapshots/cluster-cpu-tuning/pipeline_before.json](../research/m2d/snapshots/cluster-cpu-tuning/pipeline_before.json) | 원본 바이트 보존 |
| `work/cluster-cpu-tuning/threads-1.json` | [docs/research/m2d/snapshots/cluster-cpu-tuning/threads-1.json](../research/m2d/snapshots/cluster-cpu-tuning/threads-1.json) | 원본 바이트 보존 |
| `exa-results/infant-cry-research-2026-09-15/report.ko.md` | [docs/research/infant-cry-research-2026-09-15/report.ko.md](../research/infant-cry-research-2026-09-15/report.ko.md) | 본문 보존·안내/링크/줄바꿈 정리 |
| `exa-results/infant-cry-research-2026-09-15/search-log.json` | [docs/research/infant-cry-research-2026-09-15/search-log.json](../research/infant-cry-research-2026-09-15/search-log.json) | 원본 바이트 보존 |

## 이미 있던 문서와 지침

| 기존 위치 | 현재 기준본 | 처리 |
|---|---|---|
| `deliverables/security-design-v1/SECURITY-DESIGN.md` | [docs/security/보안 설계서.md](../security/보안%20설계서.md) | 기존 파일 유지·링크 정리 |
| `deliverables/security-design-v1/SECURITY-ACCEPTANCE.md` | [docs/security/62개 보안 시험표.md](../security/62개%20보안%20시험표.md) | 기존 파일 유지·링크 정리 |
| `deliverables/security-design-v1/IMPLEMENTATION-HANDOFF.md` | [docs/security/AB 보안 작업 연결표.md](../security/AB%20보안%20작업%20연결표.md) | 기존 파일 유지·링크 정리 |
| `deliverables/team-contract-v1/openapi.json` | [contracts/openapi계약.json](../../contracts/openapi계약.json) | 동일 파일 유지 |
| `deliverables/team-contract-v1/fixtures.json` | [contracts/목 응답과 시험 사용자 배치.json](../../contracts/목%20응답과%20시험%20사용자%20배치.json) | 동일 파일 유지 |
| `deliverables/team-contract-v1/validation.json` | [contracts/validation.json](../../contracts/validation.json) | 동일 파일 유지 |
| `DEVELOPMENT_PRINCIPLES.md` | [DEVELOPMENT_PRINCIPLES.md](../../DEVELOPMENT_PRINCIPLES.md) | 현재 기준본 유지·원본과 의미 대조 |
| `deliverables/team-contract-v1/README.md` | [contracts/개발계약.md](../../contracts/개발계약.md) | 현재 기준본 유지·원본과 의미 대조 |
| `AGENTS.md` | [AGENTS.md](../../AGENTS.md) | 미러 전용 지침은 원래 위치에 유지 |
| `AGENTS.override.md` | [AGENTS.md](../../AGENTS.md) | 미러 전용 지침은 원래 위치에 유지 |

## 복사하지 않은 자료와 참조의 한계

| 자료 | 판단 |
|---|---|
| `tmp/` | DOCX/PDF 렌더링, 텍스트 추출, 계약 검토용 중간 사본·스크립트. 정식 문서와 현재 계약을 우선 |
| `sources/` | 확인 시 비어 있음. 동기화 자료는 계속 읽기 전용 |
| `work/`의 Python·배열·임시 로그·캐시 | 문서 이관 범위 밖의 실행 자료. Markdown과 작은 JSON 관측 기록만 복사 |
| `.DS_Store`, 잠금·로컬 상태 파일 | 제품 문서가 아님 |
| 별도 학습 프로젝트의 코드·데이터·가중치·보고서 | 기존 폴더 밖의 자료. [연구 참조 목록](../research/m2d/README.md)에 필요한 파일의 역할과 상대 경로를 기록 |

B 계획의 과거 외부 `내가 맡을 일.md` 링크는 이관 시 원본을 찾을 수 없었다. 현재 저장소의 B v2 작업표로 안내를 바꾸고 원본 버전 동일성을 확인하지 못했다는 설명을 붙였다. 외부 기능명세 v2는 현재 저장소 파일과 바이트 일치를 확인한 뒤 저장소 링크로 바꿨다. 별도 연구 프로젝트 자료의 링크는 해당 파일의 복사본을 가장하지 않고 참조 목록으로 안내한다.

원본 JSON에 남은 개인 컴퓨터 절대 경로와 `thread://`·`chatgpt-conversation://` 식별자는 당시 관측·대화의 출처다. 현재 실행 위치나 팀 공용 링크로 사용하지 않는다. 논문·정책·공식 접수 조건의 최신성은 이번 이관에서 다시 조사하지 않았다.

## 검증

- 원본 후보 32개의 SHA-256을 다시 확인해 모두 보존됐음을 확인했다. 복사·기존 파일 연결·미러 전용 유지로 모든 후보의 처리를 기록했다.
- 저장소 Markdown 29개, 내부 링크 169개와 사용한 문서 앵커를 검사했다. 누락된 대상은 없다. 과거 대화 식별자는 별도로 구분했다.
- 복사한 JSON 8개를 파싱하고 DOCX 2개의 ZIP 구성과 CRC를 확인했다. 이 10개 파일은 원본과 바이트 단위로 같다. DOCX는 내용을 편집하지 않아 재렌더링하지 않았다.
- 이관 Markdown의 제목·절·표를 원본과 대조하고, 기존 A 작업표·보안 문서는 링크 외 내용이 유지되는지 확인했다.
- OpenAPI·목 데이터·기존 계약 검증 JSON은 기준 커밋과 바이트 단위로 같다. 계약 설명의 변경은 채택 안내·실제 파일명에 한정된다. 앱·Supabase·CI 파일은 변경하지 않았다.
- 일반적인 비밀 키 서명 검사에서 발견 항목이 없으며 Git 공백 검사를 통과했다.

실제 API·RLS·Storage·기기·모델 실행, 외부 근거의 최신성 및 과거 학습 상태의 현재 유효성은 이번 문서 이관에서 검증하지 않았다.

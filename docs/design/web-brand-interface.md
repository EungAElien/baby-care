# 웹 브랜드 인터페이스 재구성 — 2026-09-20

> 최종 시각 기준 변경: 사용자가 추가한 7개 모바일 화면과 반응형 웹앱 요청을 반영했다. 아래 초기 밝은 화면 기록보다 이 변경이 우선한다. 페이지는 #1D1841, 제목은 #FFFFFF, 주요 행동은 #FF9398/검정, 보조 문구는 #FDA1A2다. 흰색 입력 표면은 유지한다. 기존 3개 기준색과 확장색 값은 변경하지 않았다. 모바일 하단 내비게이션, 800px 이상 측면 내비게이션, 1100px 이상 2열을 적용했다. 홈의 보호자·아기 그림은 새 SVG이며 첨부 이미지 원본을 추출한 에셋이 아니다. 예시 화면에만 DEMO를 표시하며 이미지 수치를 실제 결과로 이식하지 않았다.

## 적용 범위와 근거

기능 시험용 화면의 배치·내비게이션·타이포그래피·입력 표면을 다시 구성했다. Next.js App Router, 실제 Auth, FastAPI 클라이언트, 비공개 범위 정리와 계약 타입은 유지했다. A-06·A-07은 작업 중 develop에 반영된 `ed87d29`를 병합해 같은 분석 ID 복구와 개인 초안 확인 트랜잭션을 보존했다. 별도 백엔드 기능이나 계약을 추가하지 않았다.

실제로 읽은 로컬 스킬: `apple-hig/SKILL.md`, `apple-ios-design-resources/SKILL.md`, `seed-design/SKILL.md` 및 각각의 관련 참조 문서. HIG는 위계·탐색·44 CSS px 이상 조작 대상·복구·접근성에, iOS resources는 컴포넌트 사용 맥락에 적용했다. 네이티브 전용 Apple 에셋·폰트·OS 외형은 복제하지 않았다.

- [Apple HIG 접근성](https://developer.apple.com/design/human-interface-guidelines/accessibility)
- [Apple HIG Tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars)
- [Apple 디자인 리소스](https://developer.apple.com/design/resources/)
- [SEED 공식 문서 인덱스](https://seed-design.io/llms.txt)
- [SEED ActionButton](https://seed-design.io/react/components/action-button)
- [SEED TextField](https://seed-design.io/react/components/text-field-input)
- [SEED BottomSheet](https://seed-design.io/react/components/bottom-sheet)
- [SEED AlertDialog](https://seed-design.io/react/components/alert-dialog)

seeddocsMCP의 문서 목록, Rootage 2.9.0 색상 및 action-button/text-input/bottom-sheet/dialog/typography 명세를 조회했다. 공식 CLI로 생성한 로컬 스니펫을 사용한다. 설치 버전은 React 2.5.0, CSS 2.8.3이며 실제 패키지 타입과 타입 검사를 대조했다. ActionButton `neutralSolid`/`neutralWeak`/`ghost`, large 52px를 사용하고 loading과 disabled·동기 요청 잠금을 별도로 처리한다. TextField, List, ListHeader, BottomSheet, Dialog(AlertDialog 스니펫), Callout, Badge, ResultSection을 사용한다. Bottom Navigation은 React 구현을 가정하지 않고 `nav`+Next Link, 아이콘+이름, `aria-current`로 구현했다.

## 색상 출처와 역할

[Inside the Head](https://insidethehead.co), [Awwwards 소개](https://www.awwwards.com/sites/inside-the-head-publication), [제작자 공개 이미지](https://sarajuliasvensson.com/project/insidethehead)를 시각 참고로 삼았다. Awwwards 페이지는 이번 확인에서 응답 시간이 초과됐고, 제작자 페이지와 실제 사이트는 접근했다.

| 원시 팔레트 | 출처 | 서비스 역할 |
| --- | --- | --- |
| #000000 | 사용자 브랜드 기준, 공개 이미지에도 확인된 값 | 밝은 입력 표면 글자·핑크 주요 행동 글자 |
| #FF9398 | 사용자 브랜드 기준 | 주요 행동 배경·선택 강조·일러스트 색면 |
| #D14836 | 사용자 브랜드 기준 | 유기적 장식·브랜드 포인트 |
| #FFFFFF | 사용자가 제공한 공개 이미지 추출값 | 입력 표면·어두운 화면의 제목과 본문 |
| #1D1841 | 사용자가 제공한 공개 이미지 추출값 | 전체 페이지·내비게이션 배경 |
| #8F0D3D | 사용자가 제공한 공개 이미지 추출값 | 제한된 보조색·오류 눌림 토큰 |
| #F03B34 | 사용자가 제공한 공개 이미지 추출값 | 장식용 확장색, 현재 주요 화면 미사용 |
| #FDA1A2 | 사용자가 제공한 공개 이미지 추출값 | 브랜드 색면의 눌림 상태 후보 |
| #1057BF | 사용자가 제공한 공개 이미지 추출값 | 키보드 포커스·보조 정보 |
| #51A5FF | 사용자가 제공한 공개 이미지 추출값 | 보조 그래픽용 예약, 현재 주요 화면 미사용 |
| #F73132 | 사용자가 제공한 공개 이미지 추출값 | 소량 장식용 예약, 현재 주요 화면 미사용 |

확장색은 이번 작업에서 원본 CSS를 추출한 확정값이 아니다. 실제 사이트의 첫 화면에서 추가로 확인한 computed style은 BODY 배경 `rgb(29,24,66)`(**#1D1842**), BODY 검정 글자, 제목·About/Skip intro/Start reading의 흰 글자다. 이 별도 관측값으로 사용자 제공 #1D1841을 교체하지 않았다. SVG·호버·눌림·실제로 그려진 테두리 색은 추가 추출했다고 주장하지 않는다.

`globals.css`의 `--palette-*` → `--surface-* / --text-* / --action-* / --stroke-* / --feedback-*` → SEED 역할 토큰과 컴포넌트 순서다. `foreground`, `background`, `stroke`, `layer-basement/default/floating`을 함께 연결했다. 서비스 중립 회색과 오류 #A11B31은 독립적으로 설계한 값이다. 브랜드 빨강을 오류 토큰으로 쓰지 않는다. 아이보리 #FEFAF1·크림·베이지는 제품 표면에서 제외했다. 밝은 읽기·입력·모달 표면은 순백색이다. 기존 다크 모드가 없으므로 다크 모드를 새 제품 기능으로 추가하지 않았다.

| 의미 토큰 | 연결 |
| --- | --- |
| surface-page / surface-reading | #FFFFFF |
| surface-brand | #FF9398 |
| text-primary / action-primary | #000000 |
| text-inverse | #FFFFFF |
| surface-inverse | #1D1841 |
| brand-accent | #D14836 |
| focus | #1057BF |
| feedback-error | #A11B31 |

검정/핑크, 검정/브릭, 흰색/검정·네이비·코발트·와인, 검정/스카이와 실제 primary/pressed/secondary 조합을 수치 시험한다. 브릭·코럴 위 작은 흰 글자, 핑크/빨강의 작은 텍스트 조합은 쓰지 않는다. 장식은 원시 값 반복 없이 토큰을 참조하는 단순 SVG이며 모델 상태를 추정해 표현하지 않는다.

## 화면과 상태

- 홈: 선택 아기·보호자 역할, 감지 시작, 확인 관찰의 시각·출처, 최근 기록, 보조 빠른 기록. 과거 관찰을 현재 상태로 단정하지 않는다.
- 감지/음원: 자동 감지의 검증 전 비활성을 명시하고 실제 녹음·파일 입력을 제공한다. 권한 요청 중/녹음 중을 구분한다. 숨김·offline·pagehide·mute·트랙 종료에서 폐기·중단하며 명시적 재시작만 허용한다. 실제 업로드 진행률만 표시한다.
- 분석: 최신 A-06의 실제 API 흐름을 유지한다. COMPLETE/ABSTAIN/FAILED는 제목·아이콘·설명·다음 행동으로 구분하며 점수를 원인 확률로 표시하지 않는다. REAL/STUB, USER/DEMO, inference_executed를 각각 표시한다.
- 기록: 선택지 기록과 개인 초안의 입력 방식을 구분한다. 최신 A-07의 원문·근거·행동·관찰·확인 저장·버전 충돌·같은 요청 복구 흐름을 연결한다. 실제 외부 처리 승인 게이트를 임의로 해제하지 않는다.
- 타임라인: 종류·시각·작성자·출처, 개인 초안 복구 진입.
- 요약: FastAPI summary/patterns, 아기 시간대 날짜, 오류/없음/null/실제 0 구분. 공동 변경으로 다시 조회한다.
- 설정: 실제 OWNER 아기 프로필 편집(바텀시트), 역할, 공동양육, 동의, 계정, 아기 삭제 범위. 프로필은 같은 키 재시도·버전 비교·범위 전환 후 늦은 응답 차단을 포함한다.

## A 작업표 추가 반영과 남은 범위

기존 체크박스는 실기기·팀 인수까지 포함하므로 코드 작성만으로 완료 처리하지 않는다.

| 항목 | 이번 반영 | 남은 범위 |
| --- | --- | --- |
| A-01 | 공통 UI 전면 재구성, 접근성 이름·상태, 데모 격리 | 실제 보조기술/기기 인수 |
| A-02·A-05 | 녹음 권한 경합·중단·정리, 파일 선택 경합, 중복 업로드 차단 | Safari/Android 실기기 음원·Storage 인수 |
| A-03 | 실제 아기 프로필 수정·충돌 복구 | 실제 두 계정 OTP·권한 회수 인수 |
| A-04·A-08 | 입력 UI·중복 제출 방지, 홈 관찰·요약 재조회 | 실제 두 보호자의 전파 지연 |
| A-06·A-07 | 새로 병합된 실제 분석/개인 초안 로직을 재설계 UI에 통합 | 해당 인계 문서의 미완료 분석 맥락/수정·삭제/실자료 인수 |
| A-09 | 오늘 실제 요약·패턴 조회, 없음/부족/0, 시간대 | 개인 준비 알림 설정·미루기·해제·기록일 확인, 이전 사례 화면 |
| A-10 | 검증 전 자동 감지 지원 표시 금지, 대체 입력, 실제 중단 이유 | 검증된 detector/ONNX, 서버 감지 세션·heartbeat·자동 분석, 30분 기기 시험 |
| A-11 | CSP nonce·no-store·no-referrer·Permissions-Policy, 회귀·색상·반응형 검증 | 실제 두 계정 통합 E2E·CDN·운영 AudioWorklet/WASM·배포 |
| A-12 | 독립된 정적 브랜드 장식만 추가 | 기존 캐릭터 PR #32의 자산·모션 작업은 별도 유지 |
| A-13 | 신규 API/제품 범위를 만들지 않음 | B-14 계약과 상담 흐름·인수 |

실제 환경값이나 로그인 세션을 다른 작업 폴더에서 가져오지 않았다. 정직한 비활성/미연결 표시는 해당 기능 구현 완료의 증거가 아니다. 이번 UI PR은 A 전체 완료나 P0 전체 인수 완료를 주장하지 않는다.

# 작업 중단 기록

2026-09-21 사용자의 “여기까지 하고 커밋하고 작업 중단” 요청으로 제작을 중단했다. 이후 제작·보정·애니메이션 확장은 진행하지 않았다.

## 보존한 결과

- 직전 구현 커밋 `89dfe08`: 실제 Creator 출력 8종(움직임 7종, 중립 정지 1종), 웹 재생·상태 제어, 대표 PNG와 검토 화면. 당시 199개 테스트, ESLint, Next 빌드 통과. 세부 검수 범위는 [README](README.md)를 따른다.
- 이번 보존 범위: 추가 관찰 7종의 Figma 편집 초안, `sources/observation-*.svg`, `review/observation-*-reference.png`, `*-source.png`, `*-comparison.png`. 이 7종은 Creator로 가져오거나 애니메이션을 내보내지 않았다.
- [Figma 원본](https://www.figma.com/design/etV160fUzbwxOEUwgK4g00), 페이지 `5:2`에 편집 결과가 있다. [Creator 프로젝트](https://creator.lottiefiles.com/?fileId=3ef2a7bd-3603-4157-ba93-6db315053602)는 기존 8종의 편집 프로젝트다.

## 초안 위치와 다음 확인 사항

| 관찰 | Figma 노드 | 저장한 SVG | 중단 시점 |
|---|---|---|---|
| 울음 | 26:2 | [crying](sources/observation-crying.svg) | 손 윤곽 보정 후 SVG 추출, 최종 검수 전 |
| 보챔 | 26:55 | [fussing](sources/observation-fussing.svg) | SVG 추출 뒤 Figma 오른쪽 귀 세부 보정 |
| 기분 좋아 보임 | 26:100 | [cheerful](sources/observation-cheerful.svg) | SVG 추출 뒤 Figma 오른쪽 팔뚝·손 보정 |
| 깨어 있음 | 27:2 | [awake](sources/observation-awake.svg) | SVG 추출 뒤 Figma 머리 윤곽 보정 |
| 졸려 보임 | 27:48 | [sleepy](sources/observation-sleepy.svg) | SVG 추출 뒤 Figma 머리 윤곽 보정 |
| 잠듦 | 27:97 | [asleep](sources/observation-asleep.svg) | SVG 추출 뒤 Figma 머리 윤곽 보정 |
| 알 수 없음 | 27:136 | [unknown](sources/observation-unknown.svg) | SVG 추출 뒤 Figma 머리·팔뚝·손 보정 |

울음을 제외한 6종은 **현재 Figma 편집 상태가 저장소 SVG·비교 이미지보다 앞서 있다.** 재개 시 Figma부터 확인하고 다시 내보내야 한다. 기존 SVG를 재가져와 최신 보정을 덮어쓰지 않는다. 비교 PNG는 저장소 SVG 시점의 초안이며 최신 Figma 검수 완료 증거가 아니다.

현재 Figma에서 확인한 미해결 사항은 졸림의 턱과 몸통 사이 틈, 깨어 있음의 왼팔 연결부 단차, 알 수 없음의 손목 연결부 단차, 잠듦의 팔뚝·손 접합부다. 이를 보정하고 시안과 같은 크기 및 윤곽 비교를 다시 확인한 뒤 애니메이션을 시작한다. 졸림의 `27:94` 손 세부 레이어 이름은 실제 오른손에 맞춰 정정할 필요가 있다.

## 완료로 취급하지 않을 범위

- `manifest.json`과 웹 자산 레지스트리는 직전 8종 출력 체크포인트다. 추가 7종 초안은 아직 등록하지 않았으므로 manifest의 `notStarted: 14`를 최신 전체 제작 진척 수치로 해석하지 않는다. 실제로는 출력 8종, 관찰 원본 초안 7종, 미제작 완료 조치 7종이다.
- 추가 7종의 리그·키프레임·Lottie·대표 프레임·웹/로그 크기 재생 검수는 미수행이다. 완료 조치 나머지 7종도 미제작이다.
- 실제 API 연동과 실제 기기 검증은 미수행이다. 기존 8종도 최종 외형 승인 상태가 아니다.
- 기존 Draft PR #32 본문과 임시 인계 ZIP은 최신 8종 및 관찰 초안 전체를 반영하지 않았다. 이번 중단 저장을 전체 요청 완료로 보고하지 않는다.

재개할 때는 원격 변경과 저장소 지침을 먼저 확인한다. 사용자가 재개를 요청하기 전에는 추가 제작을 진행하지 않는다.

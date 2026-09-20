# 캐릭터 웹 프로토타입 · 2026-09-20

README 7·11절의 제작 순서를 따라 PNG 시안을 부위별 SVG·React 장면과 재생 제어로 옮겼다. 관찰 8종, AI 추정 5종과 판단 어려움, 완료 조치 8종의 총 22개 포즈가 있다. **디자인 검토용 합성 흐름**이며 실제 저장·분석·권한 서비스에 연결하지 않았다. A-12 전체를 완료 표시하지 않는다.

## 실행과 파일

```sh
cd apps/web
npm ci
npm run dev
# http://localhost:3000/design/character-motion
npm run generate:characters
npm run check:characters
```

개발 서버에서 검토 화면을 연다. 프로덕션에서는 기본 비활성이며, 기존 명시적 목 화면 설정 `NEXT_PUBLIC_ENABLE_MOCK_NAV=true`가 있는 미리보기에서만 활성화한다. 페이지는 DEMO·STUB·합성 입력임을 표시한다. 서버/API/DB 호출이나 실제 저장은 하지 않는다.

| 파일 | 역할 |
|---|---|
| [baby-figure.tsx](../../../apps/web/src/components/character/baby-figure.tsx) | 머리·쉼표 머리카락·눈·눈썹·입·눈물·몸·팔·다리 경로, 누운 자세 |
| [caregiver-figure.tsx](../../../apps/web/src/components/character/caregiver-figure.tsx) | 보호자 얼굴·몸·지지 팔·동작 팔과 아기 접촉 구도 |
| [character-art.tsx](../../../apps/web/src/components/character/character-art.tsx) | 정지 상태에서 완전한 SVG 장면; ID 충돌 없는 구조 |
| [pose-catalog.ts](../../../apps/web/src/components/character/pose-catalog.ts) | 22개 포즈·문구·팔레트·버전·정지 파일 경로 |
| [character.css](../../../apps/web/src/components/character/character.css) | 깜박임·숨·어깨·손·작은 자세 동작과 반응형 검토 화면 |
| [scene-state.ts](../../../apps/web/src/components/character/scene-state.ts) | 합성 표시 이벤트, 범위·최신성·1회 조치 순서 제어 |
| [character-scene.tsx](../../../apps/web/src/components/character/character-scene.tsx) | Web Animations API 취소·완료·정지 대안·탭 숨김·동작 줄이기 |
| [검토 페이지](../../../apps/web/src/app/design/character-motion/character-lab.tsx) | 관찰→추정→확인 조치→최신 관찰, 예외 버튼, 64px 정지 로그 |
| [정지 자산 목록](../../../apps/web/public/characters/1.0.0-prototype/manifest.json) | SVG 22개의 버전·출처·대체 텍스트·허용 입력·SHA-256 |
| [내보내기](../../../apps/web/scripts/export-character-posters.mjs) | 실제 React 원본을 컴파일하여 정지 SVG 생성; `--check`로 내용 일치 검증 |

수정 대상은 React 원본이다. 정지 파일을 수작업으로 따로 수정하지 않는다. `generate:characters` 후 `check:characters`로 동일한 원본에서 파생됐는지 확인한다. 전용 재생 라이브러리나 새 패키지는 추가하지 않았다. Next 개발 서버가 생성한 `apps/web/AGENTS.md`·`CLAUDE.md`는 해당 버전의 로컬 문서 안내다.

## 제작·의미 경계

- 기존 PNG 4장과 `image-provenance.json`·`source-audit.json`은 보존했다. 웹 원본은 시안을 보며 직접 작성한 새 SVG 경로다. 외부 사이트의 일러스트·Lottie 도형을 복사하지 않았다.
- 정면·고개 기울임·웅크림·안긴 자세·매트 위 누운 자세를 만든다. AI 추정에는 말풍선·원인 아이콘을 넣지 않는다. 가능성·출처·시각은 HTML에 남긴다.
- 안기 2.2초, 토닥임 2.2초로 순서대로 1회 재생한다. 안기는 아기·지지 손·팔을 함께 작은 각도로 움직이고, 토닥임은 지지하는 팔을 유지하며 동작 팔만 움직인다. 깜박임은 눈 그룹에만 적용한다.
- 장면의 기본 경로가 대표 정지 포즈다. 로그에는 모션 제어기를 만들지 않는다. 동작 줄이기에서는 조치별 정지 포즈를 2.2초씩 보여주며 건너뛰기를 제공한다. 저장 처리를 이 시간만큼 지연시키지 않는다.
- **수유는 방식 미상의 공통 안김 포즈**, 기타는 중립 돌봄 포즈다. 젖병·모유·옷 갈아입히기를 미확인 기록에 단정하지 않는다. 환경 장면은 검토용 **조명 변형**이다. 이 변형을 실제 환경 변경의 기본 매핑으로 사용하면 안 된다.
- 기저귀·수면·환경·기타의 포즈는 확장 초안이다. 실제 접촉 동작·소품 변형·자세 전환·작은 로그에서의 의미 전달은 추가 검수가 필요하다. 토닥임/트림은 작은 동작선/확인 기호와 HTML 조치 이름을 함께 읽는다.

## 재생 경계

`scene-state.ts`의 값은 프런트엔드 검토용 모델이며 서버 enum이 아니다. `NO_CRY`는 이 모델의 표시 분기이고 실제 API에서는 유보 사유다. 완료 조치 `patting`·`sleeping` 등을 현재 API에 보내는 코드가 없다.

1. 매 화면 방문·아기/계정 전환에 고유한 `scope` 토큰을 만든다. 오래된 범위의 관찰·분석·저장·완료 콜백은 무시한다. 삭제·권한 회수는 토큰도 바꾸고 상태·대기열·미완료 요청을 정리한다.
2. 이 화면에서 새 확인 흐름의 `save-start`를 발행한 요청의 성공만 조치를 재생한다. 조회·새로고침·다른 보호자 동기화·과거 수정은 이를 발행하지 않는다. `action_id`에 대응하는 식별자를 기준으로 한 번만 소비하며 version/요청 키 변경으로 재생을 늘리지 않는다.
3. 실패·초안·추천·미확인 행동은 재생하지 않는다. 트림·재워줌은 실제 결과 확인도 요구한다. 확인된 `sequence` 순으로 재생한다.
4. 재생 완료/건너뛰기는 현재 상태의 가장 최신 관찰을 다시 선택한다. 진정을 자동 생성하지 않는다. 새 사건은 이전 조치와 미완료 저장의 재생 자격을 취소한다.
5. 현재 사건의 COMPLETE이며 지원 라벨인 결과만 원인 포즈를 표시한다. 최신 관찰보다 오래된 분석은 홈을 바꾸지 않는다. ABSTAIN·FAILED·NO_CRY는 서로 다른 안내를 유지한다.
6. 탭 숨김은 현재 재생과 남은 순서를 취소한다. 복귀 때 밀린 조치를 재연하지 않는다. 완료 Promise 거절을 처리하고, 이미 취소된 실행의 완료를 무시한다.
7. 관찰이 30분을 넘으면 정지·경과시간을 표시한다. 10초 주기로 새로 판단한다. 기록 없음과 명시적 UNKNOWN은 문구로 구별한다.

## 검증과 남은 일

자동 검사: 웹 타입 검사·ESLint, 웹 전체 121개 테스트(26개 파일), 프로덕션 빌드, 22개 SVG와 매니페스트 재생성 일치 검사. 새 16개 테스트는 중복 응답·순서·최신 관찰·실패/유보·결과 확인·범위 전환·삭제·탭 숨김·동작 줄이기·취소 Promise를 확인한다. 기본 프로덕션 서버에서 검토 URL의 HTTP 404와 조작 UI 미포함도 확인했다. 로컬 문서 링크 22개와 원본 PNG 4장의 크기·바이트 수·SHA-256도 통과했다. 실제 API 테스트로 해석하지 않는다.

로컬 인앱 Chromium에서 관찰·추정·조치 갤러리, 안기 확인 저장 후 관찰 복귀, 390px 레이아웃과 64px 정지 그림을 검토했다. 가로 넘침을 확인하고 모바일 헤더를 조정했다. 누운 아기가 세로로 눌려 보이는 비율과 조치 팔의 중복을 수정했다. 시각 검토는 정성 검토이며 사용자 이해도 시험이나 기기 성능 측정이 아니다.

작업 중 새로 도착한 `a591486`(B-07 사건 없는 정규화, 계약 1.2.0)을 fast-forward로 반영했다. 해당 변경은 실제 관찰 재조회 경로를 추가하지만, 사건 연결 ActionAttempt·Outcome과 이번 원인 포즈 표시 계약은 여전히 후속이다. 기존 디자인 문서의 1.1.1 기준 조사 기록은 당시 이력으로 보존한다.

후속 항목:

- `StateObservation`의 `state_codes`·`visual_state_code`·`visual_mapping_version`·출처·시각을 그대로 보존하는 실제 화면 어댑터. UNKNOWN/상충 관찰은 서버 NEUTRAL을 따른다.
- AI 포즈 표시와 완료 조치 8종의 계약·목·화면 매핑을 함께 확정한 뒤 실제 저장 이벤트에 연결. B-07의 CareEvent를 사건 행동 ID로 오인하지 않는다.
- 수유 방식·환경 변경 대상·기타 실제 내용별 변형과 나머지 조치 모션의 접촉 검수.
- 실제 iOS/Android·Safari/Firefox, 스크린리더, 마이크 동시 감지, 프레임 드롭·메모리·배터리 측정. 실제 API·두 계정 권한·모델·모바일 검증은 이번 작업에서 수행하지 않았다.

기존 A-12 체크박스는 직접 검수와 실제 확인 저장 연결까지 포함하므로 그대로 유지했다.

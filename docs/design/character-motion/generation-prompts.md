# 이미지 생성 기록

작성일: 2026-09-20
도구: 내장 image_gen. 세 시트는 검토용 PNG 초안이며 부위별 리그나 모션 파일이 아니다.

현재 AI 추정 시안에는 마지막의 2-2 수정 프롬프트를 적용했다. 아래 원시안의 말풍선 지시는 수정 전 이력으로만 보존한다.

2026-09-20의 직접 작성 React·인라인 SVG·CSS 프로토타입은 이전 이력이다. 사용자의 후속 요청으로 이 방식을 변경했다. 현재는 **PNG 외형 검토 → Figma 부위별 원본 편집 → Lottie Creator 타임라인 → 실제 JSON 내보내기 → lottie-web 재생**을 따른다. 원본 PNG와 아래 생성 프롬프트는 보존하며 새로운 그림체로 재생성하지 않는다. 실제 작업은 [v2 제작 기록](production-v2/README.md)을 따른다.

## 1. 관찰 상태 원시안

Use case: stylized-concept.
Create a polished original character design contact sheet for a Korean infant-care service. This is an illustration concept sheet for later vector animation, not a screenshot of an application. Landscape canvas, crisp high-resolution flat vector-like artwork.

Visual direction: contemporary editorial cut-paper shapes, large confident interlocking curved color planes, sculptural C-curves, subtly asymmetric silhouettes and expressive compact human faces. Clean matte solid fills, generous warm ivory negative space, no outlines except tiny facial features, no gradients, shadows, texture, 3D, photorealism or stock illustration scenery. Inspired by the design principles of bold flat editorial animation, without copying any existing characters, compositions or logos. Do NOT draw a Duolingo owl or giant Duolingo eyes.

Character invariant: every panel shows the EXACT SAME infant: warm apricot round pear-shaped head, one small dark ink comma-shaped hair curl bending right, tiny rounded ears, two simple dark bean-shaped eyes with small mobile brows, short round nose, a small asymmetrical mouth; short rounded arms and feet, ochre-yellow one-piece romper. Age reads clearly as an infant, not a schoolchild. Hands are soft simple mitten shapes. Same head proportions and curl in all eight panels. Palette: warm ivory #FFF6E7 background, deep blue-ink #232B43 face/hair, apricot #F1BE96 skin, ochre #E8BA63 romper, tiny coral #E9786B and sage #8AAB9A accents. Keep silhouettes legible at small sizes.

Composition: beautiful disciplined 4-column by 2-row contact sheet, exactly EIGHT equal roomy cells, no boxes, thin subtle baseline rules only, each with a large standalone baby pose and a clear small Korean caption beneath. A restrained top heading reads exactly "아기의 여덟 가지 모습", a small subtitle "보호자가 기록한 관찰 상태". Do not add other text.
Read order and exact captions:
01 우는 중 — eyes squeezed, open downturned crying mouth, two restrained tears, forearms raised; emotional but gentle, no distress spectacle.
02 칭얼거림 — furrowed brows, small wavy closed mouth, elbows tucked, a mild restless turned torso; no tears. Distinct from crying.
03 편안해 보임 — relaxed open gaze, relaxed hands resting on torso, quiet slight mouth curve. Do not make a big laughing face.
04 즐거워 보임 — delighted open-mouth smile, lifted cheeks, one lifted arm and one bent leg, readable joy.
05 깨어 있음 — alert open eyes, neutral mouth, head slightly turned looking outward, no assumed happiness.
06 졸려 보임 — half-lidded eyes, small yawn, hand near cheek, head slightly tilted; still awake.
07 잠듦 — eyes fully closed, quiet mouth, arms resting, baby lying on their back on a simple firm flat sage mat seen in a gentle overhead view; no pillow, loose blanket or plush toys.
08 상태 확인 못함 — relaxed neutral forward pose and a detached small outlined question circle beside the figure; no inferred crying, smile, or sleep, same infant.

Pose illustrations should dominate the board. Preserve anatomically coherent two arms and two legs, continuous connected limbs, avoid extra fingers. Allow breathing room around heads and hands. This should feel like an art director's original animation-ready visual language: warm, thoughtful, visually strong, never generic cute emoji stickers.

## 1-1. 관찰 상태 배경·색면 정리

대상: 최초 생성한 관찰 상태 시트. 정체성·포즈·순서·한글 문구를 유지하는 편집.

Edit this eight-panel infant illustration sheet. Keep the same infant identity, one dark comma curl, all eight poses, their order, Korean title and exact captions. Make the ENTIRE BACKGROUND a perfectly UNIFORM, SOLID, OPAQUE warm ivory #FFF6E7. Absolutely remove every dark smoky patch, halo, gradient, glow, vignette, texture and transparency. All skin, clothing, hair and mat must be perfectly flat fully opaque solid color fills, with crisp vector-like edges. Use dark #232B43 for ALL text, readable against the flat ivory. Do not change the captions. Maintain eight poses and 4-by-2 layout. No illumination effects whatsoever. The finished image should look like flat vector artwork printed on a clean ivory sheet.

## 2. AI 추정

Use case: stylized-concept. Create one polished original flat-vector-like editorial concept sheet for an infant-care service, a 3-column by 2-row grid, exactly SIX illustrations. Heading exactly "울음에서 추정한 다섯 가지 가능성", subtitle "AI 추정 · 관찰 기록과 별도". Each of the six cells carries a small readable "AI 추정" pill. Labels must include the word 가능성 exactly where specified.

Art direction: confident large simple matte color planes, round geometric shapes and expressive but restrained faces. A COMPLETELY UNIFORM OPAQUE ivory #FFF6E7 background, all fills fully opaque, crisp edges, absolutely no transparency, glows, shadows, textures or gradients. Ink navy #232B43, apricot skin #F1BE96, ochre romper #E8BA63, sage #8AAB9A, cobalt #4169C6, coral #E9786B. No stock background scenery, flowers, hearts, charts or UI cards.
All six show the EXACT SAME original infant: round pear-shaped apricot head, single small dark comma-shaped curl bending right, small bean eyes, tiny rounded nose, dark simple expressive brows and mouth, round short limbs, ochre one-piece romper. Same identity and proportions, simple readable at small size. In EVERY cell the infant has the same mildly unsettled expression and safe supported pose; do not visually claim that the inferred cause is a confirmed observation. All hypothetical cause symbols float in a separate small DASHED speech/thought bubble beside baby, not on the baby's body.

Cells and exact captions:
01 배고픔 가능성 — bubble contains a simple generic milk droplet above a small cup, no caregiver feeding, no reward.
02 졸림·피곤함 가능성 — bubble contains a small crescent moon, infant remains AWAKE, not sleeping.
03 트림 필요 가능성 — bubble contains a single small curved up-arrow and two tiny air circles, no confirmed burp from baby, no caregiver.
04 배 불편함 가능성 — bubble contains a small soft abstract belly spiral, not a red pain target or medical anatomy; infant expression remains same mild unsettled expression.
05 일반적인 불편함 가능성 — bubble contains a simple abstract uneven squiggle, no diaper, thermometer, illness, loneliness or other specific cause.
06 판단 어려움 — bubble contains a single neutral question mark, no cause symbols; beneath only this exact caption.

Each cell has just a baby and one detached dashed bubble, clear distinction between AI hypothesis symbols and the baby. Labels and "AI 추정" must be fully readable dark ink on ivory. Minimal subtle spacing guides, no heavy boxes. Sophisticated original infant-care editorial art, not emoji stickers or a clone of another brand.

## 3. 돌봄 조치

Use case: stylized-concept. Produce one exceptionally polished landscape editorial illustration sheet, 4 columns by 2 rows, EXACTLY eight numbered caregiver-and-infant scenes. This is original concept art for animated assets in a Korean baby-care application, not an application UI. Heading exactly "완료한 돌봄의 여덟 장면", subtitle "보호자가 확인해 저장한 조치".

Art direction: sophisticated flat vector editorial illustration. Large sculptural curved color planes; C-shaped parent arms create a soft enclosing silhouette around a small baby, clear negative spaces between arms and torso. Exaggerated but anatomically coherent adult forearms, comfortable grounded compositions. Deep ink #232B43, strong muted cobalt #4169C6, sage #8AAB9A, warm coral #E9786B, apricot skin #F1BE96 and ochre #E8BA63. FULLY OPAQUE UNIFORM ivory background #FFF6E7 and FULLY OPAQUE FLAT solid fills everywhere. ZERO transparency, gradients, glows, texture, shadows, lighting effects or vignette. No black outlines; only tiny dark eye/mouth curves. No decorative plants, hearts or star confetti. No copied existing illustration.

One SAME adult caregiver throughout: dark sculpted short wavy hair in two simple curves, small bean-shaped eyes, a long rounded nose, thoughtful gentle expression, cobalt elbow-length top and deep ink trousers, rounded broad hands with minimal fingers. One SAME infant throughout: large round apricot pear-shaped head, one dark comma curl bending right, tiny ears, small dark bean eyes and an ochre-yellow romper, short round limbs. Infant's head about 1/3 of their body, same face proportions across scenes. Consistent cast, not eight different families. The adult and infant are modestly clothed in all scenes; diaper area always fully covered. Parent expression can be warm, but do not automatically show the infant happy after each action. No speech from the infant.

Exact Korean captions beneath the scenes, reading order:
01 수유함: seated parent supporting baby's head and neck with forearm, feeding with a small ivory-and-sage bottle held at baby's mouth; eyes of infant open neutral. Compact side-on scene, visible bottle.
02 기저귀 갈아줌: baby safely lying on their back on a flat low changing mat, parent beside them fastening the clean diaper's side tabs; all private areas covered, a clearly visible folded clean diaper beside mat. Clearly changing, not just checking.
03 안아줌: parent seated holding infant with BOTH arms in a sculptural enveloping C shape, head and neck supported. Baby eyes open neutral; no implied soothing success.
04 토닥여줌: parent holding infant upright, supporting head and body, free broad hand gently touching infant upper back. Two restrained small arc marks around patting hand. No burp bubble.
05 트림시킴: parent supporting infant upright against shoulder, head and neck supported, hand at upper back, a small round outlined exhale symbol NEAR the baby's mouth indicating confirmed burp. Distinct from patting; baby neutral.
06 재워줌: caregiver beside low plain crib after placing sleeping infant on their back on a firm flat mattress, infant eyes closed, caregiver hand gently withdrawing. No pillow, blankets, toys or floating child.
07 환경 바꿔줌: parent reaching to DIM a small lamp, nearby infant resting safely in a low plain crib. Two clear before/after lamp rays, gentle physical action of switch. No assumed baby smile.
08 기타 완료 조치: concrete example of parent changing infant's outfit, lifting an ochre sleeve over one little arm while supporting baby, small spare shirt beside them. All private areas covered. Caption only as specified.

Each cell has ample whitespace, big figures and a clear silhouette at thumbnail size. Match face style to warm, clear simple infant animation assets. Every arm connected to a body, exactly two adult hands per scene, no extra limbs. Use subtle small numerals and dark readable captions only, no card borders. Final product: a premium original art-director's contact sheet with a rhythmic visual family.

## 2-1. AI 판단 어려움의 표정과 옷 통일

이전 수정 프롬프트의 전문은 이 파일에 보존되지 않았다. 수정 의도는 판단 어려움의 얼굴을 중립으로 만들고 아기 옷을 다른 시트와 통일하는 것이었다. 원문 프롬프트로 간주하지 않는다.

## 3-1. 조치와 아기 상태의 독립성 보정

이전 수정 프롬프트의 전문은 이 파일에 보존되지 않았다. 수정 의도는 환경 변경·옷 갈아입히기 장면의 아기를 깨어 있는 중립 상태로 바꾸는 것이었다. 원문 프롬프트로 간주하지 않는다.

## 4. 안아줌 대표 장면

이전 생성 프롬프트의 전문은 이 파일에 보존되지 않았다. 결과물은 같은 아기와 파란 옷의 보호자가 큰 곡선으로 구성된 포옹 자세를 만드는 대표 장면이다.

## 2-2. AI 추정을 아기의 표정·손동작·자세로 직접 표현

수정일: 2026-09-20. 실행 방식: 내장 image_gen 편집. 아래는 실제 사용한 프롬프트 전문이다.

- 편집 대상: 커밋 452afc5의 concepts/02-ai-inferences.png. 말풍선을 쓰던 이전 시트.
- 스타일·캐릭터 참조: concepts/01-observation-states.png.
- 결과: concepts/02-ai-inferences.png를 수정본으로 교체. 원본 위치와 해시는 image-provenance.json에 기록.

Use case: precise-object-edit.
Asset type: a revised illustrated character pose sheet for a Korean baby-care product.

Input image 1 is the EDIT TARGET: the six-cell board titled “울음에서 추정한 다섯 가지 가능성”. Input image 2 is a STYLE AND CHARACTER reference only: “아기의 여덟 가지 모습”.
Revise image 1 so EVERY state is embodied by the BABY'S OWN face, hands, posture, and silhouette. REMOVE ALL thought bubbles, speech bubbles, dots, floating icons, pictograms, question marks, arrows, moons, milk symbols, spirals and abstract emotion marks. Each cell must be a single self-contained illustration of the baby experiencing that state. No explanatory props. The infant itself, not a separate symbol, carries the meaning.

Preserve the landscape 1536x1024 format, warm ivory background, elegant spacious 3-column by 2-row arrangement, Korean title and subtitle, and caption-plus-small-navy-badge structure of image 1. Center each full-body infant over its caption, at similar visual scale, using the space freed by removing the bubbles. Maintain abundant separation; do not crop body parts.
Match the exact character design and simplified flat shape language of image 2: warm apricot skin, round pear-shaped large infant head, ONE small navy comma curl, small ears, simple navy eyes and expressive eyebrows, simple mouth, short rounded arms and legs, mustard sleeveless one-piece romper. Same proportions and identity in all six cells. Soft clean flat-color vector-like editorial illustration, negligible shading, no gradients, no texture, no 3D. Keep facial gestures bold and readable like image 2.

Draw SIX CLEARLY DIFFERENT POSES, left to right then top to bottom:
1 HUNGRY: alert eyes with pleading inward-raised brows, head turning slightly to the side as if searching for a feed, partly open searching mouth, one tiny fist pressed to the lips and the other hand resting on the belly. Leaning forward slightly. Hungry seeking expression, not yawning and not happily eating. Entire figure shown.
2 SLEEPY / TIRED: slumped shoulders, drooping half-closed eyelids, a clear soft open-mouth yawn, one hand rubbing the eye, other arm hanging loosely, relaxed low seated posture. Still awake; no closed sleeping pose.
3 NEEDS TO BURP: upright three-quarter view with chest lifted and small backward torso arch, chin slightly raised, brows tense, lips a SMALL pursed O (much smaller than tired yawn), one hand over UPPER chest just below the throat and the other at the side. Show upper-body pressure/discomfort through the pose only, with legs relatively open. No emitted breath, bubbles or completed-burp celebration.
4 BELLY DISCOMFORT: visibly hunched curled-forward torso, knees drawn toward tummy, BOTH hands holding LOWER abdomen, furrowed eyebrows, squeezed eyes, tight grimacing small mouth. Compact curled silhouette distinctly different from upright chest posture in cell 3. No localized red target or medical markings, no extreme agony.
5 GENERAL DISCOMFORT: restless squirming asymmetrical seated pose, torso twisting sideways, one leg extending outward and the other tucked in, both arms held OUT away from belly as tense bent arms, scrunched dissatisfied eyebrows, downturned small open complaining mouth. Restless body-wide discomfort, no specific abdominal gesture. Distinct from the inward-curled belly pose and from hunger.
6 UNCERTAIN: calm neutral front-facing upright sitting pose, open level eyes, straight tiny mouth, relaxed arms resting beside knees. No smile, no sadness, no symbols. This is the neutral fallback, not a sixth physical condition.

Exact Korean text to preserve:
Title: 울음에서 추정한 다섯 가지 가능성
Subtitle: AI 추정 · 관찰 기록과 별도
Top row captions:
01 배고픔 가능성
02 졸림·피곤함 가능성
03 트림 필요 가능성
Bottom row captions:
04 배 불편함 가능성
05 일반적인 불편함 가능성
06 판단 어려움
A small navy pill reading “AI 추정” under each caption. The badges and title are OUTSIDE the illustrations. No other text.

Priority: make the baby visibly EXPERIENCE each of the five states through a purposeful distinct whole-body pose. Do not repeat an identical sad seated baby. Preserve the same original infant and the visual language of the other concept sheets.

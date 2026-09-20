# Duolingo 캐릭터·모션 스킬 설치 안내

팀원에게 [설치용 ZIP](duolingo-character-motion.zip)과 이 안내를 전달하면 된다. ZIP에는 현재 사용 중인 스킬의 파일 5개가 들어 있으며, 내용은 수정하지 않았다.

이 스킬은 Duolingo의 공개 자료를 바탕으로 만든 **팀 공유용 커스텀 스킬**이다. Duolingo가 직접 배포하는 제품은 아니다. 캐릭터의 그림체·표정·포즈·상태별 모션을 설계하고 검토할 때 사용한다.

## 1. 설치 위치 선택

개인 설치를 권장한다. 설치한 컴퓨터에서 여러 프로젝트에 사용할 수 있다.

| 사용 환경 | 스킬 폴더를 넣을 위치 |
|---|---|
| Windows의 Codex | `%USERPROFILE%\.agents\skills\` |
| Mac의 Codex | `~/.agents/skills/` |
| 특정 프로젝트에만 설치 | 해당 프로젝트의 `.agents/skills/` |

위 경로는 현재 [OpenAI 공식 스킬 문서](https://learn.chatgpt.com/docs/build-skills)의 개인·저장소 설치 위치를 따른다. 작성자 컴퓨터의 기존 `.codex/skills` 경로를 그대로 따라 만들 필요는 없다. 개인 설치와 프로젝트 설치 중 하나를 선택한다. 같은 이름의 스킬을 여러 경로에 넣으면 중복으로 표시될 수 있다.

Windows에서 Codex를 WSL 안에서 실행한다면 Windows 사용자 폴더가 아니라 **해당 WSL 사용자의 `~/.agents/skills/`**를 사용한다.

## 2. ZIP으로 설치

먼저 [duolingo-character-motion.zip](duolingo-character-motion.zip)을 내려받아 압축을 푼다. GitHub에서 ZIP 파일 페이지를 보고 있다면 **Download raw file**로 내려받는다.

### Windows

1. 파일 탐색기 주소창에 `%USERPROFILE%`를 입력한다.
2. 그 안에 `.agents` 폴더를 만들고, 다시 그 안에 `skills` 폴더를 만든다. 이미 있는 폴더는 그대로 사용한다.
3. 압축을 푼 **`duolingo-character-motion` 폴더 전체**를 `skills` 안에 넣는다.

최종 파일 위치가 다음과 같아야 한다.

```text
%USERPROFILE%\.agents\skills\duolingo-character-motion\SKILL.md
```

### Mac

1. Finder에서 **이동 → 폴더로 이동…**을 선택하고 `~/.agents/skills`를 입력한다.
2. 경로가 없다면 아래 터미널 명령으로 폴더를 만든 뒤 연다. 폴더가 있다면 바로 다음 단계로 진행한다.
3. 압축을 푼 **`duolingo-character-motion` 폴더 전체**를 `skills` 안에 넣는다.

```sh
mkdir -p "$HOME/.agents/skills"
open "$HOME/.agents/skills"
```

최종 파일 위치는 다음과 같다.

```text
~/.agents/skills/duolingo-character-motion/SKILL.md
```

`SKILL.md`만 복사하면 참고 문서를 사용할 수 없다. 압축 해제 과정에서 폴더가 두 겹으로 생겼다면 `SKILL.md`가 바로 들어 있는 안쪽 폴더를 옮긴다. 같은 이름의 스킬이 이미 설치되어 있다면 기존 내용을 확인하고, 필요한 수정본은 설치 경로 밖에 백업한 뒤 교체한다.

## 3. Codex에서 확인하고 사용

1. Codex의 **Skills** 목록에서 **Duolingo 캐릭터·모션**을 찾는다. 인터페이스에 따라 내부 이름인 `duolingo-character-motion`으로 보일 수도 있다.
2. 설치 직후 보이지 않으면 Codex를 완전히 종료한 뒤 다시 연다. 공식 문서상 변경은 자동 감지되지만, 표시되지 않을 때는 재시작하도록 안내하고 있다.
3. 새 대화에서 아래처럼 스킬 이름을 지정한다. 스킬 선택 메뉴가 있는 화면에서는 해당 항목을 선택해도 된다.

```text
$duolingo-character-motion을 사용해 우리 서비스의 기존 아기 캐릭터를 유지하면서, 배고픔과 졸림을 표정·자세로 구분하는 애니메이션을 설계해줘.
```

CLI·IDE에서는 `/skills` 또는 `$`로 선택할 수 있고, ChatGPT 화면의 스킬 선택은 `@`를 사용한다. 이 ZIP은 **로컬 Codex 설치용**이므로 웹·모바일 ChatGPT 계정에 자동 설치되지는 않는다. 인터페이스별 사용 방식은 [공식 문서](https://learn.chatgpt.com/docs/build-skills)를 따른다.

스킬은 제작 지침과 참고 자료를 제공한다. 이미지 생성 도구, Lottie·Rive 편집기, 별도 연결 도구를 설치하는 패키지는 아니다. 실제 이미지 생성이나 애니메이션 제작에는 팀원이 사용하는 환경의 도구가 필요하다.

## 선택: Windows PowerShell로 설치

ZIP이 **다운로드 폴더**에 있을 때 아래 전체를 실행하면 된다. 다른 곳에 저장했다면 첫 줄의 ZIP 경로를 바꾼다. 같은 스킬이 이미 있으면 덮어쓰지 않고 중단한다.

```powershell
$skillZip = Join-Path $env:USERPROFILE "Downloads\duolingo-character-motion.zip"
$skillRoot = Join-Path $env:USERPROFILE ".agents\skills"
$skillDest = Join-Path $skillRoot "duolingo-character-motion"

if (-not (Test-Path -LiteralPath $skillZip)) {
    throw "ZIP 경로를 확인해 주세요."
}
if (Test-Path -LiteralPath $skillDest) {
    throw "같은 스킬이 이미 있습니다. 기존 버전을 확인해 주세요."
}

New-Item -ItemType Directory -Force -Path $skillRoot | Out-Null
Expand-Archive -LiteralPath $skillZip -DestinationPath $skillRoot
Test-Path -LiteralPath (Join-Path $skillDest "SKILL.md")
```

마지막 출력이 `True`이면 파일 위치가 맞다. Codex에서 인식됐는지는 위의 **Skills 목록 확인**까지 진행한다.

## 포함 파일과 검증 범위

```text
duolingo-character-motion/
├── SKILL.md
├── agents/
│   └── openai.yaml
└── references/
    ├── illustration-and-characters.md
    ├── personality-and-motion.md
    └── sources.md
```

2026-09-20에 ZIP 무결성, 압축 해제 후 원본 5개 파일과의 바이트 일치, 문서 내부 상대 링크를 검사했다. 팀원의 Windows·Mac에서 실제 설치·인식을 실행한 결과는 아직 없다. 파일별 해시와 검사 결과는 [manifest.json](manifest.json)에 기록했다.

- ZIP 크기: 9,178바이트
- ZIP SHA-256: `04ccd65d3131a740dc0d9c598d9dc8f31950c60be450db0d35a1af1b86e27414`
- 관련 제작안: [아기·양육자 일러스트와 모션 제작안](../../design/character-motion/README.md)

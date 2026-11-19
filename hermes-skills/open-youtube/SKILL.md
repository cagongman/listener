---
name: open-youtube
description: "Windows PC에서 유튜브를 기본 브라우저 또는 Chrome으로 엽니다."
version: 1.0.0
platforms: [windows]
metadata:
  hermes:
    tags: [YouTube, Browser, Windows, 유튜브, 크롬]
    requires_tools: [terminal]
---

# Open YouTube on this PC

## When to Use

사용자가 “유튜브 열어”, “유튜브 실행해”, “크롬에서 유튜브 열어”처럼
현재 Windows PC의 브라우저에 YouTube 홈페이지를 열어 달라고 할 때 사용한다.
listener가 “모니카” 호출어를 제거한 뒤 전달하는 요청에도 적용한다.

영상의 내용 요약이나 자막 추출 요청에는 해당 작업에 맞는 다른 스킬을 사용한다.

## Procedure

1. 환경 정보에서 `terminal`이 현재 사용자의 Windows PC에서 실행되는 local backend인지 확인한다.
   원격 호스트·컨테이너·WSL이면 이 스킬로 사용자 Windows 화면을 열 수 있다고 가정하지 않는다.
2. 이 스킬을 로드한 `skill_view` 결과의 실제 스킬 디렉터리에서
   `scripts/Open-YouTube.ps1`의 절대 경로를 확인한다. 현재 프로젝트 폴더를 스킬 폴더로 가정하지 않는다.
3. `terminal`로 아래 명령을 한 번 실행한다. `<SKILL_DIR>`는 확인한 실제 절대 경로로 바꾼다.

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\Open-YouTube.ps1"
   ```

4. 사용자가 Chrome/크롬을 명시한 경우에만 끝에 `-Browser chrome`을 붙인다.
   그 외에는 기본 브라우저를 사용한다.
5. 스크립트가 성공하면 “유튜브를 여는 요청을 보냈어요.”처럼 짧게 답한다.
   화면을 관찰하지 않았다면 페이지 로딩·로그인·영상 재생까지 확인했다고 말하지 않는다.

## Pitfalls

- 이 작업은 사용자 PC의 GUI를 여는 요청이다. 원격 브라우저 탐색이나 웹 검색으로 대체하지 않는다.
- 외부 패키지, API 키, YouTube 로그인이 필요하지 않다.
- 스크립트는 고정된 `https://www.youtube.com/`만 연다. 사용자 발화를 셸 코드로 삽입하지 않는다.
- `ExecutionPolicy Bypass`는 이번 PowerShell 프로세스에만 적용된다. 시스템 실행 정책을 변경하지 않는다.
- Chrome을 명시했는데 설치를 찾지 못하면 오류를 알려 주고 기본 브라우저 사용 여부를 확인한다.
- 로컬 Windows GUI를 사용할 수 없는 환경에서는 제한을 알린다. 실행하지 않고 성공했다고 답하지 않는다.

## Verification

실제 브라우저를 열지 않고 설치 경로와 명령을 점검하려면 위 명령에 `-DryRun`을 추가한다.
이는 실행 계획 확인이며 브라우저를 연 결과가 아니다.

실제 실행의 종료 코드가 0이고 JSON의 `requested`가 true면 Windows가 실행 요청을 수락한 것이다.
페이지가 보이는지는 사용자 또는 사용 가능한 화면 확인 도구로 확인한다.

# Hermes용 사용자 스킬

이 폴더는 Hermes에 설치하는 스킬의 원본이다. Codex 스킬이나 listener의 호출어 설정과 별도로 관리한다.

## open-youtube

`SKILL.md`가 동작 조건과 실행 절차를 설명하고 `scripts/Open-YouTube.ps1`이 로컬 Windows의 브라우저를 연다. 기본 브라우저를 사용하며 사용자가 Chrome을 명시하면 Chrome을 찾는다.

현재 PC의 기본 Hermes 프로필 설치 위치:

```text
%LOCALAPPDATA%\hermes\skills\productivity\open-youtube\
├─ SKILL.md
└─ scripts\Open-YouTube.ps1
```

Hermes 대화에서 다음과 같이 요청한다.

```text
open-youtube 스킬을 사용해서 유튜브 열어 줘.
크롬에서 유튜브 열어 줘.
```

listener에서는 “모니카, 유튜브 열어 줘”로 요청한다. 스킬은 Hermes 에이전트가 선택하고 실행한다. listener 자체에 브라우저 실행 분기를 추가하지 않는다.

CLI에서 `/reload-skills`로 스킬 슬래시 명령을 갱신한 뒤 `/open-youtube`로 직접 호출할 수도 있다. 프로필별로 비활성화된 스킬이면 `hermes skills`에서 활성화 상태를 확인한다. terminal은 현재 PC의 Windows local backend여야 한다.

설치 위치를 바꾸거나 다른 프로필에 추가할 때는 해당 프로필의 skills 폴더에 이 스킬 폴더를 복사한다. 스킬 파일은 수정한 원본과 설치본을 함께 갱신한다.

참고: [Hermes 스킬 작성](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills), [스킬 관리·새로고침](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills).

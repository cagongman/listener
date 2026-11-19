# Listener Hermes

“헤이 모니카” 또는 “모니카”로 Hermes CLI를 여는 Windows 트레이 앱입니다. 기존 listener와 마이크 스트림 및 Whisper 인스턴스 하나를 공유합니다.

## 설치 및 실행

프로젝트 루트에서 실행합니다. Python 3.11 이상과 설정을 마친 Hermes CLI가 필요합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e . -e ./listener-hermes

# 마이크·Hermes CLI·인증된 Gateway 연결 점검 (녹음하지 않음)
.\.venv\Scripts\python.exe -m listener_hermes --check

# GUI 실행파일에서는 --report로 점검 결과를 JSON 파일에 저장 가능
# ListenerHermes.exe --check --report check.json

# 음성 호출 앱
.\.venv\Scripts\python.exe -m listener_hermes

# 기존 받아쓰기는 다른 터미널에서 실행
.\.venv\Scripts\python.exe -m listener
```

필요한 공통 음성 서비스는 먼저 실행한 앱이 숨김 프로세스로 시작합니다. 두 앱의 실행 순서는 관계없습니다. 마지막 클라이언트가 종료되면 30초 뒤 서비스가 종료됩니다. 기존에 배포한 구버전 Listener.exe는 자체 모델을 로드하므로 새 앱과 함께 사용하려면 새 버전으로 교체해야 합니다.

설치 후에는 `listener-hermes`, `listener`, `listener-speech` 명령도 사용할 수 있습니다. 기존 listener의 관리자 창 입력은 예전과 같이 관리자 권한 실행이 필요합니다.

## Windows 로그인 시 자동 실행

프로젝트 루트에서 다음 명령으로 현재 사용자 시작프로그램 폴더에 바로가기를 등록합니다. 관리자 권한은 필요하지 않습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Set-HermesAutostart.ps1

# 자동 실행 해제
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Set-HermesAutostart.ps1 -Disable
```

로그인하면 `dist/ListenerSuite/ListenerHermes.exe`가 트레이로 실행되고 필요한 공통 음성 서비스도 시작합니다. 등록 후 프로젝트 폴더를 이동했다면 이전 등록을 해제하고 새 위치에서 다시 등록하세요. Windows 작업 관리자의 **시작 앱**에서도 사용/사용 안 함을 바꿀 수 있습니다.

## 호출 방식

- **“모니카, 이 프로젝트 설명해 줘”**: 선택된 Hermes 대화에 명령을 전달합니다. 선택된 대화가 없으면 저장된 선택/열린 대화를 확인하고, 대상이 없을 때 새 대화를 만듭니다. 여러 대화가 있으면 먼저 트레이에서 선택합니다.
- **“헤이 모니카” → 다음 명령**: 확인음 후 5초 안에 다음 발화를 시작하면 그 명령을 전달합니다.
- **“헤이 모니카”만 말하기**: 다음 발화가 없으면 선택된 대화의 CLI 창을 엽니다.
- **Hermes 창이 열려 있거나 작업 중이어도 호출 감지를 유지**합니다. 후속 요청은 같은 세션 ID에 FIFO로 접수하고 접수 알림을 표시합니다. 기존 작업을 자동으로 중단하지 않습니다.
- `Ctrl+Alt+M` 받아쓰기를 시작하면 수집 중인 Hermes 명령을 취소하고, 받아쓰기 종료 후 감시를 재개합니다.
- 최대 명령 길이는 30초, 핫키 녹음은 5분입니다. 초과하거나 오디오가 유실되면 해당 입력을 폐기합니다.

트레이에서 대화를 선택하고 새 대화·이전 대화·창 다시 열기·목록 새로고침을 실행할 수 있습니다. 선택한 대화와 직전 대화는 프로필/작업 폴더별로 저장됩니다. 앱을 종료해도 이미 열린 Hermes 대화창과 Gateway가 접수한 작업은 유지합니다.

대화 화면은 기본적으로 `hermes chat --tui --native --resume <세션 ID>`로 엽니다. 기본 classic CLI는 대기 중 외부 클라이언트의 요청을 화면에 표시하지 않는 경로가 있어, listener가 보낸 입력·응답·승인 요청을 표시하는 TUI를 사용합니다. 명시적으로 classic 화면을 원할 때는 `hermes.args`를 `["chat", "--cli"]`로 설정할 수 있습니다.

| 음성 명령 | 동작 |
|---|---|
| “모니카, 이것도 확인해 줘” | 현재 대화에 후속 요청 접수 |
| “모니카, 새 대화 시작해” | 새 세션을 만들고 선택. 기존 작업은 기존 세션에서 유지 |
| “모니카, 창 다시 열어” | 같은 세션의 CLI 화면 열기 |
| “모니카, 이전 대화 열어” | 직전 선택 세션으로 이동 |
| “모니카, 대화 목록 보여줘” | 목록을 새로 읽고 트레이 선택 메뉴에 표시 |
| “모니카, 두 번째 대화 열어” | 마지막으로 표시한 목록의 해당 대화 선택 |

현재 세션 ID로 전송하며 전역 `latest`를 임의로 사용하지 않습니다. 전송 응답이 끊기면 실제 접수되었을 수 있어 자동으로 같은 음성을 재전송하지 않고 오류를 알립니다. 작업 중 승인/답변이 필요하면 해당 CLI에서 기존 Hermes 흐름으로 처리합니다.

Hermes가 명령 실행 승인이나 추가 답변을 기다리면 트레이가 주황색 `waiting` 상태로 바뀌고 콘솔 확인 알림을 표시합니다. 뒤에 접수한 요청은 큐에 남습니다. 트레이 메뉴에서 대기 건수도 확인할 수 있습니다. 앱은 승인을 대신 선택하지 않습니다. 예를 들어 `open-youtube`의 PowerShell 실행이 승인 대상으로 분류되면 Hermes 콘솔에서 명령을 확인한 뒤 `once`(한 번 허용)를 선택하세요.

## 먼저 안전하게 점검하기

```powershell
# 마이크/Whisper 없이 텍스트의 호출어 판별만 확인
.\.venv\Scripts\python.exe -m listener_hermes --simulate "모니카, 프로젝트 설명해 줘"

# 실제 마이크와 공유 Whisper를 사용하지만 Hermes는 실행하지 않음
.\.venv\Scripts\python.exe -m listener_hermes --dry-run

# 합성 오디오·가짜 모델 및 실제 Named Pipe를 사용하는 자동 검증
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

짧은 “모니카”는 일반 대화에서 오탐하기 쉬워 “헤이 모니카”보다 엄격한 인식 메타데이터 기준을 적용합니다. 발화 시작에서만 호출어를 검사하며 “하모니카”, “모니카가 말했다” 등은 제외합니다. 실제 사용자 음성과 주변 소음에서 성공률과 지연을 별도로 평가해야 합니다. 이 버전은 Whisper 기반 호출 감지이며 전용 저지연 키워드 모델은 아직 사용하지 않습니다.

실제 테스트에서 “헤이 모니카”가 “네, 모니카.”로 인식되는 경우를 확인하여 기본 `wake_aliases`에 `"네 모니카": "헤이 모니카"` 보정을 추가했습니다. 단어 사이 쉼표와 공백을 정규화하며, 별칭도 발화 시작·단어 경계·인식 점수를 검사합니다. `"wake_aliases": {}`를 설정하면 별칭 보정을 끌 수 있습니다.

## 설정

선택 사항으로 `config.example.json`을 같은 폴더의 `config.json`으로 복사해 수정합니다. 별도 설정 파일은 `--config <경로>`로 지정할 수 있습니다. 파일이 없으면 기본값을 사용합니다.

```json
{
  "wake_phrases": ["헤이 모니카", "모니카"],
  "wake_aliases": {"네 모니카": "헤이 모니카"},
  "wake_enabled": true,
  "command_start_timeout_seconds": 5,
  "max_command_seconds": 30,
  "cooldown_seconds": 3,
  "hermes": {
    "backend": "native",
    "executable": "hermes",
    "args": ["chat", "--tui", "--native"],
    "cwd": "C:/Users/cagongman/cagongmanWS/_projects/listener",
    "profile": "",
    "session_title": "",
    "mode": "interactive"
  }
}
```

`cwd`는 Hermes가 작업할 프로젝트 폴더입니다. Windows에서는 현재 프로세스의 PATH뿐 아니라 레지스트리에 저장된 최신 시스템/사용자 PATH에서도 실행 파일을 찾습니다. 필요하면 `executable`을 `C:/Users/.../hermes/bin/hermes.exe`처럼 절대 경로로 지정하세요. PowerShell 함수/별칭 대신 실제 `.exe` 진입점을 사용합니다.

`profile`은 사용할 Hermes 프로필이며 빈 문자열이면 기본 프로필을 사용합니다. `session_title`을 지정하면 아직 선택한 대화가 없을 때 그 이름의 대화를 재개합니다. 실제 모니카 Bot Mode 구성이 준비된 뒤 `profile: monica`, `session_title: Bot Chat`을 지정할 수 있습니다. 일반 세션에 자동으로 봇 기능이 생기는 것은 아닙니다. `args`에는 `chat`과 표시 옵션(`--cli`, `--tui`, `--native`)만 사용하고, 프로필·대화·작업 폴더는 전용 설정 필드로 지정합니다.

WSL 설치라면 `backend`를 `wsl`, `executable`을 Linux 안의 Hermes 절대 경로로 설정합니다. `wsl_distribution`으로 배포판을, `wsl_cwd`로 Linux 작업 폴더를 지정할 수 있습니다. Windows 임시 프롬프트 파일은 `wslpath`로 변환합니다. WSL 어댑터는 인자 구성 자동 검증을 통과했으며 실제 WSL 실행 검증은 별도입니다.

STT 모델·device·언어·샘플레이트는 **루트 config.json**을 사용합니다. 현재 공유 서비스의 샘플레이트는 16,000으로 고정되어 있습니다. 모델 메뉴의 변경은 서비스에서 처리하며, 실패 시 이전 모델 복구를 시도합니다. Hugging Face 모델 캐시와 `HF_HOME` 사용 방식은 기존과 같습니다.

## 구조와 통신

```text
Listener UI ── SpeechClient ──┐
                             ├── Local Speech Service
Hermes UI ─── SpeechClient ──┘     AudioHub / Ring / VAD / Queue / Whisper × 1
    └── WakewordMatcher → CommandSession → SessionController
        ├── Gateway JSON-RPC → 선택된 session_id → prompt.submit (FIFO)
        └── HermesLauncher → 같은 session_id의 CLI 화면 (--resume)
```

IPC는 인증된 Windows Named Pipe와 크기를 제한한 JSON 메시지를 사용합니다. 오디오는 서비스 내부 메모리에서만 관리합니다. 모델 추론 중 들어온 음성도 프레임 ID로 보존하고, 호출이 확인된 뒤 후속 명령 수집에 이어서 사용합니다. 대기 중인 호출 탐색 작업은 최신 후보로 교체하고, 취소·만료된 인식 결과를 실행하지 않습니다.

Hermes Gateway의 공식 `gateway attach` 명령으로 받은 일회용 티켓으로 로컬 WebSocket에 인증합니다. 티켓은 로그나 설정 파일에 저장하지 않습니다. 인식문은 JSON-RPC의 텍스트 필드로 전달하며 셸 명령에 삽입하지 않습니다. CLI는 세션을 보여주는 화면으로만 열고, 같은 ID의 작업을 중복 생성하지 않습니다. 새 Windows 콘솔에는 실제 표준 입출력을 할당합니다. 기존 인증과 승인 흐름을 유지합니다.

## 로그 및 실행파일

- 공통 서비스: `%LOCALAPPDATA%\ListenerSpeech\speech.log`
- Hermes 앱: `%LOCALAPPDATA%\ListenerSpeech\hermes.log`
- 선택 세션과 접수 상태: `%LOCALAPPDATA%\ListenerSpeech\sessions-<프로필·작업폴더 해시>.json`. 인식문 원문은 이 파일에 저장하지 않습니다.
- 원본 마이크 오디오와 전체 인식문은 기본 로그에 남기지 않습니다.
- 기본 로그에는 앱 상태·서비스 연결·호출어 판정 사유·인식 점수·처리 시간·실행 결과를 남깁니다. `not_wake_phrase`는 호출어 불일치, `low_logprob`는 인식 신뢰도 미달, `high_no_speech_probability`는 무음 확률 초과, `stale_event`는 오래된 결과를 뜻합니다.
- 인식된 실제 문장을 확인할 때는 `python -m listener_hermes --debug-transcripts`로 진단 모드를 실행합니다. 이 옵션을 사용하는 동안 인식문도 `hermes.log`에 기록되며, 진단 후 앱을 종료하고 옵션 없이 다시 실행하면 기본 로그로 돌아갑니다.
- 후속 입력은 인증된 Gateway에 직접 전달합니다. 이전 버전의 CLI 첫 질문 방식에서 생성한 `%LOCALAPPDATA%\ListenerSpeech\prompts` 파일은 자동으로 새 요청에 사용하지 않습니다.

```powershell
.\.venv\Scripts\python.exe build.py
```

`dist/ListenerSuite/`에 `Listener.exe`, `ListenerHermes.exe`, `ListenerSpeech.exe` 및 공통 `_internal/` 런타임을 생성합니다. 폴더 전체를 함께 배포하세요. 모델 파일은 포함하지 않습니다. 기존 `dist/Listener/`는 그대로 두며 새 빌드는 별도 폴더를 사용합니다. 배포판의 Hermes 설정 파일은 exe 옆 `hermes.json`이며 `hermes.example.json`을 복사해 만들 수 있습니다.

초기 설계는 [PLAN.md](PLAN.md), 구조도는 [plan.html](plan.html), 봇·시세 도구·n8n의 후속 계획은 [BOT_N8N_PLAN.md](BOT_N8N_PLAN.md)를 참고하세요.

## 현재 확인한 결과 (2026-11-19)

- 자동 검증 51개 통과: 기존 음성 처리와 별칭 보정에 더해, 실제 로컬 WebSocket의 인증 프로토콜·이벤트/RPC 동시 처리, 작업 중 동일 세션 후속 입력, 새/이전/다시 열기, 접수 미확정 요청의 재전송 방지와 실제 음성 이벤트 루프 회귀 검증 포함.
- 기존 large-v3 모델을 CUDA에서 로드했고 무음 입력이 빈 결과로 반환됨을 확인.
- 한국어 합성음성 “헤이 모니카, 이 프로젝트 구조를 설명해 줘”가 인식되고 호출어와 명령으로 분리됨을 확인.
- 최신 PATH의 native Hermes v0.23.2에서 `--query-file` 지원 확인.
- 빌드된 ListenerHermes.exe에서 마이크의 16 kHz mono 입력 형식 및 Hermes 도움말 호출 점검 통과.
- 빌드된 ListenerSpeech.exe에 두 클라이언트가 같은 CUDA 모델 프로세스로 연결됨을 확인.
- 마지막 클라이언트 종료 후 서비스가 자동으로 정상 종료됨을 확인.

사용자의 실제 마이크 테스트에서 단독 “모니카” 호출, 5초 대기 후 Hermes CLI 실행과 확인음을 확인했습니다. 후속 입력 버전은 실제 Gateway에서 빈 대화 생성·재개·재연결과 대화 목록 조회를 확인했습니다. 별도 진단 세션에서 두 명령을 전송해 첫 요청 `started` / 두 번째 `queued` 상태와, 이후 두 요청 모두 `terminal / completed`가 됨을 확인했습니다. 진단 세션의 연결은 종료했습니다. 최종 배포 실행파일의 마이크·CLI·Gateway 점검도 모두 통과했고, 정상 모드로 실행한 트레이 앱이 공유 large-v3 CUDA 모델에 연결해 `listening` 상태가 됐습니다. 후속 입력 버전의 사용자 실제 음성 재확인과 장시간 오탐·성공률·지연 평가는 별도입니다.

Hermes가 업데이트 중일 때 `update_paused`로 연결을 거절하면 앱은 오류를 표시하고 재시도합니다. 업데이트 완료 후 `--check`로 연결을 확인할 수 있습니다.

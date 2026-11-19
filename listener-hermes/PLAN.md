# listener-hermes 구현 계획

작성일: 2026-11-18. **2026-11-19 업데이트:** 공통 음성 서비스, 기존 listener 연결, 웨이크 워드/명령 세션, Hermes 실행기와 공통 실행파일 빌드를 1차 구현했다. 자동 검증 30개, CUDA large-v3 로딩, 한국어 합성음성의 호출어/명령 분리 및 실제 Hermes CLI 도움말을 확인했다. 실제 사용자 발화의 성공률·오탐·지연 평가는 남아 있다. 실행 방법은 [README.md](README.md), 시각화한 구조는 [plan.html](plan.html)을 참고한다.

**후속 입력 업데이트:** CLI 실행 중에도 호출 감지를 유지하며, 인증된 Gateway에 세션 ID로 후속 입력을 FIFO 접수하도록 수정했다. 새/이전/목록 선택/창 다시 열기와 선택 상태 저장을 추가했다. 현재 동작은 README.md가 기준이며, 이 문서 아래의 “첫 CLI 실행” 설명은 최초 설계 기록이다. 봇과 n8n 확장 계획은 [BOT_N8N_PLAN.md](BOT_N8N_PLAN.md)에 정리했다.

## 1. 목표와 결정 사항

- 기존 `Ctrl+Alt+M` 녹음 → STT → 현재 커서 위치 입력 동작을 유지한다.
- 루트의 `listener-hermes/`에 Hermes CLI용 음성 호출 앱을 추가한다.
- 호출어는 **“헤이 모니카”, “모니카”** 두 가지다.
- 기존 `large-v3`, `faster-whisper`, CUDA 초기화 및 CPU int8 폴백을 재사용한다.
- 두 앱이 동시에 실행되어도 **음성 서비스 프로세스 하나가 Whisper와 마이크를 소유**한다.
- 1차는 VAD로 발화를 나누고 Whisper 인식 결과에서 호출어를 판별한다. 전용 웨이크 워드 모델은 측정 결과에 따라 후속 도입한다.
- 대상은 사용자가 확인한 Hermes Agent CLI다. 설치나 로그인, Hermes 설정 변경은 이 프로젝트의 실행 동작에 포함하지 않는다.

중요한 구분: 같은 Hugging Face 캐시를 쓰면 모델 파일은 공유되지만, 별도 프로세스가 각각 `WhisperModel`을 만들면 GPU/RAM 사용량은 중복된다. Python 모듈 재사용만으로 이 문제가 해결되지는 않는다.

## 2. 현재 코드에서 확인한 내용

| 파일 | 현재 역할 | 변경 방향 |
|---|---|---|
| `listener/app.py` | 트레이, 핫키 상태, Recorder/Transcriber 직접 생성, 붙여넣기 | 마이크/STT 처리를 SpeechClient 호출로 교체 |
| `listener/transcriber.py` | 모델 로딩, CUDA DLL 등록, CPU 폴백, 모델 변경, 직렬 추론 | 서비스에서만 생성하고 기존 로딩 로직 유지 |
| `listener/audio.py` | 핫키마다 InputStream 시작/종료, 녹음 청크 누적 | 연속 스트림과 제한된 버퍼를 지원하도록 확장 |
| `listener/config.py`, 루트 `config.json` | 모델/언어/핫키/샘플레이트 설정 | 기존 값 유지, 공통 설정 변경은 서비스가 조정 |
| `listener/inject.py`, `listener/hotkey.py` | 커서 입력, 전역 핫키 | 기존 listener UI에서 계속 사용 |
| `listener.spec`, `build.py` | Listener.exe 빌드 | 공용 서비스와 Hermes 앱을 포함하는 배포 단계 추가 |

현재 기본값은 `large-v3`, `device=auto`, `compute_type=float16`, `language=ko`, `sample_rate=16000`이다. 로컬 가상환경의 faster-whisper는 1.2.1이다.

현재 `transcribe()`는 문자열만 반환하고 모델 인스턴스 내부의 lock으로 호출을 직렬화한다. 이 lock은 다른 프로세스의 인스턴스와 공유되지 않는다. 또한 `vad_filter=True`는 전달된 녹음에서 무음을 제거하는 옵션이므로, 연속 마이크의 발화 시작/종료 검출은 별도로 구현해야 한다.

## 3. 권장 구조

```text
마이크: 16 kHz, mono, float32
           |
           v
Local Speech Service — 단일 인스턴스
  AudioHub / ring buffer / VAD / 작업 큐
  Transcriber → WhisperModel 1개
           |
           +-- SpeechClient → 기존 listener
           |                   Ctrl+Alt+M → 녹음 범위 지정
           |                   STT 결과 → paste_text()
           |
           +-- SpeechClient → listener-hermes
                               호출어 판별 → 명령 수집
                               HermesLauncher → Hermes CLI
```

서비스는 기존 `listener` 패키지 안에 추가하여 코드 이동을 최소화한다. UI 의존 모듈인 `listener.app`을 서비스에서 import하지 않는다. Hermes 쪽은 `Transcriber`나 `Recorder`를 직접 만들지 않는다.

### 공유 범위

| 자원 | 정책 |
|---|---|
| 모델 파일 | 현재 Hugging Face 캐시와 `HF_HOME` 사용 방식을 그대로 유지. 별도 모델 복사 없음 |
| GPU/RAM 모델 | 서비스에서만 로딩. 두 클라이언트에 로드된 모델 ID/실제 device를 알림 |
| 마이크 | 서비스만 InputStream 보유. Hermes 감시 중에는 연속 입력, 핫키 전용 사용 시 필요에 따라 입력 |
| 추론 | 실행 작업 하나. 핫키 요청, 활성 Hermes 명령, 호출어 탐색 순으로 대기 작업 우선순위 부여 |
| 설정 | STT 공통값은 루트 config, 호출어/CLI 실행값은 Hermes 전용 config |

### 서비스 수명과 통신

- 두 클라이언트 모두 서비스 연결을 먼저 시도하고, 없으면 숨김 백그라운드 프로세스로 시작한다. 사용자별 단일 인스턴스 잠금으로 동시 시작 경쟁을 처리한다.
- Windows Named Pipe 기반 로컬 IPC를 사용한다. 사용자 범위 접근 제어, 인증, 프로토콜 버전, 메시지 크기 제한을 둔다.
- 메시지는 검증된 JSON으로 주고받는다. PCM은 서비스 안에서만 다루므로 지속적인 오디오 IPC는 필요 없다.
- 최소 명령: `status`, `begin_dictation`, `end_dictation`, `subscribe_wake`, `arm_command`, `cancel_session`, `set_model`.
- 응답/이벤트: `ready`, `busy`, `utterance`, `transcript`, `model_changed`, `error`. 요청 ID, 세션 ID, 음성 구간의 시작/종료 시각을 포함한다.
- 세션 종료/클라이언트 연결 해제를 감지하여 녹음과 예약 작업을 정리한다. 마지막 클라이언트 종료 후 유예 시간 30초가 지나면 서비스를 종료한다.
- 서비스 장애 시 UI는 오류를 표시하고 재연결한다. 유실된 명령이나 Hermes 실행을 자동 재전송하지 않는다.

### 상세 아키텍처: 프로세스와 모듈 경계

런타임은 다음 세 프로세스로 구성한다. Hermes CLI는 호출 시 추가로 실행되는 별도 프로세스다.

| 프로세스 | 핵심 모듈 | 소유 자원과 책임 |
|---|---|---|
| 기존 Listener UI | Hotkey, Tray, SpeechClient, inject | 핫키와 UI, 서비스에 녹음 시작/종료 요청, 결과를 커서에 입력 |
| 공통 음성 서비스 | AudioHub, Ring buffer, VAD, Session manager, Job queue, Transcriber | 유일한 마이크 스트림/Whisper 인스턴스, PCM 보관, 세션별 작업과 공통 모델 설정 |
| Listener Hermes UI | SpeechClient, Wakeword matcher, Command session, HermesLauncher | 인식문에서 호출어 판별, 명령 수집 제어, 확인음/상태, Hermes 설정과 CLI 프로세스 추적 |

서비스 내부에서는 입력·VAD·추론을 분리한다.

1. 오디오 콜백은 PCM과 단조 증가하는 프레임 ID를 제한된 버퍼에 보관한다. 추론을 기다리지 않는다.
2. VAD/세션 작업자는 버퍼에서 발화와 탐색 창을 만들고 시작/종료 프레임을 기록한다.
3. 추론 작업자는 한 개의 모델을 사용해 작업을 직렬 실행한다. 새 작업의 우선순위는 대기열에만 적용하며 실행 중 추론을 즉시 선점하지 않는다.
4. 결과 전달 계층은 요청 ID와 세션 ID로 이벤트를 해당 클라이언트에 라우팅한다. 취소·만료된 세션의 결과는 폐기한다.

호출어만 말하고 다음 명령을 말하는 경우의 통신 순서는 다음과 같다.

```text
Hermes UI       → Speech service : subscribe_wake
AudioHub / VAD  → Job queue      : 음성 구간과 프레임 범위
Job queue       → Transcriber    : 호출 후보 STT
Speech service  → Hermes UI      : utterance(text, 구간 ID, 프레임 범위, 메타데이터)
Wakeword        → Command session: 호출어 확인
Hermes UI       → Speech service : arm_command(session_id, after_frame, 시작 타임아웃)
Speech service  → Hermes UI      : transcript(request_id, session_id, status, text)
HermesLauncher  → Hermes CLI     : 설정된 cwd + UTF-8 프롬프트 파일
```

호출과 명령이 같은 발화에 있으면 인식문에서 명령을 분리하여 추가 녹음 없이 실행할 수 있다. 호출만 하고 다음 발화가 없으면 명령 대기 타임아웃 후 프롬프트 없는 CLI를 연다.

원본 PCM은 서비스 안에만 두고 IPC는 제어 요청·텍스트·메타데이터를 전달한다. JSON 메시지는 `version`, `type`, `request_id`, `session_id`를 기본으로 하고 타입별 필드를 검증한다. `utterance`의 종료 프레임을 `arm_command.after_frame`에 연결하여 호출어 판별 중 들어온 후속 프레임도 이어 받는다. 중첩 창의 후보 중복은 구간 ID와 시각으로 제거한다.

Hermes CLI 실패는 Hermes UI가 표시하고 공통 서비스는 유지한다. 공통 서비스 실패는 양쪽 UI가 표시하고 재연결한다. 연결 복원 시 과거 명령이나 CLI 실행을 자동 재전송하지 않는다.

## 4. 폴더 및 모듈 계획

아래는 최초 설계의 목표 구조다. 현재 핵심 모듈은 구현되었으며 세션 관리는 speech_service.py에, 자동 검증은 루트 tests/에 통합했다.

```text
listener/                         # 기존 앱 + 공통 음성 처리
  app.py                          # SpeechClient 사용으로 수정
  transcriber.py                  # 기존 엔진 + 요청별 추론 옵션/메타데이터 확장
  audio.py                        # AudioHub, 링 버퍼, 녹음 구간 관리
  speech_service.py               # 서비스 진입점, 모델/마이크 소유
  speech_client.py                # IPC 클라이언트, 연결/서비스 시작
  speech_protocol.py              # 요청/이벤트 스키마와 버전
  speech_queue.py                 # 우선순위, 세션, 만료 처리
  vad.py                          # VAD 어댑터와 발화 종료 검출
  ...                             # 기존 hotkey/inject/config 유지

listener-hermes/
  PLAN.md
  plan.html                       # 구조도와 구현 순서를 담은 HTML 설명서
  pyproject.toml                  # 공통 listener 패키지에 대한 로컬 의존 선언
  README.md
  config.example.json             # 사용자가 복사할 Hermes 전용 설정
  run.py                          # 설치된 listener_hermes.main 호출
  listener_hermes/                # Python import 이름에는 밑줄 사용
    __init__.py
    __main__.py
    app.py                        # 트레이, 상태와 호출 세션
    config.py
    wakeword.py                    # 정규화, 호출어/문장 경계 판별
    command_session.py             # 단독 호출/한 문장 명령, 타임아웃
    hermes_launcher.py             # CLI 실행, 작업 경로, 프로세스 추적
  tests/
    test_wakeword.py
    test_command_session.py
    test_hermes_launcher.py

tests/                            # 공통 서비스 통합 검증
  test_speech_service.py

pyproject.toml                    # 루트 listener 패키지 설치 정보 추가
requirements.txt                  # 기존 실행 의존성, 검증 버전 범위 정리
```

소스 실행은 루트 가상환경을 함께 사용한다. 계획된 설치 방식은 루트에서 `pip install -e . -e ./listener-hermes`, 실행 방식은 기존 `python -m listener`와 신규 `python -m listener_hermes`다. sys.path를 임의로 수정하여 형제 폴더를 import하는 방식은 사용하지 않는다.

## 5. 호출어 및 명령 흐름

### A. 호출어만 말하기

1. “헤이 모니카” 또는 “모니카”를 인식한다.
2. 짧은 확인음을 내고 `ARMED`로 전환한다.
3. 5초 안에 다음 발화가 시작되면 그 발화를 Hermes의 첫 프롬프트로 사용한다.
4. 다음 발화가 없으면 프롬프트 없이 Hermes 대화형 CLI를 연다. 5초 안에 시작된 발화는 타임아웃 뒤에 끝나더라도 최대 명령 길이까지 수집한다.

### B. 호출어와 명령을 한 번에 말하기

`“모니카, 이 프로젝트 구조 설명해 줘”` → 호출어를 제외한 `“이 프로젝트 구조 설명해 줘”`를 Hermes의 첫 프롬프트로 전달한다.

`“헤이 모니카”` 다음에 쉬지 않고 이어 말하는 경우도 지원한다. 호출어 판별 중 도착하는 음성은 계속 버퍼링하고, 판별 결과의 음성 종료 시각을 기준으로 이후 프레임을 명령 세션에 연결한다. 판별이 끝났다는 이유로 버퍼를 비우지 않는다.

### 판별 규칙

- 유니코드/공백/문장부호를 정규화한 뒤 **발화 시작 위치**에서만 판별한다. 단순 `"모니카" in text`는 사용하지 않는다.
- 긴 호출어 “헤이 모니카”부터 매칭한다. 공백 변형 “헤이모니카”는 정규화로 허용한다.
- “모니카는 배우야”, “모니카가 말했다”, “모니카처럼” 같은 일반 문장과 “하모니카”는 호출로 처리하지 않도록 토큰 경계를 검사한다.
- STT가 문장부호와 공백을 생략할 수 있으므로 한국어 경계 규칙은 녹음 샘플로 보정한다. 동음/유사 발음까지 폭넓게 허용하는 fuzzy matching은 기본값으로 넣지 않는다.
- `avg_logprob`, `no_speech_prob` 등 메타데이터와 VAD 결과를 보조 지표로 쓴다. 이 값은 보정된 호출어 확률이 아니므로 단일 수치만으로 판단하지 않는다.
- `condition_on_previous_text=False` 등 탐색용 옵션을 요청별로 적용한다. 기존 받아쓰기의 beam_size=5 설정과 분리한다.
- 호출어를 initial_prompt로 강하게 유도하면 오탐이 증가할 수 있으므로 기본 적용하지 않는다.
- 이벤트 ID 중복 제거, 실행 중 재호출 차단, 3초 cooldown을 둔다. 짧은 “모니카”는 “헤이 모니카”보다 엄격한 규칙으로 평가한다.

Whisper 기반 방식은 전용 키워드 검출보다 지연과 연산 비용이 클 수 있다. 1차 목표는 모델 재사용과 실제 사용 가능성 검증이다. 저지연 호출이 필수이거나 장시간 대화에서 비용이 높으면, 한국어 호출어를 검증한 전용 detector를 앞단에 붙이고 명령 STT만 기존 Whisper가 담당하도록 교체한다.

## 6. 오디오·상태·동시 실행 정책

### 시작 튜닝 값

아래 수치는 확정 성능값이 아니라 실측 후 조정할 초기 설정이다.

| 항목 | 초기값/정책 |
|---|---|
| 입력 | 16 kHz mono float32 |
| 발화 전 여유 버퍼 | 500 ms |
| 발화 종료 | 무음 800 ms |
| 긴 발화 탐색 | 3초 안팎의 중첩 창으로 후보 판별, 프레임 ID로 중복 제거 |
| 다음 명령 대기 | 호출 확인 후 5초 이내 발화 시작 |
| 최대 명령 녹음 | 30초, 초과 시 잘린 명령 실행 없이 오류 안내 |
| 탐색 대기열 | 세션당 최신 후보 한 개, 시각 기준 만료. 백로그가 뒤늦게 실행되지 않게 처리 |
| 메모리 | 명령 최대 길이와 겹침을 담는 제한된 링 버퍼. 오버런 시 해당 세션 취소 |
| 저장/로그 | 원본 오디오 디스크 저장 없음. 전체 인식문은 기본 로그에서 제외 |

연속 VAD는 Whisper 추론과 다른 작업 스레드에서 수행한다. faster-whisper에 포함된 Silero ONNX 자원의 재사용을 먼저 검증하되, 내부 API 의존은 `vad.py`에 격리하고 검증한 버전을 고정한다. 단순 음량 임계값만으로 VAD를 대체하지 않는다.

Hermes 상태는 `LOADING → LISTENING → ARMED/CAPTURING → LAUNCHING → COOLDOWN → LISTENING`을 기본으로 하고, `PAUSED`, `ERROR`를 별도로 둔다.

- 기존 listener의 녹음 시작 핫키가 우선한다. 진행 중인 Hermes 수집 세션을 취소하고, 그 녹음 구간에는 호출어를 탐색하지 않는다.
- 핫키 녹음은 호출어를 말해도 기존 받아쓰기처럼 처리한다.
- 핫키 녹음/STT가 끝난 뒤 과거 후보를 버리고 새 시각부터 호출 감시를 재개한다.
- 이미 진행 중인 Whisper 추론은 즉시 선점할 수 있다고 가정하지 않는다. 취소된 탐색 결과는 버리되 현재 추론이 끝난 뒤 우선순위가 높은 작업을 시작한다.
- 모델 변경은 녹음/명령 세션이 없는 시점에만 허용하고 양쪽 UI에 로딩 상태를 알린다. 서비스가 변경을 직렬화하고 성공한 모델 설정만 저장한다. 실패 시 이전 모델 복구 또는 명시적인 ERROR 상태로 전환한다.
- Hermes CLI 응답/승인 대기는 음성 서비스와 분리한다. 확인음 재입력 구간과 나중에 추가할 TTS 재생 구간에는 재호출을 차단한다.

## 7. Hermes CLI 연동

공식 CLI 문서 기준으로 대화형 실행은 `hermes` 또는 `hermes chat`이다. 사용자가 말한 “hermes agent”를 특정 `agent` 서브커맨드로 단정하지 않는다. 실제 설치 환경의 `hermes --version`, `hermes --help`, `hermes chat --help`를 먼저 확인한다.

### 기본 동작: 터미널에서 대화 시작

- 호출만 있으면 새 터미널에서 `hermes chat`을 실행한다.
- 명령이 있으면 지원 버전에서 `hermes chat --query-file <UTF-8 파일>`로 첫 프롬프트를 전달한다. 실제 TTY를 연결하여 응답 후 대화가 유지되도록 한다.
- 실행 cwd는 설정된 프로젝트 경로로 고정한다. 트레이 앱의 우연한 현재 작업 경로를 쓰지 않는다.
- 실행 파일과 인자는 리스트로 구성하고 음성 텍스트를 셸 명령에 삽입하지 않는다. 임시 프롬프트 파일은 사용자 범위 임시 경로에 만들고 소비 완료 또는 추적 중인 프로세스 종료 후 정리한다. 프로세스 생성 직후 삭제하지 않는다.
- 1차는 이 앱이 연 Hermes 프로세스 한 개만 추적한다. 이미 실행 중이면 중복 창을 열지 않고 실행 중임을 알린다. 임의의 기존 Hermes 창 찾기/포커스/타이핑 주입은 후속 범위다.
- 기존 Hermes 인증/프로필과 도구 승인 흐름을 그대로 사용한다.

### 선택 확장: 명령 실행 결과를 앱에서 받기

설치 버전에서 지원되면 `hermes chat --oneshot --query-file <파일> --format stream-json`으로 응답 이벤트와 종료 상태를 받아 표시한다. 기존 대화에 계속 말하기는 `session_id`를 명시적으로 추적하는 별도 단계로 구현한다. 전역 latest 세션에 무조건 연결하지 않는다.

실제 점검 결과 최신 Windows 사용자 PATH의 `AppData/Local/hermes/bin/hermes.exe`에서 native Hermes v0.23.2를 확인했다. 앱도 현재 프로세스 PATH와 최신 레지스트리 PATH를 함께 조회한다. WSL 어댑터는 선택 사항이며 cwd와 프롬프트 파일을 Linux 경로로 변환하고, `bash -c` 문자열에 음성을 결합하지 않는다. 실제 WSL 실행 검증은 아직 하지 않았다.

### Hermes 전용 설정 예시

다음은 구현된 기본 설정 스키마의 예시다.

```json
{
  "wake_phrases": ["헤이 모니카", "모니카"],
  "wake_enabled": true,
  "command_start_timeout_seconds": 5.0,
  "max_command_seconds": 30.0,
  "cooldown_seconds": 3.0,
  "hermes": {
    "backend": "native",
    "executable": "hermes",
    "args": ["chat"],
    "cwd": "C:/Users/cagongman/cagongmanWS/_projects/listener",
    "mode": "interactive"
  }
}
```

Hermes 전용 설정에 STT 모델명을 복제하지 않는다. 실행 시 공통 서비스 설정을 조회한다. 키/토큰 등 Hermes 인증정보도 이 설정에 복사하지 않는다.

## 8. 구현 순서와 단계별 완료 조건

1. **환경/기준 성능 확인**: Hermes 설치 위치, CLI 옵션, native/WSL, GPU 메모리와 현재 받아쓰기 지연을 기록한다. `bench.py`로 기존 모델 기준값을 얻는다. 실제 Hermes 프롬프트 실행은 통합 검증 단계에서 한다.
2. **공통 음성 서비스 도입**: 패키지 설치 구조와 IPC, 단일 인스턴스, AudioHub, 작업 큐를 구현한다. 기존 listener를 서비스 클라이언트로 전환한다. 완료 조건은 핫키 받아쓰기·붙여넣기·모델 변경·CPU 폴백의 기존 동작 유지다.
3. **호출 탐색 구현**: 연속 VAD, Whisper 후보 인식, wakeword matcher, 두 호출 방식과 타임아웃을 구현한다. 실제 CLI 대신 기록용 launcher로 중복/오탐/명령 유실을 먼저 검증한다.
4. **Hermes 실행 연결**: 확인된 CLI 옵션으로 대화형 터미널을 열고 첫 프롬프트를 전달한다. 실행 cwd, Unicode, 인용부호, 중복 프로세스, 실행 실패를 검증한다.
5. **동시 사용/장시간 검증**: 핫키 우선 처리, 모델 변경 중 요청, 마이크 분리/재연결, 서비스 중단/재연결, 종료 시 정리를 검증한다.
6. **빌드/배포**: 기존 Listener.exe와 ListenerHermes.exe가 같은 ListenerSpeech.exe를 사용하는 배포 폴더를 만든다. CUDA 런타임은 가능한 한 공통 배포 자원을 사용하고 모델 파일은 계속 외부 캐시를 사용한다. 구버전 Listener.exe는 자체 모델을 로드하므로 새 버전으로 교체해야 실제 메모리 공유가 된다.

## 9. 검증 기준

| 범위 | 필수 확인 |
|---|---|
| 호출어 | 두 호출어, 띄어쓰기/문장부호, 단독 호출, 호출+명령, 연속 발화 |
| 오탐 | “하모니카”, “모니카가…”, 문장 중간의 이름, TV/회의/키보드 소음, 무음 |
| 세션 | 판별 중 들어온 다음 발화 보존, timeout 경계, 겹치는 창에서 정확히 한 번 실행, 버퍼 초과/만료 시 미실행 |
| CLI | 공백 있는 경로, 한국어/따옴표/달러/백틱이 포함된 프롬프트, 실행 파일 없음, 시작/실행 실패, 중복 호출 |
| 기존 앱 | 핫키 토글, 클립보드 복원, 터미널 입력, 모델 변경, CUDA→CPU 폴백 |
| 공유 | 동시 실행 시 Whisper 인스턴스 한 개, 마이크 스트림 한 개, 양쪽 종료 순서와 서비스 자동 시작 경쟁 |
| 성능 | 호출어 종료→확인음, 명령 종료→CLI 생성, 추론 실시간 비율, 유휴/대화 중 CPU·GPU·VRAM, 탐색 큐 크기 |

유닛 검증은 matcher, 세션 경계, CLI 인자 생성, 큐 만료와 중복 제거에 집중한다. 실제 마이크/GPU/터미널은 별도 통합 검증이 필요하다.

초기 평가용으로 두 호출어 각각 30회 이상, 연결/분리 명령 각각 20회 이상, 주변 음성 1시간을 수집한다. 호출 성공률 95% 이상, 주변 음성 오실행 0회를 초기 목표로 삼되 이는 출시 성능 보장이 아니다. GPU 환경의 호출어 종료→확인음 p95 2초 이내를 목표로 측정하고, CPU 폴백은 별도 결과로 보고한다. 기존 받아쓰기 지연 증가도 기준값과 비교한다.

현재 모델에서 목표를 만족하지 못하면 순서대로 탐색 창/beam_size/VAD 종료값을 조정하고, 이후 전용 한국어 웨이크 detector 도입을 판단한다. 대기 중 모든 발화를 large-v3로 해석하는 비용과 “모니카” 단독 호출의 오탐을 주요 판단 지표로 삼는다.

## 10. 확인한 공식 자료

- [faster-whisper 공식 저장소: VAD 및 추론 옵션](https://github.com/SYSTRAN/faster-whisper)
- [faster-whisper VAD 구현](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/vad.py)
- [Hermes CLI 공식 명령 레퍼런스: chat, query-file, oneshot, stream-json](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)

원격 문서는 최신판이므로 실제 구현 시 로컬 설치 버전과 대조한다.

# Listener

`Ctrl+Alt+M` 토글로 음성을 녹음하고 Whisper(Windows: faster-whisper, macOS: mlx-whisper)로 인식해 현재 커서 위치에 텍스트를 붙여넣는 트레이(메뉴바) 앱. Windows와 macOS를 지원합니다.

## 요구 사항

- Windows 10/11 또는 macOS (Apple Silicon에서 검증)
- Python 3.11+ (소스 실행 시)
- NVIDIA GPU (Windows 선택, CUDA 12). 없거나 초기화 실패 시 CPU(int8)로 자동 폴백. macOS(Apple Silicon)는 mlx-whisper로 GPU(Metal)에서 동작
- 마이크

## 설치 (소스 실행)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`nvidia-cublas-cu12`, `nvidia-cudnn-cu12` 가 pip로 함께 설치되므로 별도 CUDA Toolkit 설치는 필요 없습니다.

macOS (시스템 Python 3.9는 지원하지 않으므로 Homebrew 등으로 3.11+ 설치):

```
brew install python@3.12
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 의 플랫폼 마커에 따라 Windows는 `keyboard` + CUDA 런타임, macOS는 `pynput` + `mlx-whisper` 가 설치됩니다.

## 실행

```
python -m listener
```

1. 트레이 아이콘이 회색(모델 로딩)에서 초록(대기)으로 바뀌면 준비 완료
2. 텍스트를 입력할 곳에 커서를 두고 `Ctrl+Alt+M` → 비프음과 함께 녹음 시작 (아이콘 빨강)
3. 다시 `Ctrl+Alt+M` → 녹음 종료, 인식 중 (아이콘 노랑)
4. 인식된 텍스트가 커서 위치에 붙여넣기됨. 기존 클립보드 내용은 복원됨

macOS에서는 `Ctrl+Option+M`, 붙여넣기는 `Cmd+V`, 비프음 대신 시스템 사운드(Tink/Pop)를 씁니다. 처음 실행 시 아래 권한을 실행 주체(소스 실행: 터미널 앱, 빌드: Listener.app)에 허용해야 합니다 (시스템 설정 → 개인정보 보호 및 보안):

- **마이크**: 녹음 (첫 녹음 때 시스템 팝업)
- **손쉬운 사용**: 전역 단축키 + `Cmd+V` 키 입력 전송 (빌드 앱은 시작 시 시스템 팝업을 띄움)
- **입력 모니터링**: 목록에 나타나면 함께 허용

빌드 앱은 손쉬운 사용 권한이 허용될 때까지 기다렸다가 단축키를 자동으로 켭니다(재시작 불필요). 대기 중이면 로그에 `Waiting for Accessibility permission` 이 남습니다. 소스 실행은 권한 허용 후 다시 실행하세요.

트레이 메뉴:
- **모델**: medium / large-v3 전환, macOS는 large-v3-turbo 추가 (선택은 `config.json`에 저장됨)
- **종료**

## 설정 (`config.json`)

| 키 | 기본값 | 설명 |
|---|---|---|
| model | large-v3 | `medium` / `large-v3` / `large-v3-turbo`(macOS 메뉴에만 표시) |
| device | auto | `auto` / `cuda` / `cpu` / `mlx`. macOS의 `auto`는 mlx(GPU), `cpu`는 faster-whisper(CPU int8) |
| compute_type | float16 | GPU용 연산 정밀도. CPU 폴백 시 자동으로 int8 |
| language | ko | 인식 언어 (ISO 639-1) |
| hotkey | ctrl+alt+m | 전역 단축키 ([keyboard](https://github.com/boppreh/keyboard) 표기, macOS는 `cmd`/`option` 도 사용 가능) |
| beam_size | 5 | 빔 서치 크기. 낮추면 빠르고 정확도 하락. mlx는 greedy만 지원해 무시됨 |
| vad_filter | true | 무음 구간 제거 |
| sample_rate | 16000 | 녹음 샘플레이트 (Whisper 입력 규격) |

## 모델 저장 위치

Whisper 모델은 Hugging Face 캐시에 저장되며 첫 실행 시 자동 다운로드됩니다.

```
%USERPROFILE%\.cache\huggingface\hub\          (macOS: ~/.cache/huggingface/hub/)
├── models--Systran--faster-whisper-medium     약 1.4 GB
└── models--Systran--faster-whisper-large-v3   약 3.5 GB
```

macOS(mlx)는 별도 형식의 모델을 받습니다.

```
~/.cache/huggingface/hub/
├── models--mlx-community--whisper-medium-mlx     약 1.4 GB
├── models--mlx-community--whisper-large-v3-mlx   약 2.9 GB
└── models--mlx-community--whisper-large-v3-turbo 약 1.5 GB
```

- 소스 실행과 exe 실행이 같은 캐시를 공유합니다.
- 위치를 바꾸려면 환경변수 `HF_HOME` 을 설정하세요 (예: `HF_HOME=D:\models` → `D:\models\hub\...`).
- 오프라인 PC에서 쓰려면 위 폴더를 통째로 복사하면 됩니다.

## 실행파일 빌드

```
python build.py
```

`dist/Listener/Listener.exe` 가 생성됩니다 (폴더 약 2.2GB, CUDA 런타임 포함). `dist/Listener` 폴더째로 복사해서 사용하세요.

- 설정: exe 옆의 `config.json`
- 로그: exe 옆의 `listener.log` (콘솔 창 없음)
- Whisper 모델은 exe에 포함되지 않음 → 위 "모델 저장 위치" 참고
- 빌드 정의: `listener.spec`, 진입점: `run.py`

macOS:

```
export LISTENER_SIGN_IDENTITY="Apple Development: 이름 (XXXXXXXXXX)"   # 선택, 아래 참고
python build.py
```

macOS에서 빌드하면 `dist/Listener.app` (약 600MB, mlx 포함) 이 생성됩니다. 크로스 빌드는 안 되므로 각 OS에서 빌드하세요.

- 설정/로그: `~/Library/Application Support/Listener/` (`config.json`, `listener.log`). 앱 번들 안에 쓰면 코드 서명이 깨지므로 분리. `config.json` 이 없으면 기본값 사용
- 메뉴바 전용 앱(`LSUIElement`)이라 Dock 아이콘이 없습니다
- **서명**: `LISTENER_SIGN_IDENTITY` 를 설정하면 그 인증서로 `codesign` 합니다. 사용 가능한 인증서는 `security find-identity -v -p codesigning` 으로 확인하세요. 설정하지 않으면 ad-hoc 서명이 되는데, ad-hoc은 빌드마다 서명 해시가 바뀌어 macOS가 손쉬운 사용/입력 모니터링 권한을 **매 빌드마다 초기화**합니다
- 인증서 서명 빌드도 공증(notarization)은 되지 않았으므로 다른 Mac에서는 Gatekeeper 경고가 뜹니다(우클릭 → 열기)

## 모델 비교

```
python bench.py --record 10    # 10초 녹음 -> sample.wav
python bench.py sample.wav     # medium, large-v3 로드/인식 시간 및 결과 비교
python bench.py sample.wav --models medium   # 특정 모델만
```

참고 측정 (Apple Silicon, 6.8초 한국어 샘플, 인식 시간):

| 모델 | faster-whisper CPU int8 | mlx (GPU) |
|---|---|---|
| medium | 2.79s | 0.71s |
| large-v3 | - | 1.32s |
| large-v3-turbo | - | 0.73s |

## 프로젝트 구조

```
listener/
├── app.py          # 상태머신(LOADING/IDLE/RECORDING/PROCESSING) + 트레이 UI
├── audio.py        # sounddevice 마이크 녹음
├── transcriber.py  # faster-whisper/mlx-whisper 래퍼, GPU→CPU 폴백, CUDA DLL 경로 등록
├── inject.py       # 클립보드 백업 → 붙여넣기(Ctrl+V / macOS Cmd+V) → 복원
├── hotkey.py       # 전역 단축키 (Windows: keyboard, macOS: pynput)
└── config.py       # config.json 로드/저장
```

## 문제 해결

- **특정 창(터미널 등)에서 단축키가 안 먹음**: 그 창이 관리자 권한으로 실행 중인 경우입니다. Windows는 일반 권한 프로세스의 키보드 훅/키 전송이 관리자 창에 닿지 않게 막습니다(UIPI). exe는 실행 시 UAC로 관리자 권한을 요청하며, 소스 실행은 관리자 터미널에서 `python -m listener` 를 실행하세요.
- **키보드 입력이 안 됨**: 핫키 등록 시 `suppress=True` 를 쓰면 전체 키 입력이 막힙니다. 현재는 `suppress=False` 로 고정되어 있습니다.
- **`cublas64_12.dll is not found`**: `nvidia-cublas-cu12` 가 설치되어 있는지 확인하세요. `transcriber.py` 가 `site-packages/nvidia/*/bin` 을 DLL 검색 경로에 등록합니다.
- **GPU가 아닌 CPU로 동작**: `listener.log` 에서 `CUDA load failed` 경고를 확인하세요. `device`를 `cuda`로 고정하면 폴백 없이 오류가 출력됩니다.
- **인식 결과가 비어 있음**: 녹음이 0.3초 미만이거나 VAD가 전부 무음으로 판단한 경우입니다. `vad_filter`를 `false`로 바꿔 확인해 보세요.
- **macOS: 단축키가 반응 없음**: 로그에 `not trusted` 또는 `Waiting for Accessibility permission` 이 있으면 손쉬운 사용 권한이 없는 것입니다. 목록에 Listener가 켜져 있는데도 그렇다면 다른 빌드(ad-hoc 서명)에 대한 권한이 남은 것이니 초기화 후 다시 허용하세요:
  ```
  tccutil reset Accessibility com.cagongman.listener
  tccutil reset ListenEvent com.cagongman.listener
  ```
- **macOS: 인식은 되는데 붙여넣기가 안 됨**: 손쉬운 사용 권한이 없는 경우입니다. 로그에 `Transcribed` 줄이 있는데 텍스트가 안 들어오면 이 경우입니다.
- **macOS: 녹음이 무음 / 인식 결과가 빔**: 마이크 권한을 확인하세요.
- **macOS: `There is no Stream(gpu, 1) in current thread`**: MLX 호출이 여러 스레드에서 실행된 경우입니다. `transcriber.py` 는 모든 MLX 호출을 전용 워커 스레드 하나에서 실행합니다. 이 구조를 바꾸지 마세요.
- **macOS: 앱이 두 번 실행됨 / 종료 후 다시 안 열림**: `run.py` 의 `multiprocessing.freeze_support()` 가 빠지면 빌드 앱의 자식 프로세스가 앱 전체를 다시 실행합니다. 남은 프로세스는 `pkill -9 -f MacOS/Listener` 로 정리하세요.

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

- **마이크**: 녹음
- **입력 모니터링**: 전역 단축키
- **손쉬운 사용**: `Cmd+V` 키 입력 전송

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

macOS에서 같은 명령으로 빌드하면 `dist/Listener.app` (약 600MB, mlx 포함) 이 생성됩니다. 크로스 빌드는 안 되므로 각 OS에서 빌드하세요.

- 설정/로그: `~/Library/Application Support/Listener/` (`config.json`, `listener.log`). 앱 번들 안에 쓰면 코드 서명이 깨지므로 분리. `config.json` 이 없으면 기본값 사용
- 메뉴바 전용 앱(`LSUIElement`)이라 Dock 아이콘이 없습니다
- 서명되지 않은 빌드라 다른 Mac에서는 Gatekeeper 경고가 뜹니다(우클릭 → 열기). 재빌드하면 권한을 다시 허용해야 할 수 있습니다

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
- **macOS: 단축키가 반응 없음**: 입력 모니터링 권한을 확인하세요. 권한 목록에 이미 있다면 껐다 켜거나 제거 후 다시 추가합니다(재빌드 후 흔함).
- **macOS: 인식은 되는데 붙여넣기가 안 됨**: 손쉬운 사용 권한이 없는 경우입니다. 로그에 `Transcribed` 줄이 있는데 텍스트가 안 들어오면 이 경우입니다.
- **macOS: 녹음이 무음**: 마이크 권한을 확인하세요.

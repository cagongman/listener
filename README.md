# Listener

`Ctrl+Alt+M` 토글로 음성을 녹음하고 faster-whisper로 인식해 현재 커서 위치에 텍스트를 붙여넣는 Windows 트레이 앱.

## 요구 사항

- Windows 10/11
- Python 3.11+ (소스 실행 시)
- NVIDIA GPU (선택, CUDA 12). 없거나 초기화 실패 시 CPU(int8)로 자동 폴백
- 마이크

## 설치 (소스 실행)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`nvidia-cublas-cu12`, `nvidia-cudnn-cu12` 가 pip로 함께 설치되므로 별도 CUDA Toolkit 설치는 필요 없습니다.

## 실행

```
python -m listener
```

1. 트레이 아이콘이 회색(모델 로딩)에서 초록(대기)으로 바뀌면 준비 완료
2. 텍스트를 입력할 곳에 커서를 두고 `Ctrl+Alt+M` → 비프음과 함께 녹음 시작 (아이콘 빨강)
3. 다시 `Ctrl+Alt+M` → 녹음 종료, 인식 중 (아이콘 노랑)
4. 인식된 텍스트가 커서 위치에 붙여넣기됨. 기존 클립보드 내용은 복원됨

트레이 메뉴:
- **모델**: medium / large-v3 전환 (선택은 `config.json`에 저장됨)
- **종료**

## 설정 (`config.json`)

| 키 | 기본값 | 설명 |
|---|---|---|
| model | large-v3 | `medium` 또는 `large-v3` |
| device | auto | `auto` / `cuda` / `cpu` |
| compute_type | float16 | GPU용 연산 정밀도. CPU 폴백 시 자동으로 int8 |
| language | ko | 인식 언어 (ISO 639-1) |
| hotkey | ctrl+alt+m | 전역 단축키 ([keyboard](https://github.com/boppreh/keyboard) 표기) |
| beam_size | 5 | 빔 서치 크기. 낮추면 빠르고 정확도 하락 |
| vad_filter | true | 무음 구간 제거 |
| sample_rate | 16000 | 녹음 샘플레이트 (Whisper 입력 규격) |

## 모델 저장 위치

Whisper 모델은 Hugging Face 캐시에 저장되며 첫 실행 시 자동 다운로드됩니다.

```
%USERPROFILE%\.cache\huggingface\hub\
├── models--Systran--faster-whisper-medium     약 1.4 GB
└── models--Systran--faster-whisper-large-v3   약 3.5 GB
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

## 모델 비교

```
python bench.py --record 10    # 10초 녹음 -> sample.wav
python bench.py sample.wav     # medium, large-v3 로드/인식 시간 및 결과 비교
python bench.py sample.wav --models medium   # 특정 모델만
```

## 프로젝트 구조

```
listener/
├── app.py          # 상태머신(LOADING/IDLE/RECORDING/PROCESSING) + 트레이 UI
├── audio.py        # sounddevice 마이크 녹음
├── transcriber.py  # faster-whisper 래퍼, GPU→CPU 폴백, CUDA DLL 경로 등록
├── inject.py       # 클립보드 백업 → 붙여넣기(Ctrl+V) → 복원
├── hotkey.py       # 전역 단축키
└── config.py       # config.json 로드/저장
```

## 문제 해결

- **특정 창(터미널 등)에서 단축키가 안 먹음**: 그 창이 관리자 권한으로 실행 중인 경우입니다. Windows는 일반 권한 프로세스의 키보드 훅/키 전송이 관리자 창에 닿지 않게 막습니다(UIPI). exe는 실행 시 UAC로 관리자 권한을 요청하며, 소스 실행은 관리자 터미널에서 `python -m listener` 를 실행하세요.
- **키보드 입력이 안 됨**: 핫키 등록 시 `suppress=True` 를 쓰면 전체 키 입력이 막힙니다. 현재는 `suppress=False` 로 고정되어 있습니다.
- **`cublas64_12.dll is not found`**: `nvidia-cublas-cu12` 가 설치되어 있는지 확인하세요. `transcriber.py` 가 `site-packages/nvidia/*/bin` 을 DLL 검색 경로에 등록합니다.
- **GPU가 아닌 CPU로 동작**: `listener.log` 에서 `CUDA load failed` 경고를 확인하세요. `device`를 `cuda`로 고정하면 폴백 없이 오류가 출력됩니다.
- **인식 결과가 비어 있음**: 녹음이 0.3초 미만이거나 VAD가 전부 무음으로 판단한 경우입니다. `vad_filter`를 `false`로 바꿔 확인해 보세요.

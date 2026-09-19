"""medium vs large-v3 비교.

  python bench.py --record 10      # 10초 녹음 -> sample.wav
  python bench.py sample.wav       # 두 모델로 인식, 시간/결과 출력
"""
import argparse
import sys
import time
import wave

import numpy as np

from listener.config import AVAILABLE_MODELS, load_config
from listener.transcriber import Transcriber


def record(seconds: int, path: str, sample_rate: int) -> None:
    import sounddevice as sd

    print(f"{seconds}초 녹음 중... 말하세요.")
    audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="float32")
    sd.wait()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes((audio[:, 0] * 32767).astype(np.int16).tobytes())
    print(f"저장: {path}")


def load_wav(path: str, sample_rate: int) -> np.ndarray:
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1 and w.getframerate() == sample_rate and w.getsampwidth() == 2, \
            f"16kHz mono 16-bit wav 필요 (현재 {w.getnchannels()}ch {w.getframerate()}Hz)"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("wav", nargs="?", default="sample.wav")
    p.add_argument("--record", type=int, metavar="SECONDS")
    p.add_argument("--models", nargs="+", default=AVAILABLE_MODELS)
    args = p.parse_args()
    cfg = load_config()

    if args.record:
        record(args.record, args.wav, cfg.sample_rate)
        return

    audio = load_wav(args.wav, cfg.sample_rate)
    print(f"오디오 길이: {audio.size / cfg.sample_rate:.1f}s\n")

    for name in args.models:
        t = Transcriber(name, cfg.device, cfg.compute_type, cfg.language,
                        cfg.beam_size, cfg.vad_filter, cfg.sample_rate)
        t0 = time.perf_counter()
        t.load()
        load_s = time.perf_counter() - t0
        t.transcribe(audio[: cfg.sample_rate])  # warm-up
        t0 = time.perf_counter()
        text = t.transcribe(audio)
        run_s = time.perf_counter() - t0
        print(f"[{name}] device={t.device}/{t.compute_type} load={load_s:.1f}s transcribe={run_s:.2f}s")
        print(f"  {text}\n")
        del t


if __name__ == "__main__":
    sys.exit(main())

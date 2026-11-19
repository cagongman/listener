"""Streaming adapter for the Silero v6 resource shipped with faster-whisper 1.2.1."""
from dataclasses import dataclass

import numpy as np

BLOCK_SIZE = 512
SAMPLE_RATE = 16000


class StreamingVad:
    def __init__(self):
        from faster_whisper.vad import get_vad_model
        self.session = get_vad_model().session
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros(64, dtype=np.float32)

    def __call__(self, audio: np.ndarray) -> float:
        if audio.size != BLOCK_SIZE:
            raise ValueError("Silero streaming input must contain 512 samples")
        output, self.h, self.c = self.session.run(None, {
            "input": np.concatenate((self.context, audio))[None, :],
            "h": self.h, "c": self.c,
        })
        self.context = audio[-64:].copy()
        return float(np.ravel(output)[-1])


@dataclass(frozen=True)
class SpeechChunk:
    start: int
    end: int
    final: bool
    overflow: bool = False


class Segmenter:
    """Endpointing with hysteresis, pre-roll, partial windows and a hard limit."""
    def __init__(self, *, sample_rate=16000, silence_seconds=0.8,
                 max_seconds=30, window_seconds=3.0, pre_roll_seconds=0.5):
        self.rate = sample_rate
        self.silence = int(silence_seconds * sample_rate)
        self.maximum = int(max_seconds * sample_rate)
        self.window = int(window_seconds * sample_rate) if window_seconds else 0
        self.pre_roll = int(pre_roll_seconds * sample_rate)
        self.reset()

    def reset(self):
        self.start: int | None = None
        self.last_voice = 0
        self.last_candidate = 0

    def feed(self, start: int, end: int, probability: float, oldest: int) -> list[SpeechChunk]:
        events = []
        if probability >= (0.35 if self.start is not None else 0.5):
            if self.start is None:
                self.start = max(oldest, start - self.pre_roll)
                self.last_candidate = end
            self.last_voice = end
        if self.start is None:
            return events
        if end - self.last_voice >= self.silence:
            events.append(SpeechChunk(self.start, end, True))
            self.reset()
        elif end - self.start >= self.maximum:
            events.append(SpeechChunk(self.start, end, True, True))
            self.reset()
        elif self.window and end - self.last_candidate >= self.window:
            window_start = max(self.start, end - self.window - self.pre_roll)
            events.append(SpeechChunk(window_start, end, False))
            self.last_candidate = end
        return events

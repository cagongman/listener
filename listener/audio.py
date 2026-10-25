import logging
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


def input_devices(rescan: bool = False) -> list[str]:
    if rescan:
        # PortAudio caches the device list at init; re-init to see headsets connected after launch.
        sd._terminate()
        sd._initialize()
    names = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    return list(dict.fromkeys(names))


class Recorder:
    def __init__(self, sample_rate: int = 16000, device: str = ""):
        self.sample_rate = sample_rate
        self.device = device or None
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time, status):
        with self._lock:
            self._chunks.append(indata[:, 0].copy())

    def start(self) -> None:
        if self._stream is not None:
            return
        self._chunks = []
        try:
            stream = self._open(self.device)
        except ValueError:  # named device unplugged/renamed: record from the system default instead
            log.warning("Input device %r not found; using system default", self.device)
            stream = self._open(None)
        stream.start()
        self._stream = stream

    def _open(self, device: str | None) -> sd.InputStream:
        return sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=device,
            callback=self._callback,
        )

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            chunks = self._chunks
            self._chunks = []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

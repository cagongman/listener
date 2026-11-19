import logging
import threading
import queue
from collections import deque

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


class AudioRing:
    """Frame-addressed, bounded PCM storage. Missing ranges fail explicitly."""
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.end = 0
        self._chunks = deque()
        self._lock = threading.RLock()

    @property
    def oldest(self) -> int:
        with self._lock:
            return self._chunks[0][0] if self._chunks else self.end

    def append(self, audio: np.ndarray) -> tuple[int, int]:
        with self._lock:
            start = self.end
            self.end += audio.size
            self._chunks.append((start, audio.copy()))
            while self._chunks and self._chunks[0][0] + self._chunks[0][1].size <= self.end - self.capacity:
                self._chunks.popleft()
            if self._chunks and self._chunks[0][0] < self.end - self.capacity:
                first, samples = self._chunks.popleft()
                cut = self.end - self.capacity - first
                self._chunks.appendleft((first + cut, samples[cut:]))
            return start, self.end

    def read(self, start: int, end: int | None = None) -> np.ndarray:
        with self._lock:
            end = self.end if end is None else end
            if not self.oldest <= start <= end <= self.end:
                raise ValueError("Requested audio is no longer in the buffer")
            pieces = [data[max(0, start - first): min(data.size, end - first)]
                      for first, data in self._chunks
                      if first < end and first + data.size > start]
            return np.concatenate(pieces) if pieces else np.empty(0, dtype=np.float32)

    def clear(self):
        with self._lock:
            self._chunks.clear()


class AudioHub:
    """The service's only InputStream. Callbacks copy into a bounded queue."""
    def __init__(self, sample_rate=16000, device=""):
        if sample_rate != 16000:
            raise ValueError("Shared speech service requires sample_rate=16000")
        self.sample_rate = sample_rate
        self.device = device or None
        self.ring = AudioRing(sample_rate * 45)
        self.blocks: queue.Queue = queue.Queue(maxsize=128)
        self.overrun = threading.Event()
        self._stream = None

    @property
    def running(self):
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status):
        if status:
            self.overrun.set()
        try:
            self.blocks.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.overrun.set()

    def start(self):
        if self._stream is not None:
            if not self._stream.active:
                raise RuntimeError("Microphone stream stopped unexpectedly")
            return
        self.ring.clear()
        self.overrun.clear()
        while not self.blocks.empty():
            try:
                self.blocks.get_nowait()
            except queue.Empty:
                break
        try:
            stream = self._open(self.device)
        except ValueError:
            if not self.device:
                raise
            log.warning("Input device %r not found; using system default", self.device)
            stream = self._open(None)
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self._stream = stream

    def _open(self, device):
        return sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                              device=device, blocksize=512, callback=self._callback)

    def close(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

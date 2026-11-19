import gc
import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np

_dll_handles = []

def _register_nvidia_dlls() -> None:
    if sys.platform != "win32":
        return
    roots = list(sys.path)
    if getattr(sys, "frozen", False):
        roots.insert(0, sys._MEIPASS)
    for site in roots:
        nvidia_dir = Path(site) / "nvidia"
        if not nvidia_dir.is_dir():
            continue
        for bin_dir in nvidia_dir.glob("*/bin"):
            _dll_handles.append(os.add_dll_directory(str(bin_dir)))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


_register_nvidia_dlls()

from faster_whisper import WhisperModel  # noqa: E402

log = logging.getLogger(__name__)

MIN_AUDIO_SECONDS = 0.3


class Transcriber:
    def __init__(self, model: str, device: str, compute_type: str, language: str,
                 beam_size: int, vad_filter: bool, sample_rate: int):
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.sample_rate = sample_rate
        self._model: WhisperModel | None = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        with self._lock:
            self._model = self._create_model()

    def _create_model(self) -> WhisperModel:
        if self.device in ("auto", "cuda"):
            try:
                log.info("Loading %s on cuda/%s", self.model_name, self.compute_type)
                model = WhisperModel(self.model_name, device="cuda", compute_type=self.compute_type)
                self.device = "cuda"
                return model
            except Exception as e:
                if self.device == "cuda":
                    raise
                log.warning("CUDA load failed (%s); falling back to CPU int8", e)
        log.info("Loading %s on cpu/int8", self.model_name)
        model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        self.device = "cpu"
        self.compute_type = "int8"
        return model

    def switch_model(self, model_name: str) -> None:
        with self._lock:
            previous = self.model_name
            self._model = None
            gc.collect()
            self.model_name = model_name
            try:
                self._model = self._create_model()
            except Exception:
                self.model_name = previous
                self._model = self._create_model()
                raise

    def transcribe(self, audio: np.ndarray) -> str:
        return self.transcribe_result(audio)["text"]

    def transcribe_result(self, audio: np.ndarray, *, wake: bool = False) -> dict:
        if audio.size < MIN_AUDIO_SECONDS * self.sample_rate:
            return {"text": "", "avg_logprob": -100.0, "no_speech_prob": 1.0}
        with self._lock:
            if self._model is None:
                self._model = self._create_model()
            segments, _ = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=1 if wake else self.beam_size,
                vad_filter=self.vad_filter,
                condition_on_previous_text=False,
            )
            segments = list(segments)
            return {
                "text": " ".join(s.text.strip() for s in segments).strip(),
                "avg_logprob": min((s.avg_logprob for s in segments), default=-100.0),
                "no_speech_prob": max((s.no_speech_prob for s in segments), default=1.0),
            }

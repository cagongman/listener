import gc
import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np


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
            os.add_dll_directory(str(bin_dir))
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
            self._model = None
            gc.collect()
            self.model_name = model_name
            self._model = self._create_model()

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < MIN_AUDIO_SECONDS * self.sample_rate:
            return ""
        with self._lock:
            if self._model is None:
                self._model = self._create_model()
            segments, _ = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
            )
            return " ".join(s.text.strip() for s in segments).strip()

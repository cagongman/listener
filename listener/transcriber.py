import gc
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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
from faster_whisper.vad import collect_chunks, get_speech_timestamps  # noqa: E402

log = logging.getLogger(__name__)

MIN_AUDIO_SECONDS = 0.3

# Apple Silicon GPU (Metal) weights; device "auto"/"mlx" on macOS uses these, "cpu" stays on faster-whisper.
MLX_REPOS = {
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


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
        # MLX GPU streams are per-thread, so every mlx call must run on this one worker.
        self._mlx = ThreadPoolExecutor(1, thread_name_prefix="mlx") if sys.platform == "darwin" else None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        with self._lock:
            self._model = self._create_model()

    def _create_model(self):
        if sys.platform == "darwin" and self.device in ("auto", "mlx"):
            from mlx_whisper.transcribe import ModelHolder, mx

            repo = MLX_REPOS[self.model_name]
            log.info("Loading %s on mlx (%s)", self.model_name, repo)
            self._mlx.submit(ModelHolder.get_model, repo, mx.float16).result()  # download + cache for transcribe()
            self.device = "mlx"
            self.compute_type = "float16"
            return repo
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
            if self.device == "mlx":
                from mlx_whisper.transcribe import ModelHolder

                ModelHolder.model = None
            gc.collect()
            self.model_name = model_name
            self._model = self._create_model()

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < MIN_AUDIO_SECONDS * self.sample_rate:
            return ""
        with self._lock:
            if self._model is None:
                self._model = self._create_model()
            if self.device == "mlx":
                return self._transcribe_mlx(audio)
            segments, _ = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
            )
            return " ".join(s.text.strip() for s in segments).strip()

    def _transcribe_mlx(self, audio: np.ndarray) -> str:
        import mlx_whisper

        if self.vad_filter:
            # mlx-whisper has no VAD; reuse faster-whisper's Silero VAD so silence doesn't hallucinate text.
            speech = get_speech_timestamps(audio, sampling_rate=self.sample_rate)
            if not speech:
                return ""
            audio = np.concatenate(collect_chunks(audio, speech, sampling_rate=self.sample_rate)[0])
        # beam_size is ignored: mlx-whisper only implements greedy decoding.
        result = self._mlx.submit(
            mlx_whisper.transcribe, audio, path_or_hf_repo=self._model, language=self.language,
        ).result()
        return result["text"].strip()

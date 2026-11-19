"""One microphone and one Whisper model for both desktop clients."""
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from multiprocessing.connection import Listener
from multiprocessing import AuthenticationError

import numpy as np

from .audio import AudioHub
from .config import AVAILABLE_MODELS, Config, load_config, save_config
from .speech_protocol import ServiceLock, auth_key, endpoint, receive, runtime_dir, send
from .speech_queue import Job, JobQueue
from .vad import BLOCK_SIZE, Segmenter, StreamingVad

log = logging.getLogger(__name__)


class Peer:
    def __init__(self, connection):
        self.connection = connection
        self.id = uuid.uuid4().hex
        self.subscribed = False
        self.closed = False
        self._close_lock = threading.Lock()
        self.outbox = queue.Queue(maxsize=128)
        threading.Thread(target=self._write, daemon=True).start()

    def emit(self, message):
        if not self.closed:
            try:
                self.outbox.put_nowait(message)
            except queue.Full:
                self.close()

    def reply(self, request_id, result=None, error=None):
        self.emit({"type": "response", "request_id": request_id, "ok": error is None,
                   "result": result or {}, "error": error})

    def _write(self):
        try:
            while not self.closed:
                message = self.outbox.get()
                if message is None:
                    break
                send(self.connection, message)
        except (OSError, EOFError, ValueError):
            pass
        finally:
            self.close()

    def close(self):
        with self._close_lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.outbox.put_nowait(None)
            except queue.Full:
                pass
            self.connection.close()


@dataclass
class Dictation:
    peer: Peer
    chunks: list = field(default_factory=list)
    samples: int = 0
    error: str = ""


@dataclass
class Capture:
    peer: Peer
    request_id: str
    session_id: str
    cursor: int
    deadline: float
    segmenter: Segmenter
    vad: object


class SpeechService:
    def __init__(self, cfg: Config, *, transcriber=None, hub=None, vad_factory=StreamingVad):
        if cfg.sample_rate != 16000:
            raise ValueError("sample_rate must be 16000 for the shared speech service")
        if transcriber is None:
            from .transcriber import Transcriber
            transcriber = Transcriber(cfg.model, cfg.device, cfg.compute_type, cfg.language,
                                      cfg.beam_size, cfg.vad_filter, cfg.sample_rate)
        self.cfg = cfg
        self.transcriber = transcriber
        self.hub = hub or AudioHub(cfg.sample_rate)
        self.vad_factory = vad_factory
        self.vad = None
        self.segmenter = Segmenter()
        self.jobs = JobQueue()
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.listening = threading.Event()
        self.peers: dict[str, Peer] = {}
        self.state = "loading"
        self.error = ""
        self.generation = 0
        self.dictation: Dictation | None = None
        self.capture: Capture | None = None
        self.processing: Job | None = None
        self.model_pending = False
        self.last_client = time.monotonic()

    @property
    def busy(self):
        return bool(self.dictation or self.capture or self.processing or self.model_pending)

    def status(self):
        return {"state": self.state, "error": self.error, "model": self.transcriber.model_name,
                "device": self.transcriber.device, "busy": self.busy,
                "sample_rate": 16000, "pid": os.getpid()}

    def broadcast(self, kind, **payload):
        for peer in list(self.peers.values()):
            peer.emit({"type": kind, **payload})

    def _invalidate(self):
        self.generation += 1
        self.jobs.discard(lambda job: job.kind == "wake")
        self.segmenter.reset()
        if self.vad is not None:
            self.vad.reset()

    def _start_audio(self):
        self.hub.start()

    def _cancel_capture(self, reason="cancelled"):
        capture, self.capture = self.capture, None
        if capture is not None:
            capture.peer.emit({"type": "cancelled", "session_id": capture.session_id,
                               "reason": reason})

    def request(self, peer: Peer, message: dict):
        """Short control requests; inference completion replies come from the worker."""
        request_id = message.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
            raise ValueError("Invalid request_id")
        kind = message["type"]
        with self.lock:
            if kind == "status":
                peer.reply(request_id, self.status())
                return
            if kind == "detach":
                peer.close()
                return
            if self.state != "ready":
                raise RuntimeError(self.error or "Speech model is not ready")
            if kind == "begin_dictation":
                log.info("Dictation requested: peer=%s", peer.id)
                if self.dictation or self.model_pending or (self.processing and self.processing.kind == "dictation"):
                    raise RuntimeError("Speech service is busy")
                self._start_audio()
                if self.processing:
                    self.processing.peer.emit({"type": "cancelled", "session_id": self.processing.session_id,
                                               "reason": "dictation"})
                    self.processing = None
                self._cancel_capture("dictation")
                self._invalidate()
                self.dictation = Dictation(peer)
                self.broadcast("wake_paused", reason="dictation")
                peer.reply(request_id)
            elif kind == "end_dictation":
                recording = self.dictation
                if recording is None or recording.peer is not peer:
                    raise RuntimeError("This client does not own a recording")
                self.dictation = None
                self._invalidate()
                if recording.error:
                    self.broadcast("wake_resumed")
                    raise RuntimeError(recording.error)
                audio = np.concatenate(recording.chunks) if recording.chunks else np.empty(0, np.float32)
                job = Job("dictation", peer, request_id, audio=audio)
                self.processing = job
                try:
                    self.jobs.put(job)
                except RuntimeError:
                    self.processing = None
                    raise
            elif kind == "subscribe_wake":
                enabled = message.get("enabled", True)
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be a boolean")
                if enabled:
                    if any(p.subscribed and p is not peer for p in self.peers.values()):
                        raise RuntimeError("Another Hermes client already owns wake detection")
                    if self.vad is None:
                        self.vad = self.vad_factory()
                    self._start_audio()
                peer.subscribed = enabled
                log.info("Wake subscription: peer=%s enabled=%s frame=%s", peer.id, enabled, self.hub.ring.end)
                if not enabled:
                    if self.capture and self.capture.peer is peer:
                        self._cancel_capture()
                    self.jobs.discard(lambda job: job.kind == "wake" and job.peer is peer)
                self._invalidate()
                peer.reply(request_id, {"enabled": enabled, "frame": self.hub.ring.end})
            elif kind == "arm_command":
                if self.busy:
                    raise RuntimeError("Speech service is busy")
                if not peer.subscribed:
                    raise RuntimeError("Wake detection is not subscribed")
                after = message.get("after_frame")
                session = message.get("session_id")
                timeout = message.get("start_timeout_seconds", 5.0)
                maximum = message.get("max_command_seconds", 30.0)
                if (type(after) is not int or not self.hub.ring.oldest <= after <= self.hub.ring.end
                        or not isinstance(session, str) or not 1 <= len(session) <= 64
                        or type(timeout) not in (int, float) or not 0.2 <= timeout <= 30
                        or type(maximum) not in (int, float) or not 1 <= maximum <= 30):
                    raise ValueError("Invalid command session or expired audio range")
                self._invalidate()
                self.capture = Capture(peer, request_id, session, after, time.monotonic() + timeout,
                                       Segmenter(window_seconds=None, max_seconds=maximum,
                                                 pre_roll_seconds=0.0), self.vad_factory())
                log.info("Command armed: session=%s after_frame=%s buffered_seconds=%.3f", session, after,
                         (self.hub.ring.end - after) / 16000)
                peer.reply(request_id, {"session_id": session})
            elif kind == "cancel_session":
                if self.capture and self.capture.peer is peer:
                    self._cancel_capture()
                if self.dictation and self.dictation.peer is peer:
                    self.dictation = None
                    self.broadcast("wake_resumed")
                if self.processing and self.processing.peer is peer:
                    if self.processing.kind == "dictation":
                        peer.reply(self.processing.request_id, error="Dictation cancelled")
                    self.processing = None
                self._invalidate()
                peer.reply(request_id)
            elif kind == "set_model":
                model = message.get("model")
                if model not in AVAILABLE_MODELS:
                    raise ValueError("Unsupported Whisper model")
                if self.busy:
                    raise RuntimeError("Finish recording or command capture before switching models")
                self._invalidate()
                self.model_pending = True
                try:
                    self.jobs.put(Job("model", peer, request_id, value=model))
                except RuntimeError:
                    self.model_pending = False
                    raise
            else:
                raise ValueError(f"Unknown speech operation: {kind}")

    def _valid(self, job):
        if job.kind in ("load", "model"):
            return True
        if job.peer.closed:
            return False
        if job.kind == "wake":
            return (job.peer.subscribed and not self.busy and self.state == "ready"
                    and job.generation == self.generation and time.monotonic() < job.expires)
        return self.processing is job

    def _inference_loop(self):
        while (job := self.jobs.get()) is not None:
            with self.lock:
                if not self._valid(job):
                    log.info("Speech job discarded before inference: kind=%s age=%.3fs", job.kind,
                             time.monotonic() - job.created_at)
                    continue
            started = time.monotonic()
            try:
                if job.kind in ("load", "model"):
                    with self.lock:
                        self.state = "loading"
                        self.broadcast("loading")
                    if job.kind == "load":
                        self.transcriber.load()
                    else:
                        self.transcriber.switch_model(job.value)
                        self.cfg.model = job.value
                        save_config(self.cfg)
                    with self.lock:
                        self.state, self.error = "ready", ""
                        log.info("Model ready: model=%s device=%s seconds=%.3f", self.transcriber.model_name,
                                 self.transcriber.device, time.monotonic() - started)
                        if job.peer:
                            job.peer.reply(job.request_id, self.status())
                        self.broadcast("model_changed", **self.status())
                else:
                    result = self.transcriber.transcribe_result(job.audio, wake=job.kind == "wake")
                    log.info("Speech job completed: kind=%s audio_seconds=%.3f inference_seconds=%.3f age_seconds=%.3f characters=%s logprob=%s no_speech=%s",
                             job.kind, job.audio.size / 16000, time.monotonic() - started,
                             time.monotonic() - job.created_at, len(result.get("text", "")),
                             result.get("avg_logprob"), result.get("no_speech_prob"))
                    with self.lock:
                        if not self._valid(job):
                            log.info("Speech result discarded: kind=%s reason=cancelled_or_expired age_seconds=%.3f", job.kind,
                                     time.monotonic() - job.created_at)
                            continue
                        if job.kind == "dictation":
                            job.peer.reply(job.request_id, result)
                        else:
                            job.peer.emit({"type": "utterance" if job.kind == "wake" else "transcript",
                                           "session_id": job.session_id,
                                           "request_id": job.request_id, "status": "ok",
                                           "utterance_id": f"{job.generation}:{job.start_frame}:{job.end_frame}",
                                           "start_frame": job.start_frame, "end_frame": job.end_frame,
                                           "created_at": job.created_at, "final": job.final, **result})
            except Exception as exc:
                log.exception("Speech job failed: %s", job.kind)
                with self.lock:
                    if job.kind in ("load", "model"):
                        self.state = "ready" if self.transcriber.ready else "error"
                        self.error = str(exc)
                    if job.peer:
                        if job.kind in ("dictation", "model"):
                            job.peer.reply(job.request_id, error=str(exc))
                        else:
                            job.peer.emit({"type": "error", "session_id": job.session_id,
                                           "error": str(exc)})
            finally:
                with self.lock:
                    if self.processing is job:
                        self.processing = None
                        self._invalidate()
                        self.broadcast("wake_resumed")
                    if job.kind == "model":
                        self.model_pending = False
                        self.broadcast("model_changed", **self.status())

    def _advance_capture(self):
        capture = self.capture
        if capture is None:
            return
        available = self.hub.ring.read(capture.cursor)
        complete = available.size // BLOCK_SIZE * BLOCK_SIZE
        for offset in range(0, complete, BLOCK_SIZE):
            start = capture.cursor
            end = start + BLOCK_SIZE
            probability = capture.vad(available[offset:offset + BLOCK_SIZE])
            capture.cursor = end
            chunks = capture.segmenter.feed(start, end, probability, start)
            if chunks:
                chunk = chunks[0]
                self.capture = None
                if chunk.overflow:
                    capture.peer.emit({"type": "error", "session_id": capture.session_id,
                                       "error": "Command exceeds maximum length; nothing was launched"})
                else:
                    job = Job("command", capture.peer, request_id=capture.request_id, session_id=capture.session_id,
                              audio=self.hub.ring.read(chunk.start, chunk.end),
                              start_frame=chunk.start, end_frame=chunk.end)
                    self.processing = job
                    self.jobs.put(job)
                return
        if capture.segmenter.start is None and time.monotonic() >= capture.deadline:
            self.capture = None
            capture.peer.emit({"type": "command_timeout", "session_id": capture.session_id})
            log.info("Command start timeout: session=%s", capture.session_id)
            self._invalidate()

    def ingest(self, audio):
        """Audio worker entry point, also usable with synthetic PCM in tests."""
        with self.lock:
            start, end = self.hub.ring.append(audio)
            if self.dictation:
                recording = self.dictation
                recording.samples += audio.size
                if recording.samples <= 16000 * 300 and not recording.error:
                    recording.chunks.append(audio)
                else:
                    recording.error = "Recording exceeds five minutes; recording discarded"
                    recording.chunks.clear()
                return
            if self.capture:
                self._advance_capture()
                return
            if self.busy or self.vad is None or not any(p.subscribed for p in self.peers.values()):
                return
            probability = self.vad(audio)
            for chunk in self.segmenter.feed(start, end, probability, self.hub.ring.oldest):
                if chunk.overflow:
                    continue
                for peer in self.peers.values():
                    if peer.subscribed:
                        self.jobs.put(Job("wake", peer, audio=self.hub.ring.read(chunk.start, chunk.end),
                                          generation=self.generation, start_frame=chunk.start,
                                          end_frame=chunk.end, final=chunk.final,
                                          expires=time.monotonic() + 15))

    def _audio_loop(self):
        while not self.stopping.is_set():
            try:
                with self.lock:
                    needed = bool(self.dictation or self.capture or any(p.subscribed for p in self.peers.values()))
                    if not needed and self.hub.running:
                        self.hub.close()
                    elif needed:
                        self.hub.start()
                    if self.hub.overrun.is_set():
                        raise RuntimeError("Microphone audio was lost; current session discarded")
                    if self.capture:
                        self._advance_capture()
                try:
                    audio = self.hub.blocks.get(timeout=0.1)
                except queue.Empty:
                    continue
                if audio.size != BLOCK_SIZE:
                    raise RuntimeError("Unexpected microphone block size")
                self.ingest(audio)
            except Exception as exc:
                log.exception("Audio capture failed")
                with self.lock:
                    if self.dictation:
                        self.dictation.error = str(exc)
                        self.dictation.chunks.clear()
                    self._cancel_capture("audio_error")
                    self._invalidate()
                    self.broadcast("error", error=str(exc))
                    self.hub.close()
                self.stopping.wait(2.0)

    def _serve_peer(self, peer):
        try:
            while not self.stopping.is_set() and not peer.closed:
                message = receive(peer.connection)
                try:
                    self.request(peer, message)
                except (ValueError, RuntimeError, OSError) as exc:
                    peer.reply(message.get("request_id", ""), error=str(exc))
        except (OSError, EOFError, ValueError):
            pass
        finally:
            peer.close()
            with self.lock:
                self.peers.pop(peer.id, None)
                self.last_client = time.monotonic()
                if self.dictation and self.dictation.peer is peer:
                    self.dictation = None
                if self.capture and self.capture.peer is peer:
                    self.capture = None
                if self.processing and self.processing.peer is peer:
                    self.processing = None
                self.jobs.discard(lambda job: job.peer is peer and job.kind != "model")
                self._invalidate()
                self.broadcast("wake_resumed")

    def run(self, *, address_override=None, key_override=None, idle_timeout=30):
        address, family = address_override or endpoint()
        if family == "AF_UNIX":
            from pathlib import Path
            Path(address).unlink(missing_ok=True)
        listener = Listener(address, family=family, authkey=key_override or auth_key())
        self.listening.set()
        self.jobs.put(Job("load"))
        threading.Thread(target=self._inference_loop, daemon=True).start()
        threading.Thread(target=self._audio_loop, daemon=True).start()

        def accept():
            while not self.stopping.is_set():
                try:
                    peer = Peer(listener.accept())
                    with self.lock:
                        if len(self.peers) >= 16:
                            peer.close()
                            continue
                        self.peers[peer.id] = peer
                        self.last_client = time.monotonic()
                    threading.Thread(target=self._serve_peer, args=(peer,), daemon=True).start()
                except (OSError, EOFError, AuthenticationError):
                    if not self.stopping.is_set():
                        log.exception("Speech pipe accept failed")

        threading.Thread(target=accept, daemon=True).start()
        try:
            while not self.stopping.wait(1.0):
                with self.lock:
                    if not self.peers and time.monotonic() - self.last_client > idle_timeout:
                        break
        finally:
            self.stopping.set()
            self.jobs.close()
            listener.close()
            with self.lock:
                for peer in list(self.peers.values()):
                    peer.close()
                self.hub.close()


def main():
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[RotatingFileHandler(runtime_dir() / "speech.log", maxBytes=2_000_000,
                                                      backupCount=2, encoding="utf-8")])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        with ServiceLock():
            SpeechService(load_config()).run()
    except RuntimeError as exc:
        if str(exc) != "Speech service already running":
            log.exception("Speech service startup failed")
            raise


if __name__ == "__main__":
    main()

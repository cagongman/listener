import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from listener.audio import AudioRing
from listener.config import Config
from listener.speech_protocol import ServiceLock, receive, send
from listener.speech_queue import Job, JobQueue
from listener.speech_service import SpeechService
from listener.vad import Segmenter


class Peer:
    def __init__(self):
        self.id = "test"
        self.closed = False
        self.subscribed = False
        self.messages = []

    def emit(self, message):
        self.messages.append(message)

    def reply(self, request_id, result=None, error=None):
        self.emit({"type": "response", "request_id": request_id,
                   "result": result, "error": error, "ok": error is None})


class Hub:
    def __init__(self):
        self.ring = AudioRing(16000 * 45)
        self.running = False
        self.blocks = queue.Queue()
        self.overrun = threading.Event()

    def start(self):
        self.running = True

    def close(self):
        self.running = False


class Vad:
    def __call__(self, audio):
        return float(audio[0])

    def reset(self):
        pass


class Transcriber:
    model_name = "large-v3"
    device = "cpu"
    ready = True

    def __init__(self):
        self.loads = 0
        self.calls = 0

    def load(self):
        self.loads += 1

    def transcribe_result(self, audio, *, wake=False):
        self.calls += 1
        return {"text": "recognized", "avg_logprob": -.2, "no_speech_prob": .05}


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.peer = Peer()
        self.service = SpeechService(Config(), transcriber=Transcriber(), hub=Hub(), vad_factory=Vad)
        self.service.state = "ready"
        self.service.peers[self.peer.id] = self.peer

    def request(self, operation, **payload):
        self.service.request(self.peer, {"type": operation, "request_id": "test", **payload})

    def test_ring_does_not_return_partial_expired_audio(self):
        ring = AudioRing(8)
        ring.append(np.arange(6, dtype=np.float32))
        ring.append(np.arange(6, 12, dtype=np.float32))
        np.testing.assert_array_equal(ring.read(4, 9), [4, 5, 6, 7, 8])
        with self.assertRaises(ValueError):
            ring.read(3)
        ring.clear()
        self.assertEqual(ring.oldest, 12)
        self.assertEqual(ring.end, 12)

    def test_queue_priority_and_latest_wake_only(self):
        jobs = JobQueue()
        jobs.put(Job("wake", self.peer, value="old"))
        jobs.put(Job("wake", self.peer, value="new"))
        jobs.put(Job("command"))
        jobs.put(Job("dictation"))
        self.assertEqual(jobs.get().kind, "dictation")
        self.assertEqual(jobs.get().kind, "command")
        self.assertEqual(jobs.get().value, "new")
        jobs.close()
        self.assertIsNone(jobs.get())

    def test_dictation_owns_recording_and_returns_a_job(self):
        self.request("begin_dictation")
        self.service.ingest(np.ones(512, dtype=np.float32))
        other = Peer()
        with self.assertRaisesRegex(RuntimeError, "own"):
            self.service.request(other, {"type": "end_dictation", "request_id": "other"})
        self.request("end_dictation")
        job = self.service.jobs.get()
        self.assertEqual(job.kind, "dictation")
        self.assertEqual(job.audio.size, 512)
        self.assertTrue(self.service.busy)

    def test_hotkey_cancels_command_capture(self):
        self.request("subscribe_wake")
        self.request("arm_command", session_id="voice", after_frame=0)
        self.request("begin_dictation")
        self.assertIsNone(self.service.capture)
        self.assertTrue(any(m["type"] == "cancelled" and m["reason"] == "dictation"
                            for m in self.peer.messages))

    def test_arm_replays_command_that_arrived_during_wake_inference(self):
        self.request("subscribe_wake")
        # Wake ended at frame zero for this fixture. The entire next utterance
        # arrived before the UI got its wake result and asked to capture it.
        for _ in range(12):
            self.service.hub.ring.append(np.ones(512, dtype=np.float32))
        for _ in range(26):
            self.service.hub.ring.append(np.zeros(512, dtype=np.float32))
        self.request("arm_command", session_id="late", after_frame=0)
        self.service._advance_capture()
        job = self.service.jobs.get()
        self.assertEqual(job.kind, "command")
        self.assertEqual(job.session_id, "late")
        np.testing.assert_array_equal(job.audio[:12 * 512], np.ones(12 * 512))
        self.assertIsNone(self.service.capture)

    def test_no_speech_timeout_never_creates_transcription(self):
        self.request("subscribe_wake")
        self.request("arm_command", session_id="timeout", after_frame=0)
        self.service.capture.deadline = time.monotonic() - 1
        self.service._advance_capture()
        self.assertIsNone(self.service.capture)
        self.assertTrue(any(m["type"] == "command_timeout" for m in self.peer.messages))

    def test_model_switch_rejected_during_recording(self):
        self.request("begin_dictation")
        with self.assertRaisesRegex(RuntimeError, "Finish"):
            self.request("set_model", model="medium")
        self.assertFalse(self.service.model_pending)

    def test_expired_wake_and_cancelled_work_are_invalid(self):
        self.peer.subscribed = True
        job = Job("wake", self.peer, expires=time.monotonic() - 1)
        self.assertFalse(self.service._valid(job))
        job.expires = time.monotonic() + 10
        self.assertTrue(self.service._valid(job))
        self.request("begin_dictation")
        self.assertFalse(self.service._valid(job))

    def test_hard_limit_discards_overlong_command(self):
        self.request("subscribe_wake")
        self.request("arm_command", session_id="long", after_frame=0, max_command_seconds=1)
        for _ in range(35):
            self.service.ingest(np.ones(512, dtype=np.float32))
        self.assertIsNone(self.service.capture)
        self.assertIsNone(self.service.processing)
        self.assertTrue(any(m["type"] == "error" for m in self.peer.messages))

    def test_audio_buffer_expiry_rejected_on_arm(self):
        self.request("subscribe_wake")
        self.service.hub.ring.append(np.zeros(46 * 16000, dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "expired"):
            self.request("arm_command", session_id="expired", after_frame=0)

    def test_segmenter_uses_endpoint_and_has_no_partial_command(self):
        segmenter = Segmenter(window_seconds=None, silence_seconds=.8)
        chunks = []
        for index in range(40):
            chunks += segmenter.feed(index * 512, (index + 1) * 512, 1 if index < 12 else 0, 0)
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].final)
        self.assertFalse(chunks[0].overflow)

    def test_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as folder, patch("listener.speech_protocol.runtime_dir", return_value=Path(folder)):
            with ServiceLock():
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with ServiceLock():
                        self.fail("Second instance acquired lock")
            with ServiceLock():
                pass

    def test_protocol_is_bytes_json_and_checks_version(self):
        class Connection:
            def send_bytes(self, data):
                self.data = data
            def recv_bytes(self, limit):
                return self.data
        connection = Connection()
        send(connection, {"type": "status", "text": "한국어"})
        self.assertEqual(receive(connection)["text"], "한국어")
        connection.data = b'{"version":99,"type":"status"}'
        with self.assertRaises(ValueError):
            receive(connection)

    def test_hotkey_preempts_pending_command_result(self):
        old = Job("command", self.peer, session_id="previous")
        self.service.processing = old
        self.request("begin_dictation")
        self.assertFalse(self.service._valid(old))
        self.assertIsNotNone(self.service.dictation)

    def test_inflight_wake_result_is_discarded_after_hotkey(self):
        entered, release = threading.Event(), threading.Event()
        original = self.service.transcriber.transcribe_result
        def slow(audio, **options):
            entered.set()
            release.wait(3)
            return original(audio, **options)
        self.service.transcriber.transcribe_result = slow
        self.peer.subscribed = True
        self.service.jobs.put(Job("wake", self.peer, audio=np.zeros(512, np.float32)))
        worker = threading.Thread(target=self.service._inference_loop)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.request("begin_dictation")
        finally:
            release.set()
            self.service.jobs.close()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(any(m["type"] == "utterance" for m in self.peer.messages))


if __name__ == "__main__":
    unittest.main()

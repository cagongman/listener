"""Use a real authenticated OS pipe with fake audio/model; no microphone or GPU."""
import os
import tempfile
import threading
import unittest
import uuid
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client
from pathlib import Path
from unittest.mock import patch

import numpy as np

from listener.config import Config
from listener.speech_client import SpeechClient
from listener.speech_service import SpeechService
from test_speech import Hub, Transcriber, Vad


class IpcTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.address = ((rf"\\.\pipe\ListenerSpeech-test-{uuid.uuid4().hex}", "AF_PIPE")
                        if os.name == "nt" else (str(Path(self.folder.name) / "test.sock"), "AF_UNIX"))
        self.key = os.urandom(32)
        self.service = SpeechService(Config(), transcriber=Transcriber(), hub=Hub(), vad_factory=Vad)
        self.thread = threading.Thread(target=self.service.run, daemon=True,
                                       kwargs={"address_override": self.address, "key_override": self.key})
        self.thread.start()
        self.assertTrue(self.service.listening.wait(3))
        self.endpoint_patch = patch("listener.speech_client.endpoint", return_value=self.address)
        self.key_patch = patch("listener.speech_client.auth_key", return_value=self.key)
        self.endpoint_patch.start()
        self.key_patch.start()
        self.clients = []

    def tearDown(self):
        try:
            for client in self.clients:
                client.close()
        finally:
            self.service.stopping.set()
            self.thread.join(4)
            self.endpoint_patch.stop()
            self.key_patch.stop()
            self.folder.cleanup()
            self.assertFalse(self.thread.is_alive())

    def client(self):
        client = SpeechClient()
        self.clients.append(client)
        client.connect(start_service=False, timeout=3)
        return client

    def test_two_clients_share_one_loaded_backend_and_own_their_recordings(self):
        first, second = self.client(), self.client()
        self.assertEqual(first.call("status")["pid"], second.call("status")["pid"])
        self.assertEqual(self.service.transcriber.loads, 1)
        first.call("begin_dictation")
        with self.assertRaisesRegex(RuntimeError, "own"):
            second.call("end_dictation")
        self.service.ingest(np.ones(8192, dtype=np.float32))
        self.assertEqual(first.call("end_dictation", timeout=3)["text"], "recognized")
        self.assertEqual(self.service.transcriber.calls, 1)

    def test_bad_authentication_does_not_kill_accept_loop(self):
        with self.assertLogs("listener.speech_service", level="ERROR"):
            with self.assertRaises(AuthenticationError):
                Client(self.address[0], family=self.address[1], authkey=b"incorrect")
            client = self.client()
            self.assertEqual(client.call("status")["state"], "ready")

    def test_connection_close_releases_recording_and_allows_reconnect(self):
        first = self.client()
        first.call("begin_dictation")
        first.close()
        # Status is synchronized by the service lock; wait for detach ownership
        # cleanup, bounded by an Event rather than unbounded sleeps.
        event = threading.Event()
        def await_detach():
            for _ in range(100):
                with self.service.lock:
                    if self.service.dictation is None:
                        event.set()
                        return
                self.service.stopping.wait(.01)
        waiter = threading.Thread(target=await_detach)
        waiter.start()
        self.assertTrue(event.wait(2))
        waiter.join()
        first.connect(start_service=False, timeout=3)
        first.call("begin_dictation")
        first.call("cancel_session")
        self.assertFalse(first.call("status")["busy"])


if __name__ == "__main__":
    unittest.main()

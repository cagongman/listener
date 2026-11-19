"""Regressions for integrating upstream device, sound and macOS changes."""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from listener.config import Config
from listener.speech_service import SpeechService
from test_speech import Hub, Peer, Transcriber, Vad


class CompatibilityTests(unittest.TestCase):
    def service(self):
        service = SpeechService(Config(), transcriber=Transcriber(), hub=Hub(), vad_factory=Vad)
        service.state = "ready"
        return service, Peer()

    def test_device_switch_preserves_frame_ids_and_invalidates_wake(self):
        service, peer = self.service()
        service.hub.running = True
        service.hub.ring.end = 16000
        generation = service.generation
        with patch("listener.speech_service.save_config") as save:
            service.request(peer, {"type": "set_input_device", "request_id": "mic", "input_device": "Headset"})
        self.assertEqual(service.hub.device, "Headset")
        self.assertTrue(service.hub.running)
        self.assertEqual(service.hub.ring.end, 16000)
        self.assertGreater(service.generation, generation)
        self.assertEqual(service.cfg.input_device, "Headset")
        save.assert_called_once_with(service.cfg)

    def test_device_switch_rejected_during_dictation(self):
        service, peer = self.service()
        service.request(peer, {"type": "begin_dictation", "request_id": "record"})
        with self.assertRaisesRegex(RuntimeError, "Finish recording"):
            service.request(peer, {"type": "set_input_device", "request_id": "mic", "input_device": "Headset"})
        self.assertEqual(service.cfg.input_device, "")

    def test_failed_device_switch_restores_previous_stream(self):
        service, peer = self.service()
        service.cfg.input_device = "Working mic"
        service.hub.running = True
        start = service.hub.start
        attempts = []
        def reopen():
            attempts.append(service.hub.device)
            if len(attempts) == 1:
                raise RuntimeError("Cannot open selected input")
            start()
        service.hub.start = reopen
        with patch("listener.speech_service.save_config") as save:
            with self.assertRaisesRegex(RuntimeError, "Cannot open"):
                service.request(peer, {"type": "set_input_device", "request_id": "mic", "input_device": "Broken"})
        self.assertEqual(attempts, ["Broken", "Working mic"])
        self.assertTrue(service.hub.running)
        self.assertEqual(service.cfg.input_device, "Working mic")
        save.assert_not_called()

    def test_rescan_closes_and_reopens_service_owned_microphone(self):
        service, peer = self.service()
        service.hub.running = True
        def scan(**kwargs):
            self.assertFalse(service.hub.running)
            return ["USB mic"]
        with patch("listener.speech_service.input_devices", side_effect=scan) as scanner:
            service.request(peer, {"type": "list_input_devices", "request_id": "list", "rescan": True})
        scanner.assert_called_once_with(rescan=True)
        self.assertTrue(service.hub.running)
        self.assertEqual(peer.messages[-1]["result"]["devices"], ["USB mic"])

    @unittest.skipUnless(sys.platform == "win32", "Windows client")
    def test_recording_cue_precedes_begin_dictation(self):
        from listener.app import App, State, SOUND_DIR
        app = App.__new__(App)
        app.state = State.IDLE
        app._state_lock = threading.RLock()
        app.stopping = threading.Event()
        app._set_state = lambda value: setattr(app, "state", value)
        actions = []
        app._beep = lambda start: actions.append("start cue")
        app.client = Mock()
        app.client.call.side_effect = lambda kind: actions.append(kind)
        app._on_toggle()
        self.assertEqual(actions, ["start cue", "begin_dictation"])
        self.assertEqual(app.state, State.RECORDING)
        self.assertTrue((SOUND_DIR / "record_start.wav").is_file())
        self.assertTrue((SOUND_DIR / "record_stop.wav").is_file())

    def test_macos_entry_uses_preserved_menu_bar_app(self):
        from listener.app import main
        macos = SimpleNamespace(main=Mock())
        with patch("listener.app.sys.platform", "darwin"), patch.dict(sys.modules, {"listener.macos_app": macos}):
            main()
        macos.main.assert_called_once_with()

    def test_mlx_results_keep_text_and_recognition_metadata(self):
        from listener.transcriber import Transcriber as RealTranscriber
        import numpy as np
        transcriber = RealTranscriber("large-v3", "cpu", "int8", "ko", 5, False, 16000)
        transcriber._model = "mlx-model"
        transcriber.device = "mlx"
        transcriber._mlx = Mock()
        transcriber._mlx.submit.return_value.result.return_value = {
            "text": "  hello  ", "segments": [{"avg_logprob": -.2, "no_speech_prob": .1}]}
        mlx = SimpleNamespace(transcribe=Mock())
        with patch.dict(sys.modules, {"mlx_whisper": mlx}):
            result = transcriber.transcribe_result(np.zeros(16000, dtype=np.float32))
        self.assertEqual(result, {"text": "hello", "avg_logprob": -.2, "no_speech_prob": .1})
        transcriber._mlx.submit.assert_called_once()

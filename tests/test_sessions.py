import json
import queue
import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import Mock, patch

from listener_hermes.config import HermesConfig
from listener_hermes.gateway import GatewayError, OutcomeUnknown
from listener_hermes.session_controller import SessionController, SessionChoiceRequired
from listener_hermes.session_router import classify


class FakeGateway:
    def __init__(self):
        self.connected = True
        self.runtime = {"profile_id": "profile-one", "session_create": {"parameters": ["cwd"]}}
        self.events = queue.Queue()
        self.sessions = {}
        self.requests = []
        self.fail_submit = False
        self.on_submit = lambda: None

    def connect(self):
        self.connected = True
        return self.runtime

    def close(self):
        self.connected = False

    def call(self, method, **params):
        self.requests.append((method, params))
        if method == "session.create":
            sid = f"session-{len(self.sessions) + 1}"
            self.sessions[sid] = {"session_id": sid, "stored_session_id": sid,
                                  "info": {"title": sid, "running": False}, "pending": []}
            return self.sessions[sid]
        if method == "session.list":
            return {"sessions": [{"id": sid, "title": sid, "source": "cli"} for sid in self.sessions], "scope": params["scope"]}
        if method in {"session.resume", "session.info"}:
            sid = params["session_id"]
            if sid not in self.sessions:
                raise GatewayError("not_found")
            return self.sessions[sid] if method.endswith("resume") else self.sessions[sid]["info"]
        if method == "prompt.submit":
            self.on_submit()
            if self.fail_submit:
                raise OutcomeUnknown("Connection lost after submission")
            return {"admission_id": "admit-" + params["input_id"], "status": "queued"}
        raise AssertionError(method)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "state.json"
        self.gateway = FakeGateway()
        self.launcher = Mock(windows={})
        self.launcher.open_session.return_value = {"pid": 123, "already_open": False}
        self.controller = SessionController(HermesConfig(), self.launcher, gateway=self.gateway, state_path=self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_followups_stay_in_same_session_while_agent_is_busy(self):
        self.controller.connect()
        first = self.controller.send("first", input_id="voice-1")
        sid = first["session_id"]
        self.gateway.sessions[sid]["info"]["running"] = True
        second = self.controller.send("second", input_id="voice-2")
        self.assertEqual(second["session_id"], sid)
        self.assertEqual(second["status"], "queued")
        submissions = [p for method, p in self.gateway.requests if method == "prompt.submit"]
        self.assertEqual([p["text"] for p in submissions], ["first", "second"])
        self.assertTrue(all(p["queued"] is True for p in submissions))
        self.launcher.open_session.assert_called_once_with(sid)

    def test_new_and_previous_preserve_original_queue_target(self):
        first = self.controller.send("original", input_id="one")
        new = self.controller.new()
        self.assertNotEqual(first["session_id"], new["session_id"])
        self.assertEqual(self.controller.state["submissions"][0]["session_id"], first["session_id"])
        self.assertEqual(self.controller.previous()["session_id"], first["session_id"])
        self.assertEqual(self.controller.state["previous"], new["session_id"])

    def test_reopen_keeps_id_and_does_not_submit_a_prompt(self):
        sid = self.controller.new()["session_id"]
        self.controller.reopen()
        self.assertEqual(self.controller.current, sid)
        self.assertEqual(len(self.gateway.sessions), 1)
        self.assertFalse(any(method == "prompt.submit" for method, _ in self.gateway.requests))

    def test_restart_resumes_only_saved_session(self):
        self.controller.connect()
        sid = self.controller.new()["session_id"]
        other = self.gateway.call("session.create", request_id="another", source="cli", cwd="other")
        restarted = SessionController(HermesConfig(), self.launcher, gateway=self.gateway, state_path=self.path)
        restarted.connect()
        self.assertEqual(restarted.current, sid)
        self.assertNotEqual(restarted.current, other["session_id"])

    def test_ambiguous_live_sessions_require_selection_before_sending(self):
        for _ in range(2):
            self.gateway.call("session.create", source="cli")
        with self.assertRaises(SessionChoiceRequired):
            self.controller.send("must not go to a random session")
        self.assertEqual(len(self.controller.catalog), 2)
        self.assertFalse(any(method == "prompt.submit" for method, _ in self.gateway.requests))

    def test_lost_ack_is_journaled_without_text_and_never_replayed(self):
        self.controller.connect()
        self.gateway.fail_submit = True
        with self.assertRaises(OutcomeUnknown):
            self.controller.send("private prompt", input_id="stable-voice-id")
        self.assertEqual(self.controller.state["submissions"][0]["status"], "unknown")
        self.assertNotIn("private prompt", self.path.read_text(encoding="utf-8"))
        self.gateway.fail_submit = False
        with self.assertRaises(OutcomeUnknown):
            self.controller.send("private prompt", input_id="stable-voice-id")
        self.assertEqual(sum(method == "prompt.submit" for method, _ in self.gateway.requests), 1)

    def test_profile_mismatch_refuses_connection(self):
        self.controller.connect()
        self.gateway.runtime["profile_id"] = "another-profile"
        with self.assertRaises(GatewayError):
            self.controller.connect()
        self.assertFalse(self.gateway.connected)

    def test_dry_run_does_not_connect_create_or_submit(self):
        controller = SessionController(HermesConfig(), self.launcher, gateway=self.gateway,
                                       state_path=self.path, dry_run=True)
        controller.connect()
        controller.new()
        controller.send("test")
        controller.reopen()
        self.assertEqual(self.gateway.requests, [])
        self.launcher.open_session.assert_not_called()

    def test_control_phrases_are_separate_from_normal_prompts(self):
        for text, action in [("새 대화 시작해", "new"), ("창 다시 열어", "reopen"),
                             ("이전 대화 열어", "previous"), ("대화 목록 보여줘", "list"),
                             ("두 번째 대화 열어", "select_index")]:
            with self.subTest(text=text):
                self.assertEqual(classify(text).action, action)
        self.assertEqual(classify("새 대화 기능을 설명해 줘").action, "send")
        self.assertEqual(classify("주식 시세 확인해 줘").action, "send")

    def test_poll_recovers_approval_waiter_and_running_state_without_events(self):
        result = self.controller.send("open youtube", input_id="voice-approval")
        snapshot = self.gateway.sessions[result["session_id"]]
        snapshot.update({"running": True, "prompts": [{"kind": "approval", "prompt_id": "approve-1"}],
                         "pending": [{"admission_id": result["admission_id"], "status": "started"},
                                     {"admission_id": "later", "status": "queued"}]})
        self.controller.poll()
        self.assertTrue(self.controller.active_info["running"])
        self.assertTrue(self.controller.active_info["waiting"])
        self.assertEqual(self.controller.active_info["waiting_ids"], ("approve-1",))
        self.assertEqual(self.controller.active_info["queue_count"], 1)
        self.assertEqual(self.controller.state["submissions"][0]["status"], "started")
        self.assertEqual(self.gateway.requests[-1], ("session.resume", {
            "session_id": result["session_id"], "omit_messages": True}))
        self.assertFalse(any(method == "approval.respond" for method, _ in self.gateway.requests))

    def test_settled_waiter_refreshes_snapshot_immediately(self):
        sid = self.controller.new()["session_id"]
        snapshot = self.gateway.sessions[sid]
        snapshot.update({"running": True, "prompts": [{"kind": "approval", "prompt_id": "approve-1"}]})
        self.controller.poll()
        snapshot.update({"running": False, "prompts": []})
        self.gateway.events.put({"type": "approval.settled", "session_id": sid})
        self.controller.poll()
        self.assertFalse(self.controller.active_info["waiting"])
        self.assertFalse(self.controller.active_info["running"])

    def test_waiting_notification_is_deduplicated_until_new_prompt(self):
        from listener_hermes.app import App
        app = App.__new__(App)
        app.conversations = self.controller
        app.icon = Mock()
        self.controller.active_info = {"waiting": True, "waiting_ids": ("approve-1",), "queue_count": 2}
        app._notify_waiting()
        app._notify_waiting()
        app.icon.notify.assert_called_once()
        self.controller.active_info["waiting"] = False
        app._notify_waiting()
        self.controller.active_info.update(waiting=True, waiting_ids=("approve-2",))
        app._notify_waiting()
        self.assertEqual(app.icon.notify.call_count, 2)

    def test_voice_loop_accepts_followup_while_cli_process_is_running(self):
        from listener_hermes.app import App
        from listener_hermes.config import Config
        from listener_hermes.command_session import CommandSession
        from listener_hermes.wakeword import WakewordMatcher
        self.controller.connect()
        sid = self.controller.new()["session_id"]
        self.gateway.sessions[sid]["info"]["running"] = True
        app = App.__new__(App)
        app.cfg = Config()
        app.debug_transcripts = False
        app.conversations = self.controller
        app.launcher = self.launcher
        app.launcher.running = True
        app.launcher.dry_run = False
        app.launcher.probe.return_value = {"command": ["hermes.exe", "chat"], "cwd": "."}
        app.session = CommandSession(WakewordMatcher(app.cfg.wake_phrases), cooldown=1)
        app.enabled, app.subscribed, app.temporary_pause = True, False, False
        app.state = "loading"
        app.stopping = threading.Event()
        app.controls = queue.Queue()
        app.icon = Mock()
        self.gateway.on_submit = app.stopping.set
        class Speech:
            connected = True
            watching = False
            emitted = False
            def call(self, kind, **params):
                if kind == "subscribe_wake":
                    self.watching = params["enabled"]
                return {"frame": 0}
            def get(self, timeout):
                if self.watching and not self.emitted:
                    self.emitted = True
                    return {"type": "utterance", "text": "모니카, 이어서 확인해 줘", "final": True,
                            "utterance_id": "followup", "start_frame": 0, "end_frame": 16000,
                            "avg_logprob": -.2, "no_speech_prob": .1}
                app.stopping.wait(min(timeout, .05))
                raise queue.Empty
        speech = Speech()
        speech.events = speech
        app.client = speech
        deadline = threading.Timer(2, app.stopping.set)
        deadline.start()
        try:
            with patch("winsound.Beep"):
                app._loop()
        finally:
            deadline.cancel()
        sent = [params for method, params in self.gateway.requests if method == "prompt.submit"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["session_id"], sid)
        self.assertEqual(sent[0]["text"], "이어서 확인해 줘")


if __name__ == "__main__":
    unittest.main()

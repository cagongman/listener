import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from listener_hermes.command_session import CommandSession
from listener_hermes.config import HermesConfig, load_config
from listener_hermes.hermes_launcher import HermesLauncher
from listener_hermes.wakeword import WakewordMatcher


class HermesTests(unittest.TestCase):
    def setUp(self):
        self.matcher = WakewordMatcher(["헤이 모니카", "모니카"])

    def event(self, text, final=True, identity="one"):
        return {"text": text, "final": final, "utterance_id": identity,
                "start_frame": 0, "end_frame": 16000,
                "avg_logprob": -.2, "no_speech_prob": .1}

    def test_both_phrases_and_spacing(self):
        for text in ["헤이 모니카", "헤이모니카!", "모니카", "“헤이   모니카”"]:
            with self.subTest(text=text):
                self.assertIsNotNone(self.matcher.match(text))
        self.assertEqual(self.matcher.match("모니카, 프로젝트 설명해 줘").command, "프로젝트 설명해 줘")

    def test_ordinary_mentions_do_not_wake(self):
        for text in ["하모니카", "모니카가 말했다", "모니카는 배우야", "모니카처럼", "오늘 모니카", "모니카 가 말했다"]:
            with self.subTest(text=text):
                self.assertIsNone(self.matcher.match(text))

    def test_observed_spoken_hey_misrecognition_and_internal_comma(self):
        matcher = WakewordMatcher(["헤이 모니카", "모니카"], {"네 모니카": "헤이 모니카"})
        for text, score, silence in [("네, 모니카.", -.6943732367, .192749),
                                      ("네, 모니카.", -.6079610188, .42334),
                                      ("헤이, 모니카", -.4, .1),
                                      ("네모니카", -.4, .1)]:
            with self.subTest(text=text):
                match = matcher.match(text, avg_logprob=score, no_speech_prob=silence)
                self.assertIsNotNone(match)
                self.assertEqual(match.phrase, "헤이 모니카")
        self.assertIsNone(matcher.match("네 모니카가 말했다"))
        self.assertIsNone(matcher.match("오늘 네 모니카"))
        self.assertIsNone(matcher.match("네 모니카", no_speech_prob=.9))

    def test_short_wake_requires_stricter_metadata(self):
        self.assertIsNone(self.matcher.match("모니카", avg_logprob=-.8))
        self.assertIsNotNone(self.matcher.match("헤이 모니카", avg_logprob=-.8))
        self.assertIsNone(self.matcher.match("헤이 모니카", no_speech_prob=.9))

    def test_diagnostics_distinguish_text_and_quality_rejection(self):
        session = CommandSession(self.matcher)
        self.assertIsNone(session.on_utterance(self.event("일반 대화")))
        self.assertEqual(session.last_decision, "not_wake_phrase")
        event = self.event("모니카", identity="low-confidence")
        event["avg_logprob"] = -.9
        self.assertIsNone(session.on_utterance(event))
        self.assertEqual(session.last_decision, "low_logprob")
        event = self.event("헤이 모니카", identity="no-speech")
        event["no_speech_prob"] = .9
        self.assertIsNone(session.on_utterance(event))
        self.assertEqual(session.last_decision, "high_no_speech_probability")

    def test_diagnostics_explain_expired_events_without_launching(self):
        import time
        session = CommandSession(self.matcher)
        event = self.event("헤이 모니카")
        event["created_at"] = time.monotonic() - 20
        self.assertIsNone(session.on_utterance(event))
        self.assertEqual(session.last_decision, "stale_event")

    def test_one_sentence_launches_once(self):
        session = CommandSession(self.matcher)
        event = self.event("모니카, 설명해 줘")
        self.assertEqual(session.on_utterance(event), {"action": "launch", "prompt": "설명해 줘"})
        self.assertIsNone(session.on_utterance(event))
        session.reset(cooldown=False)
        self.assertIsNone(session.on_utterance(event))

    def test_partial_command_waits_for_completed_utterance(self):
        session = CommandSession(self.matcher)
        self.assertEqual(session.on_utterance(self.event("모니카, 프로젝트", False))["action"], "arm")
        result = session.on_transcript({"session_id": session.id, "text": "모니카, 프로젝트 구조 설명해 줘", "end_frame": 32000})
        self.assertEqual(result["prompt"], "프로젝트 구조 설명해 줘")

    def test_partial_wake_only_arms_again_for_the_next_utterance(self):
        session = CommandSession(self.matcher)
        session.on_utterance(self.event("헤이 모니카", False))
        self.assertEqual(session.on_transcript({"session_id": session.id, "text": "헤이 모니카", "end_frame": 32000}),
                         {"action": "arm", "after_frame": 32000})
        result = session.on_transcript({"session_id": session.id, "text": "설명해 줘", "end_frame": 64000})
        self.assertEqual(result["prompt"], "설명해 줘")

    def test_stale_session_cannot_launch(self):
        session = CommandSession(self.matcher)
        session.on_utterance(self.event("모니카"))
        self.assertIsNone(session.on_transcript({"session_id": "stale", "text": "실행해"}))

    def test_prompt_is_a_file_never_shell_interpolation(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = HermesConfig(cwd=folder)
            launcher = HermesLauncher(cfg)
            launcher._probed = True
            process = Mock(pid=42)
            process.poll.return_value = None
            prompt = '한국어 "quoted" $(anything) `text` & > file'
            with patch.object(launcher, "_prefix", return_value=["hermes.exe"]), \
                 patch("listener_hermes.hermes_launcher.runtime_dir", return_value=Path(folder)), \
                 patch("listener_hermes.hermes_launcher.spawn_console", return_value=process) as spawn, \
                 patch("listener_hermes.hermes_launcher.threading.Thread.start"):
                launcher.launch(prompt)
                command = spawn.call_args.args[0]
                self.assertEqual(command[:3], ["hermes.exe", "chat", "--query-file"])
                path = Path(command[3])
                self.assertEqual(path.read_text(encoding="utf-8"), prompt)
                self.assertNotIn(prompt, command)
                self.assertFalse(spawn.call_args.kwargs.get("shell", False))
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    launcher.launch("again")
                launcher._cleanup(process, path)
                self.assertFalse(path.exists())

    def test_dry_run_never_resolves_or_spawns_cli(self):
        launcher = HermesLauncher(HermesConfig(), dry_run=True)
        with patch.object(launcher, "probe") as probe, patch("listener_hermes.hermes_launcher.spawn_console") as spawn:
            self.assertTrue(launcher.launch("test")["dry_run"])
            probe.assert_not_called()
            spawn.assert_not_called()

    def test_session_viewer_uses_tui_for_external_inputs_and_reuses_same_id(self):
        launcher = HermesLauncher(HermesConfig())
        process = Mock(pid=42)
        process.poll.return_value = None
        with patch.object(launcher, "_prefix", return_value=["hermes.exe"]), \
             patch("listener_hermes.hermes_launcher.spawn_console", return_value=process) as spawn:
            launcher.open_session("stable-id")
            self.assertEqual(spawn.call_args.args[0], ["hermes.exe", "chat", "--tui", "--native", "--resume", "stable-id"])
            self.assertTrue(launcher.open_session("stable-id")["already_open"])
            spawn.assert_called_once()

    def test_session_viewer_respects_explicit_classic_cli(self):
        launcher = HermesLauncher(HermesConfig(args=["chat", "--cli"]))
        with patch.object(launcher, "_prefix", return_value=["hermes.exe"]), \
             patch("listener_hermes.hermes_launcher.spawn_console", return_value=Mock(pid=42)) as spawn:
            launcher.open_session("stable-id")
            self.assertEqual(spawn.call_args.args[0], ["hermes.exe", "chat", "--cli", "--resume", "stable-id"])

    def test_config_validates_finite_timings_and_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for value in [-1, 31, True, float("nan")]:
                path.write_text(json.dumps({"max_command_seconds": value}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)
            path.write_text(json.dumps({"hermes": {"mode": "oneshot"}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "interactive"):
                load_config(path)

    def test_wsl_uses_argument_vector_and_explicit_cwd(self):
        cfg = HermesConfig(backend="wsl", executable="/home/me/.local/bin/hermes",
                           wsl_distribution="Ubuntu", wsl_cwd="/home/me/project")
        with patch("listener_hermes.hermes_launcher.shutil.which", return_value="wsl.exe"):
            command = HermesLauncher(cfg)._prefix()
        self.assertEqual(command, ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/me/project",
                                    "--", "/home/me/.local/bin/hermes"])


if __name__ == "__main__":
    unittest.main()

import argparse
import json
import logging
import queue
import threading
import time
import sys
from pathlib import Path

from listener.speech_client import SpeechClient

from .command_session import CommandSession
from .config import CONFIG_PATH, load_config
from .hermes_launcher import HermesLauncher
from .wakeword import WakewordMatcher
from .session_controller import SessionController, SessionChoiceRequired
from .session_router import Intent, classify

log = logging.getLogger(__name__)
COLORS = {"loading": (128, 128, 128), "listening": (60, 160, 80),
          "capturing": (220, 50, 50), "launching": (230, 180, 40),
          "running": (90, 130, 220), "cooldown": (160, 150, 65),
          "paused": (100, 100, 100), "waiting": (230, 150, 40), "error": (210, 75, 45)}


class App:
    def __init__(self, cfg, *, dry_run=False, debug_transcripts=False):
        import pystray
        from listener.ui import make_icon
        self.cfg = cfg
        self.debug_transcripts = debug_transcripts
        self.client = SpeechClient()
        self.launcher = HermesLauncher(cfg.hermes, dry_run=dry_run)
        self.conversations = SessionController(cfg.hermes, self.launcher, dry_run=dry_run)
        self.session = CommandSession(WakewordMatcher(cfg.wake_phrases, cfg.wake_aliases), cooldown=cfg.cooldown_seconds)
        self.enabled = cfg.wake_enabled
        self.subscribed = False
        self.temporary_pause = False
        self.state = "loading"
        self.stopping = threading.Event()
        self.controls = queue.Queue()
        self._waiting_notice = None
        self.icon = pystray.Icon("listener-hermes", make_icon(COLORS[self.state]), "Listener Hermes", menu=self._menu())

    def _menu(self):
        import pystray
        def select_item(row):
            return pystray.MenuItem(row["title"][:55],
                                   lambda: self.controls.put({"action": "select", "value": row["id"]}),
                                   checked=lambda _: self.conversations.current == row["id"], radio=True)
        choices = [select_item(row) for row in self.conversations.catalog]
        if not choices:
            choices = [pystray.MenuItem("대화 목록을 새로고침하세요", None, enabled=False)]
        return pystray.Menu(
                pystray.MenuItem(lambda _: f"상태: {self.state}", None, enabled=False),
                pystray.MenuItem(lambda _: "대화: " + self.conversations.title[:55], None, enabled=False),
                pystray.MenuItem(lambda _: "Hermes: " + ("승인/답변 대기" if self.conversations.active_info.get("waiting")
                    else "작업 중 · 음성 접수 가능" if self.conversations.active_info.get("running")
                    else "대기 · 음성 접수 가능"), None, enabled=False),
                pystray.MenuItem("호출어: 헤이 모니카 / 모니카", None, enabled=False),
                pystray.MenuItem(lambda _: f"대기 중인 요청: {self.conversations.active_info.get('queue_count', 0)}개", None, enabled=False),
                pystray.MenuItem("대화 선택", pystray.Menu(*choices)),
                pystray.MenuItem("새 대화", lambda: self.controls.put({"action": "new"})),
                pystray.MenuItem("현재 대화 창 다시 열기", lambda: self.controls.put({"action": "reopen"})),
                pystray.MenuItem("이전 대화", lambda: self.controls.put({"action": "previous"})),
                pystray.MenuItem("대화 목록 새로고침", lambda: self.controls.put({"action": "list"})),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("음성 호출 사용", lambda: self.controls.put("toggle"),
                                 checked=lambda _: self.enabled),
                pystray.MenuItem("재연결", lambda: self.controls.put("reconnect")),
                pystray.MenuItem("종료", self._quit))

    def _refresh_menu(self):
        if not self.stopping.is_set():
            self.icon.menu = self._menu()
            self.icon.update_menu()
            self._notify_waiting()

    def _notify_waiting(self):
        activity = self.conversations.active_info
        key = ((self.conversations.current, activity.get("waiting_ids"))
               if activity.get("waiting") else None)
        if key is not None and key != getattr(self, "_waiting_notice", None):
            self.icon.notify("Hermes가 승인 또는 답변을 기다리고 있습니다. 열린 Hermes 콘솔에서 내용을 확인해 주세요. 뒤의 요청은 큐에서 대기합니다.",
                             "Listener Hermes · 확인 필요")
            log.info("Hermes waiting for user: session=%s queued=%s", self.conversations.current,
                     activity.get("queue_count", 0))
        self._waiting_notice = key

    def _state(self, value):
        from listener.ui import make_icon
        if self.state == value:
            return
        log.info("State: %s -> %s", self.state, value)
        self.state = value
        if not self.stopping.is_set():
            self.icon.icon = make_icon(COLORS[value])
            self.icon.title = "Listener Hermes - 승인/답변 대기" if value == "waiting" else f"Listener Hermes - {value}"
            self.icon.update_menu()

    def _subscribe(self, enabled):
        if self.subscribed != enabled:
            result = self.client.call("subscribe_wake", enabled=enabled)
            if enabled:
                self.session.minimum_frame = result["frame"]
            self.subscribed = enabled
            log.info("Wake subscription: enabled=%s frame=%s", enabled, result["frame"])

    def _arm(self, frame):
        log.info("Arm command: session=%s after_frame=%s", self.session.id, frame)
        self.client.call("arm_command", session_id=self.session.id, after_frame=frame,
                         start_timeout_seconds=self.cfg.command_start_timeout_seconds,
                         max_command_seconds=self.cfg.max_command_seconds)
        self._state("capturing")

    def _launch(self, prompt):
        self._perform(classify(prompt) if prompt else Intent("reopen"))

    def _perform(self, intent):
        self._subscribe(False)
        self._state("launching")
        try:
            if intent.action == "send":
                result = self.conversations.send(intent.value, input_id=self.session.id)
            elif intent.action == "new":
                result = self.conversations.new()
            elif intent.action == "previous":
                result = self.conversations.previous()
            elif intent.action == "select":
                result = self.conversations.select(intent.value)
            elif intent.action == "select_index":
                index = int(intent.value) - 1
                rows = self.conversations.catalog
                if not 0 <= index < len(rows):
                    raise SessionChoiceRequired("‘대화 목록 보여줘’로 목록을 확인한 뒤 대화를 선택해 주세요.")
                result = self.conversations.select(rows[index]["id"])
            elif intent.action == "list":
                rows = self.conversations.list_sessions()
                result = {"action": "list", "count": len(rows)}
            else:
                result = self.conversations.reopen()
            log.info("Session action: action=%s session=%s admission=%s status=%s dry_run=%s", intent.action,
                     result.get("session_id"), result.get("admission_id"), result.get("status"), result.get("dry_run", False))
            if result.get("dry_run"):
                self.icon.notify(f"Dry run: {intent.action} 명령을 인식했습니다. 전송하지 않았습니다.", "Listener Hermes")
            elif intent.action == "send":
                receipt = "다음 요청으로 접수했습니다" if result.get("status") == "queued" else "명령을 전달했습니다"
                if self.conversations.active_info.get("waiting"):
                    receipt += " · Hermes 콘솔에서 승인/답변이 필요합니다"
                self.icon.notify(f"{receipt}: {result.get('title', '')[:55]}", "Listener Hermes")
            elif intent.action == "list":
                self.icon.notify(f"대화 {result['count']}개: 트레이의 ‘대화 선택’ 메뉴에서 선택하거나 ‘두 번째 대화 열어’처럼 말하세요.", "Listener Hermes")
            else:
                self.icon.notify(f"{result.get('title', '대화')} · {intent.action}", "Listener Hermes")
        except SessionChoiceRequired as exc:
            self.icon.notify(str(exc), "Listener Hermes")
        finally:
            self.session.reset()
            self._refresh_menu()
        self._state("cooldown")

    def _action(self, action):
        if not action:
            return
        if action["action"] == "launch":
            self._launch(action["prompt"])
        elif action["action"] == "arm":
            self._arm(action["after_frame"])
        else:
            self.session.reset()
            self.icon.notify(action["error"], "Listener Hermes")

    def _handle(self, event):
        kind = event["type"]
        if kind == "utterance" and self.enabled and not self.temporary_pause:
            action = self.session.on_utterance(event)
            log.info("Wake decision: reason=%s id=%s final=%s characters=%s logprob=%s no_speech=%s age=%.3fs frames=%s..%s minimum=%s",
                     self.session.last_decision, event.get("utterance_id"), event.get("final"),
                     len(event.get("text", "")), event.get("avg_logprob"), event.get("no_speech_prob"),
                     time.monotonic() - event.get("created_at", time.monotonic()),
                     event.get("start_frame"), event.get("end_frame"), self.session.minimum_frame)
            if self.debug_transcripts:
                log.info("Wake diagnostic transcript: %r", event.get("text"))
            if action:
                # Suppress further wake work immediately, but keep the subscription
                # until arm_command has transferred the preserved PCM range.
                try:
                    import winsound
                    winsound.Beep(880, 100)
                except (ImportError, RuntimeError):
                    pass
                self._action(action)
        elif kind == "transcript":
            log.info("Command transcript received: session=%s characters=%s", event.get("session_id"), len(event.get("text", "")))
            if self.debug_transcripts:
                log.info("Command diagnostic transcript: %r", event.get("text"))
            self._action(self.session.on_transcript(event))
        elif kind == "command_timeout" and self.session.id == event.get("session_id"):
            self._launch("")
        elif kind == "wake_paused":
            self.temporary_pause = True
            self.session.reset()
            self._state("paused")
        elif kind == "wake_resumed":
            self.temporary_pause = False
        elif kind == "cancelled" and self.session.id == event.get("session_id"):
            self.session.reset()
        elif kind == "error":
            self.session.reset()
            self._state("error")
            self.icon.notify(event.get("error", "음성 서비스 오류"), "Listener Hermes")
        elif kind == "loading":
            self.session.reset()
            self._state("loading")
        elif kind == "disconnected" and not self.client.connected:
            self.session.reset()
            self.subscribed = False
            self.temporary_pause = False
            self._state("error")

    def _loop(self):
        checked = self.launcher.dry_run
        last_error = ""
        while not self.stopping.is_set():
            try:
                if not checked:
                    probe = self.launcher.probe()
                    log.info("Hermes CLI ready: command=%s cwd=%s", probe["command"], probe["cwd"])
                    checked = True
                if not self.conversations.connected:
                    self._state("loading")
                    self.conversations.connect()
                    self._refresh_menu()
                if not self.client.connected:
                    self._state("loading")
                    status = self.client.connect()
                    log.info("Speech service connected: pid=%s model=%s device=%s", status["pid"], status["model"], status["device"])
                    self.subscribed = False
                    self.session.reset()
                    while not self.client.events.empty():
                        self.client.events.get_nowait()
                try:
                    control = self.controls.get_nowait()
                    if isinstance(control, dict):
                        self.client.call("cancel_session")
                        self.session.reset(cooldown=False)
                        self._perform(Intent(control["action"], control.get("value", "")))
                    elif control == "toggle":
                        self.enabled = not self.enabled
                        self.session.reset()
                        self.client.call("cancel_session")
                    elif control == "reconnect":
                        self.client.close()
                        self.conversations.close()
                        self.subscribed = False
                        continue
                except queue.Empty:
                    pass
                cooling = time.monotonic() < self.session.cooldown_until
                should_watch = self.enabled and not cooling
                self._subscribe(should_watch)
                if not self.enabled or self.temporary_pause:
                    self._state("paused")
                elif cooling:
                    self._state("cooldown")
                elif not self.session.id:
                    self._state("waiting" if self.conversations.active_info.get("waiting") else "listening")
                if self.conversations.poll():
                    self._refresh_menu()
                try:
                    self._handle(self.client.events.get(timeout=0.2))
                except queue.Empty:
                    pass
                last_error = ""
            except Exception as exc:
                log.exception("Hermes voice client failed")
                self.session.reset()
                self._state("error")
                if str(exc) != last_error:
                    self.icon.notify(str(exc), "Listener Hermes")
                    last_error = str(exc)
                self.stopping.wait(3)

    def _quit(self):
        self.stopping.set()
        self.client.close()
        self.conversations.close()
        # A CLI the user is talking to belongs to the user; leave it running.
        self.icon.stop()

    def run(self):
        threading.Thread(target=self._loop, daemon=True).start()
        self.icon.run()


def main():
    parser = argparse.ArgumentParser(description="헤이 모니카 / 모니카 → Hermes CLI")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--check", action="store_true", help="Check microphone devices and Hermes CLI without listening")
    parser.add_argument("--dry-run", action="store_true", help="Listen and detect commands but do not execute Hermes")
    parser.add_argument("--simulate", metavar="TEXT", help="Test wake matching without microphone, model or CLI execution")
    parser.add_argument("--report", type=Path, help="Write --check/--simulate JSON to a file (also works in windowed exe)")
    parser.add_argument("--debug-transcripts", action="store_true", help="Temporarily log recognized text for wake detection troubleshooting")
    args = parser.parse_args()
    cfg = load_config(args.config)

    def report(result):
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.report:
            args.report.write_text(text + "\n", encoding="utf-8")
        if sys.stdout is not None:
            print(text)

    if args.simulate is not None:
        match = WakewordMatcher(cfg.wake_phrases, cfg.wake_aliases).match(args.simulate)
        report({"matched": bool(match), "phrase": match.phrase if match else None,
                "prompt": match.command if match else None,
                "action": classify(match.command).action if match and match.command else "wait" if match else None})
        return
    if args.check:
        import sounddevice as sd
        result = {"config": str(args.config), "devices": str(sd.query_devices())}
        try:
            sd.check_input_settings(samplerate=16000, channels=1, dtype="float32")
            result["microphone"] = {"available": True, "sample_rate": 16000}
        except Exception as exc:
            result["microphone"] = {"available": False, "error": str(exc)}
        try:
            launcher = HermesLauncher(cfg.hermes)
            result["hermes"] = launcher.probe()
        except Exception as exc:
            result["hermes"] = {"available": False, "error": str(exc)}
        if result["hermes"]["available"]:
            from .gateway import GatewayClient
            gateway = GatewayClient(launcher)
            try:
                runtime = gateway.connect()
                result["gateway"] = {"available": True, "profile_id": runtime.get("profile_id"),
                                     "session_create": runtime.get("session_create", {})}
            except Exception as exc:
                result["gateway"] = {"available": False, "error": str(exc)}
            finally:
                gateway.close()
        report(result)
        if (not result["hermes"]["available"] or not result["microphone"]["available"]
                or not result.get("gateway", {}).get("available")):
            raise SystemExit(1)
        return
    from logging.handlers import RotatingFileHandler
    from listener.speech_protocol import runtime_dir
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[RotatingFileHandler(runtime_dir() / "hermes.log", maxBytes=2_000_000,
                                                      backupCount=2, encoding="utf-8")])
    log.info("Hermes client starting: dry_run=%s debug_transcripts=%s config=%s", args.dry_run, args.debug_transcripts, args.config)
    App(cfg, dry_run=args.dry_run, debug_transcripts=args.debug_transcripts).run()

import ctypes
import enum
import logging
import queue
import sys
import threading
from pathlib import Path

if sys.platform == "win32":
    import winsound

import pystray

from .config import APP_DIR, AVAILABLE_MODELS, Config, load_config
from .hotkey import Hotkey
from .inject import paste_text
from .speech_client import SpeechClient
from .ui import make_icon

log = logging.getLogger(__name__)
SOUND_DIR = Path(__file__).resolve().parent / "assets" / "sounds"


class State(enum.Enum):
    LOADING = "loading"
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    PROCESSING = "processing"
    ERROR = "error"


COLORS = {State.LOADING: (128, 128, 128), State.IDLE: (60, 160, 80),
          State.STARTING: (230, 180, 40), State.RECORDING: (220, 50, 50),
          State.PROCESSING: (230, 180, 40), State.ERROR: (210, 75, 45)}


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = State.LOADING
        self._state_lock = threading.RLock()
        self.stopping = threading.Event()
        self.client = SpeechClient()
        self.model_name = cfg.model
        self.device = "connecting"
        self._devices = []
        self.icon = pystray.Icon("listener", make_icon(COLORS[self.state]),
                                 "Listener", menu=self._menu())
        self.hotkey = None

    def _menu(self):
        def device_item(label, name):
            return pystray.MenuItem(label, lambda: self._switch_device(name),
                                   checked=lambda _: self.cfg.input_device == name, radio=True)

        def device_menu():
            yield device_item("시스템 기본", "")
            yield pystray.Menu.SEPARATOR
            for name in self._devices:
                yield device_item(name, name)
            yield pystray.Menu.SEPARATOR
            yield pystray.MenuItem("목록 새로고침", self._rescan_devices)

        def model_item(name):
            return pystray.MenuItem(name, lambda: self._switch_model(name),
                                    checked=lambda item: self.model_name == name, radio=True)

        return pystray.Menu(
            pystray.MenuItem(lambda _: f"상태: {self.state.value} ({self.device})", None, enabled=False),
            pystray.MenuItem(f"단축키: {self.cfg.hotkey}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("모델", pystray.Menu(*[model_item(name) for name in AVAILABLE_MODELS])),
            pystray.MenuItem("마이크", pystray.Menu(device_menu)),
            pystray.MenuItem("재연결", lambda: self.client.close()),
            pystray.MenuItem("종료", self._quit),
        )

    def _set_state(self, state):
        if self.stopping.is_set():
            return
        self.state = state
        self.icon.icon = make_icon(COLORS[state])
        self.icon.title = f"Listener - {state.value}"
        self.icon.update_menu()

    def _beep(self, start):
        path = SOUND_DIR / ("record_start.wav" if start else "record_stop.wav")
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        except (OSError, RuntimeError):
            log.warning("Could not play recording cue: %s", path, exc_info=True)

    def _on_toggle(self):
        with self._state_lock:
            if self.state == State.IDLE:
                self._set_state(State.STARTING)
                start = True
            elif self.state == State.RECORDING:
                self._set_state(State.PROCESSING)
                start = False
            else:
                return
        try:
            if start:
                self._beep(True)
                self.client.call("begin_dictation")
                with self._state_lock:
                    self._set_state(State.RECORDING)
            else:
                threading.Thread(target=self._beep, args=(False,), daemon=True).start()
                result = self.client.call("end_dictation", timeout=300)
                if not self.stopping.is_set():
                    paste_text(result["text"])
                with self._state_lock:
                    self._set_state(State.IDLE)
        except Exception as exc:
            log.exception("Dictation failed")
            self.icon.notify(str(exc), "Listener")
            with self._state_lock:
                self._set_state(State.IDLE if self.client.connected else State.ERROR)

    def _switch_model(self, name):
        with self._state_lock:
            if self.state != State.IDLE or name == self.model_name:
                return
            self._set_state(State.LOADING)

        def work():
            try:
                result = self.client.call("set_model", model=name, timeout=300)
                self.model_name, self.device = result["model"], result["device"]
            except Exception as exc:
                log.exception("Model switch failed")
                self.icon.notify(str(exc), "Listener")
            finally:
                with self._state_lock:
                    self._set_state(State.IDLE if self.client.connected else State.ERROR)

        threading.Thread(target=work, daemon=True).start()

    def _switch_device(self, name):
        with self._state_lock:
            if self.state != State.IDLE:
                return
            self._set_state(State.LOADING)

        def work():
            try:
                result = self.client.call("set_input_device", input_device=name)
                self.cfg.input_device = result["input_device"]
            except Exception as exc:
                log.exception("Input device switch failed")
                self.icon.notify(str(exc), "Listener")
            finally:
                with self._state_lock:
                    self._set_state(State.IDLE if self.client.connected else State.ERROR)
        threading.Thread(target=work, daemon=True).start()

    def _rescan_devices(self):
        if self.state != State.IDLE:
            return
        try:
            self._devices = self.client.call("list_input_devices", rescan=True)["devices"]
            self.icon.update_menu()
        except Exception as exc:
            log.exception("Input device rescan failed")
            self.icon.notify(str(exc), "Listener")

    def _supervise(self):
        while not self.stopping.is_set():
            try:
                if not self.client.connected:
                    self._set_state(State.LOADING)
                    result = self.client.connect()
                    self.model_name, self.device = result["model"], result["device"]
                    self.cfg.input_device = result.get("input_device", "")
                    self._devices = self.client.call("list_input_devices")["devices"]
                    self._set_state(State.IDLE)
                event = self.client.events.get(timeout=1)
                if event["type"] == "disconnected":
                    if not self.client.connected:
                        self._set_state(State.ERROR)
                elif event["type"] == "model_changed":
                    self.model_name, self.device = event["model"], event["device"]
                    self.icon.update_menu()
                elif event["type"] == "input_device_changed":
                    self.cfg.input_device = event["input_device"]
                    self.icon.update_menu()
            except queue.Empty:
                continue
            except Exception:
                log.exception("Speech service connection failed")
                self._set_state(State.ERROR)
                self.stopping.wait(3)

    def _quit(self):
        self.stopping.set()
        if self.hotkey:
            self.hotkey.remove()
        self.client.close()
        self.icon.stop()

    def run(self):
        if not ctypes.windll.shell32.IsUserAnAdmin():
            log.warning("Elevated target windows require running Listener as administrator")
        self.hotkey = Hotkey(self.cfg.hotkey, self._on_toggle)
        threading.Thread(target=self._supervise, daemon=True).start()
        self.icon.run()


def main():
    if sys.platform == "darwin":
        from .macos_app import main as macos_main
        return macos_main()
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt,
                        **({"filename": APP_DIR / "listener.log", "encoding": "utf-8"}
                           if getattr(sys, "frozen", False) else {}))
    App(load_config()).run()

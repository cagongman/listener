import ctypes
import enum
import logging
import sys
import threading
import time

if sys.platform == "win32":
    import winsound
else:
    from AppKit import NSSound
    from PyObjCTools.AppHelper import callAfter

import pystray
from PIL import Image, ImageDraw

from .audio import Recorder, input_devices
from .config import APP_DIR, AVAILABLE_MODELS, Config, load_config, save_config
from .hotkey import Hotkey
from .inject import paste_text
from .transcriber import Transcriber

log = logging.getLogger(__name__)


# ponytail: fixed wait so the start beep plays before a Bluetooth headset switches profile; raise it if the beep is still cut off.
START_BEEP_SECONDS = 0.3


class State(enum.Enum):
    LOADING = "loading"
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"


COLORS = {
    State.LOADING: (128, 128, 128),
    State.IDLE: (60, 160, 80),
    State.RECORDING: (220, 50, 50),
    State.PROCESSING: (230, 180, 40),
}


def make_icon(color: tuple[int, int, int], size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), fill=color)
    d.rounded_rectangle((size * 0.38, size * 0.2, size * 0.62, size * 0.6), radius=8, fill=(255, 255, 255))
    d.rectangle((size * 0.48, size * 0.6, size * 0.52, size * 0.78), fill=(255, 255, 255))
    return img


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = State.LOADING
        self._state_lock = threading.Lock()
        self.recorder = Recorder(cfg.sample_rate, cfg.input_device)
        self.transcriber = Transcriber(
            cfg.model, cfg.device, cfg.compute_type, cfg.language,
            cfg.beam_size, cfg.vad_filter, cfg.sample_rate,
        )
        self.icon = pystray.Icon("listener", make_icon(COLORS[State.LOADING]), "Listener", menu=self._menu())
        self.hotkey: Hotkey | None = None
        self._devices = input_devices()

    def _menu(self) -> pystray.Menu:
        def model_item(name: str):
            return pystray.MenuItem(
                name,
                lambda: self._switch_model(name),
                checked=lambda item: self.transcriber.model_name == name,
                radio=True,
            )

        def device_item(label: str, name: str):
            return pystray.MenuItem(
                label,
                lambda: self._switch_device(name),
                checked=lambda item: self.cfg.input_device == name,
                radio=True,
            )

        def device_menu():
            yield device_item("시스템 기본", "")
            yield pystray.Menu.SEPARATOR
            for name in self._devices:
                yield device_item(name, name)
            yield pystray.Menu.SEPARATOR
            yield pystray.MenuItem("목록 새로고침", self._rescan_devices)

        return pystray.Menu(
            pystray.MenuItem(lambda item: f"상태: {self.state.value} ({self.transcriber.device})", None, enabled=False),
            pystray.MenuItem(f"단축키: {self.cfg.hotkey}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("모델", pystray.Menu(*[model_item(m) for m in AVAILABLE_MODELS])),
            pystray.MenuItem("마이크", pystray.Menu(device_menu)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", self._quit),
        )

    def _set_state(self, state: State) -> None:
        self.state = state
        if sys.platform == "darwin":
            callAfter(self._refresh_icon, state)  # AppKit UI must be touched on the main thread
        else:
            self._refresh_icon(state)

    def _refresh_icon(self, state: State) -> None:
        self.icon.icon = make_icon(COLORS[state])
        self.icon.title = f"Listener - {state.value}"
        self.icon.update_menu()

    def _beep(self, start: bool) -> None:
        if sys.platform == "darwin":
            # In-process NSSound starts in a few ms; afplay took ~1s to launch, delaying the mic.
            NSSound.soundNamed_("Tink" if start else "Pop").play()
            if start:
                time.sleep(START_BEEP_SECONDS)
            return
        try:
            winsound.Beep(880 if start else 440, 120)
        except RuntimeError:
            pass

    def _on_toggle(self) -> None:
        with self._state_lock:
            if self.state == State.IDLE:
                self._set_state(State.RECORDING)
                # Beep before opening the mic: a Bluetooth headset switches to its call profile when the
                # mic opens and drops any sound playing during the switch.
                self._beep(True)
                self.recorder.start()
            elif self.state == State.RECORDING:
                audio = self.recorder.stop()
                self._set_state(State.PROCESSING)
                threading.Thread(target=self._beep, args=(False,), daemon=True).start()
                threading.Thread(target=self._process, args=(audio,), daemon=True).start()
            else:
                log.info("Hotkey ignored in state %s", self.state.value)

    def _process(self, audio) -> None:
        try:
            text = self.transcriber.transcribe(audio)
            log.info("Transcribed (%.1fs audio): %r", audio.size / self.cfg.sample_rate, text)
            paste_text(text)
        except Exception:
            log.exception("Transcription failed")
        finally:
            with self._state_lock:
                self._set_state(State.IDLE)

    def _switch_model(self, name: str) -> None:
        with self._state_lock:
            if self.state != State.IDLE or name == self.transcriber.model_name:
                return
            self._set_state(State.LOADING)

        def work():
            try:
                self.transcriber.switch_model(name)
                self.cfg.model = name
                save_config(self.cfg)
            except Exception:
                log.exception("Model switch failed")
            finally:
                with self._state_lock:
                    self._set_state(State.IDLE)

        threading.Thread(target=work, daemon=True).start()

    def _switch_device(self, name: str) -> None:
        with self._state_lock:
            if self.state == State.RECORDING:
                return
            self.recorder.device = name or None
            self.cfg.input_device = name
            save_config(self.cfg)
        log.info("Input device: %s", name or "system default")
        self.icon.update_menu()

    def _rescan_devices(self) -> None:
        with self._state_lock:
            if self.state == State.RECORDING:  # re-initializing PortAudio would kill the open stream
                return
            self._devices = input_devices(rescan=True)
        self.icon.update_menu()

    def _load_model(self) -> None:
        try:
            self.transcriber.load()
        except Exception:
            log.exception("Model load failed")
            self.icon.notify("모델 로드 실패. 로그를 확인하세요.", "Listener")
            return
        with self._state_lock:
            self._set_state(State.IDLE)
        log.info("Ready: %s on %s", self.transcriber.model_name, self.transcriber.device)

    def _quit(self) -> None:
        if self.hotkey:
            self.hotkey.remove()
        if self.recorder.recording:
            self.recorder.stop()
        self.icon.stop()

    def _start_hotkey(self) -> None:
        if sys.platform == "darwin":
            from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

            # The key event tap only works once Accessibility is granted: show the system prompt,
            # then wait for the grant so the user doesn't have to restart the app.
            if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
                log.warning("Waiting for Accessibility permission (System Settings > Privacy & Security)")
                while not AXIsProcessTrusted():
                    time.sleep(1)
                log.info("Accessibility granted")
        self.hotkey = Hotkey(self.cfg.hotkey, self._on_toggle)

    def run(self) -> None:
        if sys.platform == "win32" and not ctypes.windll.shell32.IsUserAnAdmin():
            log.warning("Not running as administrator: hotkey and paste will not work in elevated windows")
        threading.Thread(target=self._start_hotkey, daemon=True).start()
        threading.Thread(target=self._load_model, daemon=True).start()
        self.icon.run()


def main() -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    if getattr(sys, "frozen", False):
        logging.basicConfig(level=logging.INFO, format=fmt, filename=APP_DIR / "listener.log", encoding="utf-8")
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    App(load_config()).run()

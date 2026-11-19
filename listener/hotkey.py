import sys
import threading
from typing import Callable

if sys.platform == "darwin":
    from pynput import keyboard
    from pynput._util.darwin_vks import SYMBOLS  # static vk -> US-layout char table
else:
    import keyboard


def to_pynput(combo: str) -> str:
    """'ctrl+alt+m' (keyboard lib notation) -> '<ctrl>+<alt>+m'."""
    names = {"windows": "cmd", "command": "cmd", "option": "alt"}
    parts = [names.get(p.strip().lower(), p.strip().lower()) for p in combo.split("+")]
    return "+".join(p if len(p) == 1 else f"<{p}>" for p in parts)


class Hotkey:
    def __init__(self, combo: str, on_toggle: Callable[[], None]):
        self.combo = combo
        self._on_toggle = on_toggle
        if sys.platform == "darwin":
            self._hotkey = keyboard.HotKey(keyboard.HotKey.parse(to_pynput(combo)), self._dispatch)
            self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
            self._listener.start()
        else:
            # Callback runs off the keyboard hook thread so the hook returns immediately;
            # suppress must stay False — suppress mode swallows all key input on Windows.
            self._handle = keyboard.add_hotkey(combo, self._dispatch, suppress=False)

    def _canonical(self, key):
        # Match letters by physical key: Option and the Korean IME change the typed char (m -> µ, ㅡ).
        vk = getattr(key, "vk", None)
        if isinstance(key, keyboard.KeyCode) and vk in SYMBOLS:
            return keyboard.KeyCode.from_char(SYMBOLS[vk])
        return self._listener.canonical(key)

    def _press(self, key) -> None:
        self._hotkey.press(self._canonical(key))

    def _release(self, key) -> None:
        self._hotkey.release(self._canonical(key))

    def _dispatch(self) -> None:
        threading.Thread(target=self._on_toggle, daemon=True).start()

    def remove(self) -> None:
        if sys.platform == "darwin":
            self._listener.stop()
        else:
            keyboard.remove_hotkey(self._handle)


if __name__ == "__main__":
    assert to_pynput("ctrl+alt+m") == "<ctrl>+<alt>+m"
    assert to_pynput("Windows+Shift+F9") == "<cmd>+<shift>+<f9>"
    print("ok")

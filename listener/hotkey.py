import threading
from typing import Callable

import keyboard


class Hotkey:
    def __init__(self, combo: str, on_toggle: Callable[[], None]):
        self.combo = combo
        self._on_toggle = on_toggle
        # Callback runs off the keyboard hook thread so the hook returns immediately;
        # suppress must stay False — suppress mode swallows all key input on Windows.
        self._handle = keyboard.add_hotkey(combo, self._dispatch, suppress=False)

    def _dispatch(self) -> None:
        threading.Thread(target=self._on_toggle, daemon=True).start()

    def remove(self) -> None:
        keyboard.remove_hotkey(self._handle)

import time

import keyboard
import pyperclip

MODIFIERS = ("ctrl", "alt", "shift", "windows")


def _wait_modifiers_released(timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(keyboard.is_pressed(m) for m in MODIFIERS):
            return
        time.sleep(0.02)


def paste_text(text: str) -> None:
    if not text:
        return
    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None

    pyperclip.copy(text)
    _wait_modifiers_released()
    time.sleep(0.05)
    keyboard.send("ctrl+v")
    time.sleep(0.15)

    if previous is not None:
        pyperclip.copy(previous)

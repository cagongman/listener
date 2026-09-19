import ctypes
import time

import keyboard
import pyperclip

MODIFIERS = ("ctrl", "alt", "shift", "windows")

# Console hosts read raw key input, so Ctrl+V is not a paste there; type unicode directly instead.
TERMINAL_WINDOW_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}


def _foreground_window_class() -> str:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _wait_modifiers_released(timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(keyboard.is_pressed(m) for m in MODIFIERS):
            return
        time.sleep(0.02)


def _paste_via_clipboard(text: str) -> None:
    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None

    pyperclip.copy(text)
    time.sleep(0.05)
    keyboard.send("ctrl+v")
    time.sleep(0.15)

    if previous is not None:
        pyperclip.copy(previous)


def paste_text(text: str) -> None:
    if not text:
        return
    _wait_modifiers_released()
    if _foreground_window_class() in TERMINAL_WINDOW_CLASSES:
        keyboard.write(text)
    else:
        _paste_via_clipboard(text)

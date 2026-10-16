import ctypes
import sys
import time

import pyperclip

if sys.platform == "darwin":
    import Quartz
    from pynput.keyboard import Controller, Key, KeyCode

    _MOD_MASK = (Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskControl
                 | Quartz.kCGEventFlagMaskAlternate | Quartz.kCGEventFlagMaskShift)
    _kb = Controller()
else:
    import keyboard

MODIFIERS = ("ctrl", "alt", "shift", "windows")

# Console hosts read raw key input, so Ctrl+V is not a paste there; type unicode directly instead.
TERMINAL_WINDOW_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}


def _foreground_window_class() -> str:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _modifiers_down() -> bool:
    if sys.platform == "darwin":
        return bool(Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateCombinedSessionState) & _MOD_MASK)
    return any(keyboard.is_pressed(m) for m in MODIFIERS)


def _wait_modifiers_released(timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _modifiers_down():
            return
        time.sleep(0.02)


def _send_paste() -> None:
    if sys.platform == "darwin":
        with _kb.pressed(Key.cmd):
            _kb.tap(KeyCode.from_vk(9))  # physical V key, independent of input source (Korean IME)
    else:
        keyboard.send("ctrl+v")


def _paste_via_clipboard(text: str) -> None:
    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None

    pyperclip.copy(text)
    time.sleep(0.05)
    _send_paste()
    time.sleep(0.15)

    if previous is not None:
        pyperclip.copy(previous)


def paste_text(text: str) -> None:
    if not text:
        return
    _wait_modifiers_released()
    # macOS terminals accept Cmd+V, so only Windows needs the console fallback.
    if sys.platform == "win32" and _foreground_window_class() in TERMINAL_WINDOW_CLASSES:
        keyboard.write(text)
    else:
        _paste_via_clipboard(text)

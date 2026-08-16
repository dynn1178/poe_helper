"""Key/mouse output wrapper.

Uses ``pydirectinput`` (SendInput-based) instead of plain ``pyautogui`` for
key/mouse output because DirectX-heavy games often ignore the higher-level
message-posting some automation libraries fall back to. All delays are
explicit (no library-level auto-pause) so behaviour matches the original
AHK's tight, randomised ``Sleep`` timings.
"""
from __future__ import annotations

import ctypes
import random
import time

import pydirectinput
import pyperclip

pydirectinput.PAUSE = 0.0
pydirectinput.FAILSAFE = False

_MOUSEEVENTF_WHEEL = 0x0800
_WHEEL_DELTA = 120


def jitter_sleep(min_ms: int, max_ms: int) -> None:
    time.sleep(random.randint(min_ms, max_ms) / 1000.0)


def press(key: str) -> None:
    pydirectinput.press(key)


def key_down(key: str) -> None:
    pydirectinput.keyDown(key)


def key_up(key: str) -> None:
    pydirectinput.keyUp(key)


def get_cursor_pos() -> tuple[int, int]:
    import win32api

    return win32api.GetCursorPos()


def move_to(x: int, y: int) -> None:
    pydirectinput.moveTo(x, y)


def click(x: int, y: int, button: str = "left") -> None:
    pydirectinput.click(x=x, y=y, button=button)


def click_current(button: str = "left") -> None:
    pydirectinput.click(button=button)


def scroll_wheel(notches: int) -> None:
    """Positive = scroll up, negative = scroll down (one notch each)."""
    ctypes.windll.user32.mouse_event(
        _MOUSEEVENTF_WHEEL, 0, 0, notches * _WHEEL_DELTA, 0
    )


def type_text(text: str) -> None:
    """Best-effort literal typing for freeform custom-key strings (e.g. the
    6th flask slot's custom trigger). Unmappable characters are skipped
    rather than raising, since pydirectinput's key table only covers basic
    ASCII."""
    for ch in text:
        try:
            pydirectinput.press(ch.lower())
        except Exception:
            continue


def click_restore(x: int, y: int, button: str = "left", settle_ms: int = 10) -> None:
    """Click a point then return the cursor to where it was before -
    mirrors the AHK pattern of MouseGetPos -> Click -> MouseMove back so the
    player's actual cursor position isn't disturbed mid-fight."""
    origin = get_cursor_pos()
    click(x, y, button)
    time.sleep(settle_ms / 1000.0)
    move_to(*origin)


def copy_hovered_item() -> None:
    """Ask the game to put the hovered item's text on the clipboard.

    Ctrl+C over an item is the client's own feature; this only presses the
    keys. The modifiers are released first for the same reason as the chat
    macros -- the hotkey that triggered this may still be held.
    """
    for mod in ("alt", "shift"):
        key_up(mod)
    time.sleep(0.02)
    pydirectinput.keyDown("ctrl")
    press("c")
    pydirectinput.keyUp("ctrl")


def open_chat_box() -> None:
    """Open the game's chat input and empty it.

    Split out of send_chat_message so a caller can stop after the typing and
    leave the caret in the box. Called on hotkey *release* (see HotkeyManager)
    so a still-held modifier can't collide with the Enter; the explicit
    key_up()s are a second line of defense in case a driver reports the
    release-edge slightly early. Delays are real (not 1ms) because the game's
    chat box needs a moment to take focus/selection between each step.
    """
    for mod in ("alt", "ctrl", "shift"):
        key_up(mod)
    time.sleep(0.03)

    press("enter")  # open the chat input
    time.sleep(0.04)

    pydirectinput.keyDown("ctrl")
    press("a")  # select any text already sitting in the input box
    pydirectinput.keyUp("ctrl")
    press("backspace")  # ...and clear it
    time.sleep(0.02)


def paste_into_chat(text: str) -> None:
    """Paste rather than type: the phrases are Korean as often as not, and
    pydirectinput's key table only covers ASCII."""
    pyperclip.copy(text)
    time.sleep(0.02)
    pydirectinput.keyDown("ctrl")
    press("v")
    pydirectinput.keyUp("ctrl")
    time.sleep(0.03)


def send_chat_message(text: str) -> None:
    """Chat macro sequence: open chat, clear it, paste the phrase, submit."""
    open_chat_box()
    paste_into_chat(text)
    press("enter")  # submit

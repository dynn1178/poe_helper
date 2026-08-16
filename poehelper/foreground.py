"""Foreground-window detection used to scope game hotkeys to PoE only."""
from __future__ import annotations

import logging

import win32gui

logger = logging.getLogger(__name__)
_SW_RESTORE = 9


def foreground_class_name() -> str:
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return ""
    try:
        return win32gui.GetClassName(hwnd)
    except Exception:
        return ""


def is_poe_focused(poe_window_class: str) -> bool:
    if not poe_window_class:
        return True
    return foreground_class_name() == poe_window_class


def focus_game(poe_window_class: str) -> bool:
    """Bring Path of Exile to the foreground, best effort.

    Needed by anything that types into the game in response to a *click*: a
    hotkey only fires while the game is already focused, but a click on a
    panel may have taken the foreground away -- and PoE receives no keyboard
    input at all while it is not active, so the keys would go nowhere.

    ``SetForegroundWindow`` is refused when the caller does not own the
    foreground, so the thread-input attach is the fallback: while our input
    queue is attached to the game's we count as the same context and the call
    is allowed.
    """
    if not poe_window_class:
        return False
    hwnd = win32gui.FindWindow(poe_window_class, None)
    if not hwnd:
        return False
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, _SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        logger.debug("SetForegroundWindow refused; attaching input", exc_info=True)
        _attach_and_focus(hwnd)
    if win32gui.GetForegroundWindow() != hwnd:
        _attach_and_focus(hwnd)
    return win32gui.GetForegroundWindow() == hwnd


def _attach_and_focus(hwnd: int) -> None:
    try:
        import win32api
        import win32process
    except ImportError:
        return
    if not win32gui.GetForegroundWindow():
        return
    try:
        target_thread, _pid = win32process.GetWindowThreadProcessId(hwnd)
        current_thread = win32api.GetCurrentThreadId()
        if target_thread == current_thread:
            return
        win32process.AttachThreadInput(current_thread, target_thread, True)
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            win32process.AttachThreadInput(current_thread, target_thread, False)
    except Exception:
        logger.debug("could not attach input to the game window", exc_info=True)

"""Foreground-window detection used to scope game hotkeys to PoE only."""
from __future__ import annotations

import win32gui


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

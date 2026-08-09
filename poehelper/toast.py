"""Non-blocking on-screen notifications, replacing the original AHK's
blocking ``MsgBox`` calls used during coordinate-calibration flows (those
froze the whole app until the user clicked OK). Safe to call from any
thread -- work is marshalled onto the Tk main loop via ``after()``.

Shown as a passive overlay (``overlay_win``): a toast pops up at the end of
a stash/inventory command, i.e. exactly while the user is playing, and a
plain Toplevel takes the foreground the moment it is mapped -- which stopped
the game (and every window-scoped hotkey here) receiving keys until the user
alt-tabbed back.
"""
from __future__ import annotations

import tkinter as tk

from . import overlay_win

_root: tk.Misc | None = None


def bind_root(root: tk.Misc) -> None:
    global _root
    _root = root


def show(message: str, duration_ms: int = 2500, y_offset: int = 40) -> None:
    if _root is None:
        return
    _root.after(0, _show_impl, message, duration_ms, y_offset)


def _show_impl(message: str, duration_ms: int, y_offset: int) -> None:
    assert _root is not None
    win = overlay_win.new_overlay(_root)
    # Fully opaque on purpose: a whole-window ``-alpha`` also dims the label
    # text inside it, which is what made calibration confirmations hard to
    # read against a bright game screen.

    label = tk.Label(
        win,
        text=message,
        bg="#202020",
        fg="#ffffff",
        font=("맑은 고딕", 11),
        padx=16,
        pady=10,
        justify="left",
    )
    label.pack()

    win.update_idletasks()
    screen_w = win.winfo_screenwidth()
    w = win.winfo_width()
    x = (screen_w - w) // 2
    win.geometry(f"+{x}+{y_offset}")
    overlay_win.show_passive(win)

    win.after(duration_ms, win.destroy)

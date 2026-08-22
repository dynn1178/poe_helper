"""Make a panel over the game go away the way a panel over a game should.

Three ways out, because a window that appears mid-map is read in a second and
then in the way: Esc, space, or a click anywhere that is not it -- which is
usually a click back into the game.

The click has to be found by polling rather than by binding <Button-1>. The
click that dismisses one of these lands in a different process entirely and
never reaches our event loop; and only the press *edge* counts, at the
position the cursor had at that moment, or dragging the window by its header
would dismiss it on release. Both lessons are the price window's, which has
carried this logic since long before it was shared.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import tkinter as tk

logger = logging.getLogger(__name__)

# Fast enough that the window is gone before the click it reacts to finishes
# registering in game, cheap enough to run for the seconds it is on screen.
POLL_MS = 110
_VK_LBUTTON, _VK_RBUTTON = 0x01, 0x02
_DOWN_BIT = 0x8000

if sys.platform == "win32":
    _async_key_state = ctypes.windll.user32.GetAsyncKeyState
    _async_key_state.argtypes = [ctypes.c_int]
    _async_key_state.restype = ctypes.c_short
else:  # tests only
    _async_key_state = None


def mouse_button_down() -> bool:
    if _async_key_state is None:
        return False
    return any(_async_key_state(vk) & _DOWN_BIT for vk in (_VK_LBUTTON, _VK_RBUTTON))


def point_is_ours(x: int, y: int) -> bool:
    """Does the window under (x, y) belong to this process?

    Not "is it inside the panel": CustomTkinter drops an option menu's list
    into a separate popup that extends past the panel's edges, and treating a
    click on one of those as outside would dismiss the panel in the middle of
    choosing something. Every such popup is ours, so ownership -- not
    geometry -- is the question.
    """
    try:
        import win32gui
        import win32process

        hwnd = win32gui.WindowFromPoint((x, y))
        if not hwnd:
            return False
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid == os.getpid()
    except Exception:
        # Better to leave it open than to close it on a guess.
        logger.debug("could not identify the window under the cursor", exc_info=True)
        return True


class DismissWatcher:
    """Wire Esc, space and click-outside to *close* on a Toplevel."""

    def __init__(self, window: tk.Misc, close, *, outside: bool = True):
        self.window = window
        self.close = close
        self.outside = outside
        self._after: str | None = None
        self._closed = False
        # Seeded from the real button state, not from False: these open on a
        # hotkey that may well be pressed with a mouse button already held,
        # and starting at False reads that hold as a fresh click and dismisses
        # the window before it has finished appearing.
        self._was_down = mouse_button_down()

        window.bind("<Escape>", lambda _e: self.close())
        window.bind("<space>", self._on_space)
        self._after = window.after(POLL_MS, self._poll)

    def stop(self) -> None:
        self._closed = True
        if self._after is not None:
            try:
                self.window.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _on_space(self, _event: tk.Event) -> str | None:
        """Space dismisses -- unless a text box has the caret, where it is
        just a keystroke."""
        try:
            focused = self.window.focus_get()
        except (tk.TclError, KeyError):
            focused = None
        if isinstance(focused, (tk.Entry, tk.Text)) or "Entry" in type(focused).__name__:
            return None
        self.close()
        return "break"

    def _poll(self) -> None:
        if self._closed:
            return
        self._after = None
        down = mouse_button_down()
        pressed = down and not self._was_down
        self._was_down = down
        if self.outside and pressed and not self._cursor_over_us():
            self.close()
            return
        try:
            self._after = self.window.after(POLL_MS, self._poll)
        except tk.TclError:
            pass

    def _cursor_over_us(self) -> bool:
        try:
            x, y = self.window.winfo_pointerxy()
            left, top = self.window.winfo_rootx(), self.window.winfo_rooty()
            if (
                left <= x < left + self.window.winfo_width()
                and top <= y < top + self.window.winfo_height()
            ):
                return True
        except tk.TclError:
            return True
        return point_is_ours(x, y)

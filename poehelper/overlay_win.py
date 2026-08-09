"""Make an always-on-top Tk window behave like a real game overlay.

A plain ``tk.Toplevel`` -- even an ``overrideredirect``/``-topmost`` one --
is a perfectly ordinary activatable window on Windows: showing it takes the
foreground away from Path of Exile, and clicking it makes it the active
window. Both are fatal here, and in the same way:

* PoE only receives keyboard input while it is the *foreground* window, so
  the moment a cooldown bar, a peek image or a toast takes the foreground,
  the game itself stops seeing keys (pressing ``b`` does nothing) until the
  user alt-tabs back. That is the "가끔 명령을 실행하고 나면 단축키가 먹히지
  않는" bug: the toast shown at the end of a stash/inventory command was
  quietly stealing focus from the game.
* Every hotkey in this app is additionally scoped to "is PoE focused?"
  (``foreground.is_poe_focused``), so the same focus theft silently disabled
  our own hotkeys too -- ``hotkey ignored (window scope)`` in the log.

Three extended styles fix it, and they have to be set *before* the window is
first mapped, or the very first show still steals the foreground:

``WS_EX_NOACTIVATE``
    The window is never made active, no matter how it is shown or clicked.
``WS_EX_TRANSPARENT``
    Removes it from mouse hit-testing entirely, so a click goes straight
    through to the game underneath as if the overlay were not there -- which
    matters most for the flask bars, since they sit directly on top of the
    flask row the player clicks.
``WS_EX_TOOLWINDOW``
    Keeps overlays out of the alt-tab list.

Everything here is best-effort: any failure leaves a plain (if focus-stealing)
overlay rather than taking the feature down with it.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import tkinter as tk
from ctypes import wintypes

logger = logging.getLogger(__name__)

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000

_IS_WINDOWS = sys.platform == "win32"


def _user32():
    if not _IS_WINDOWS:
        return None
    user32 = ctypes.windll.user32
    # 64-bit safe: the ...Ptr variants exist only on win64, and using the
    # plain 32-bit ones there truncates the value we write back.
    getter = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    getter.restype = ctypes.c_ssize_t
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    setter.restype = ctypes.c_ssize_t
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    return user32, getter, setter


def _hwnd_of(win: tk.Misc) -> int:
    """The real top-level HWND behind a Tk window.

    Tk puts its own child HWND inside a wrapper window that actually carries
    the window styles, so ``winfo_id()`` alone is the wrong handle to poke.
    ``wm frame`` reports the wrapper directly; ``GetParent`` of the Tk child
    is the fallback for the rare case Tcl hands back something unusable.
    """
    try:
        hwnd = int(win.wm_frame(), 16)
    except (ValueError, tk.TclError):
        hwnd = 0
    parts = _user32()
    if parts is None:
        return hwnd
    user32 = parts[0]
    if hwnd and user32.IsWindow(hwnd):
        return hwnd
    try:
        child = win.winfo_id()
    except tk.TclError:
        return 0
    return user32.GetParent(child) or child


def make_passive(win: tk.Misc, click_through: bool = True) -> None:
    """Apply the overlay extended styles to an already-realised window.

    Prefer :func:`show_passive`, which also guarantees the styles land before
    the first map; this is the escape hatch for a window that is already up.
    """
    parts = _user32()
    if parts is None:
        return
    _user32_handle, getter, setter = parts
    hwnd = _hwnd_of(win)
    if not hwnd:
        return
    flags = _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
    if click_through:
        flags |= _WS_EX_TRANSPARENT
    try:
        # OR only -- never assign: Tk sets WS_EX_LAYERED itself for
        # ``-alpha``/``-transparentcolor``, and clearing that would make the
        # bars opaque black plates.
        current = getter(hwnd, _GWL_EXSTYLE)
        setter(hwnd, _GWL_EXSTYLE, current | flags)
    except OSError:
        logger.debug("could not apply overlay styles", exc_info=True)


def show_passive(win: tk.Misc, click_through: bool = True) -> None:
    """Reveal *win* as a non-activating overlay.

    The window must have been created withdrawn (see ``new_overlay``): the
    extended styles are applied while it is still hidden, so its very first
    appearance already cannot take the foreground. Applying them after the
    first map is too late -- that map is exactly when the focus is stolen.
    """
    try:
        win.update_idletasks()  # realise the HWND without mapping it
    except tk.TclError:
        return
    make_passive(win, click_through)
    try:
        win.deiconify()
    except tk.TclError:
        pass


def new_overlay(master: tk.Misc, *, topmost: bool = True) -> tk.Toplevel:
    """A borderless, hidden-until-``show_passive`` overlay Toplevel."""
    win = tk.Toplevel(master)
    win.withdraw()  # stays hidden until the styles are on -- see show_passive
    win.overrideredirect(True)
    if topmost:
        win.attributes("-topmost", True)
    return win

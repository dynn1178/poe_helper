"""Stop CustomTkinter from painting long widget lists to the screen one
row at a time.

``CTkScrollbar._draw()`` and ``CTkOptionMenu._draw()`` both end with a
``self._canvas.update_idletasks()`` call (see customtkinter 6.0.0,
``windows/widgets/ctk_scrollbar.py:168`` and ``ctk_optionmenu.py:223``).
That forces Tk to flush *every* pending geometry + repaint job right then,
synchronously, in the middle of whatever else is being built.

That matters a lot here because a ``CTkScrollableFrame`` wires its inner
canvas's ``yscrollcommand`` to its scrollbar: every child packed into it
changes the scrollregion -> scrollbar ``set()`` -> ``_draw()`` -> forced
flush. So mapping a list of N rows (the chat-macro table, the game-hotkey
list) produced N separate screen updates and the rows visibly marched in
one at a time instead of appearing together.

The forced flush is not needed for correctness -- Tk processes idle work
on the next pass through the event loop anyway -- it only makes the repaint
happen *sooner*, at the cost of never letting Tk batch. Neutralising it on
these two widgets' private canvases lets the whole list land in a single
paint. Nothing else in this project calls ``update_idletasks()`` on a
CTkCanvas, so no other code path is affected.

Tied to the customtkinter version pinned in this project's venv, in the
same spirit as the dropdown swap in ``widgets/hotkey_picker.py``.
"""
from __future__ import annotations

import customtkinter as ctk

_installed = False


def _no_flush() -> None:
    """Replacement for ``canvas.update_idletasks`` -- deliberately a no-op."""


def _patch_init(cls: type) -> None:
    original_init = cls.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # Bound per instance (not on the class) so only these internal
        # canvases lose the forced flush.
        self._canvas.update_idletasks = _no_flush

    cls.__init__ = __init__


def install() -> None:
    """Idempotent; called once when ``poehelper.gui.app`` is imported."""
    global _installed
    if _installed:
        return
    _patch_init(ctk.CTkScrollbar)
    _patch_init(ctk.CTkOptionMenu)
    _installed = True

"""Stop CustomTkinter from painting long widget lists to the screen one
row at a time -- while they are being built, and again on every resize.

Two separate library behaviours cause the same visible symptom.

**1. The forced flush.**
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

**2. The synchronous redraw on every ``<Configure>``.**
``CTkBaseClass.__init__`` binds ``<Configure>`` to
``_update_dimensions_event``, which -- whenever the widget's own size
changed -- calls ``self._draw()`` right there, inside the event handler
(``core_widget_classes/ctk_base_class.py:182``). CustomTkinter gives every
widget its own ``CTkCanvas`` and redraws it by deleting and re-creating its
rounded-rectangle polygons, so this is real work per widget, not a cheap
invalidate.

Dragging the main window's edge resizes every widget inside it, and Tk
delivers those ``<Configure>`` events one at a time, deepest-first. Each
one repaints one widget and yields, so the tab visibly re-renders piece by
piece -- controls popping back in a row at a time -- for the whole drag,
and with a few hundred widgets on a tab the drag itself goes to a crawl.

The fix here is not to skip the redraw but to *stop doing it inside the
event handler*. The new size is still recorded immediately, so any layout
or geometry query made in between sees the truth; only the canvas repaint
is deferred, to a single ``after_idle`` pass per toplevel that redraws
every widget that changed size in this burst, back to back, with no event
processing in between. Tk then puts the lot on screen in one paint.
Coalescing is per widget, so a widget that gets ten ``<Configure>`` events
during one drag step is still drawn once.

``after_idle`` (rather than a timer) keeps this invisible to the rest of
the app: ``update_idletasks()`` runs idle callbacks, so the existing
"build it, flush it, then show it" sequences in ``gui/app.py`` still get a
fully painted tab out of their flush the way they did before.

Tied to the customtkinter version pinned in this project's venv, in the
same spirit as the dropdown swap in ``widgets/hotkey_picker.py``.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any

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


# Widgets whose size changed and whose canvas has not been repainted yet,
# grouped by the toplevel they live in: one pending idle pass per window, and
# a window that is destroyed takes its pending batch with it. Keyed by Tk
# pathname (a str) rather than by the widget, because CTk widgets are
# tkinter.Frame subclasses and hashing one that has already been destroyed is
# not something to rely on. Insertion order is Tk's own delivery order, so
# the batch repaints parents before the children laid out inside them.
_pending: dict[str, dict[str, Any]] = {}


def _flush_dirty(key: str) -> None:
    for widget in list(_pending.pop(key, {}).values()):
        _redraw(widget)


def _redraw(widget: Any) -> None:
    try:
        if widget.winfo_exists():
            widget._draw(no_color_updates=True)
    except tk.TclError:
        pass  # destroyed between being queued and being drawn


def _mark_dirty(widget: Any) -> None:
    try:
        key = str(widget.winfo_toplevel())
    except tk.TclError:
        return
    batch = _pending.get(key)
    if batch is None:
        try:
            widget.winfo_toplevel().after_idle(_flush_dirty, key)
        except tk.TclError:
            # No event loop to defer into (a window on its way out).
            _redraw(widget)
            return
        batch = _pending[key] = {}
    batch[str(widget)] = widget


def _patch_resize_redraw() -> None:
    """Defer ``_update_dimensions_event``'s repaint; keep its bookkeeping.

    Mirrors customtkinter 6.0.0's own guard (redraw only when the size
    actually changed, measured unscaled) so nothing is repainted that the
    library would not have repainted.
    """

    def _update_dimensions_event(self, event):
        width = self._reverse_widget_scaling(event.width)
        height = self._reverse_widget_scaling(event.height)
        if round(self._current_width) == round(width) and round(self._current_height) == round(height):
            return
        # Recorded now, drawn later: anything that reads the widget's
        # dimensions before the idle pass still sees the new size.
        self._current_width = width
        self._current_height = height
        _mark_dirty(self)

    ctk.CTkBaseClass._update_dimensions_event = _update_dimensions_event


def install() -> None:
    """Idempotent; called once when ``poehelper.gui.app`` is imported."""
    global _installed
    if _installed:
        return
    _patch_init(ctk.CTkScrollbar)
    _patch_init(ctk.CTkOptionMenu)
    _patch_resize_redraw()
    _installed = True

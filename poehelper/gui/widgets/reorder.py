"""Drag-to-reorder for a list of packed rows.

Shared by the chat-macro table and the link list, which are both "a column of
rows whose order is the saved order". Arrow buttons were tried first and cost
a stacked pair of controls on every row, which made each row taller and the
list busier than the thing it was listing.

Tk has no built-in reordering, and pack() has no insert-at-index, so a move
is: work out which row the pointer is over, reorder the backing list, and
re-pack every row. That is cheap at these list sizes and keeps the widgets
themselves stateless -- the list is the single source of truth for order.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

GRIP = "⠿"          # same handle the route/layout overlays use
GRIP_WIDTH = 14

# How close to the edge of the scroll area a drag has to get before the view
# follows it. Without this, a 16-row list cannot be reordered past whatever
# happens to be visible.
_EDGE_PX = 18


def _effective_background(widget: tk.Widget) -> str | None:
    """The colour actually showing behind *widget*, as a plain Tk colour.

    A plain tk.Label has to be told its background or it renders as a grey
    plate on the row. The row itself is ``fg_color="transparent"``, so the
    answer is the nearest ancestor that sets a real colour -- and CTk stores
    that as a (light, dark) pair which ``_apply_appearance_mode`` resolves.
    ``cget("bg")`` is no help: CustomTkinter raises ValueError for it.
    """
    node: tk.Misc | None = widget
    while node is not None:
        try:
            colour = node.cget("fg_color")
        except (ValueError, tk.TclError, AttributeError):
            colour = None
        if colour and colour != "transparent":
            try:
                return node._apply_appearance_mode(colour)
            except (AttributeError, ValueError, tk.TclError):
                return None
        node = getattr(node, "master", None)
    return None


class DragReorder:
    """Attach drag handles to rows and keep ``rows`` in the dropped order.

    ``rows`` is mutated in place, so callers must not rebind the attribute
    (use ``rows[:] = ...`` if the whole order is being replaced).
    """

    def __init__(
        self,
        *,
        rows: list,
        widget_of: Callable[[object], tk.Widget],
        repack: Callable[[], None],
        on_drop: Callable[[], None],
        scroll_canvas: tk.Canvas | None = None,
        highlight: str = "#d8d8dc",
    ):
        self.rows = rows
        self.widget_of = widget_of
        self.repack = repack
        self.on_drop = on_drop
        self.scroll_canvas = scroll_canvas
        self.highlight = highlight
        self._entry = None
        self._moved = False

    # ---- handle -----------------------------------------------------------
    def handle(self, parent: tk.Widget, entry: object) -> tk.Label:
        """A grip for *entry*'s row, already bound.

        A plain tk.Label rather than a CTkLabel: CustomTkinter puts its text
        in an inner widget, so both the cursor and the button press would
        stop at the wrapper and never reach a binding on it.
        """
        grip = tk.Label(
            parent, text=GRIP, width=2, cursor="fleur",
            bd=0, highlightthickness=0, fg="#9a9aa2",
        )
        background = _effective_background(parent)
        if background:
            grip.configure(bg=background)
        grip.bind("<ButtonPress-1>", lambda e, en=entry: self._press(en))
        grip.bind("<B1-Motion>", self._motion)
        grip.bind("<ButtonRelease-1>", self._release)
        return grip

    # ---- drag -------------------------------------------------------------
    def _press(self, entry: object) -> None:
        self._entry = entry
        self._moved = False
        self._set_row_colour(entry, self.highlight)

    def _motion(self, event: tk.Event) -> None:
        if self._entry is None:
            return
        self._autoscroll(event.y_root)
        target = self._row_at(event.y_root)
        if target is None:
            return
        current = self.rows.index(self._entry)
        if target == current:
            return
        self.rows.insert(target, self.rows.pop(current))
        self._moved = True
        # Only the dragged row is re-placed. Forgetting and re-packing every
        # row (which is what a general repack has to do) rebuilds the whole
        # layout on every mouse move, and the list visibly flickered the
        # entire time it was being dragged. pack_configure(before=/after=)
        # moves one widget within the existing order and leaves the rest
        # untouched, so nothing else is redrawn.
        self._place(target)

    def _release(self, _event: tk.Event) -> None:
        if self._entry is None:
            return
        self._set_row_colour(self._entry, "transparent")
        entry, self._entry = self._entry, None
        if self._moved:
            self.on_drop()

    # ---- helpers ----------------------------------------------------------
    def _place(self, index: int) -> None:
        """Move the row at *index* into position without touching the others.

        ``pack_configure`` keeps every option the row was originally packed
        with (fill, pady); it only changes where it sits in the order.
        """
        widget = self.widget_of(self.rows[index])
        try:
            if index == 0:
                if len(self.rows) > 1:
                    widget.pack_configure(before=self.widget_of(self.rows[1]))
            else:
                widget.pack_configure(after=self.widget_of(self.rows[index - 1]))
        except tk.TclError:
            self.repack()  # a row was destroyed mid-drag; rebuild instead

    def _row_at(self, y_root: int) -> int | None:
        """Index of the row under this screen y, or None if past the ends."""
        for index, entry in enumerate(self.rows):
            widget = self.widget_of(entry)
            if not widget.winfo_exists():
                continue
            top = widget.winfo_rooty()
            if top <= y_root < top + widget.winfo_height():
                return index
        return None

    def _autoscroll(self, y_root: int) -> None:
        canvas = self.scroll_canvas
        if canvas is None or not canvas.winfo_exists():
            return
        top = canvas.winfo_rooty()
        bottom = top + canvas.winfo_height()
        if y_root < top + _EDGE_PX:
            canvas.yview_scroll(-1, "units")
        elif y_root > bottom - _EDGE_PX:
            canvas.yview_scroll(1, "units")

    def _set_row_colour(self, entry: object, colour: str) -> None:
        widget = self.widget_of(entry)
        try:
            if widget.winfo_exists():
                widget.configure(fg_color=colour)
        except (tk.TclError, ValueError):
            pass

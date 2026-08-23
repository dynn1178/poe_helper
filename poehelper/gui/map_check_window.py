"""Show a copied map against the filter, with the reason marked on the map.

The question this answers is never "did it match" -- the game answers that by
showing or hiding the map. It is *why*: which of this map's modifiers the
filter caught, and whether the filter is landing on modifiers at all rather
than on the rules blurb under a curse.

So the map's modifiers are listed in the order the client printed them, and
the ones the filter landed on are shaded in place:

* **red** -- 제외 mode. This line is why the map is thrown away.
* **blue** -- 포함 mode. This line is why the map is kept.
* **amber** -- the filter landed on something that is not a modifier at all
  (a ``{ ... }`` affix header, a parenthesised reminder). The map is being
  judged on text that is not a mod, which means the fragment is wrong; that
  is counted in the footer rather than shown as a row.

Everything else stays dim, so the shaded rows are the only thing to read.

Dismissed the way the price window is -- Esc, space, or a click back into the
game (see widgets/dismiss.py) -- because it is opened on a hotkey in the
middle of sorting a stash tab and is read in about a second.

Shaped like Awakened PoE Trade's panels rather than like a dialog: no title
bar, no legend, no per-row annotations, padding in single pixels, and a
height measured off the content instead of a fixed 660x600 that was mostly
empty grey on a five-mod map. What is left is the modifier list and the
shading, which is the entire question, answered in the second it takes to
glance at where the colour is.
"""
from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk

import customtkinter as ctk

from .. import map_mods
from ..map_mods import EXCLUDE, INCLUDE_ALL
from . import theme
from .widgets.dismiss import DismissWatcher
from .widgets.tooltip import Tooltip

logger = logging.getLogger(__name__)

_MODE_LABELS = {
    "include": "포함(하나)", INCLUDE_ALL: "포함(모두)", EXCLUDE: "제외",
}

# One colour per reason a line is worth looking at. Amber is deliberately not
# red: a hit on a header or a reminder is a bug in the fragment, not a verdict
# about the map, and colouring it the same as a real exclusion would hide the
# difference this window exists to show.
#
# Single values, not the light/dark pairs these used to be: the panel is its
# own dark surface (theme.OVERLAY_*) regardless of the app's appearance mode,
# the same way the price window is, so there is no second mode to answer for.
_EXCLUDE_COLOR = "#f07a7a"
_INCLUDE_COLOR = "#7ab8ef"
_SUSPECT_COLOR = "#e0a33a"
# Row fills. Tinted rather than saturated: the row still has to be read, and
# the text on it is already carrying the same colour.
_INCLUDE_SHADE = "#1d3450"
_EXCLUDE_SHADE = "#4a2020"
_PLAIN = theme.OVERLAY_TEXT_DIM
_DIM = "#6e6e73"

# Row metrics. Every row here is one line of text tall plus a pixel: the
# padding is what made the old window 600px tall for eight modifiers.
_ROW_PAD_Y = 1
_ROW_PAD_X = 7
# Never taller than this share of the screen. A map with more modifiers than
# fit scrolls; every shorter one gets a window exactly its own height.
_MAX_SCREEN_SHARE = 0.7
_MIN_W, _MAX_W = 300, 560


def _mod_lines(item_text: str) -> list[str]:
    """The map's modifiers, as the item parser understands them.

    Not "every line that is not a header or a reminder", which is what a
    shape test gives and which also returns the separators, the map's name
    and its tier. The price check's parser already knows an item's structure
    -- which block is properties, which is modifiers -- so it is asked
    instead of the question being answered twice, differently.

    Falls back to the shape test when the parser cannot read the item, so a
    map it has not learned still shows something.
    """
    try:
        from ..trade import item as trade_item

        parsed = trade_item.parse(item_text)
    except Exception:  # noqa: BLE001 - the window must open regardless
        logger.debug("could not parse the map", exc_info=True)
        parsed = None
    if parsed is not None and parsed.mods:
        return [mod.text.strip() for mod in parsed.mods]
    return [
        line.strip()
        for line in item_text.splitlines()
        if line.strip() and map_mods.classify_line(line) == map_mods.MOD_LINE
    ]


def matched_lines(pattern: str, item_text: str) -> dict[str, list[str]]:
    """Line text -> the fragments that landed on it.

    Keyed by the line rather than by the fragment, because the map is what is
    being drawn and each of its lines needs to know whether anything caught
    it. The same line can be caught by several fragments and each is worth
    naming.
    """
    _mode, conditions = map_mods.split_pattern(pattern)
    found: dict[str, list[str]] = {}
    for condition in conditions:
        try:
            compiled = re.compile(condition)
        except re.error:
            continue  # a half-typed hand edit; the header reports it
        for line in item_text.splitlines():
            if line.strip() and compiled.search(line):
                found.setdefault(line.strip(), []).append(condition)
    return found


class MapCheckWindow(ctk.CTkToplevel):
    def __init__(self, master, pattern: str, item_text: str, on_closed=None):
        super().__init__(master)
        self.pattern = pattern
        self.item_text = item_text
        self.on_closed = on_closed
        self._closed = False
        self._drag_origin: tuple[int, int] | None = None
        self._body: ctk.CTkScrollableFrame | None = None

        self.title("지도 검사")
        # No OS titlebar, same as the price window: this is a panel thrown up
        # over the game for a second, and the grey Windows chrome alone is
        # taller than several of the rows underneath it.
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=theme.OVERLAY_BG)
        self.protocol("WM_DELETE_WINDOW", self.close)

        mode, _conditions = map_mods.split_pattern(pattern)
        matched, _report = map_mods.describe_matches(pattern, item_text)
        # 제외 keeps the maps the pattern did *not* match; every other mode
        # keeps the ones it did.
        kept = (not matched) if mode == EXCLUDE else matched

        self._build(mode, kept, matched_lines(pattern, item_text))
        self._fit_to_content()
        self._dismiss = DismissWatcher(self, self.close)
        self.after(60, self._take_focus)

    # ---- widgets ----------------------------------------------------------
    def _build(self, mode: str, kept: bool, hits: dict[str, list[str]]) -> None:
        colour = _EXCLUDE_COLOR if mode == EXCLUDE else _INCLUDE_COLOR
        shading = _EXCLUDE_SHADE if mode == EXCLUDE else _INCLUDE_SHADE
        mods = _mod_lines(self.item_text)
        shown = sum(1 for line in mods if hits.get(line))

        card = ctk.CTkFrame(
            self, corner_radius=8, fg_color=theme.OVERLAY_BG,
            border_width=1, border_color=theme.OVERLAY_BORDER,
        )
        card.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_header(card, mode, kept, colour, shown, len(mods))
        self._build_rows(card, mods, hits, colour, shading)
        self._build_footer(card, hits)

    def _build_header(
        self, card, mode: str, kept: bool, colour: str, shown: int, total: int
    ) -> None:
        """One 24px line: the verdict, the mode, the count, a close button.

        The pattern itself moved onto a hover tooltip. It is by far the
        longest string here and it is read about once a month -- when a
        fragment is suspected of being wrong -- whereas the verdict is read
        every time a map is picked up.
        """
        head = ctk.CTkFrame(
            card, fg_color=theme.OVERLAY_SURFACE, corner_radius=6, height=24,
        )
        head.pack(fill="x", padx=2, pady=(2, 1))
        head.pack_propagate(False)

        ctk.CTkButton(
            head, text="×", width=18, height=18,
            font=(theme.FONT_FAMILY, 12, "bold"), corner_radius=4,
            fg_color="transparent", hover_color=theme.DANGER,
            text_color=theme.OVERLAY_TEXT_DIM, command=self.close,
        ).pack(side="right", padx=(2, 3))

        verdict = ctk.CTkLabel(
            head, text="남습니다" if kept else "걸러집니다",
            font=(theme.FONT_FAMILY, 12, "bold"), text_color=colour, anchor="w",
        )
        verdict.pack(side="left", padx=(8, 0))
        detail = ctk.CTkLabel(
            head, text=f"{_MODE_LABELS.get(mode, mode)} · {shown}/{total}",
            font=theme.FONT_SMALL, text_color=theme.OVERLAY_TEXT_DIM, anchor="w",
        )
        detail.pack(side="left", padx=(6, 0))
        for widget in (head, verdict, detail):
            Tooltip(widget, self.pattern, wraplength=420)
        # overrideredirect() took the titlebar away, so the header is the
        # handle -- otherwise a panel that lands on top of the item it is
        # describing cannot be moved off it.
        self._bind_drag(head, verdict, detail)

    def _build_rows(self, card, mods, hits, colour: str, shading: str) -> None:
        """The map's modifiers, one per line, shaded where the filter landed.

        Nothing else on the row -- not the fragment that caught it, not a
        marker column. Which rows carry colour *is* the answer, and a second
        column of text beside them gets read instead of the colour.
        """
        body = ctk.CTkScrollableFrame(
            card, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=theme.OVERLAY_ACCENT,
            scrollbar_button_hover_color=theme.OVERLAY_ACCENT_HOVER,
        )
        body.pack(fill="both", expand=True, padx=2, pady=(1, 2))
        self._body = body

        if not mods:
            ctk.CTkLabel(
                body, text="이 지도에는 옵션이 없습니다.", font=theme.FONT_BODY,
                text_color=_DIM, anchor="w",
            ).pack(fill="x", padx=_ROW_PAD_X, pady=3)
            return

        for line in mods:
            hit = bool(hits.get(line))
            # Shaded, not annotated: a filled row reads as "this one" at a
            # glance down a list, where a marker beside it has to be found.
            row = ctk.CTkFrame(
                body, fg_color=shading if hit else "transparent", corner_radius=4,
            )
            row.pack(fill="x", pady=_ROW_PAD_Y)
            ctk.CTkLabel(
                row, text=line,
                font=theme.FONT_BODY_STRONG if hit else theme.FONT_BODY,
                text_color=colour if hit else _PLAIN,
                anchor="w", justify="left", wraplength=_MAX_W - 60,
            ).pack(fill="x", padx=_ROW_PAD_X, pady=1)

    def _build_footer(self, card, hits: dict[str, list[str]]) -> None:
        """Only on screen when there is something wrong to say.

        A fragment landing on an affix header or a reminder paragraph is a
        bug in the fragment, and dropping those lines from the list would
        hide it too -- so it is still counted, just not spelled out line by
        line. When the count is zero there is no footer at all and the panel
        ends at the last modifier.
        """
        stray = sum(
            1
            for line, _f in hits.items()
            if map_mods.classify_line(line)
            not in (map_mods.MOD_LINE, map_mods.PROPERTY_LINE)
        )
        if not stray:
            return
        ctk.CTkLabel(
            card, text=f"⚠ 옵션이 아닌 줄에 걸린 조각 {stray}개",
            font=theme.FONT_SMALL, text_color=_SUSPECT_COLOR, anchor="w",
        ).pack(fill="x", padx=_ROW_PAD_X + 2, pady=(0, 3))

    # ---- geometry ---------------------------------------------------------
    def _fit_to_content(self) -> None:
        """Size the window to what is in it, then put it under the cursor.

        The old fixed 660x600 was picked for the worst case and left most of
        itself empty: eight modifiers is about 200px, and the rest was a grey
        field the eye still had to cross.

        Asking the window what it wants is not enough on its own, because a
        ``CTkScrollableFrame`` is a fixed-size canvas with the real content
        scrolling behind it -- it asks for its ``height=`` (200 by default)
        no matter how many rows are in it. So the list is measured on the
        *inner* frame, the canvas is resized to that, and only then is the
        window asked what it now needs.
        """
        self.update_idletasks()
        cap = int(self.winfo_screenheight() * _MAX_SCREEN_SHARE)
        body = self._body
        if body is not None:
            with contextlib.suppress(tk.TclError, AttributeError):
                # Everything the window needs that is not the list itself.
                chrome = self.winfo_reqheight() - body._parent_canvas.winfo_reqheight()
                content = body.winfo_reqheight()
                room = max(60, cap - chrome)
                # The scrollbar is a permanent grid column, so on the maps
                # that fit -- nearly all of them -- it is an empty gutter
                # down the side of a panel whose whole point is not having
                # any. Taken out unless the list really is taller.
                if content <= room:
                    body._scrollbar.grid_remove()
                body.configure(
                    width=body._reverse_widget_scaling(body.winfo_reqwidth()),
                    height=body._reverse_widget_scaling(min(content, room)),
                )
                self.update_idletasks()

        width = max(_MIN_W, min(_MAX_W, self.winfo_reqwidth()))
        height = min(cap, self.winfo_reqheight())

        x, y = self._spawn_point(width, height)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _spawn_point(self, width: int, height: int) -> tuple[int, int]:
        """Just below-right of the cursor, kept fully on screen.

        The map is under the pointer when the hotkey is pressed, so that is
        where the eye already is; a panel that opens in the middle of the
        screen makes it travel there and back.
        """
        try:
            x = self.winfo_pointerx() + 18
            y = self.winfo_pointery() + 18
            screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        except tk.TclError:
            return 100, 100
        return max(0, min(x, screen_w - width)), max(0, min(y, screen_h - height))

    def _bind_drag(self, *widgets) -> None:
        for widget in widgets:
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_origin = (
            event.x_root - self.winfo_x(), event.y_root - self.winfo_y(),
        )

    def _do_drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        dx, dy = self._drag_origin
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _take_focus(self) -> None:
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._dismiss.stop()
        if self.on_closed is not None:
            self.on_closed()
        try:
            self.destroy()
        except tk.TclError:
            pass

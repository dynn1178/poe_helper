"""Show a copied map against the filter, with the reason marked on the map.

The question this answers is never "did it match" -- the game answers that by
showing or hiding the map. It is *why*: which line on this particular map the
filter caught, and whether that line is a modifier at all or a stray hit on
the rules blurb under a curse.

So the map is shown whole, exactly as the client printed it, and the lines the
filter landed on are coloured in place:

* **red** -- 제외 mode. This line is why the map is thrown away.
* **blue** -- 포함 mode. This line is why the map is kept.
* **amber** -- the filter landed here, but the line is a ``{ ... }`` affix
  header or a parenthesised reminder rather than a modifier. The map is being
  judged on text that is not a mod, which means the fragment is wrong.

Everything else stays dim, so the coloured lines are the only thing to read.

Dismissed the way the price window is -- Esc, space, or a click back into the
game (see widgets/dismiss.py) -- because it is opened on a hotkey in the
middle of sorting a stash tab and is read in about a second.
"""
from __future__ import annotations

import logging
import re
import tkinter as tk

import customtkinter as ctk

from .. import map_mods
from ..map_mods import EXCLUDE, INCLUDE_ALL
from . import theme
from .widgets.dismiss import DismissWatcher

logger = logging.getLogger(__name__)

_MODE_LABELS = {
    "include": "포함(하나)", INCLUDE_ALL: "포함(모두)", EXCLUDE: "제외",
}

# One colour per reason a line is worth looking at. Amber is deliberately not
# red: a hit on a header or a reminder is a bug in the fragment, not a verdict
# about the map, and colouring it the same as a real exclusion would hide the
# difference this window exists to show.
_EXCLUDE_COLOR = "#e05252"
_INCLUDE_COLOR = "#4f9ae0"
_SUSPECT_COLOR = "#e0a33a"
_PLAIN = ("#3a3a3c", "#c8c8cc")
_DIM = ("#8a8a8e", "#6e6e73")


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

        self.title("지도 검사")
        self.geometry("660x600")
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.close)

        mode, _conditions = map_mods.split_pattern(pattern)
        matched, _report = map_mods.describe_matches(pattern, item_text)
        # 제외 keeps the maps the pattern did *not* match; every other mode
        # keeps the ones it did.
        kept = (not matched) if mode == EXCLUDE else matched

        self._build(mode, kept, matched_lines(pattern, item_text))
        self._dismiss = DismissWatcher(self, self.close)
        self.after(60, self._take_focus)

    # ---- widgets ----------------------------------------------------------
    def _build(self, mode: str, kept: bool, hits: dict[str, list[str]]) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            head,
            text="이 지도는 검색에 남습니다" if kept else "이 지도는 걸러집니다",
            font=theme.FONT_TITLE,
            text_color=_INCLUDE_COLOR if kept else _EXCLUDE_COLOR,
        ).pack(anchor="w")
        ctk.CTkLabel(
            head, text=f"{_MODE_LABELS.get(mode, mode)}   ·   {self.pattern}",
            font=theme.FONT_CAPTION, text_color=theme.PRIMARY, anchor="w",
            wraplength=600, justify="left",
        ).pack(anchor="w", pady=(2, 0))

        legend = ctk.CTkFrame(self, fg_color="transparent")
        legend.pack(fill="x", padx=14, pady=(6, 2))
        colour = _EXCLUDE_COLOR if mode == EXCLUDE else _INCLUDE_COLOR
        for text, fg in (
            (f"■ 조건에 걸린 옵션 ({'제외' if mode == EXCLUDE else '포함'})", colour),
            ("■ 옵션이 아닌 줄에 걸림 (조각이 잘못됨)", _SUSPECT_COLOR),
        ):
            ctk.CTkLabel(legend, text=text, font=theme.FONT_CAPTION, text_color=fg).pack(
                side="left", padx=(0, 14)
            )

        body = ctk.CTkScrollableFrame(self, fg_color=("#f6f6f8", "#232325"))
        body.pack(fill="both", expand=True, padx=14, pady=(4, 8))

        shown = 0
        for raw in self.item_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            fragments = hits.get(line)
            kind = map_mods.classify_line(line)
            if fragments:
                suspect = kind not in (map_mods.MOD_LINE, map_mods.PROPERTY_LINE)
                fg = _SUSPECT_COLOR if suspect else colour
                font = theme.FONT_BODY_STRONG
                shown += 1
            else:
                fg = _DIM if kind != map_mods.MOD_LINE else _PLAIN
                font = theme.FONT_BODY
            ctk.CTkLabel(
                body, text=line, font=font, text_color=fg,
                anchor="w", justify="left", wraplength=590,
            ).pack(fill="x", anchor="w", padx=6, pady=1)
            if fragments:
                ctk.CTkLabel(
                    body, text="       ↳ " + ", ".join(fragments),
                    font=theme.FONT_CAPTION, text_color=fg,
                    anchor="w", justify="left", wraplength=560,
                ).pack(fill="x", anchor="w", padx=6)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(
            foot,
            text=(
                f"걸린 줄 {shown}개   ·   스페이스바 · Esc · 다른 곳 클릭으로 닫기"
                if shown
                else "이 지도에는 조건에 걸리는 줄이 없습니다   ·   "
                     "스페이스바 · Esc · 다른 곳 클릭으로 닫기"
            ),
            font=theme.FONT_CAPTION, text_color=theme.PRIMARY, anchor="w",
        ).pack(side="left")

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

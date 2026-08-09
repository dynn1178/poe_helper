"""Searchable picker listing every known PoE1 chat slash-command, so a chat
macro row can be created from the reference pool instead of the user having
to remember exact console syntax."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from ... import poe_commands
from .. import theme


class CommandPickerDialog(ctk.CTkToplevel):
    def __init__(self, master, on_pick: Callable[[str], None]):
        super().__init__(master)
        # Hidden until every command row is built -- a Toplevel is mapped
        # (visible) the instant it's created, so without this the ~70 rows
        # below were built one-by-one in full view, showing up as a
        # sequential "list loading" flash every time this dialog opened.
        self.attributes("-alpha", 0.0)
        self.on_pick = on_pick
        self.title("PoE1 명령어에서 추가")
        self.geometry("420x480")
        self.attributes("-topmost", True)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.search_var = ctk.StringVar()
        entry = ctk.CTkEntry(top, textvariable=self.search_var, placeholder_text="명령어 검색...")
        entry.pack(fill="x")
        self.search_var.trace_add("write", lambda *_: self._render())
        entry.focus_set()

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._render()
        self.update()
        self.attributes("-alpha", 1.0)

    def _render(self) -> None:
        # Also re-run on every keystroke in the search box (see trace_add
        # above) to re-filter the list. Deliberately NOT re-hiding the whole
        # dialog on every one of those calls the way the initial open above
        # does: toggling this Toplevel's alpha off/on for every keystroke
        # would flicker the search box itself (part of the same window)
        # while typing, which is worse than the rebuild it'd be covering up.
        # Filtered re-renders are also normally a smaller row count than the
        # full ~70-command list, so the rebuild itself is quick enough that
        # this hasn't shown up as a visible flash the way the initial open
        # (always the full list) did.
        for child in list(self.scroll.winfo_children()):
            child.destroy()

        query = self.search_var.get().strip().lower()
        for command, desc in poe_commands.POE1_COMMANDS:
            if query and query not in command.lower() and query not in desc.lower():
                continue
            row = ctk.CTkFrame(self.scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkButton(
                row, text="추가", width=44, command=lambda c=command: self._pick(c)
            ).pack(side="left", padx=(0, 8))
            text_col = ctk.CTkFrame(row, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(text_col, text=command, anchor="w", font=theme.FONT_BODY_STRONG).pack(fill="x")
            ctk.CTkLabel(
                text_col, text=desc, anchor="w", justify="left",
                text_color=("#7a7a7a", "#8e8e93"), font=theme.FONT_CAPTION,
            ).pack(fill="x")

    def _pick(self, command: str) -> None:
        self.on_pick(command)

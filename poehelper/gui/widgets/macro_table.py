"""Editable list of chat-phrase macros: chat text first, then its
rebindable hotkey (label field and hotkey-capture button removed -- the
text itself is the identity of the row, and combo selection covers binding
needs)."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from .. import theme
from .command_picker import CommandPickerDialog
from .hotkey_picker import HotkeyPicker
from .tooltip import Tooltip


class MacroTableEditor(ctk.CTkFrame):
    def __init__(
        self,
        master,
        macros: list[dict],
        on_change: Callable[[list[dict]], None] | None = None,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_change = on_change
        self.rows: list[dict] = []

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="채팅 문구").pack(side="left", padx=(0, 4))
        ctk.CTkLabel(header, text="단축키", width=180).pack(side="right", padx=(4, 34))

        self.scroll = ctk.CTkScrollableFrame(self, height=280)
        self.scroll.pack(fill="both", expand=True)
        for macro in macros:
            self._add_row(macro.get("hotkey", ""), macro.get("text", ""))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(6, 0))
        ctk.CTkButton(
            btn_row, text="+ 문구 추가", command=lambda: self._add_row("", "")
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            btn_row, text="+ PoE 명령어에서 추가", command=self._open_command_picker
        ).pack(side="left")

    def _open_command_picker(self) -> None:
        CommandPickerDialog(self, on_pick=lambda cmd: self._add_row("", cmd))

    def _add_row(self, hotkey: str, text: str) -> None:
        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.pack(fill="x", pady=3)

        text_var = ctk.StringVar(value=text)

        ctk.CTkEntry(
            row, textvariable=text_var, placeholder_text="채팅 문구"
        ).pack(side="left", padx=(0, 4), fill="x", expand=True)
        picker = HotkeyPicker(row, on_change=lambda _c: self._notify(), show_capture=False)
        picker.set_combo(hotkey)
        picker.pack(side="left", padx=(0, 4))

        entry = {"row": row, "picker": picker, "text": text_var}
        ctk.CTkButton(
            row,
            text="X",
            width=28,
            fg_color=theme.DANGER,
            hover_color=theme.DANGER_HOVER,
            command=lambda: self._remove_row(entry),
        ).pack(side="left")

        tooltip = Tooltip(row, "")
        entry["tooltip"] = tooltip
        self._refresh_tooltip(entry)

        self.rows.append(entry)
        text_var.trace_add("write", lambda *_: (self._refresh_tooltip(entry), self._notify()))

    def _refresh_tooltip(self, entry: dict) -> None:
        text = entry["text"].get().strip()
        if text:
            entry["tooltip"].set_text(f"이 단축키를 누르면 채팅창에 다음 문구가 입력됩니다:\n{text}")
        else:
            entry["tooltip"].set_text("이 단축키를 누르면 채팅창에 왼쪽 문구가 입력됩니다.")

    def _remove_row(self, entry: dict) -> None:
        entry["row"].destroy()
        self.rows.remove(entry)
        self._notify()

    def get_macros(self) -> list[dict]:
        result = []
        for entry in self.rows:
            combo = entry["picker"].get_combo()
            text = entry["text"].get()
            if text or combo:
                result.append({"hotkey": combo, "text": text})
        return result

    def _notify(self) -> None:
        if self.on_change:
            self.on_change(self.get_macros())

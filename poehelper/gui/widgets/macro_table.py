"""Editable list of chat-phrase macros: the text, whether it is sent through
the game's chat box, and its rebindable hotkey.

The ENTER 필요 tick is what separates the two things this list is used for. A
slash-command or a whisper has to go through the chat box -- opened, typed
into, submitted with Enter -- and that is what the box being ticked means.
Untick it and the phrase is only put on the clipboard and pasted wherever
the caret already is: a trade whisper you want to edit before sending, a
note going into a stash-tab name, a search string for the item filter box.
Sending Enter into those is at best wrong and at worst destructive.

(Label field and hotkey-capture button were removed -- the text itself is
the identity of the row, and combo selection covers binding needs.)"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from .. import theme
from .command_picker import CommandPickerDialog
from .hotkey_picker import HotkeyPicker
from .reorder import DragReorder
from .tooltip import Tooltip

# The ENTER 필요 column. One constant so the heading and the tick under it
# cannot drift apart.
_CHAT_COL_WIDTH = 72
# What one row is tall -- the height CTkEntry and the hotkey picker take. Only
# the checkbox's holder needs telling, but it has to agree with them or it
# sets the height of the whole row.
_ROW_HEIGHT = 28
# The heading strip. Fixed, because its two right-hand labels are placed
# rather than packed and so contribute nothing to the frame's own size --
# and an unsized CTkFrame asks for 200px.
_HEADER_HEIGHT = 22


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

        # The two right-hand headings are positioned from the widgets they
        # label rather than packed at a guessed width. Packing them meant
        # repeating the rows' widths here -- 180 for the hotkey picker, 34 to
        # clear the delete button -- and every one of those numbers was
        # wrong: the picker is 271 wide, so both headings sat about a hundred
        # pixels right of the column they named. Reading the real geometry
        # cannot drift, and re-runs whenever the window is resized.
        self.header = ctk.CTkFrame(self, fg_color="transparent", height=_HEADER_HEIGHT)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        # All three headings are positioned from the widgets they label, and
        # centred over them -- including 채팅 문구, which packed at the left
        # edge sat a long way from the middle of the wide text column it
        # names. A label placed at its column's x and width centres itself,
        # since that is CTkLabel's default anchor.
        #
        # Sized on construction, not in place(): CustomTkinter rejects width
        # and height as place() arguments outright.
        def heading(text: str) -> ctk.CTkLabel:
            return ctk.CTkLabel(
                self.header, text=text, width=_CHAT_COL_WIDTH, height=_HEADER_HEIGHT
            )

        self.text_head = heading("채팅 문구")
        # Named for what ticking it does rather than for the thing it is
        # about: "채팅" over a column of checkboxes says nothing about which
        # way is which, and this is the only control here whose two states do
        # visibly different things to the game.
        self.chat_head = heading("ENTER 필요")
        self.key_head = heading("단축키")
        self.header.bind("<Configure>", lambda _e: self._align_headers())

        self.scroll = ctk.CTkScrollableFrame(self, height=280)
        self.scroll.pack(fill="both", expand=True)
        self._drag = DragReorder(
            rows=self.rows,
            widget_of=lambda entry: entry["row"],
            repack=self._repack,
            on_drop=self._notify,
            scroll_canvas=self.scroll._parent_canvas,
        )
        for macro in macros:
            # Missing means True: every macro that existed before this option
            # did went through the chat box, and a saved config must keep
            # behaving the way it did.
            self._add_row(
                macro.get("hotkey", ""),
                macro.get("text", ""),
                bool(macro.get("chat", True)),
            )

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(6, 0))
        ctk.CTkButton(
            btn_row, text="+ 문구 추가", command=lambda: self._add_row("", "")
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            btn_row, text="+ PoE 명령어에서 추가", command=self._open_command_picker
        ).pack(side="left")

    def _open_command_picker(self) -> None:
        # A slash-command is only meaningful typed into the chat box, so one
        # picked from the list arrives with 채팅 already ticked.
        CommandPickerDialog(self, on_pick=lambda cmd: self._add_row("", cmd, True))

    def _add_row(self, hotkey: str, text: str, chat: bool = True) -> None:
        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.pack(fill="x", pady=3)

        text_var = ctk.StringVar(value=text)
        chat_var = ctk.BooleanVar(value=chat)
        entry = {"row": row, "text": text_var, "chat": chat_var}

        # Order is meaningful -- it is the order saved, and the order shown --
        # so rows are draggable by this grip.
        self._drag.handle(row, entry).pack(side="left", padx=(0, 2))

        text_entry = ctk.CTkEntry(
            row, textvariable=text_var, placeholder_text="채팅 문구"
        )
        text_entry.pack(side="left", padx=(0, 4), fill="x", expand=True)
        entry["entry"] = text_entry
        # In a fixed-size holder so the tick sits centred under its heading.
        # A bare checkbox with an empty label still reserves room for the
        # label, which left a wide dead strip to its right and pushed the
        # hotkey picker out of line with the heading above it.
        #
        # The height is as deliberate as the width. A CTkFrame asks for 200px
        # when it is not told otherwise, and pack_propagate(False) -- needed
        # so the checkbox cannot shrink the cell back to its own width --
        # makes it *keep* that 200. Every row became a 200px band with one
        # checkbox floating in the middle of it.
        chat_cell = ctk.CTkFrame(
            row, fg_color="transparent", width=_CHAT_COL_WIDTH, height=_ROW_HEIGHT
        )
        chat_cell.pack(side="left", padx=(0, 4))
        chat_cell.pack_propagate(False)
        entry["chat_cell"] = chat_cell
        chat_box = ctk.CTkCheckBox(
            chat_cell, text="", width=18,
            variable=chat_var, checkbox_width=18, checkbox_height=18,
            command=lambda: (self._refresh_tooltip(entry), self._notify()),
        )
        chat_box.pack(expand=True)
        Tooltip(
            chat_box,
            "체크: Enter로 채팅창을 열고 문구를 넣은 뒤 Enter로 전송합니다.\n"
            "  (/명령어, 귓속말처럼 채팅으로 보내야 하는 문구)\n\n"
            "해제: Enter를 전혀 누르지 않고, 클립보드에 복사한 뒤 붙여넣기만 합니다.\n"
            "  (보관함 이름, 아이템 필터 검색창, 보내기 전에 고칠 귓속말)",
        )

        picker = HotkeyPicker(row, on_change=lambda _c: self._notify(), show_capture=False)
        picker.set_combo(hotkey)
        picker.pack(side="left", padx=(0, 4))
        entry["picker"] = picker

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
        # The first row is what the headings measure themselves against, so a
        # list that was empty until now has to re-align.
        self.after_idle(self._align_headers)

    def _align_headers(self) -> None:
        """Put each right-hand heading over the column it names.

        Measured off the first row rather than reproduced from constants,
        because the two are laid out by different code: the row packs a
        checkbox holder and a HotkeyPicker, and only the picker knows how
        wide it is. Positions are converted through screen coordinates since
        the heading and the row sit in different parents -- the rows are
        inside a scrollable frame with padding of its own.
        """
        if not self.rows:
            # Nothing to line up with, and a heading over an empty list names
            # nothing.
            for label in (self.text_head, self.chat_head, self.key_head):
                label.place_forget()
            return
        first = self.rows[0]
        try:
            origin = self.header.winfo_rootx()
            for label, widget in (
                (self.text_head, first["entry"]),
                (self.chat_head, first["chat_cell"]),
                (self.key_head, first["picker"]),
            ):
                width = widget.winfo_width()
                if width <= 1:
                    return  # not laid out yet; the next <Configure> will do it
                label.configure(width=width)
                label.place(x=widget.winfo_rootx() - origin, y=0)
        except tk.TclError:
            pass  # widget destroyed mid-layout

    def _refresh_tooltip(self, entry: dict) -> None:
        text = entry["text"].get().strip()
        how = (
            "채팅창에 입력하고 전송합니다"
            if entry["chat"].get()
            else "붙여넣기만 합니다 (Enter 없음)"
        )
        if text:
            entry["tooltip"].set_text(f"이 단축키를 누르면 다음 문구를 {how}:\n{text}")
        else:
            entry["tooltip"].set_text(f"이 단축키를 누르면 왼쪽 문구를 {how}.")

    def _remove_row(self, entry: dict) -> None:
        entry["row"].destroy()
        self.rows.remove(entry)
        self._notify()
        self.after_idle(self._align_headers)

    def _repack(self) -> None:
        """Re-lay the rows in ``self.rows`` order.

        pack() appends, so moving a row means forgetting them all and adding
        them back in the new order -- there is no "insert at index" for a
        packed widget.
        """
        for entry in self.rows:
            entry["row"].pack_forget()
        for entry in self.rows:
            entry["row"].pack(fill="x", pady=3)

    def sort_unbound_last(self) -> None:
        """Rows with a hotkey first, unbound ones after, order kept within each.

        Called from the 저장 button. A phrase with no key assigned cannot fire,
        so it is only ever in the way of the ones that can -- but it is still
        worth keeping, which is why they sink instead of being dropped.
        """
        bound = [e for e in self.rows if e["picker"].get_combo()]
        unbound = [e for e in self.rows if not e["picker"].get_combo()]
        if self.rows == bound + unbound:
            return
        # In place: DragReorder holds a reference to this exact list.
        self.rows[:] = bound + unbound
        self._repack()
        self._notify()

    def get_macros(self) -> list[dict]:
        result = []
        for entry in self.rows:
            combo = entry["picker"].get_combo()
            text = entry["text"].get()
            if text or combo:
                result.append(
                    {"hotkey": combo, "text": text, "chat": bool(entry["chat"].get())}
                )
        return result

    def _notify(self) -> None:
        if self.on_change:
            self.on_change(self.get_macros())

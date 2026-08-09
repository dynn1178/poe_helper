"""Memo tab: a card grid (one card per memo) that switches to a full text
editor on double-click, replacing the old single always-open memo Toplevel.
Any http(s) link typed into the text is auto-highlighted -- left-click opens
it directly, right-click offers open/copy/favorite.

(A rich-text formatting toolbar -- font/size/bold/color -- used to live here
but was pulled: applying it while typing kept corrupting how Korean IME
composition rendered, and no amount of retiming the tag application fully
fixed it. Plain text only, for now.)"""
from __future__ import annotations

import re
import tkinter as tk
import uuid
import webbrowser
from typing import Callable

import customtkinter as ctk

from ..config import Config
from . import theme
from .widgets.tooltip import Tooltip

_URL_PATTERN = re.compile(r"https?://\S+")
_CARD_COLUMNS = 3


class MemoTab(ctk.CTkFrame):
    def __init__(self, master, config: Config, on_favorite_added: Callable[[str], None], **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.config = config
        self.on_favorite_added = on_favorite_added
        self._link_ranges: list[tuple[str, str, str]] = []
        self._suppress_save = False
        self._current_memo_id: str | None = None

        self.card_view = ctk.CTkFrame(self, fg_color="transparent")
        self.detail_view = ctk.CTkFrame(self, fg_color="transparent")

        self._build_card_view()
        self._build_detail_view()

        self._show_card_view()

    # ---- data -----------------------------------------------------------
    def _memos(self) -> list[dict]:
        return self.config.data["memos"]

    def _find_memo(self, memo_id: str) -> dict | None:
        for m in self._memos():
            if m["id"] == memo_id:
                return m
        return None

    # ---- card grid view ---------------------------------------------------
    def _build_card_view(self) -> None:
        top = ctk.CTkFrame(self.card_view, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkButton(top, text="+ 새 메모", width=90, command=self._new_memo).pack(side="left")
        hint = ctk.CTkLabel(
            top, text="카드를 더블클릭하면 메모 내용으로 들어갑니다.", text_color=("#7a7a7a", "#8e8e93")
        )
        hint.pack(side="left", padx=(10, 0))

        self.card_scroll = ctk.CTkScrollableFrame(self.card_view, fg_color="transparent")
        self.card_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        for col in range(_CARD_COLUMNS):
            self.card_scroll.grid_columnconfigure(col, weight=1)

        self._render_cards()

    # No 저장 button anywhere in this tab: the title entry (_on_name_changed)
    # and the editor (_on_text_changed) both call config.save() on every
    # keystroke, and creating/deleting a memo saves too -- so a manual save
    # had nothing left to write and only implied that unsaved work existed.

    def _render_cards(self) -> None:
        for child in list(self.card_scroll.winfo_children()):
            child.destroy()

        for idx, memo in enumerate(self._memos()):
            row, col = divmod(idx, _CARD_COLUMNS)
            self._build_card(memo, row, col)

    def _build_card(self, memo: dict, row: int, col: int) -> None:
        card = ctk.CTkFrame(
            self.card_scroll, corner_radius=12, fg_color=("#fafafc", "#2a2a2c"),
            border_width=1, border_color=("#e0e0e0", "#3a3a3c"),
        )
        card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

        name_label = ctk.CTkLabel(card, text=memo.get("name", ""), font=theme.FONT_BODY_STRONG, anchor="w")
        name_label.pack(fill="x", padx=10, pady=(10, 2))

        preview_text = (memo.get("text", "") or "").strip().replace("\n", " ")
        if len(preview_text) > 60:
            preview_text = preview_text[:60] + "…"
        preview = ctk.CTkLabel(
            card, text=preview_text or "(내용 없음)", anchor="w", justify="left",
            wraplength=150, text_color=("#7a7a7a", "#8e8e93"), font=theme.FONT_CAPTION,
        )
        preview.pack(fill="x", padx=10, pady=(0, 8))

        delete_btn = ctk.CTkButton(
            card, text="X", width=22, height=22, fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            command=lambda: self._delete_memo(memo["id"]),
        )
        delete_btn.place(relx=1.0, x=-6, y=6, anchor="ne")

        for widget in (card, name_label, preview):
            widget.bind("<Double-Button-1>", lambda _e, mid=memo["id"]: self._open_detail(mid))
            Tooltip(widget, "더블클릭하면 이 메모 내용으로 들어갑니다.")

    def _new_memo(self) -> None:
        n = len(self._memos()) + 1
        memo = {"id": uuid.uuid4().hex[:8], "name": f"메모 {n}", "text": ""}
        self._memos().append(memo)
        self.config.save()
        self._render_cards()
        self._open_detail(memo["id"])

    def _delete_memo(self, memo_id: str) -> None:
        memos = self._memos()
        if len(memos) <= 1:
            return
        memo = self._find_memo(memo_id)
        if memo is not None:
            memos.remove(memo)
            self.config.save()
        self._render_cards()

    def _show_card_view(self) -> None:
        self.detail_view.pack_forget()
        self._render_cards()
        self.card_view.pack(fill="both", expand=True)

    # ---- detail (editor) view ---------------------------------------------
    def _build_detail_view(self) -> None:
        top = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkButton(top, text="← 목록으로", width=90, command=self._show_card_view).pack(side="left")

        self.name_var = ctk.StringVar()
        name_entry = ctk.CTkEntry(top, textvariable=self.name_var, width=160)
        name_entry.pack(side="left", padx=(10, 4))
        self.name_var.trace_add("write", lambda *_: self._on_name_changed())

        ctk.CTkButton(
            top, text="삭제", width=50, fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            command=self._delete_current,
        ).pack(side="left", padx=(4, 0))

        # Internal padx/pady (not just padding around the widget) so the
        # memo doesn't read as text jammed against the edges.
        # height=1: a plain tk.Text defaults to a 24-line requested height
        # regardless of the fill="both"/expand=True below, which counted
        # towards the whole window's total requested height -- once that
        # exceeded the fixed 620x680 window size, pack compressed whatever
        # else it could to make up the difference, which was the app's
        # bottom bar (트레이로 최소화/단축키 정지/종료): squeezed from its
        # normal ~28px down to ~11px tall every time the memo editor (not
        # the card list) was open. height=1 makes the *requested* size
        # negligible; the actual on-screen size still comes entirely from
        # fill/expand, so nothing about the editor's real size changes.
        self.text = tk.Text(
            self.detail_view, wrap="word", undo=True, font=("맑은 고딕", 11),
            padx=14, pady=12, borderwidth=0, height=1,
        )
        self.text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.text.tag_configure("link", foreground="#2997ff", underline=True)
        self.text.tag_bind("link", "<Button-1>", self._on_link_click)
        self.text.tag_bind("link", "<Enter>", lambda _e: self.text.configure(cursor="hand2"))
        self.text.tag_bind("link", "<Leave>", lambda _e: self.text.configure(cursor=""))
        self.text.bind("<KeyRelease>", self._on_text_changed)
        self.text.bind("<Button-3>", self._on_right_click)
        # Tkinter's Text widget treats Tab as "move focus to the next
        # widget" by default, which -- with nothing else in the tab order
        # nearby -- can silently hand focus off to unrelated buttons
        # (including the window's tray/exit buttons at the bottom). Typing
        # a literal tab character is the behavior users actually expect
        # while writing a memo.
        self.text.bind("<Tab>", self._on_tab_key)

    def _open_detail(self, memo_id: str) -> None:
        memo = self._find_memo(memo_id)
        if memo is None:
            return
        self._current_memo_id = memo_id
        self.config.data["active_memo_id"] = memo_id

        self._suppress_save = True
        self.name_var.set(memo.get("name", ""))
        self.text.delete("1.0", "end")
        self.text.insert("1.0", memo.get("text", ""))
        self._suppress_save = False
        self._highlight_links()

        self.card_view.pack_forget()
        self.detail_view.pack(fill="both", expand=True)

    def _on_tab_key(self, _event: tk.Event) -> str:
        self.text.insert("insert", "\t")
        return "break"

    def _current_memo(self) -> dict | None:
        if self._current_memo_id is None:
            return None
        return self._find_memo(self._current_memo_id)

    def _on_name_changed(self) -> None:
        if self._suppress_save:
            return
        memo = self._current_memo()
        if memo is None:
            return
        memo["name"] = self.name_var.get()
        self.config.save()

    def _delete_current(self) -> None:
        if self._current_memo_id is None:
            return
        self._delete_memo(self._current_memo_id)
        self._show_card_view()

    def _on_text_changed(self, _event: tk.Event) -> None:
        if self._suppress_save:
            return
        memo = self._current_memo()
        if memo is None:
            return
        memo["text"] = self.text.get("1.0", "end-1c")
        self.config.save()
        self._highlight_links()

    # ---- links: left-click opens, right-click offers more -----------------
    def _highlight_links(self) -> None:
        self.text.tag_remove("link", "1.0", "end")
        self._link_ranges = []
        content = self.text.get("1.0", "end-1c")
        for m in _URL_PATTERN.finditer(content):
            start, end = f"1.0+{m.start()}c", f"1.0+{m.end()}c"
            self.text.tag_add("link", start, end)
            self._link_ranges.append((start, end, m.group(0)))

    def _url_at_event(self, event: tk.Event) -> str | None:
        index = self.text.index(f"@{event.x},{event.y}")
        for start, end, url in self._link_ranges:
            if self.text.compare(index, ">=", start) and self.text.compare(index, "<", end):
                return url
        return None

    def _on_link_click(self, event: tk.Event) -> None:
        url = self._url_at_event(event)
        if url:
            webbrowser.open(url)

    def _on_right_click(self, event: tk.Event) -> None:
        url = self._url_at_event(event)
        if url:
            self._show_link_menu(event, url)

    def _show_link_menu(self, event: tk.Event, url: str) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="열기", command=lambda: webbrowser.open(url))
        menu.add_command(label="복사", command=lambda: self._copy(url))
        menu.add_command(label="즐겨찾기에 추가", command=lambda: self._add_favorite(url))
        menu.tk_popup(event.x_root, event.y_root)

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    def _add_favorite(self, url: str) -> None:
        name = url.split("//", 1)[-1][:24]
        self.config.data["links"].append({"name": name, "url": url})
        self.config.save()
        self.on_favorite_added(url)

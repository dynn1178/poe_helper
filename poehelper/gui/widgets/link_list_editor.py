"""CRUD editor for the quick-launch link buttons. Rows can hold a web URL,
a local file, or a folder -- each row has its own "열기" button that opens
the target immediately (browser for http(s) links, the file/folder's
default handler otherwise via ``os.startfile``)."""
from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from .. import theme
from .reorder import DragReorder
from .tooltip import Tooltip


def open_target(value: str) -> None:
    value = (value or "").strip()
    if not value:
        return
    if value.startswith("http://") or value.startswith("https://"):
        webbrowser.open(value)
        return
    path = Path(value)
    if path.exists():
        os.startfile(str(path))  # noqa: S606 -- Windows-only app, opens file/folder w/ default handler
        return
    webbrowser.open(value)


class LinkListEditor(ctk.CTkFrame):
    def __init__(
        self,
        master,
        links: list[dict],
        on_change: Callable[[list[dict]], None] | None = None,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_change = on_change
        self.rows: list[list] = []
        self._loading = False

        self.scroll = ctk.CTkScrollableFrame(self, height=220)
        self.scroll.pack(fill="both", expand=True)
        self._drag = DragReorder(
            rows=self.rows,
            widget_of=lambda entry: entry[0],
            repack=self._repack,
            on_drop=self._notify,
            scroll_canvas=self.scroll._parent_canvas,
        )
        # Silent while restoring saved links: _add_row() notifies on every
        # row, and the tab's on_change writes config.json and fans out to
        # every config listener (which re-registers all global hotkeys).
        # Merely displaying N saved links did all of that N times, for no
        # change at all.
        self._loading = True
        try:
            for link in links:
                self._add_row(link.get("name", ""), link.get("url", ""))
        finally:
            self._loading = False

        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.pack(pady=(6, 0))
        ctk.CTkButton(add_row, text="+ 링크 추가", command=lambda: self._add_row("", "")).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(add_row, text="+ 파일 추가", command=self._add_file).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(add_row, text="+ 폴더 추가", command=self._add_folder).pack(side="left")

    def _add_row(self, name: str, url: str) -> None:
        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.pack(fill="x", pady=2)

        name_var = ctk.StringVar(value=name)
        url_var = ctk.StringVar(value=url)
        entry = [row, name_var, url_var]

        # The list order is the button order in the tab, so rows are
        # draggable by this grip.
        self._drag.handle(row, entry).pack(side="left", padx=(0, 2))

        ctk.CTkEntry(row, textvariable=name_var, width=90, placeholder_text="이름").pack(
            side="left", padx=(0, 4)
        )
        ctk.CTkEntry(
            row, textvariable=url_var, width=220, placeholder_text="https://... 또는 파일/폴더 경로"
        ).pack(side="left", padx=(0, 4), fill="x", expand=True)
        open_btn = ctk.CTkButton(
            row, text="열기", width=44, command=lambda: open_target(url_var.get())
        )
        open_btn.pack(side="left", padx=(0, 4))
        Tooltip(open_btn, "클릭하면 이 링크(또는 파일/폴더)를 바로 엽니다.")
        ctk.CTkButton(
            row,
            text="X",
            width=28,
            fg_color=theme.DANGER,
            hover_color=theme.DANGER_HOVER,
            command=lambda: self._remove_row(entry),
        ).pack(side="left")

        self.rows.append(entry)
        name_var.trace_add("write", lambda *_: self._notify())
        url_var.trace_add("write", lambda *_: self._notify())
        self._notify()

    def _add_file(self) -> None:
        chosen = filedialog.askopenfilename()
        if chosen:
            self._add_row(Path(chosen).name, chosen)

    def _add_folder(self) -> None:
        chosen = filedialog.askdirectory()
        if chosen:
            self._add_row(Path(chosen).name, chosen)

    def _remove_row(self, entry: list) -> None:
        entry[0].destroy()
        self.rows.remove(entry)
        self._notify()

    def _repack(self) -> None:
        """Re-lay the rows in ``self.rows`` order.

        pack() only appends, so a reorder means forgetting every row and
        adding them back in the new order.
        """
        for entry in self.rows:
            entry[0].pack_forget()
        for entry in self.rows:
            entry[0].pack(fill="x", pady=2)

    def get_links(self) -> list[dict]:
        return [
            {"name": name_var.get(), "url": url_var.get()}
            for (_row, name_var, url_var) in self.rows
            if name_var.get() or url_var.get()
        ]

    def _notify(self) -> None:
        if self.on_change and not self._loading:
            self.on_change(self.get_links())

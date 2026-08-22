"""CRUD editor for the quick-launch shortcut buttons. A row can hold a web
URL, a local file, or a folder, and each one says which it is so a list of
thirty is readable at a glance. Every row has its own "열기" button that
opens the target immediately."""
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


# What a row points at. Read off the value rather than stored, so a row
# edited from a URL into a folder path relabels itself as you type.
KIND_SITE, KIND_FOLDER, KIND_FILE, KIND_MISSING = "site", "folder", "file", "missing"

_KIND_LABEL = {
    KIND_SITE: ("사이트", "#5b9bd5"),
    KIND_FOLDER: ("폴더", "#e0a458"),
    KIND_FILE: ("파일", "#7fb069"),
    KIND_MISSING: ("없음", "#b0b0b4"),
}


def kind_of(value: str) -> str:
    """Which of the three kinds this value is, or that it points nowhere.

    A path that no longer exists is worth saying out loud: a shortcut to a
    program that has since been moved or uninstalled otherwise looks exactly
    like a working one until the day you press it.
    """
    value = (value or "").strip()
    if not value:
        return KIND_MISSING
    if value.startswith(("http://", "https://")):
        return KIND_SITE
    path = Path(value)
    if path.is_dir():
        return KIND_FOLDER
    if path.is_file():
        return KIND_FILE
    return KIND_MISSING


def open_target(value: str) -> None:
    """Open a URL, file or folder the way double-clicking it would.

    The working directory matters and is the whole reason this does not
    simply call ``os.startfile(value)``. Explorer runs a program *from its
    own folder*; ``os.startfile`` hands the child whatever directory this
    app happens to be running in. A program that looks for its own data
    relative to the current directory then finds this app's folder instead,
    and behaves as if half its files were missing.

    That is not hypothetical. The Korean Path of Building launcher
    (PoeCharm3) reads ``Data/Settings.conf`` and ``Data/Translate/ko-KR`` by
    relative path and builds the path to its own ``Path of Building.exe`` the
    same way. Started from here it opened, then died with an access violation
    the moment its 작동 button was pressed -- while the identical exe
    double-clicked in Explorer worked every time. ``hotkeys/actions.py``
    already learned this lesson for the 실행 buttons; this is the same fix for
    the shortcut list.
    """
    value = (value or "").strip()
    if not value:
        return
    if value.startswith(("http://", "https://")):
        webbrowser.open(value)
        return
    path = Path(value)
    if path.exists():
        directory = str(path.parent if path.is_file() else path)
        # noqa: S606 -- Windows-only app, opens file/folder with its default handler
        os.startfile(str(path), cwd=directory)
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

        # What this row points at, in front of the name where it can be read
        # straight down the column. Fixed width so the name fields stay in
        # line whichever kind each row happens to be.
        kind_label = ctk.CTkLabel(
            row, text="", width=42, font=theme.FONT_CAPTION, anchor="w"
        )
        kind_label.pack(side="left", padx=(0, 4))
        entry.append(kind_label)

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
        self._refresh_kind(entry)
        name_var.trace_add("write", lambda *_: self._notify())
        url_var.trace_add(
            "write", lambda *_: (self._refresh_kind(entry), self._notify())
        )
        self._notify()

    def _refresh_kind(self, entry: list) -> None:
        """Relabel a row as its value is edited.

        Read from the value rather than remembered, so pasting a folder path
        over a URL relabels the row without anything else having to be told.
        """
        _row, _name_var, url_var, label = entry[:4]
        text, colour = _KIND_LABEL[kind_of(url_var.get())]
        label.configure(text=text, text_color=colour)

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
            for (_row, name_var, url_var, *_rest) in self.rows
            if name_var.get() or url_var.get()
        ]

    def _notify(self) -> None:
        if self.on_change and not self._loading:
            self.on_change(self.get_links())

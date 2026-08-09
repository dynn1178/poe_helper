"""Hover tooltip: attaches a small delayed popup describing what a hotkey
actually does when pressed, so labels stop being the only source of truth
(requirement: every hotkey title must explain its script behaviour on
hover).

``HelpMark`` is the discoverable form of the same thing. A tooltip bound to a
plain label is invisible until hovered by accident, so most of the help text
in this app was never found; the "?" badge advertises that an explanation
exists before the pointer goes anywhere near it.
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk


class Tooltip:
    _DELAY_MS = 350

    def __init__(self, widget: tk.Widget, text: str, wraplength: int = 320):
        self.widget = widget
        self.text = text
        self.wraplength = wraplength
        self._after_id: str | None = None
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self._DELAY_MS, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._win is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8

        win = tk.Toplevel(self.widget)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        frame = tk.Frame(win, bg="#0066cc", padx=1, pady=1)
        frame.pack()
        label = tk.Label(
            frame,
            text=self.text,
            bg="#1d1d1f",
            fg="#ffffff",
            font=("맑은 고딕", 10),
            padx=10,
            pady=7,
            justify="left",
            wraplength=self.wraplength,
        )
        label.pack()
        win.geometry(f"+{x}+{y}")
        self._win = win

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._win is not None:
            self._win.destroy()
            self._win = None


class HelpMark(ctk.CTkLabel):
    """Small "?" badge that shows *text* on hover. Place it to the left of
    the thing it explains."""

    def __init__(self, master, text: str, **kwargs):
        super().__init__(
            master,
            text=" ? ",
            width=18,
            height=18,
            corner_radius=9,
            fg_color=("#d8d8dd", "#4a4a4e"),
            text_color=("#1d1d1f", "#ffffff"),
            font=("맑은 고딕", 10, "bold"),
            **kwargs,
        )
        self.tooltip = Tooltip(self, text, wraplength=380)
        self.configure(cursor="question_arrow")

    def set_help(self, text: str) -> None:
        self.tooltip.set_text(text)


def labelled_row(master, title: str, help_text: str, **label_kwargs):
    """``(row_frame, title_label)`` with a HelpMark already packed in front.

    Returned so callers can keep packing their own controls into the row --
    every settings line in this app is "? | name | control(s)".
    """
    row = ctk.CTkFrame(master, fg_color="transparent")
    HelpMark(row, help_text).pack(side="left", padx=(0, 6))
    label = ctk.CTkLabel(row, text=title, anchor="w", **label_kwargs)
    label.pack(side="left")
    Tooltip(label, help_text)
    return row, label

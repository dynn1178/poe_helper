"""Reusable hotkey-combo editor: Ctrl/Alt/Shift checkboxes + a base-key
dropdown (satisfies "직접 조합 선택"), plus an optional live-capture button
that reads the next real key combo the user presses via ``keyboard`` --
best of both worlds without forcing either interaction style.
"""
from __future__ import annotations

import threading
import time
import tkinter as tk
from typing import Callable

import customtkinter as ctk
import keyboard

from .. import theme

UNSET = "미지정"

_BASE_KEYS = (
    [UNSET]
    + [chr(c) for c in range(ord("a"), ord("z") + 1)]
    + [str(d) for d in range(10)]
    # F13-F24 don't exist on any consumer keyboard -- trimmed to keep the
    # already-long list from getting even longer for entries nobody can
    # actually press.
    + [f"f{n}" for n in range(1, 13)]
    + [f"num {n}" for n in range(10)]
    + [
        "tab",
        "capslock",
        "enter",
        "space",
        "backspace",
        "`",
        "\\",
        "/",
        ";",
        "'",
        "[",
        "]",
        "insert",
        "delete",
        "home",
        "end",
        "page up",
        "page down",
        "up",
        "down",
        "left",
        "right",
    ]
)


class _ScrollableKeyDropdown:
    """Drop-in replacement for the ``DropdownMenu`` that ``CTkComboBox``
    normally builds internally (same ``open``/``close``/``is_open``/
    ``configure`` surface -- see ``customtkinter``'s
    ``core_widget_classes/dropdown_menu.py``). The native version is a plain
    OS menu with no height limit: with ~90 keys in ``_BASE_KEYS`` it turned
    into a single menu stretching almost the full screen height, only
    scrollable one row at a time via Windows' tiny built-in scroll arrows.
    This caps the popup's height and makes it wheel-scrollable instead.

    Reaches into ``CTkComboBox``'s private ``_dropdown_menu`` attribute to
    install itself (see ``_install_scrollable_dropdown`` below), since
    customtkinter doesn't expose a supported way to swap it out -- tied to
    the customtkinter version pinned in this project's venv.
    """

    _ROW_HEIGHT = 26
    _MAX_VISIBLE_ROWS = 10
    _WIDTH = 130
    _ROW_BG = theme.CANVAS
    _ROW_FG = theme.INK
    _ROW_HOVER = theme.SURFACE_LIGHT_2

    def __init__(self, master, values: list[str], command: Callable[[str], None]):
        self._master = master
        self._values = list(values)
        self._command = command
        self._toplevel: ctk.CTkToplevel | None = None
        self._visible = False

    def configure(self, **kwargs) -> None:
        if "values" in kwargs:
            self._values = list(kwargs.pop("values"))
            self._discard()  # cached rows no longer match

    def _discard(self) -> None:
        """Throw the cached popup away so the next open rebuilds it."""
        top, self._toplevel = self._toplevel, None
        self._visible = False
        if top is not None and top.winfo_exists():
            try:
                top.grab_release()
            except tk.TclError:
                pass
            top.destroy()

    def cget(self, attribute_name: str):
        if attribute_name == "values":
            return list(self._values)
        return None

    def is_open(self) -> bool:
        return (
            self._visible
            and self._toplevel is not None
            and self._toplevel.winfo_exists()
        )

    def close(self) -> None:
        """Hide, but keep the built rows for next time."""
        self._visible = False
        top = self._toplevel
        if top is not None and top.winfo_exists():
            try:
                top.grab_release()
            except tk.TclError:
                pass
            top.withdraw()

    def open(self, x, y) -> None:
        # Reuse the popup if it has already been built. Assembling ~80 rows
        # and a scroll frame costs ~200ms, which is long enough to feel like
        # the click did nothing; paying it once per picker rather than once
        # per open is the difference between sluggish and instant.
        if self._toplevel is not None and self._toplevel.winfo_exists():
            self._show_at(x, y)
            return

        rows = max(1, min(len(self._values), self._MAX_VISIBLE_ROWS))
        height = rows * self._ROW_HEIGHT + 8

        top = ctk.CTkToplevel(self._master)
        # Hidden until every row button below is built -- a Toplevel is
        # mapped (visible) the instant it's created, so without this,
        # opening a picker with many keys (a-z/0-9/f1-f12/... ~80 entries)
        # built its rows one-by-one in full view: a visible "list loading"
        # flash every time the dropdown opened.
        top.attributes("-alpha", 0.0)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.geometry(f"{self._WIDTH}x{height}+{int(x)}+{int(y)}")
        top.configure(fg_color=theme.HAIRLINE_LIGHT)

        scroll = ctk.CTkScrollableFrame(
            top, width=self._WIDTH - 2, height=height - 2, fg_color=theme.CANVAS,
        )
        scroll.pack(fill="both", expand=True, padx=1, pady=1)

        # Plain tk.Labels, not CTkButtons: every CTkButton builds its own
        # canvas, and ~80 of them took long enough that the popup lagged
        # visibly behind the click that asked for it. Labels with explicit
        # hover/click bindings look the same here and cost a fraction.
        inner = scroll
        for value in self._values:
            row = tk.Label(
                inner, text=value, anchor="w", padx=8,
                height=1, bg=self._ROW_BG, fg=self._ROW_FG,
                font=(theme.FONT_FAMILY, 10),
            )
            row.pack(fill="x")
            row.bind("<Enter>", lambda _e, w=row: w.configure(bg=self._ROW_HOVER))
            row.bind("<Leave>", lambda _e, w=row: w.configure(bg=self._ROW_BG))
            row.bind("<Button-1>", lambda _e, v=value: self._pick(v))

        # Dismissal is driven by an application grab, not by focus. An
        # override-redirect window cannot reliably hold focus on Windows, so
        # the old <FocusOut> binding fired the moment the popup appeared and
        # closed it again -- the popup looked like it flashed and vanished.
        # With a grab, every click in the app is delivered here first, and we
        # decide from its screen coordinates whether it was meant for us.
        def on_click(event) -> None:
            x, y = event.x_root, event.y_root
            gx, gy = top.winfo_rootx(), top.winfo_rooty()
            if not (gx <= x <= gx + top.winfo_width() and gy <= y <= gy + top.winfo_height()):
                self.close()

        top.bind("<Button-1>", on_click, add="+")
        top.bind("<Escape>", lambda _e: self.close())
        top.update()
        top.attributes("-alpha", 1.0)
        self._toplevel = top
        self._show_at(x, y)

    def _show_at(self, x, y) -> None:
        top = self._toplevel
        if top is None or not top.winfo_exists():
            return
        rows = max(1, min(len(self._values), self._MAX_VISIBLE_ROWS))
        height = rows * self._ROW_HEIGHT + 8
        top.geometry(f"{self._WIDTH}x{height}+{int(x)}+{int(y)}")
        top.deiconify()
        top.lift()
        self._visible = True
        try:
            top.grab_set()  # application-local: safer than grab_set_global
        except tk.TclError:
            pass

    def _pick(self, value: str) -> None:
        self.close()
        self._command(value)


def _install_scrollable_dropdown(combobox: ctk.CTkComboBox, values: list[str]) -> None:
    """Swap in the scrollable popup above. The key list is passed in rather
    than read back off the native menu, so the combo box can be built with
    an empty ``values`` list: CTkComboBox populates a real OS menu in its
    constructor, and building ~90 entries per picker (34 of them across the
    chat-macro and game-hotkey tabs) was pure waste when the menu is
    discarded here anyway.

    ``_values`` still has to be filled in by hand afterwards. Constructing
    the combo box with ``values=[]`` left it empty, and ``_clicked()`` guards
    the popup behind ``len(self._values) > 0`` -- so clicking the box did
    nothing at all and no hotkey could be picked from the list. Assigning the
    attribute directly (rather than ``configure(values=...)``) keeps the
    saving above, since configure would rebuild the very OS menu we discard.
    """
    native = combobox._dropdown_menu
    combobox._dropdown_menu = _ScrollableKeyDropdown(
        combobox, values=values, command=native.cget("command")
    )
    combobox._values = list(values)

    # CTkComboBox binds _clicked to two overlapping canvas tags -- both
    # "right_parts" and the "dropdown_arrow" drawn on top of it -- so one
    # click on the arrow calls it twice. The first call opens the popup and
    # arms _close_on_next_click; the second sees that flag and closes it
    # again, which is why the list appeared and vanished in the same frame.
    # Swallow the duplicate that arrives in the same click.
    #
    # Deduplicated on the X event serial, not on elapsed time: both callbacks
    # for one click run back-to-back, but the first one does not return until
    # the popup is built, which is far longer than any sane time window --
    # a wall-clock guard let the duplicate straight through.
    state = {"serial": None, "last": 0.0}

    def clicked_once(event=None):
        serial = getattr(event, "serial", None)
        if serial is not None:
            if serial == state["serial"]:
                return
            state["serial"] = serial
        else:  # programmatic call: nothing to compare but the clock
            now = time.monotonic()
            if now - state["last"] < 0.20:
                return
            state["last"] = now
        combobox._clicked(event)

    for tag in ("right_parts", "dropdown_arrow"):
        combobox._canvas.tag_unbind(tag, "<Button-1>")
        combobox._canvas.tag_bind(tag, "<Button-1>", clicked_once)
    combobox._guarded_click = clicked_once  # exposed so it can be exercised


class HotkeyPicker(ctk.CTkFrame):
    def __init__(
        self,
        master,
        on_change: Callable[[str], None] | None = None,
        show_capture: bool = True,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_change = on_change

        self.var_ctrl = ctk.BooleanVar(value=False)
        self.var_alt = ctk.BooleanVar(value=False)
        self.var_shift = ctk.BooleanVar(value=False)
        self.var_key = ctk.StringVar(value=UNSET)

        cb_kwargs = dict(width=52, command=self._notify)
        ctk.CTkCheckBox(self, text="Ctrl", variable=self.var_ctrl, **cb_kwargs).pack(
            side="left", padx=2
        )
        ctk.CTkCheckBox(self, text="Alt", variable=self.var_alt, **cb_kwargs).pack(
            side="left", padx=2
        )
        ctk.CTkCheckBox(self, text="Shift", variable=self.var_shift, **cb_kwargs).pack(
            side="left", padx=2
        )
        self.combo = ctk.CTkComboBox(
            self,
            values=[],  # real list goes to the replacement dropdown below
            variable=self.var_key,
            width=90,
            command=lambda _v: self._notify(),
        )
        self.combo.pack(side="left", padx=(4, 4))
        _install_scrollable_dropdown(self.combo, _BASE_KEYS)

        self.capture_btn: ctk.CTkButton | None = None
        if show_capture:
            self.capture_btn = ctk.CTkButton(
                self, text="캡처", width=48, command=self._start_capture
            )
            self.capture_btn.pack(side="left")

    def get_combo(self) -> str:
        mods = []
        if self.var_ctrl.get():
            mods.append("ctrl")
        if self.var_alt.get():
            mods.append("alt")
        if self.var_shift.get():
            mods.append("shift")
        key = self.var_key.get().strip()
        if key == UNSET:
            key = ""
        return "+".join(mods + [key]) if key else ""

    def set_combo(self, combo: str) -> None:
        parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
        if not parts:
            # No hotkey configured yet -- show that plainly instead of
            # silently leaving whatever key the dropdown happened to
            # default to (previously "a", which looked like a real binding).
            self.var_ctrl.set(False)
            self.var_alt.set(False)
            self.var_shift.set(False)
            self.var_key.set(UNSET)
            return
        *mods, key = parts
        self.var_ctrl.set("ctrl" in mods)
        self.var_alt.set("alt" in mods)
        self.var_shift.set("shift" in mods)
        self.var_key.set(key)

    def _notify(self) -> None:
        if self._on_change:
            self._on_change(self.get_combo())

    def _start_capture(self) -> None:
        self.capture_btn.configure(state="disabled", text="입력...")
        threading.Thread(target=self._capture_worker, daemon=True).start()

    def _capture_worker(self) -> None:
        try:
            combo = keyboard.read_hotkey(suppress=False)
        except Exception:
            combo = ""
        self.after(0, self._capture_done, combo)

    def _capture_done(self, combo: str) -> None:
        if self.capture_btn is not None:
            self.capture_btn.configure(state="normal", text="캡처")
        if combo:
            self.set_combo(combo)
            self._notify()

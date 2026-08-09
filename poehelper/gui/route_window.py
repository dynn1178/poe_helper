"""Level-up route overlay window: meant to be left floating on screen while
playing, so it stays out of the way -- a single-row compact toolbar (no OS
titlebar), a small 9pt font, and narrow controls, all so the window can be
kept small instead of eating a big vertical slice of the screen. A full-CSV
text editor mode shares the same window for direct edits that save back to
disk.

One instance per game version (``mode`` is "poe1" or "poe2", each loading
its own CSV and remembering its own geometry/transparency/last-viewed row,
so reopening the window resumes exactly where you left off)."""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from .. import paths, zone
from ..config import Config
from ..routes import csv_route
from . import theme

_FONT = ("맑은 고딕", 9)
_FONT_BOLD = ("맑은 고딕", 9, "bold")

# What the single-row toolbar measures. Enforced on both the saved geometry
# and the resize grip, because narrower than this does not scroll or wrap --
# pack simply starts shaving pixels off whichever controls it reaches last,
# and they disappear with no indication anything is missing. The old floor
# was 280, which silently cost you the 자동 checkbox and most of the opacity
# slider.
_MIN_W = 560

# Dark, unlike the settings window: this one sits on top of the game for a
# whole levelling run, and in the light theme it was a bright white panel
# permanently in the corner of the eye. See theme.OVERLAY_*.
_BTN = dict(
    fg_color=theme.OVERLAY_ACCENT,
    hover_color=theme.OVERLAY_ACCENT_HOVER,
    text_color=theme.OVERLAY_TEXT,
)
_TEXT_AREA = dict(
    bg=theme.OVERLAY_BG,
    fg=theme.OVERLAY_TEXT,
    insertbackground=theme.OVERLAY_TEXT,   # the caret, invisible if left black
    selectbackground=theme.OVERLAY_HIGHLIGHT,
    selectforeground=theme.OVERLAY_TEXT,
)


class RouteWindow(ctk.CTkToplevel):
    def __init__(self, master, config: Config, mode: str):
        super().__init__(master)
        self.config = config
        self.mode = mode
        self.route_cfg = config.data["route"][mode]
        self.csv_path = paths.data_path(self.route_cfg["csv"])
        self.steps: list[csv_route.RouteStep] = []
        self.acts: list[tuple[str, int]] = []
        self.pos = 0
        self._editing = False
        self._drag_origin: tuple[int, int] | None = None
        self._resize_origin: tuple[int, int, int, int] | None = None

        geo = config.data["windows"][f"route_{mode}"]
        self.overrideredirect(True)
        self.geometry(f"{max(_MIN_W, int(geo['w']))}x{geo['h']}+{geo['x']}+{geo['y']}")
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", geo.get("alpha", 1.0))
        except tk.TclError:
            pass
        self.configure(fg_color=theme.OVERLAY_BG)

        self._build_top_bar()
        self._build_step_view()
        self._build_edit_view()
        self._build_resize_grip()

        self._reload_csv()
        self._show_step_view()
        self._goto_row(self.route_cfg.get("last_row", 1))

        zone.on_zone_change(self._on_zone_change)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_configure)

    # ---- following the character around -----------------------------------
    def _save_follow(self) -> None:
        self.config.data["zone"]["route_follow"] = self.follow_var.get()
        self.config.save()

    def _on_zone_change(self, zone_name: str, _kind: str) -> None:
        """Runs on the tracker thread -- hop back onto the Tk loop first."""
        try:
            self.after(0, self._follow_zone, zone_name)
        except tk.TclError:
            pass  # window already closed

    def _follow_zone(self, zone_name: str) -> None:
        if not self.follow_var.get() or self._editing or not self.steps:
            return
        # Search from the step after the current one: a route revisits zones,
        # and staying put when the match is the step you are already on stops
        # it fighting a manual jump you just made.
        target = csv_route.find_step_for_zone(self.steps, zone_name, self.pos + 1)
        if target is None or target == self.pos:
            return
        self.pos = target
        self._render()
        self._flash_followed(zone_name)

    def _flash_followed(self, zone_name: str) -> None:
        """Say *why* the list moved. A route that jumps on its own with no
        explanation reads as a bug the first time it happens."""
        self.follow_check.configure(text=f"→{zone_name[:6]}")
        self.after(2500, lambda: self.follow_check.configure(text="자동"))

    # ---- layout: everything above the content lives in ONE row ------------
    def _build_top_bar(self) -> None:
        bar = ctk.CTkFrame(self, height=26, fg_color=theme.OVERLAY_SURFACE)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        drag_handle = ctk.CTkLabel(
            bar, text="⠿", width=16, font=_FONT, cursor="fleur",
            text_color=theme.OVERLAY_TEXT_DIM,
        )
        drag_handle.pack(side="left", padx=(4, 2))
        for widget in (bar, drag_handle):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)

        # Close and opacity are packed here, before the controls that appear
        # to their left, because pack hands out space in *packing* order
        # regardless of side: packed last, the slider was left with whatever
        # the row in front of it had not already taken -- 19px of a requested
        # 60 at the default window width, which drew as a bare handle with no
        # track under it. Packed first, the two right-hand controls always
        # get their full size and any shortfall falls on the row instead.
        ctk.CTkButton(
            bar, text="×", width=20, height=20, font=_FONT_BOLD,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER, command=self._on_close,
        ).pack(side="right", padx=(2, 4))

        self.alpha_slider = ctk.CTkSlider(
            bar, from_=0.3, to=1.0, height=14, width=60, command=self._on_alpha_change,
            fg_color=theme.OVERLAY_SURFACE_2, progress_color=theme.OVERLAY_TEXT_DIM,
            button_color=theme.OVERLAY_TEXT, button_hover_color=theme.OVERLAY_TEXT_DIM,
        )
        self.alpha_slider.set(self.config.data["windows"][f"route_{self.mode}"].get("alpha", 1.0))
        self.alpha_slider.pack(side="right", padx=6)

        ctk.CTkButton(
            bar, text="◀", width=20, height=20, font=_FONT, command=self._prev, **_BTN
        ).pack(side="left", padx=1)
        ctk.CTkButton(
            bar, text="▶", width=20, height=20, font=_FONT, command=self._next, **_BTN
        ).pack(side="left", padx=(1, 4))

        self.act_var = ctk.StringVar(value="액트")
        self.act_menu = ctk.CTkOptionMenu(
            bar, variable=self.act_var, values=["액트"], command=self._on_act_selected,
            width=64, height=20, font=_FONT,
            fg_color=theme.OVERLAY_ACCENT, button_color=theme.OVERLAY_ACCENT,
            button_hover_color=theme.OVERLAY_ACCENT_HOVER, text_color=theme.OVERLAY_TEXT,
            # The dropdown is its own toplevel and keeps the app-wide light
            # theme unless told otherwise -- a white list flashing open over
            # the game is the exact thing this window is getting away from.
            dropdown_fg_color=theme.OVERLAY_SURFACE,
            dropdown_hover_color=theme.OVERLAY_SURFACE_2,
            dropdown_text_color=theme.OVERLAY_TEXT,
        )
        self.act_menu.pack(side="left", padx=(0, 4))

        # No textvariable: CTkEntry only shows its placeholder when one is
        # absent (_activate_placeholder bails out on `self._textvariable is
        # not None`), so with a variable bound the box just sat there empty
        # -- which in a dark bar reads as a gap rather than a search field.
        # Nothing needed the variable; the widget is read directly instead.
        self.search_entry = ctk.CTkEntry(
            bar, placeholder_text="검색어", width=70, height=20, font=_FONT,
            fg_color=theme.OVERLAY_SURFACE_2, border_color=theme.OVERLAY_BORDER,
            text_color=theme.OVERLAY_TEXT, placeholder_text_color=theme.OVERLAY_TEXT_DIM,
        )
        self.search_entry.pack(side="left", padx=(0, 2))
        self.search_entry.bind("<Return>", lambda _e: self._search())
        ctk.CTkButton(
            bar, text="찾기", width=32, height=20, font=_FONT, command=self._search, **_BTN
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            bar, text="복사", width=32, height=20, font=_FONT,
            command=self._copy_current, **_BTN
        ).pack(side="left", padx=(0, 4))

        self.edit_toggle = ctk.CTkButton(
            bar, text="편집", width=36, height=20, font=_FONT,
            command=self._toggle_edit, **_BTN
        )
        self.edit_toggle.pack(side="left", padx=(0, 4))

        # Assistive, never authoritative: the route jumps to the zone you
        # walked into, and every manual control keeps working exactly as
        # before. Off with one click if it guesses wrong.
        self.follow_var = ctk.BooleanVar(
            value=bool(self.config.data["zone"].get("route_follow", True))
        )
        self.follow_check = ctk.CTkCheckBox(
            bar, text="자동", variable=self.follow_var, width=20, height=20,
            checkbox_width=14, checkbox_height=14, font=_FONT,
            command=self._save_follow,
            fg_color=theme.OVERLAY_ACCENT, hover_color=theme.OVERLAY_ACCENT_HOVER,
            border_color=theme.OVERLAY_BORDER, text_color=theme.OVERLAY_TEXT,
            checkmark_color=theme.OVERLAY_TEXT,
        )
        self.follow_check.pack(side="left", padx=(0, 4))

    def _build_step_view(self) -> None:
        self.step_text = tk.Text(
            self, wrap="word", state="disabled", font=_FONT, borderwidth=0,
            highlightthickness=0, **_TEXT_AREA,
        )
        self.step_text.tag_configure(
            "current", background=theme.OVERLAY_HIGHLIGHT, foreground=theme.OVERLAY_TEXT
        )

    def _build_edit_view(self) -> None:
        self.edit_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.edit_text = tk.Text(
            self.edit_frame, wrap="none", undo=True, font=_FONT, borderwidth=0,
            highlightthickness=0, **_TEXT_AREA,
        )
        self.edit_text.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        ctk.CTkButton(
            self.edit_frame, text="저장", height=22, font=_FONT,
            command=self._save_edit, **_BTN
        ).pack(pady=4)

    def _build_resize_grip(self) -> None:
        grip = ctk.CTkLabel(
            self, text="⋰", width=14, height=14, font=_FONT, cursor="size_nw_se",
            text_color=theme.OVERLAY_TEXT_DIM,
        )
        grip.place(relx=1.0, rely=1.0, x=-2, y=-2, anchor="se")
        grip.bind("<ButtonPress-1>", self._start_resize)
        grip.bind("<B1-Motion>", self._do_resize)

    def _show_step_view(self) -> None:
        self.edit_frame.pack_forget()
        self.step_text.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._editing = False
        self.edit_toggle.configure(text="편집")

    def _show_edit_view(self) -> None:
        self.step_text.pack_forget()
        self.edit_text.delete("1.0", "end")
        self.edit_text.insert("1.0", csv_route.load_raw_text(self.csv_path))
        self.edit_frame.pack(fill="both", expand=True)
        self._editing = True
        self.edit_toggle.configure(text="목록")

    def _toggle_edit(self) -> None:
        if self._editing:
            self._show_step_view()
        else:
            self._show_edit_view()

    # ---- data ---------------------------------------------------------------
    def _reload_csv(self) -> None:
        self.steps = csv_route.load_route(self.csv_path)
        self.acts = csv_route.act_boundaries(self.steps)
        labels = [label for label, _pos in self.acts] or ["액트"]
        self.act_menu.configure(values=labels)

    def _save_edit(self) -> None:
        csv_route.save_raw_text(self.csv_path, self.edit_text.get("1.0", "end-1c"))
        self._reload_csv()
        self._show_step_view()
        self._render()

    # ---- navigation -----------------------------------------------------------
    def _goto_row(self, index: int) -> None:
        for pos, step in enumerate(self.steps):
            if step.index == index:
                self.pos = pos
                break
        else:
            self.pos = 0
        self._render()

    def _prev(self) -> None:
        if self.pos > 0:
            self.pos -= 1
            self._render()

    def _next(self) -> None:
        if self.pos < len(self.steps) - 1:
            self.pos += 1
            self._render()

    def _on_act_selected(self, label: str) -> None:
        for act_label, pos in self.acts:
            if act_label == label:
                self.pos = pos
                self._render()
                return

    def _search(self) -> None:
        query = self.search_entry.get().strip().lower()
        if not query:
            return
        for offset in range(1, len(self.steps) + 1):
            idx = (self.pos + offset) % len(self.steps)
            if query in self.steps[idx].text.lower():
                self.pos = idx
                self._render()
                return

    def _copy_current(self) -> None:
        if not self.steps:
            return
        self.clipboard_clear()
        self.clipboard_append(self.steps[self.pos].text)

    def _render(self) -> None:
        if not self.steps:
            return
        # Remembers the last-viewed step (requirement: reopening the route
        # window resumes where you left off) -- saved on every navigation,
        # not just on close, so a crash/force-quit doesn't lose the spot.
        self.route_cfg["last_row"] = self.steps[self.pos].index
        self.config.save()

        self.step_text.configure(state="normal")
        self.step_text.delete("1.0", "end")
        lo = max(0, self.pos - 2)
        hi = min(len(self.steps), self.pos + 10)
        for pos in range(lo, hi):
            step = self.steps[pos]
            line_start = self.step_text.index("end-1c")
            self.step_text.insert("end", f"{step.text}\n")
            if pos == self.pos:
                line_end = self.step_text.index("end-1c")
                self.step_text.tag_add("current", line_start, line_end)
        self.step_text.configure(state="disabled")

    # ---- window chrome (no OS titlebar -> manual drag/resize/close) -------
    def _start_drag(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root, event.y_root)

    def _do_drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        x, y = self.winfo_x() + dx, self.winfo_y() + dy
        self.geometry(f"+{x}+{y}")
        self._drag_origin = (event.x_root, event.y_root)

    def _start_resize(self, event: tk.Event) -> None:
        self._resize_origin = (event.x_root, event.y_root, self.winfo_width(), self.winfo_height())

    def _do_resize(self, event: tk.Event) -> None:
        if self._resize_origin is None:
            return
        ox, oy, ow, oh = self._resize_origin
        w = max(_MIN_W, ow + (event.x_root - ox))
        h = max(120, oh + (event.y_root - oy))
        self.geometry(f"{w}x{h}")

    def _on_alpha_change(self, value: float) -> None:
        try:
            self.attributes("-alpha", value)
        except tk.TclError:
            pass
        self.config.data["windows"][f"route_{self.mode}"]["alpha"] = value
        self.config.save()

    def _on_configure(self, _event: tk.Event) -> None:
        geo = self.config.data["windows"][f"route_{self.mode}"]
        geo["x"], geo["y"] = self.winfo_x(), self.winfo_y()
        geo["w"], geo["h"] = self.winfo_width(), self.winfo_height()

    def _on_close(self) -> None:
        # Unregister first: the tracker keeps a reference, and a listener
        # bound to a destroyed window would fire on every zone change for the
        # rest of the session.
        zone.remove_listener(self._on_zone_change)
        self.config.save()
        self.destroy()

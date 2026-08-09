"""Click-to-calibrate screen point/region capture.

Replaces the original AHK flow of "Msgbox instructions -> KeyWait Enter ->
MouseGetPos" (which blocked the whole app on a modal dialog) with a
transparent full-screen overlay: an on-screen instruction banner plus a
crosshair that the user clicks directly, advancing through however many
points are requested. Must be invoked from the Tk main thread.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from .gui import theme

Point = tuple[int, int]


def pick_points(
    root: tk.Misc,
    prompts: list[str],
    on_done: Callable[[list[Point]], None],
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """Show *prompts[i]* while waiting for the i-th click; calls
    ``on_done(points)`` once all are collected, or ``on_cancel()`` on Esc.

    Two separate windows on purpose: the dim click-catching overlay needs
    ``-alpha`` to stay see-through, but setting that on the whole window
    used to wash out the instruction banner too (a child inherits its
    parent Toplevel's alpha), making it nearly invisible against a bright
    game screen. The banner is now its own fully-opaque, high-contrast red
    window layered on top so it stays readable regardless of what's behind
    it."""
    points: list[Point] = []

    overlay = tk.Toplevel(root)
    overlay.attributes("-topmost", True)
    overlay.attributes("-alpha", 0.15)
    overlay.configure(bg="black")
    overlay.overrideredirect(True)
    overlay.geometry(
        f"{overlay.winfo_screenwidth()}x{overlay.winfo_screenheight()}+0+0"
    )
    overlay.configure(cursor="crosshair")

    banner_win = tk.Toplevel(root)
    banner_win.attributes("-topmost", True)
    banner_win.overrideredirect(True)
    banner = tk.Label(
        banner_win,
        text="",
        bg="#cc1414",
        fg="#ffffff",
        font=("맑은 고딕", 15, "bold"),
        padx=24,
        pady=14,
        justify="center",
    )
    banner.pack()

    def refresh_banner() -> None:
        idx = len(points)
        text = prompts[idx] if idx < len(prompts) else ""
        banner.configure(text=f"{text}\n(Esc: 취소)")
        banner_win.update_idletasks()
        sw = overlay.winfo_screenwidth()
        w = banner_win.winfo_width()
        y = int(overlay.winfo_screenheight() * 0.08)
        banner_win.geometry(f"+{(sw - w) // 2}+{y}")

    def finish(cancelled: bool) -> None:
        overlay.destroy()
        banner_win.destroy()
        if cancelled:
            if on_cancel:
                on_cancel()
        else:
            on_done(points)

    def handle_click(event: tk.Event) -> None:
        points.append((event.x_root, event.y_root))
        if len(points) >= len(prompts):
            finish(cancelled=False)
        else:
            refresh_banner()

    def handle_escape(_event: tk.Event) -> None:
        finish(cancelled=True)

    overlay.bind("<Button-1>", handle_click)
    overlay.bind("<Escape>", handle_escape)
    overlay.focus_force()
    refresh_banner()


def pick_rect(
    root: tk.Misc,
    label: str,
    on_done: Callable[[dict], None],
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """Convenience wrapper for the common "top-left then bottom-right"
    two-click rectangle capture used by the inventory/stash calibration."""

    def done(points: list[Point]) -> None:
        (x1, y1), (x2, y2) = points
        on_done(
            {
                "x1": min(x1, x2),
                "y1": min(y1, y2),
                "x2": max(x1, x2),
                "y2": max(y1, y2),
            }
        )

    pick_points(
        root,
        [f"{label}: 좌측 상단 모서리를 클릭하세요", f"{label}: 우측 하단 모서리를 클릭하세요"],
        done,
        on_cancel,
    )


# ---------------------------------------------------------------------------
# Drag-to-size grid picker
# ---------------------------------------------------------------------------
# Clicking two opposite corners (pick_rect above) gives no feedback until it is
# already too late to tell whether the box lines up with the grid: a stash cell
# is ~30px, so being two pixels out on either corner drifts the computed centre
# of the last column far enough to click the wrong cell. This picker instead
# keeps the box on screen while it is being sized, subdivides it live into the
# exact cell grid the caller will use (24x24 quad tab, 12x12 stash, 12x5
# inventory), and only commits on an explicit confirm -- so the grid can be
# lined up against the real one underneath before anything is saved.

_HANDLE = 8          # px, grab radius of each resize handle
_MIN_SIZE = 20       # px, smallest rect we allow


def _clamp_rect(x1: int, y1: int, x2: int, y2: int) -> tuple[int, int, int, int]:
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


class _GridRectPicker:
    """Full-screen overlay: drag out a box, nudge it, confirm it."""

    def __init__(
        self,
        root: tk.Misc,
        label: str,
        cols: int,
        rows: int,
        on_done: Callable[[dict], None],
        on_cancel: Callable[[], None] | None,
        initial: dict | None,
    ):
        self.root = root
        self.label = label
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        self.on_done = on_done
        self.on_cancel = on_cancel

        self.mode: str | None = None      # "new" | "move" | "resize"
        self.handle: str | None = None    # which corner/edge is being dragged
        self.drag_origin = (0, 0)
        self.rect_at_press = (0, 0, 0, 0)

        self.overlay = tk.Toplevel(root)
        self.overlay.attributes("-topmost", True)
        self.overlay.attributes("-alpha", 0.35)
        self.overlay.overrideredirect(True)
        sw = self.overlay.winfo_screenwidth()
        sh = self.overlay.winfo_screenheight()
        self.overlay.geometry(f"{sw}x{sh}+0+0")
        self.overlay.configure(cursor="crosshair", bg="black")

        self.canvas = tk.Canvas(
            self.overlay, width=sw, height=sh, bg="black", highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        if initial and all(k in initial for k in ("x1", "y1", "x2", "y2")):
            self.rect = _clamp_rect(
                int(initial["x1"]), int(initial["y1"]), int(initial["x2"]), int(initial["y2"])
            )
        else:
            # A sensible starting box beats an empty screen: the user can drag
            # a fresh one anywhere, but usually only needs to nudge this.
            w, h = min(600, sw // 3), min(600, sh // 3)
            cx, cy = sw // 2, sh // 2
            self.rect = (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)

        self._build_panel()

        for seq, fn in (
            ("<Escape>", lambda _e: self._finish(True)),
            ("<Return>", lambda _e: self._finish(False)),
            ("<Left>", lambda e: self._nudge(e, -1, 0)),
            ("<Right>", lambda e: self._nudge(e, 1, 0)),
            ("<Up>", lambda e: self._nudge(e, 0, -1)),
            ("<Down>", lambda e: self._nudge(e, 0, 1)),
        ):
            self.overlay.bind(seq, fn)
            self.panel.bind(seq, fn)

        # Key bindings need focus somewhere, but focusing the full-screen
        # overlay raises it over the panel; the panel goes back on top right
        # after, and _place_panel() re-lifts it on every redraw.
        self.overlay.focus_force()
        self.panel.lift()
        self._redraw()

    # ---- chrome ---------------------------------------------------------
    def _build_panel(self) -> None:
        """Instructions + confirm buttons in their own opaque window.

        Separate window because a child of the dimmed overlay inherits its
        alpha and would be washed out too; built with the app's own
        CustomTkinter widgets and palette so the picker looks like the rest
        of the program rather than a raw Tk dialog.
        """
        self.panel = ctk.CTkToplevel(self.root)
        self.panel.overrideredirect(True)
        self.panel.attributes("-topmost", True)
        self.panel.configure(fg_color=theme.CANVAS)

        card = ctk.CTkFrame(self.panel, fg_color=theme.CANVAS, corner_radius=12)
        card.pack(padx=2, pady=2)

        ctk.CTkLabel(
            card, text=self.label, font=theme.FONT_TITLE, text_color=theme.INK,
        ).pack(padx=22, pady=(14, 0))

        self.info = ctk.CTkLabel(
            card, text="", font=theme.FONT_BODY, text_color=theme.INK, justify="center",
        )
        self.info.pack(padx=22, pady=(4, 0))

        ctk.CTkLabel(
            card,
            text=("드래그로 크기 조절 · 안쪽을 끌어 이동 · 방향키 1px 이동 "
                  "(Shift+방향키: 크기)\n점선이 게임의 칸과 맞으면 확인 (Enter) · 취소 (Esc)"),
            font=theme.FONT_CAPTION, text_color=theme.PRIMARY, justify="center",
        ).pack(padx=22, pady=(6, 0))

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(padx=22, pady=(12, 14))
        ctk.CTkButton(
            btns, text="확인", width=110, font=theme.FONT_BODY_STRONG,
            command=lambda: self._finish(False),
        ).pack(side="left", padx=5)
        ctk.CTkButton(
            btns, text="취소", width=110,
            fg_color=theme.SURFACE_LIGHT_2, hover_color=theme.HAIRLINE_LIGHT,
            text_color=theme.INK, command=lambda: self._finish(True),
        ).pack(side="left", padx=5)

    def _panel_bounds(self) -> tuple[int, int, int, int]:
        try:
            px, py = self.panel.winfo_rootx(), self.panel.winfo_rooty()
            return px, py, px + self.panel.winfo_width(), py + self.panel.winfo_height()
        except tk.TclError:
            return (0, 0, 0, 0)

    def _place_panel(self) -> None:
        """Keep the panel clear of the box being sized, and keep it above the
        overlay.

        Both windows are ``-topmost``, and the overlay is full-screen; when it
        ends up on top (which ``focus_force`` alone was enough to cause) every
        click aimed at 확인 landed on the canvas underneath and started a new
        drag instead. Re-lifting on each redraw is what keeps the buttons
        actually clickable.
        """
        self.panel.update_idletasks()
        sw = self.overlay.winfo_screenwidth()
        sh = self.overlay.winfo_screenheight()
        pw, ph = self.panel.winfo_width(), self.panel.winfo_height()
        _x1, y1, _x2, y2 = self.rect
        y = y2 + 24 if y1 < ph + 48 else max(12, y1 - ph - 24)
        y = min(max(12, y), max(12, sh - ph - 12))
        self.panel.geometry(f"+{(sw - pw) // 2}+{int(y)}")
        self.panel.lift()

    def _refresh_info(self) -> None:
        x1, y1, x2, y2 = self.rect
        w, h = x2 - x1, y2 - y1
        self.info.configure(
            text=(
                f"{self.cols} x {self.rows} 칸    |    영역 {w} x {h} px\n"
                f"칸 크기 {w / self.cols:.1f} x {h / self.rows:.1f} px"
            )
        )

    # ---- drawing --------------------------------------------------------
    def _redraw(self) -> None:
        self.canvas.delete("all")
        x1, y1, x2, y2 = self.rect

        # Cut the dimming out of the selection so the grid underneath stays
        # readable while it is being matched.
        self.canvas.create_rectangle(x1, y1, x2, y2, fill="#404040", outline="")
        self.canvas.create_rectangle(x1, y1, x2, y2, outline="#00ff88", width=2)

        w, h = x2 - x1, y2 - y1
        for c in range(1, self.cols):
            x = x1 + w * c / self.cols
            self.canvas.create_line(x, y1, x, y2, fill="#00ff88", dash=(3, 3))
        for r in range(1, self.rows):
            y = y1 + h * r / self.rows
            self.canvas.create_line(x1, y, x2, y, fill="#00ff88", dash=(3, 3))

        for hx, hy in self._handle_points().values():
            self.canvas.create_rectangle(
                hx - _HANDLE // 2, hy - _HANDLE // 2, hx + _HANDLE // 2, hy + _HANDLE // 2,
                fill="#ffffff", outline="#00aa55",
            )

        self._refresh_info()
        self._place_panel()

    def _handle_points(self) -> dict[str, tuple[int, int]]:
        x1, y1, x2, y2 = self.rect
        mx, my = (x1 + x2) // 2, (y1 + y2) // 2
        return {
            "nw": (x1, y1), "n": (mx, y1), "ne": (x2, y1),
            "w": (x1, my), "e": (x2, my),
            "sw": (x1, y2), "s": (mx, y2), "se": (x2, y2),
        }

    def _hit_handle(self, x: int, y: int) -> str | None:
        for name, (hx, hy) in self._handle_points().items():
            if abs(x - hx) <= _HANDLE and abs(y - hy) <= _HANDLE:
                return name
        return None

    # ---- interaction ----------------------------------------------------
    def _on_hover(self, event: tk.Event) -> None:
        handle = self._hit_handle(event.x, event.y)
        cursors = {
            "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
            "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
            "w": "sb_h_double_arrow", "e": "sb_h_double_arrow",
        }
        if handle:
            self.overlay.configure(cursor=cursors[handle])
        elif self._inside(event.x, event.y):
            self.overlay.configure(cursor="fleur")
        else:
            self.overlay.configure(cursor="crosshair")

    def _inside(self, x: int, y: int) -> bool:
        x1, y1, x2, y2 = self.rect
        return x1 <= x <= x2 and y1 <= y <= y2

    def _on_press(self, event: tk.Event) -> None:
        # Second line of defence for the confirm buttons: even if the overlay
        # somehow sits above the panel, a press over the panel must not start
        # a drag underneath it.
        px1, py1, px2, py2 = self._panel_bounds()
        if px1 <= event.x_root <= px2 and py1 <= event.y_root <= py2:
            return
        self.drag_origin = (event.x, event.y)
        self.rect_at_press = self.rect
        handle = self._hit_handle(event.x, event.y)
        if handle:
            self.mode, self.handle = "resize", handle
        elif self._inside(event.x, event.y):
            self.mode = "move"
        else:
            self.mode = "new"
            self.rect = (event.x, event.y, event.x, event.y)
            self._redraw()

    def _on_drag(self, event: tk.Event) -> None:
        if self.mode is None:
            return
        ox, oy = self.drag_origin
        dx, dy = event.x - ox, event.y - oy
        px1, py1, px2, py2 = self.rect_at_press

        if self.mode == "new":
            self.rect = _clamp_rect(ox, oy, event.x, event.y)
        elif self.mode == "move":
            self.rect = (px1 + dx, py1 + dy, px2 + dx, py2 + dy)
        else:
            x1, y1, x2, y2 = px1, py1, px2, py2
            if "n" in self.handle:
                y1 = py1 + dy
            if "s" in self.handle:
                y2 = py2 + dy
            if "w" in self.handle:
                x1 = px1 + dx
            if "e" in self.handle:
                x2 = px2 + dx
            self.rect = _clamp_rect(x1, y1, x2, y2)
        self._redraw()

    def _on_release(self, _event: tk.Event) -> None:
        self.mode = self.handle = None
        x1, y1, x2, y2 = self.rect
        # A stray click (rather than a drag) would otherwise collapse the box
        # to nothing and leave no handles to grab it back by.
        if x2 - x1 < _MIN_SIZE or y2 - y1 < _MIN_SIZE:
            self.rect = self.rect_at_press
        self._redraw()

    def _nudge(self, event: tk.Event, dx: int, dy: int) -> None:
        x1, y1, x2, y2 = self.rect
        if event.state & 0x0001:  # Shift held -> resize instead of move
            self.rect = _clamp_rect(x1, y1, max(x1 + _MIN_SIZE, x2 + dx), max(y1 + _MIN_SIZE, y2 + dy))
        else:
            self.rect = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
        self._redraw()

    def _finish(self, cancelled: bool) -> None:
        self.overlay.destroy()
        self.panel.destroy()
        if cancelled:
            if self.on_cancel:
                self.on_cancel()
            return
        x1, y1, x2, y2 = self.rect
        self.on_done({"x1": x1, "y1": y1, "x2": x2, "y2": y2})


def pick_grid_rect(
    root: tk.Misc,
    label: str,
    cols: int,
    rows: int,
    on_done: Callable[[dict], None],
    on_cancel: Callable[[], None] | None = None,
    initial: dict | None = None,
) -> None:
    """Drag out a ``cols`` x ``rows`` region, adjust it, then confirm.

    ``on_done`` receives the same ``{"x1","y1","x2","y2"}`` dict ``pick_rect``
    produces, so callers are interchangeable.
    """
    _GridRectPicker(root, label, cols, rows, on_done, on_cancel, initial)

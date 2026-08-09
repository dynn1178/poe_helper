"""Map-layout overlay: the act-layout picture for wherever the character is.

Opened from the 레벨업 tab and then left alone -- it follows the client log
(``zone.py``), so walking into a new area swaps the picture with no input at
all. That is the whole point: a layout you have to go and look up is one you
stop looking up by act 4.

Two things shape the design:

*Passive.* It is a game overlay, so it must never take the foreground -- PoE
only receives keys while it is the active window (see ``overlay_win``). It is
also the one overlay here that has to stay *clickable*, since it is dragged
and adjusted, so it gets ``WS_EX_NOACTIVATE`` without the click-through flag:
mouse messages arrive, activation never does.

*Out of the way.* Controls appear only while the pointer is over the window,
and they are ``place``d on top of the picture rather than packed around it,
so the window is always exactly the size of the image it shows -- revealing
the controls never moves or resizes anything on screen.
"""
from __future__ import annotations

import logging
import tkinter as tk

import customtkinter as ctk

from .. import map_layout, overlay_win, zone
from ..config import Config
from . import theme

logger = logging.getLogger(__name__)

_FONT = ("맑은 고딕", 9)
# Shared with the route window -- both are dark panels laid over the game.
_CHROME_BG = theme.OVERLAY_BG
_CHROME_FG = theme.OVERLAY_TEXT
_CHROME_DIM = theme.OVERLAY_TEXT_DIM
_EMPTY_BG = theme.OVERLAY_BG

_MIN_SCALE, _MAX_SCALE = 0.4, 2.5
_MIN_ALPHA, _MAX_ALPHA = 0.2, 1.0
# Each control bar sizes itself to its contents; this is the height of
# the individual controls that set it.
_CONTROL_H = 18

# How often the pointer is checked against the window box. Polling rather
# than <Enter>/<Leave>: those fire on every crossing into a child widget too,
# so the chrome flickered as the pointer moved between the sliders it had
# just revealed. One position test answers the real question directly.
_HOVER_POLL_MS = 120


class LayoutWindow(tk.Toplevel):
    """One always-on-top picture of the current zone's layout."""

    def __init__(self, master: tk.Misc, config: Config, mode: str = "poe1"):
        super().__init__(master)
        self.withdraw()  # revealed by overlay_win once the styles are on
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        # NOT self.config, unlike RouteWindow: this is a plain tk.Toplevel, and
        # CTkBaseClass replaces a non-CTk master's .config/.configure with its
        # own wrapper the first time a CustomTkinter child is created inside
        # it -- which silently overwrote the settings object stored there.
        self.app_config = config
        self.mode = mode
        self.geo = config.data["windows"].setdefault(
            f"layout_{mode}", {"x": 200, "y": 200, "scale": 1.0, "alpha": 0.9}
        )

        self.zone_name: str = ""
        self.layout: map_layout.Layout | None = None
        self._act_hint: int | None = None
        self._photo: object | None = None  # ImageTk ref must outlive the draw
        self._source = None                # PIL image at native size
        self._drag_origin: tuple[int, int] | None = None
        self._resize_origin: tuple[int, int, int, int] | None = None
        self._no_drag: list = []  # filled by _build; see _owns_its_drag
        self._hover = False
        self._poll_id: str | None = None
        self._closed = False

        self.configure(bg=_EMPTY_BG)
        self._build()

        self.geometry(f"+{int(self.geo.get('x', 200))}+{int(self.geo.get('y', 200))}")
        self._apply_alpha(float(self.geo.get("alpha", 0.9)))

        self._show_zone(zone.current_zone())
        zone.on_zone_change(self._on_zone_change)

        overlay_win.show_passive(self, click_through=False)
        self._poll_hover()

    # ---- widgets ----------------------------------------------------------
    def _build(self) -> None:
        # The picture is the window. Everything else is placed on top of it.
        # The move cursor covers the whole picture, since the whole picture
        # really is a drag handle -- the ⠿ grip in the bar advertises that,
        # this confirms it wherever the pointer actually is.
        self.image_label = tk.Label(
            self, bd=0, bg=_EMPTY_BG, fg=_CHROME_FG, font=_FONT, cursor="fleur"
        )
        self.image_label.pack()
        for widget in (self, self.image_label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)
            widget.bind("<ButtonRelease-1>", self._end_drag)

        self.top_bar = ctk.CTkFrame(self, fg_color=_CHROME_BG, corner_radius=0)
        # The whole window moves when dragged, but nothing said so -- the same
        # ⠿ grip the route window uses is the thing people look for, and it
        # sits opposite the ◢ resize grip so the two handles read as a pair.
        # A plain tk.Label so the "fleur" cursor applies to the glyph itself
        # (a CTkLabel's cursor stops at its wrapper, not the inner label).
        self.move_handle = tk.Label(
            self.top_bar, text="⠿", bg=_CHROME_BG, fg=_CHROME_DIM, font=_FONT,
            bd=0, cursor="fleur",
        )
        self.move_handle.pack(side="left", padx=(5, 2))

        # Everything in the bars carries an explicit compact height, because
        # the bar sizes to its tallest child and CustomTkinter's defaults
        # (28px labels, 200px sliders) are built for a settings dialog, not
        # for a strip laid over a 220px-wide picture.
        self.zone_label = ctk.CTkLabel(
            self.top_bar, text="", font=_FONT, text_color=_CHROME_FG, anchor="w",
            height=_CONTROL_H, width=0,
        )
        # Dragging from the bar as well as the picture: once the chrome is up
        # it covers the top strip of the image, and a handle that refuses to
        # move the window is worse than no handle.
        for widget in (self.top_bar, self.zone_label, self.move_handle):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)
            widget.bind("<ButtonRelease-1>", self._end_drag)

        # Packed before the zone label so the fixed-size controls claim their
        # width first -- the label is the elastic one and would otherwise take
        # the whole bar and squeeze these out (which is exactly what happened
        # to the second slider below).
        close_button = ctk.CTkButton(
            self.top_bar, text="✕", width=20, height=_CONTROL_H, font=_FONT,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER, command=self.close,
        )
        close_button.pack(side="right", padx=(2, 4))
        self._no_drag.append(close_button)

        # Only meaningful for the zones that exist in two acts; packed and
        # unpacked by _render_act_switch.
        self.act_frame = ctk.CTkFrame(self.top_bar, fg_color="transparent", height=_CONTROL_H)
        prev_act = self._act_button("◀", -1)
        self.act_label = ctk.CTkLabel(
            self.act_frame, text="", font=_FONT, text_color=_CHROME_FG,
            width=32, height=_CONTROL_H,
        )
        next_act = self._act_button("▶", 1)
        prev_act.pack(side="left")
        self.act_label.pack(side="left")
        next_act.pack(side="left")

        self.bottom_bar = ctk.CTkFrame(self, fg_color=_CHROME_BG, corner_radius=0)
        ctk.CTkLabel(
            self.bottom_bar, text="◐", font=_FONT, text_color=_CHROME_FG,
            width=12, height=_CONTROL_H,
        ).pack(side="left", padx=(6, 2))

        # A plain tk.Label, not a CTkLabel: a CustomTkinter label is a canvas
        # plus an inner tkinter label, and a press lands on that inner widget,
        # so a binding on the CTk wrapper never sees it.
        self.grip = tk.Label(
            self.bottom_bar, text="◢", bg=_CHROME_BG, fg=_CHROME_FG, font=_FONT,
            bd=0, cursor="size_nw_se",
        )
        self.grip.pack(side="right", padx=(2, 3))
        self.grip.bind("<ButtonPress-1>", self._start_resize)
        self.grip.bind("<B1-Motion>", self._do_resize)
        self.grip.bind("<ButtonRelease-1>", self._end_resize)

        self.alpha_slider = ctk.CTkSlider(
            self.bottom_bar, from_=_MIN_ALPHA, to=_MAX_ALPHA, height=14,
            width=60, command=self._on_alpha,
        )
        self.alpha_slider.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.alpha_slider.set(float(self.geo.get("alpha", 0.9)))
        # Saved on release, not per pixel of travel: config.save() fans out to
        # every listener, including a full hotkey re-registration.
        self.alpha_slider.bind("<ButtonRelease-1>", lambda _e: self.app_config.save())

        self._no_drag += [self.alpha_slider, self.grip]

    def _act_button(self, arrow: str, step: int) -> ctk.CTkButton:
        button = ctk.CTkButton(
            self.act_frame, text=arrow, width=16, height=_CONTROL_H, font=_FONT,
            command=lambda: self._step_act(step),
        )
        self._no_drag.append(button)
        return button

    # ---- hover-revealed chrome -------------------------------------------
    def _poll_hover(self) -> None:
        if self._closed:
            return
        try:
            px, py = self.winfo_pointerxy()
            x, y = self.winfo_rootx(), self.winfo_rooty()
            inside = (
                x <= px < x + self.winfo_width() and y <= py < y + self.winfo_height()
            )
        except tk.TclError:
            return
        # A drag that wanders outside the window keeps the chrome up, so the
        # controls do not vanish out from under the pointer mid-adjustment --
        # which a resize does constantly, since shrinking the picture pulls
        # the corner out from under the grip being dragged.
        inside = inside or self._drag_origin is not None or self._resize_origin is not None
        if inside != self._hover:
            self._hover = inside
            self._render_chrome()
        self._poll_id = self.after(_HOVER_POLL_MS, self._poll_hover)

    def _render_chrome(self) -> None:
        if self._hover:
            # No height= here: CustomTkinter takes its widgets' dimensions
            # from the constructor only and rejects them on place(), so each
            # bar keeps the height its controls give it and place() just
            # stretches it across the width of the picture.
            self.top_bar.place(relx=0, rely=0, relwidth=1.0)
            self.bottom_bar.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0)
        else:
            self.top_bar.place_forget()
            self.bottom_bar.place_forget()

    def _render_act_switch(self) -> None:
        """Show the act switch only where there is a genuine choice.

        Re-packs the zone label afterwards rather than leaving it in place:
        pack hands out space in packing order, so the elastic label has to go
        in last or it takes the whole bar and the act buttons get nothing.
        """
        options = map_layout.candidates(self.zone_name)
        self.zone_label.pack_forget()
        if len(options) > 1 and self.layout is not None:
            self.act_label.configure(text=self.layout.act_label)
            self.act_frame.pack(side="right", padx=(2, 2))
        else:
            self.act_frame.pack_forget()
        self.zone_label.pack(side="left", fill="x", expand=True, padx=(6, 4))

    def _step_act(self, delta: int) -> None:
        """Flip to the neighbouring act's version of this zone by hand."""
        options = map_layout.candidates(self.zone_name)
        if len(options) < 2 or self.layout is None:
            return
        position = options.index(self.layout)
        chosen = options[(position + delta) % len(options)]
        # A correction is also information: the character is evidently in
        # that half of the campaign, so let it steer the next ambiguous zone
        # instead of making the user fix each one in turn.
        self._act_hint = chosen.act
        self._set_layout(chosen)

    # ---- following the character ------------------------------------------
    def _on_zone_change(self, zone_name: str, _kind: str) -> None:
        """Runs on the tracker thread -- hop back onto the Tk loop first."""
        try:
            self.after(0, self._show_zone, zone_name)
        except (tk.TclError, RuntimeError):
            pass  # window already closed

    def _show_zone(self, zone_name: str) -> None:
        if self._closed:
            return
        self.zone_name = zone_name or ""
        hint = map_layout.act_hint_from(self.zone_name)
        if hint is not None:
            self._act_hint = hint
        self._set_layout(map_layout.choose(self.zone_name, self._act_hint))

    def _set_layout(self, layout: map_layout.Layout | None) -> None:
        self.layout = layout
        self._source = None
        if layout is not None:
            try:
                from PIL import Image

                self._source = Image.open(layout.path)
                self._source.load()  # decode now, not on first resize
            except (OSError, ImportError):
                logger.warning("could not open layout %s", layout.path, exc_info=True)
                self._source = None
        self.zone_label.configure(text=self.zone_name or "지역 확인 중")
        self._render_act_switch()
        self._redraw()

    # ---- drawing ----------------------------------------------------------
    def _scale(self) -> float:
        try:
            return min(_MAX_SCALE, max(_MIN_SCALE, float(self.geo.get("scale", 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def _redraw(self, fast: bool = False) -> None:
        """Re-render at the current scale.

        ``fast`` swaps LANCZOS for bilinear, used while the resize grip is
        being dragged: a full-quality resample of a 220x665 layout runs on
        every motion event, and the difference is not visible on something
        that is still moving under the cursor. The final draw on release is
        always full quality.
        """
        if self._closed:
            return
        if self._source is None:
            # No picture for this zone (a town, a hideout, or simply one we
            # have no image for). Shown as a small caption rather than hidden:
            # a window that disappears on its own is one the user cannot find
            # to move or close, and the label doubles as "yes, it is working".
            self._photo = None
            self.image_label.configure(
                image="", text=f"  {self.zone_name or '지역 확인 중'} · 레이아웃 없음  ",
                width=0, height=0, padx=8, pady=8,
            )
            return
        try:
            from PIL import Image, ImageTk

            scale = self._scale()
            width = max(1, int(self._source.width * scale))
            height = max(1, int(self._source.height * scale))
            resample = Image.BILINEAR if fast else Image.LANCZOS
            self._photo = ImageTk.PhotoImage(self._source.resize((width, height), resample))
        except (OSError, ImportError, ValueError):
            logger.warning("could not render layout", exc_info=True)
            return
        self.image_label.configure(
            image=self._photo, text="", padx=0, pady=0, width=width, height=height
        )

    # ---- controls ---------------------------------------------------------
    def _on_alpha(self, value: float) -> None:
        self.geo["alpha"] = round(float(value), 2)
        self._apply_alpha(float(value))

    def _apply_alpha(self, value: float) -> None:
        try:
            self.attributes("-alpha", max(_MIN_ALPHA, min(_MAX_ALPHA, value)))
        except tk.TclError:
            pass

    # ---- moving -----------------------------------------------------------
    def _owns_its_drag(self, widget) -> bool:
        """Is *widget* (or an inner part of one) a control with its own drag?

        The move handler is bound on the toplevel so that grabbing anywhere on
        the window moves it. Tk puts the toplevel in every descendant's
        bindtags, so that one binding also fires for presses on the opacity
        slider and the resize grip -- which is why dragging the slider used to
        drag the whole window along with it. Walking up from the reported
        widget catches the composite widgets too, where the press actually
        lands on an inner canvas rather than on the control itself.
        """
        while widget is not None and widget is not self:
            if widget in self._no_drag:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _start_drag(self, event: tk.Event) -> None:
        if self._owns_its_drag(event.widget):
            return
        self._drag_origin = (event.x_root, event.y_root)

    def _do_drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        x, y = self.winfo_x() + dx, self.winfo_y() + dy
        self.geometry(f"+{x}+{y}")
        self._drag_origin = (event.x_root, event.y_root)

    def _end_drag(self, _event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        self._drag_origin = None
        x, y = self.winfo_x(), self.winfo_y()
        if (x, y) == (self.geo.get("x"), self.geo.get("y")):
            # A press-and-release that never moved is still a completed drag
            # as far as this handler is concerned, and saving fans out to
            # every config listener -- including a full hotkey re-register.
            return
        self.geo["x"], self.geo["y"] = x, y
        self.app_config.save()

    # ---- resizing ---------------------------------------------------------
    def _start_resize(self, event: tk.Event) -> None:
        if self._source is None:
            return  # nothing but the "레이아웃 없음" caption to resize
        self._resize_origin = (
            event.x_root, event.y_root, self.winfo_width(), self.winfo_height(),
        )

    def _do_resize(self, event: tk.Event) -> None:
        if self._resize_origin is None or self._source is None:
            return
        ox, oy, start_w, start_h = self._resize_origin
        natural_w, natural_h = self._source.width, self._source.height
        dx, dy = event.x_root - ox, event.y_root - oy
        # The picture keeps its aspect ratio, so only one axis can be
        # followed: whichever the pointer has moved further along *relative to
        # that side's length*. Following x alone would make these tall narrow
        # layouts crawl, and taking the larger of the two scales would refuse
        # to shrink whenever only one axis moved inwards.
        if abs(dx) * natural_h >= abs(dy) * natural_w:
            scale = (start_w + dx) / natural_w
        else:
            scale = (start_h + dy) / natural_h
        scale = max(_MIN_SCALE, min(_MAX_SCALE, scale))
        if abs(scale - self._scale()) < 0.004:
            return  # sub-pixel: not worth a resample
        self.geo["scale"] = round(scale, 3)
        self._redraw(fast=True)

    def _end_resize(self, _event: tk.Event) -> None:
        if self._resize_origin is None:
            return
        self._resize_origin = None
        self._redraw()  # full quality, now that it has stopped moving
        self.app_config.save()

    # ---- lifecycle --------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Unregistered first: the tracker holds the reference, and a listener
        # bound to a destroyed window would raise on every zone change for the
        # rest of the session.
        zone.remove_listener(self._on_zone_change)
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        self.app_config.save()
        self.destroy()

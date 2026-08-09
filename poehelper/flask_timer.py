"""Cooldown bar overlays for flask slots 1-5 and skill keys Q/W/E/R/T.

The original AHK GUI had a "쿨다운"(cooldown) button storing per-flask
durations and a "위치 설정"(position) button storing one anchor point, but no
code anywhere actually drew a timer using those values -- an unfinished
stub. This module finishes it: it watches the real keypresses while PoE is
focused (whether typed by the player or sent by our own macros) and draws a
draining bar per slot.

Bars rather than numbers: a countdown like "2.4" has to be read, and during a
fight there is no attention spare for reading. A bar's remaining length is
legible from peripheral vision, which is the only way a cooldown display is
actually used.

Flasks and skills get separate overlays with separate placement, because they
sit in different parts of the HUD -- one shared position could never suit
both. Each is placed by dragging out its size and nudging it into position
(``calibration.calibrate_flask_overlay`` / ``calibrate_skill_overlay``).
"""
from __future__ import annotations

import colorsys
import math
import time
import tkinter as tk
from typing import Callable

import keyboard

from . import foreground, overlay_win, toast, zone
from .config import SKILL_KEYS, Config

SLOT_COUNT = 5

# The drained part of a bar. Near-black but not black -- pure black is the
# colour key that gets punched out of the window entirely. Drawn with no
# outline: a Tk canvas does not antialias, so a contrasting hairline around a
# rounded corner comes out as a visible staircase of dashes rather than an
# edge, and the track is already dark enough to separate the bars from any
# HUD behind them.
_TRACK_BG = "#15151a"
_FLASK_FILL = "#39b6ff"
_SKILL_FILL = "#ffb03a"
_TRANSPARENT = "black"  # colour-keyed away so only the bars are visible

# Fixed 8pt rather than scaled from the bar height. Sizing the text to the box
# meant a taller bar grew huge numerals that dominated the HUD, and the digits
# were never the point -- the bar length is what gets read at a glance, the
# number is only there for when an exact value is wanted.
_BAR_FONT = ("Consolas", 8, "bold")
_TEXT_MIN_H = 15  # below this a bar is too short for legible numerals

_MIN_ALPHA = 0.15  # below this the overlay is effectively invisible

# Vertical shading: the slot's colour at the top fading to _DARKEST of itself
# at the bottom. Tk's canvas has no gradient primitive, so it is drawn as
# horizontal slices -- enough to read as smooth, few enough to stay cheap at
# the 50ms repaint rate (5 bars x 2 overlays x _GRADIENT_SLICES rectangles).
_GRADIENT_SLICES = 20
_DARKEST = 0.42  # multiplier applied to the base colour at the bottom edge

# A second gradient, this one *across* the strip: slot 1 starts bright and
# slightly hue-rotated one way, slot 5 ends deeper and rotated the other, so
# the five bars read as one continuous object rather than five identical
# stickers. Kept subtle on purpose -- each bar still has to be recognisable
# as "the flask colour I chose", and a slot must stay identifiable by
# position, not by having been given its own unrelated colour.
_ACROSS_LIGHT = (1.16, 0.78)  # lightness multiplier, first slot -> last
_ACROSS_HUE = 0.05            # total hue rotation across the strip
_ACROSS_SAT = (0.92, 1.06)    # saturation multiplier, first slot -> last

# Corner rounding: proportional so a bar dragged out short and wide rounds as
# gracefully as a tall thin one, but capped in absolute pixels. Without the
# cap a normally-sized bar rounds until the straight edges disappear and each
# slot reads as a capsule/pill, which is no less crude than the squares this
# replaced -- just crude in the other direction. A few px of relief is the
# whole effect.
_RADIUS_OF_WIDTH = 0.25
_RADIUS_OF_HEIGHT = 0.25
_RADIUS_MAX = 7.0
_ARC_STEPS = 5  # points sampled per rounded corner


def _hex_to_rgb(colour: str) -> tuple[int, int, int]:
    colour = (colour or "").lstrip("#")
    if len(colour) != 6:
        raise ValueError(colour)
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _scale(colour: str, factor: float) -> str:
    try:
        r, g, b = _hex_to_rgb(colour)
    except ValueError:
        return colour
    return "#%02x%02x%02x" % tuple(int(_clamp01(c * factor / 255.0) * 255) for c in (r, g, b))


def _slot_colour(colour: str, index: int, count: int = SLOT_COUNT) -> str:
    """The base colour for slot *index*, shifted along the across-strip ramp."""
    try:
        r, g, b = _hex_to_rgb(colour)
    except ValueError:
        return colour
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    t = index / max(1, count - 1)
    h = (h + _lerp(-_ACROSS_HUE / 2, _ACROSS_HUE / 2, t)) % 1.0
    l = _clamp01(l * _lerp(*_ACROSS_LIGHT, t))
    s = _clamp01(s * _lerp(*_ACROSS_SAT, t))
    return "#%02x%02x%02x" % tuple(
        int(_clamp01(c) * 255) for c in colorsys.hls_to_rgb(h, l, s)
    )


def _shade_ramp(colour: str, slices: int = _GRADIENT_SLICES) -> list[str]:
    """``slices`` hex colours running from *colour* down to a darker version."""
    try:
        r, g, b = _hex_to_rgb(colour)
    except ValueError:
        return [colour] * slices
    ramp = []
    for i in range(slices):
        # 0 at the top, 1 at the bottom edge of the bar.
        t = i / max(1, slices - 1)
        factor = 1.0 - (1.0 - _DARKEST) * t
        ramp.append(f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}")
    return ramp


# Keyed by (configured colour, slot) -- every one of these is pure arithmetic
# on inputs that change only when the user picks a new colour, and the draw
# loop asks for them 20 times a second.
_slot_cache: dict[tuple[str, int], tuple[str, list[str], str]] = {}


def slot_style(colour: str, index: int) -> tuple[str, list[str], str]:
    """``(base, vertical ramp, surface-highlight)`` for one slot."""
    key = (colour, index)
    if key not in _slot_cache:
        base = _slot_colour(colour, index)
        _slot_cache[key] = (base, _shade_ramp(base), _scale(base, 1.45))
    return _slot_cache[key]


def _corner_cuts(
    x1: float, y1: float, x2: float, y2: float, r: float
) -> list[list[float]]:
    """Four polygons covering what a rounded rectangle leaves *out* of the
    square ``(x1, y1)-(x2, y2)``.

    The bar is drawn square -- track, then gradient, then the drain line --
    and these are painted over the result in the window's colour key, so the
    rounding is punched out of the finished bar in one step. Rounding each
    layer separately instead (insetting every gradient slice to follow the
    arc) means two independent approximations of the same curve, and the few
    pixels where they disagreed showed up as dark notches of exposed track
    at every corner.

    Sampled arcs rather than Tk's ``smooth=True`` spline: a spline through
    the corner points bows the straight edges outwards, which on a 10px-wide
    bar turns the whole thing into a lozenge.
    """
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    if r <= 0.5:
        return []
    cuts = []
    for cx, cy, start, corner in (
        (x1 + r, y1 + r, 180.0, (x1, y1)),  # top-left
        (x2 - r, y1 + r, 270.0, (x2, y1)),  # top-right
        (x2 - r, y2 - r, 0.0, (x2, y2)),    # bottom-right
        (x1 + r, y2 - r, 90.0, (x1, y2)),   # bottom-left
    ):
        pts = list(corner)
        for step in range(_ARC_STEPS + 1):
            angle = math.radians(start + 90.0 * step / _ARC_STEPS)
            pts.extend((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        cuts.append(pts)
    return cuts


class _CooldownBars:
    """One always-on-top strip of ``SLOT_COUNT`` draining bars."""

    def __init__(
        self,
        root: tk.Misc,
        config: Config,
        keys: list[str],
        section: str,
        fill: str,
        label: str,
    ):
        self.root = root
        self.config = config
        self.keys = keys
        self.section = section  # config key: "flask" or "skills"
        self.default_fill = fill
        self.label = label

        self.deadlines = [0.0] * SLOT_COUNT
        self.durations = [0.0] * SLOT_COUNT
        self.overlay: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self._rect_in_use: tuple[int, int, int, int] | None = None
        self._alpha_in_use: float | None = None
        self._hooks: list[object] = []
        self._warned = False

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        for i, key in enumerate(self.keys):
            try:
                self._hooks.append(keyboard.on_press_key(key, self._make_handler(i)))
            except (ValueError, ImportError):
                continue

    def stop(self) -> None:
        for hook in self._hooks:
            try:
                keyboard.unhook(hook)
            except (KeyError, ValueError):
                pass
        self._hooks.clear()
        self._destroy_overlay()

    def _destroy_overlay(self) -> None:
        if self.overlay is not None:
            self.overlay.destroy()
        self.overlay = None
        self.canvas = None
        self._rect_in_use = None
        self._alpha_in_use = None

    def _make_handler(self, idx: int) -> Callable[[object], None]:
        def handler(_event: object) -> None:
            self.root.after(0, self._on_used, idx)

        return handler

    # ---- state ----------------------------------------------------------
    def _settings(self) -> dict:
        return self.config.data[self.section]

    def _on_used(self, idx: int) -> None:
        misc = self.config.data["misc"]
        if misc.get("scope_to_poe_window", True) and not foreground.is_poe_focused(
            misc.get("poe_window_class", "")
        ):
            return
        # In town or a hideout, "12345"/"qwert" is far more likely to be a
        # chat message being typed than flasks or skills being used, and bars
        # flashing over the chat box every few characters is worse than no
        # bars at all.
        if zone.overlay_muted_here(self.config):
            return
        cfg = self._settings()
        if not cfg.get("overlay_enabled", True):
            return
        durations = cfg.get("cooldowns_ms", [0] * SLOT_COUNT)
        duration = durations[idx] if idx < len(durations) else 0
        if duration <= 0:
            return
        self.durations[idx] = duration / 1000.0
        self.deadlines[idx] = time.monotonic() + self.durations[idx]
        self._ensure_overlay()

    def _ensure_overlay(self) -> None:
        rect = self._settings().get("overlay_rect")
        if not rect or not all(k in rect for k in ("x1", "y1", "x2", "y2")):
            if not self._warned:
                self._warned = True
                toast.show(f"{self.label} 쿨다운 막대 위치가 설정되지 않았습니다.\n'위치 설정'을 먼저 눌러주세요.")
            return
        box = (int(rect["x1"]), int(rect["y1"]), int(rect["x2"]), int(rect["y2"]))
        # Rebuild when the user re-places it, so a new position takes effect
        # without restarting the app.
        if self.overlay is not None and self._rect_in_use != box:
            self._destroy_overlay()
        if self.overlay is None:
            self._build_overlay(box)

    def _build_overlay(self, box: tuple[int, int, int, int]) -> None:
        x1, y1, x2, y2 = box
        w, h = max(1, x2 - x1), max(1, y2 - y1)

        # Created hidden and revealed by show_passive() below: the flask bars
        # sit directly on top of the flask row the player clicks, so an
        # overlay that could take the foreground (which is what a plain
        # Toplevel does on its first map, and again on every click) would
        # stop both the game and our own window-scoped hotkeys from seeing
        # any keys at all until the user alt-tabbed back.
        self.overlay = overlay_win.new_overlay(self.root)
        try:
            # Colour-key the background away so the bars float over the game
            # instead of sitting on a black plate.
            self.overlay.wm_attributes("-transparentcolor", _TRANSPARENT)
        except tk.TclError:
            pass
        self.overlay.configure(bg=_TRANSPARENT)
        self.overlay.geometry(f"{w}x{h}+{x1}+{y1}")

        self.canvas = tk.Canvas(
            self.overlay, width=w, height=h, bg=_TRANSPARENT, highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)
        self._rect_in_use = box
        self._apply_alpha()
        overlay_win.show_passive(self.overlay)

    def _apply_alpha(self) -> None:
        """Window opacity, refreshed whenever the setting changes.

        ``-alpha`` and ``-transparentcolor`` coexist on Windows (both are
        layered-window attributes): the colour key still drops the background
        entirely, and alpha then fades what is left -- the bars themselves.
        """
        if self.overlay is None:
            return
        alpha = self._settings().get("overlay_alpha", 1.0)
        try:
            alpha = float(alpha)
        except (TypeError, ValueError):
            alpha = 1.0
        alpha = max(_MIN_ALPHA, min(1.0, alpha))
        if alpha == self._alpha_in_use:
            return
        try:
            self.overlay.attributes("-alpha", alpha)
            self._alpha_in_use = alpha
        except tk.TclError:
            pass

    # ---- drawing --------------------------------------------------------
    def draw(self, now: float) -> None:
        if self.canvas is None or self.overlay is None:
            return
        self._apply_alpha()  # cheap no-op unless the setting actually moved
        self.canvas.delete("all")
        w = self.overlay.winfo_width()
        h = self.overlay.winfo_height()
        slot_w = w / SLOT_COUNT
        pad = max(1.0, slot_w * 0.10)
        bar_w = max(1.0, slot_w - 2 * pad)
        radius = min(bar_w * _RADIUS_OF_WIDTH, h * _RADIUS_OF_HEIGHT, _RADIUS_MAX)
        slice_h = h / _GRADIENT_SLICES
        # Read live so a colour change shows up without a restart.
        fill = self._settings().get("bar_color") or self.default_fill

        for i in range(SLOT_COUNT):
            remain = self.deadlines[i] - now
            total = self.durations[i]
            if remain <= 0.03 or total <= 0:
                continue
            frac = max(0.0, min(1.0, remain / total))
            bx1 = i * slot_w + pad
            bx2 = (i + 1) * slot_w - pad
            _base, ramp, crest = slot_style(fill, i)

            # Track first: the empty part of the bar, and the plate the
            # gradient is laid onto. Square at this point -- the rounding is
            # punched out of the whole stack further down.
            self.canvas.create_rectangle(
                bx1, 0, bx2, h, fill=_TRACK_BG, outline="",
            )

            # Drains downwards: the bar shrinks from the top so the eye reads
            # "how much is left" from one edge that never moves.
            top = h * (1.0 - frac)
            # The ramp spans the whole bar height, not just the filled part,
            # so the shading stays put as the bar drains instead of sliding
            # around inside it -- the level is what should appear to move.
            for s, shade in enumerate(ramp):
                sy1 = s * slice_h
                sy2 = sy1 + slice_h
                if sy2 <= top:
                    continue  # already drained away
                self.canvas.create_rectangle(
                    bx1, max(sy1, top), bx2, sy2, fill=shade, outline="",
                )

            # A bright 1px line on the draining edge. It is what makes the
            # bar read as a level dropping rather than a rectangle being
            # progressively cropped -- and it is the part actually tracked
            # out of the corner of the eye. Skipped while still full, where
            # the bar's own top edge already reads as the level.
            if top > 1.0:
                self.canvas.create_line(bx1, top, bx2, top, fill=crest, width=1)

            for cut in _corner_cuts(bx1, 0, bx2, h, radius):
                self.canvas.create_polygon(cut, fill=_TRANSPARENT, outline="")

            if h >= _TEXT_MIN_H:
                cx, cy = (bx1 + bx2) / 2, h / 2
                text = f"{remain:0.1f}"
                # Drop shadow: the numerals sit on a colour the user picked,
                # which can be light enough to swallow white text entirely.
                self.canvas.create_text(
                    cx + 1, cy + 1, text=text, fill="#000000", font=_BAR_FONT,
                )
                self.canvas.create_text(
                    cx, cy, text=text, fill="#ffffff", font=_BAR_FONT,
                )


class FlaskTimer:
    """Owns both bar overlays and the single shared repaint tick."""

    def __init__(self, root: tk.Misc, config: Config):
        self.root = root
        self.config = config
        self.flask = _CooldownBars(
            root, config, [str(i + 1) for i in range(SLOT_COUNT)], "flask", _FLASK_FILL, "물약"
        )
        self.skills = _CooldownBars(
            root, config, list(SKILL_KEYS), "skills", _SKILL_FILL, "스킬"
        )
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.flask.start()
        self.skills.start()
        self._tick()

    def stop(self) -> None:
        self._running = False
        self.flask.stop()
        self.skills.stop()

    def _tick(self) -> None:
        if not self._running:
            return
        now = time.monotonic()
        self.flask.draw(now)
        self.skills.draw(now)
        # 50ms: a bar visibly stepping is worse than no bar at all, and at
        # 100ms (the old countdown's rate) the drain reads as a stutter.
        self.root.after(50, self._tick)

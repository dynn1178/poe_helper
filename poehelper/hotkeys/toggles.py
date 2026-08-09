"""Hold- and toggle-based gestures that don't fit the simple "press combo,
fire once" model ``HotkeyManager`` covers, so they hook press/release
events directly. Every gesture re-reads config live, so
rebinding a key or flipping an enable checkbox takes effect on next press
without needing ``apply()`` to rebuild anything for most of them; the
image-peek keys are the exception (their hook needs the base key name up
front) and expose ``rebuild()`` for that.
"""
from __future__ import annotations

import random
import threading
import tkinter as tk
from pathlib import Path

import keyboard

from .. import foreground, input_io, overlay_win, paths, zone
from ..config import Config
from . import suspend
from .keys_util import modifiers_held, parse_combo


def _poe_focused(config: Config) -> bool:
    misc = config.data["misc"]
    if not misc.get("scope_to_poe_window", True):
        return True
    return foreground.is_poe_focused(misc.get("poe_window_class", ""))


class ContinuousKeyToggle:
    """Ctrl+1: press each configured key in order, then wait out the cycle
    interval and do it again, until toggled off.

    Two independent randomisations, both there to keep the pattern from
    looking machine-generated: 100-300ms between the keys of one round, and
    the round interval itself stretched by up to 300ms (so "3 seconds" is
    really 3.0-3.3s and never lands on the same beat twice).
    """

    _GAP_MS = (100, 300)      # between keys within a round
    _CYCLE_JITTER_MS = 300    # added on top of the configured interval

    def __init__(self, root: tk.Misc, config: Config):
        self.root = root
        self.config = config
        self.active = False
        self._after_id: str | None = None

    def toggle(self) -> None:
        self.active = not self.active
        self._cancel()
        if self.active:
            self.root.after(0, self._fire)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def keys(self) -> list[str]:
        """Configured slots in order, with the unused ones dropped."""
        misc = self.config.data["misc"]
        slots = misc.get("ckeys") or []
        return [k for k in slots if isinstance(k, str) and k and k != "없음"]

    def _fire(self) -> None:
        if not self.active:
            return
        misc = self.config.data["misc"]
        keys = self.keys()
        # Left running across a portal, this would spam skill keys at the town
        # chat box. The toggle stays *on* -- it just holds its fire until the
        # character is back somewhere those keys mean something.
        if zone.macros_paused_here(self.config):
            keys = []
        if keys and not suspend.is_paused() and _poe_focused(self.config):
            # Runs on a worker thread: the gaps between keys are real sleeps,
            # and a round of five would otherwise freeze the whole UI for over
            # a second every cycle.
            threading.Thread(target=self._press_round, args=(keys,), daemon=True).start()

        delay = int(misc.get("ckey_delay_ms", 3000))
        wait = delay + random.randint(0, self._CYCLE_JITTER_MS)
        self._after_id = self.root.after(wait, self._fire)

    def _press_round(self, keys: list[str]) -> None:
        for i, key in enumerate(keys):
            if not self.active:
                return
            input_io.press(key)
            if i < len(keys) - 1:
                input_io.jitter_sleep(*self._GAP_MS)


class AutoClickToggle:
    """F12: toggle continuous left-click every 100ms."""

    def __init__(self, root: tk.Misc, config: Config):
        self.root = root
        self.config = config
        self.active = False

    def toggle(self) -> None:
        self.active = not self.active
        if self.active:
            self.root.after(0, self._loop)

    def _loop(self) -> None:
        if not self.active:
            return
        if not suspend.is_paused() and _poe_focused(self.config):
            input_io.click_current()
        self.root.after(100, self._loop)


class _RebindableHoldGesture:
    """Shared plumbing for hold-gestures whose *trigger key itself* is
    rebindable (unlike Ctrl+1/F12/etc, which are simple edge-triggered
    combos the generic HotkeyManager already handles). Subclasses implement
    ``_on_down``/``_on_up``; ``rebuild()`` re-hooks the configured base key
    whenever settings change, mirroring ``HotkeyManager.apply()``."""

    action_id: str
    default_combo: str

    def __init__(self, config: Config):
        self.config = config
        self._hooks: list[object] = []
        self._mods: list[str] = []
        self.rebuild()

    def rebuild(self) -> None:
        for hook in self._hooks:
            try:
                keyboard.unhook(hook)
            except (KeyError, ValueError):
                pass
        self._hooks.clear()
        combo = self.config.data["hotkeys"].get(self.action_id, self.default_combo)
        self._mods, base = parse_combo(combo)
        if not base:
            return
        self._hooks.append(keyboard.on_press_key(base, self._on_down, suppress=False))
        self._hooks.append(keyboard.on_release_key(base, self._on_up, suppress=False))

    def _on_down(self, _event: object) -> None:
        raise NotImplementedError

    def _on_up(self, _event: object) -> None:
        raise NotImplementedError


class MinefieldHold(_RebindableHoldGesture):
    """Tab (hold, rebindable): keeps the configured mine-trigger key held
    down for as long as the trigger is held (and enabled via the GUI
    checkbox), then releases it and presses 'd' to detonate on release."""

    action_id = "minefield_hold"
    default_combo = "tab"

    def __init__(self, config: Config):
        self._down = False
        super().__init__(config)

    def _enabled(self) -> bool:
        return bool(self.config.data["misc"].get("minefield_enabled", False))

    def _on_down(self, _event: object) -> None:
        if self._down or not self._enabled() or suspend.is_paused() or not _poe_focused(self.config):
            return
        holdkey = self.config.data["misc"].get("holdkey", "")
        if not holdkey:
            return
        self._down = True
        input_io.key_down(holdkey)

    def _on_up(self, _event: object) -> None:
        if not self._down:
            return
        holdkey = self.config.data["misc"].get("holdkey", "")
        self._down = False
        if holdkey:
            input_io.key_up(holdkey)
        input_io.press("d")


class HoldClicker(_RebindableHoldGesture):
    """Hold to click repeatedly every 15ms (rebindable, Ctrl/Alt required).

    The combo's own modifier must be held for this to start -- a bare key
    would fire the clicker every time that letter was typed in chat, which is
    also why the GUI refuses to save a combo without Ctrl or Alt. *Extra*
    modifiers are deliberately still allowed through (``modifiers_held`` is a
    subset test), so holding Shift as well keeps the clicker running and
    turns it into a stash/inventory transfer.
    """

    action_id = "autoclick_hold"
    default_combo = "ctrl+capslock"

    def __init__(self, root: tk.Misc, config: Config):
        self.root = root
        self._active = False
        super().__init__(config)

    def _on_down(self, _event: object) -> None:
        if self._active or suspend.is_paused():
            return
        if not modifiers_held(self._mods):
            return
        self._active = True
        self.root.after(0, self._loop)

    def _on_up(self, _event: object) -> None:
        self._active = False

    def _loop(self) -> None:
        if not self._active:
            return
        if not suspend.is_paused() and _poe_focused(self.config):
            input_io.click_current()
        self.root.after(15, self._loop)


class ImageKeyDisplay:
    """Ctrl+Numpad1~3 (hold): shows a reference screenshot while held.
    Rebuilds its press/release hooks whenever config changes, since (unlike
    the other gestures here) the base key itself is what's being rebound."""

    ACTION_IDS = ["show_image_1", "show_image_2", "show_image_3"]
    IMAGE_FILES = {1: "ctrl1.png", 2: "ctrl2.png", 3: "ctrl3.png"}

    def __init__(self, root: tk.Misc, config: Config):
        self.root = root
        self.config = config
        self._hooks: list[object] = []
        self._windows: dict[int, tk.Toplevel] = {}
        self._photo_cache: dict[int, object] = {}
        self.rebuild()

    def rebuild(self) -> None:
        for hook in self._hooks:
            try:
                keyboard.unhook(hook)
            except (KeyError, ValueError):
                pass
        self._hooks.clear()

        for idx, action_id in enumerate(self.ACTION_IDS, start=1):
            combo = self.config.data["hotkeys"].get(action_id)
            if not combo:
                continue
            mods, base = parse_combo(combo)
            if not base:
                continue
            self._hooks.append(
                keyboard.on_press_key(base, self._make_down(idx, mods))
            )
            self._hooks.append(
                keyboard.on_release_key(base, self._make_up(idx))
            )

    def _make_down(self, idx: int, mods: list[str]):
        def handler(_event: object) -> None:
            if suspend.is_paused() or not modifiers_held(mods) or not _poe_focused(self.config):
                return
            self.root.after(0, self._show, idx)

        return handler

    def _make_up(self, idx: int):
        def handler(_event: object) -> None:
            self.root.after(0, self._hide, idx)

        return handler

    def _show(self, idx: int) -> None:
        if idx in self._windows:
            return
        try:
            from PIL import Image, ImageTk
        except ImportError:
            return
        image_path = paths.resource_path(self.IMAGE_FILES[idx])
        if not Path(image_path).exists():
            return
        img = Image.open(image_path)
        photo = ImageTk.PhotoImage(img)
        self._photo_cache[idx] = photo  # keep a reference alive

        # Passive: this pops up mid-fight while a key is held, and a normal
        # Toplevel grabs the foreground as it maps -- which leaves the game
        # deaf to keyboard input even after the image is gone.
        win = overlay_win.new_overlay(self.root)
        tk.Label(win, image=photo, borderwidth=0).pack()
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")
        overlay_win.show_passive(win)
        self._windows[idx] = win

    def _hide(self, idx: int) -> None:
        win = self._windows.pop(idx, None)
        if win is not None:
            win.destroy()
        self._photo_cache.pop(idx, None)

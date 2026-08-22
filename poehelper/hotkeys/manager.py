"""Global-hotkey dispatch, modelled directly on the AHK original's

    #IfWinActive, ahk_class POEWindowClass

i.e. one always-on keyboard hook that decides, at the moment a key fires,
whether the foreground window is the game -- rather than registering
"context-sensitive hotkeys" with a library that keeps its own match state.

Why not ``keyboard.add_hotkey``
------------------------------
That is what this module used to do, and it failed in exactly the situation
the app exists for. ``add_hotkey`` drives an internal state machine over the
whole key stream; while playing, the game floods that stream with unrelated
key traffic (movement, skills, held keys), and the machine stops matching --
so the callback simply never runs. The raw hook underneath was verified to
receive every one of those keys, so the events were arriving and only the
matching layer was losing them. Matching the combo ourselves off the raw
event stream removes that layer entirely.

Modifier state comes from Windows, not from us
----------------------------------------------
"Is Ctrl down?" is answered by ``keys_util.held_modifiers`` (GetAsyncKeyState)
at the moment a bound key fires, rather than by a set this module maintains
from the key-down/key-up stream. The tally version drifted -- a key-up missed
while a hook was being rebuilt, ``apply()`` resetting it mid-keypress on a
config save, our own macros injecting modifier releases -- and a drifted
tally fails silently and completely: one phantom modifier stuck in the set
makes every unmodified hotkey stop matching, which is what "hotkeys randomly
stop working until I alt-tab" (alt-tab being when the stuck key finally gets
released) actually was.

Handlers also must not run on the hook thread. A flask macro sleeps for the
better part of a second (``jitter_sleep`` x5); Windows silently stops
calling a low-level hook whose callback overruns ``LowLevelHooksTimeout``,
which kills every hotkey until restart. Each trigger is therefore dispatched
to a worker thread, and callbacks that touch Tk still marshal back onto the
main loop themselves (see ``toast.py``).

Rebinding stays live: ``apply()`` rebuilds the binding table in place after
any config save, with no reload step (the AHK original needed F11).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

import keyboard

from .. import foreground
from ..config import Config
from . import suspend
from .keys_util import held_modifiers

logger = logging.getLogger(__name__)

_MODIFIERS = frozenset({"ctrl", "alt", "shift", "win"})

# keyboard reports sided names ("left ctrl"); AHK's ^ ! + # do not care which.
_MOD_ALIASES = {
    "ctrl": "ctrl", "left ctrl": "ctrl", "right ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "left alt": "alt", "right alt": "alt", "alt gr": "alt",
    "shift": "shift", "left shift": "shift", "right shift": "shift",
    "win": "win", "windows": "win", "left windows": "win", "right windows": "win",
}


@dataclass
class _Binding:
    action_id: str
    combo: str
    mods: frozenset[str]
    base: str
    scan_codes: frozenset[int]
    keypad: bool
    fn: Callable[[], None]
    armed: bool = False
    running: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def label(self) -> str:
        return f"{self.action_id}={self.combo}"


def _parse(combo: str) -> tuple[frozenset[str], str]:
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return frozenset(), ""
    *mods, base = parts
    return frozenset(_MOD_ALIASES.get(m, m) for m in mods if _MOD_ALIASES.get(m, m) in _MODIFIERS), base


class HotkeyManager:
    def __init__(self, config: Config):
        self.config = config
        self._handlers: dict[str, Callable[[], None]] = {}
        self._bindings: list[_Binding] = []
        self._hook = None

    # ---- wiring ---------------------------------------------------------
    def set_handler(self, action_id: str, fn: Callable[[], None]) -> None:
        """Register the Python callback backing a config-defined action id
        (e.g. "flask_macro", "macro_0"). Does not itself bind a hotkey --
        call apply() afterwards."""
        self._handlers[action_id] = fn

    def apply(self) -> None:
        """Rebuild every binding from the current config. Safe to call
        repeatedly (e.g. after every settings save)."""
        bindings: list[_Binding] = []

        for action_id, combo in self.config.data["hotkeys"].items():
            fn = self._handlers.get(action_id)
            if fn and combo:
                b = self._build(action_id, combo, fn)
                if b:
                    bindings.append(b)

        for i, macro in enumerate(self.config.data["chat_macros"]):
            combo = macro.get("hotkey")
            if not combo:
                continue
            fn = self._handlers.get("__send_chat_macro__")
            if fn:
                text = macro.get("text", "")
                # Missing means True: every macro saved before the option
                # existed went through the chat box, and an old config has to
                # keep behaving the way it did.
                chat = bool(macro.get("chat", True))
                b = self._build(
                    f"macro_{i}", combo, lambda t=text, c=chat: fn(t, c)
                )
                if b:
                    bindings.append(b)

        # Saved map-search regexes (정규식 tab). Bound the same way as chat
        # macros -- config rows rather than fixed action ids -- because how
        # many there are is up to the user.
        presets = self.config.data.get("map_regex", {}).get("presets", [])
        for i, preset in enumerate(presets):
            combo = preset.get("hotkey")
            pattern = preset.get("pattern", "")
            if not combo or not pattern:
                continue
            fn = self._handlers.get("__paste_map_regex__")
            if fn:
                b = self._build(f"map_regex_{i}", combo, lambda p=pattern: fn(p))
                if b:
                    bindings.append(b)

        self._bindings = bindings
        self._ensure_hook()
        logger.info("registered %d hotkeys", len(bindings))

        # Duplicates still all fire -- that is how a raw hook works -- so say
        # so rather than leaving the user to work out why one key does two
        # things. (A chat macro on F8 plus an action on F8 sends the macro
        # *and* empties a stash tab.)
        seen: dict[str, str] = {}
        for b in bindings:
            key = f"{'+'.join(sorted(b.mods))}|{b.base}"
            if key in seen:
                logger.warning(
                    "hotkey conflict: %s and %s are both bound to %r - both will fire",
                    seen[key], b.action_id, b.combo,
                )
            else:
                seen[key] = b.action_id
        for b in bindings:
            logger.debug("  bound %s -> mods=%s base=%r scans=%s",
                         b.label, sorted(b.mods) or "-", b.base, sorted(b.scan_codes))

    def _build(self, action_id: str, combo: str, fn: Callable[[], None]) -> _Binding | None:
        mods, base = _parse(combo)
        if not base:
            logger.warning("skipping hotkey %s=%r: no key", action_id, combo)
            return None
        try:
            scans = frozenset(keyboard.key_to_scan_codes(base))
        except (ValueError, KeyError):
            # Unknown name: fall back to matching on the reported key name.
            scans = frozenset()
            logger.warning("hotkey %s=%r: unknown key %r, matching by name", action_id, combo, base)
        return _Binding(
            action_id=action_id,
            combo=combo,
            mods=mods,
            base=base,
            scan_codes=scans,
            keypad=base.startswith("num "),
            fn=fn,
        )

    def _ensure_hook(self) -> None:
        if self._hook is None:
            self._hook = keyboard.hook(self._on_event)
            logger.info("keyboard hook installed")

    def unregister_all(self) -> None:
        self._bindings = []
        if self._hook is not None:
            try:
                keyboard.unhook(self._hook)
            except (KeyError, ValueError):
                pass
            self._hook = None

    # ---- the #IfWinActive equivalent ------------------------------------
    def _scoped_ok(self) -> bool:
        misc = self.config.data["misc"]
        if not misc.get("scope_to_poe_window", True):
            return True
        return foreground.is_poe_focused(misc.get("poe_window_class", ""))

    def _matches(self, b: _Binding, event) -> bool:
        if b.keypad != bool(getattr(event, "is_keypad", False)):
            return False
        if b.scan_codes:
            return event.scan_code in b.scan_codes
        return (event.name or "").lower() == b.base

    def _on_event(self, event) -> None:
        """Runs on the hook thread -- must stay fast and never raise."""
        try:
            name = (event.name or "").lower()
            own_mod = _MOD_ALIASES.get(name)
            down = event.event_type == keyboard.KEY_DOWN

            # Asked of Windows rather than accumulated from the event stream:
            # a running tally drifts (see keys_util), and one phantom
            # modifier in it silently stops every hotkey from matching until
            # that key happens to be pressed and released again. Resolved
            # lazily, only once a bound key is actually involved -- this runs
            # on the hook thread for every keystroke of normal play, and a
            # low-level hook that overruns its timeout gets removed by
            # Windows outright.
            active: frozenset[str] | None = None

            for b in self._bindings:
                if not self._matches(b, event):
                    continue
                if down:
                    if active is None:
                        # A combo's own modifier must not count against the
                        # exact-match test, or "ctrl" as a standalone hotkey
                        # could never fire.
                        active = frozenset(held_modifiers() - {own_mod or ""})
                    # AHK requires an exact modifier state: ` does not fire
                    # while Ctrl is down, that would be ^`.
                    b.armed = active == b.mods
                elif b.armed:
                    b.armed = False
                    self._trigger(b)
        except Exception:
            logger.exception("hotkey hook event failed")

    def _trigger(self, b: _Binding) -> None:
        """Fire on the RELEASE edge, off the hook thread.

        Release rather than press because these are one-shot actions and a
        still-held modifier bleeds into whatever the handler presses next --
        notably Alt+Enter (the game's fullscreen toggle) when a chat macro
        opened chat with Enter while Alt was physically still down.
        """
        if suspend.is_paused():
            logger.debug("hotkey ignored (suspended): %s", b.label)
            return
        if not self._scoped_ok():
            logger.debug(
                "hotkey ignored (window scope): %s foreground=%r expected=%r",
                b.label,
                foreground.foreground_class_name(),
                self.config.data["misc"].get("poe_window_class", ""),
            )
            return

        with b._lock:
            if b.running:
                logger.debug("hotkey ignored (already running): %s", b.label)
                return
            b.running = True

        def run() -> None:
            logger.debug("hotkey fired: %s", b.label)
            try:
                b.fn()
                logger.debug("hotkey done: %s", b.label)
            except Exception:
                logger.exception("hotkey handler failed: %s", b.label)
            finally:
                with b._lock:
                    b.running = False

        threading.Thread(target=run, name=f"hotkey-{b.action_id}", daemon=True).start()

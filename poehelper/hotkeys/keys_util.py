"""Shared helpers for parsing "ctrl+alt+f1"-style combo strings into a
(modifiers, base_key) pair, used by hold/release-gesture features that the
generic edge-triggered ``HotkeyManager`` model doesn't fit (image-peek keys,
the minefield hold-key, autoclick-hold) -- plus the one authoritative answer
to "which modifiers are down right now?".

That last part is asked of Windows directly rather than of any running tally
of key-down/key-up events. Every tally-based answer drifts, and a drifted
modifier set is silent and total: one phantom "ctrl" stuck in the set makes
*every* unmodified hotkey stop matching (` for flasks, the F-key macros),
with no error anywhere, until whatever key is stuck happens to get pressed
and released again -- which is exactly what alt-tabbing away and back does,
and why the failure looked random and self-healing. Ways the tally drifts,
all of them real here:

* a key-up that arrives while our hook is momentarily not installed (the
  hold-gesture hooks are torn down and rebuilt on every config save),
* ``HotkeyManager.apply()`` resetting its own tally on every config save --
  a save while Ctrl is held used to leave Ctrl unaccounted for,
* our own macros injecting modifier ups/downs (``send_chat_message``
  releases Ctrl/Alt/Shift before typing) into the same event stream the
  tally is built from.

``GetAsyncKeyState`` has none of that: it is the state Windows itself will
use, so it agrees with the game by construction.
"""
from __future__ import annotations

import ctypes
import sys

_MODIFIERS = {"ctrl", "alt", "shift", "win"}

# Modifier name -> the virtual-key codes that count as "this is held". The
# sided variants are what a keyboard actually reports; the unsided VK is what
# Windows sets in addition, and checking both keeps a right-hand Alt/Ctrl
# working as a modifier exactly like the left one.
_VK_CODES: dict[str, tuple[int, ...]] = {
    "ctrl": (0x11, 0xA2, 0xA3),   # VK_CONTROL, VK_LCONTROL, VK_RCONTROL
    "alt": (0x12, 0xA4, 0xA5),    # VK_MENU, VK_LMENU, VK_RMENU
    "shift": (0x10, 0xA0, 0xA1),  # VK_SHIFT, VK_LSHIFT, VK_RSHIFT
    "win": (0x5B, 0x5C),          # VK_LWIN, VK_RWIN
}

_DOWN_BIT = 0x8000

_get_async_key_state = None
if sys.platform == "win32":
    _get_async_key_state = ctypes.windll.user32.GetAsyncKeyState
    _get_async_key_state.argtypes = [ctypes.c_int]
    _get_async_key_state.restype = ctypes.c_short


def parse_combo(combo: str) -> tuple[list[str], str]:
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return [], ""
    *mods, base = parts
    mods = [m for m in mods if m in _MODIFIERS]
    return mods, base


def is_modifier_down(name: str) -> bool:
    """Is *name* ("ctrl"/"alt"/"shift"/"win") physically down right now?"""
    codes = _VK_CODES.get(name)
    if not codes or _get_async_key_state is None:
        # Non-Windows (tests only) -- fall back to the keyboard library's
        # own tally rather than claiming nothing is held.
        import keyboard

        return bool(keyboard.is_pressed(name))
    return any(_get_async_key_state(vk) & _DOWN_BIT for vk in codes)


def held_modifiers() -> set[str]:
    """Every modifier currently down, as a set of canonical names."""
    return {name for name in _VK_CODES if is_modifier_down(name)}


def modifiers_held(mods: list[str]) -> bool:
    return all(is_modifier_down(m) for m in mods)

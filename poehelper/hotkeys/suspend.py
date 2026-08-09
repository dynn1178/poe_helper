"""Global, in-memory (never persisted) hotkey-suspend flag: lets the user
temporarily pause every registered hotkey/hold-gesture from the GUI without
unregistering anything, so re-enabling instantly restores normal behaviour.
Session-only on purpose -- this is for "I need to alt-tab and type in
Discord for a second," not a setting that should survive a restart."""
from __future__ import annotations

_paused = False


def is_paused() -> bool:
    return _paused


def toggle() -> bool:
    global _paused
    _paused = not _paused
    return _paused

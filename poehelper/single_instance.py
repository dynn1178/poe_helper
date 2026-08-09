"""Refuse to start a second copy, and raise the first one instead.

Two copies of this app is not a harmless annoyance. Both install a global
low-level keyboard hook and both register the same hotkeys, so every key the
user presses fires its macro *twice* -- two flask presses, two chat messages,
two stash-pull runs racing each other over the same inventory grid. The
second copy is easy to start by accident too: the window hides to the tray,
so "it doesn't look like it's running" is exactly what the user sees right
before double-clicking the exe again.

A named mutex is the standard Windows answer. The kernel releases it when
the process ends, however it ends -- crash, kill, or clean exit -- so there
is no stale-lock file to clean up, unlike a pidfile.
"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

# A restart hands over: the outgoing copy starts the new one and only then
# shuts itself down, so for a moment both exist and the lock is still held by
# the copy on its way out. The incoming copy is told to expect that with this
# flag and waits for the handover instead of mistaking it for a duplicate.
RESTART_FLAG = "--restarted"
_HANDOVER_TIMEOUT = 15.0  # seconds; generous -- teardown unhooks and saves
_HANDOVER_POLL = 0.2

# Session-local (no "Global\\") on purpose: a second user logged into the
# same machine gets their own game and their own helper.
_MUTEX_NAME = "KuanPoeHelper.SingleInstance"
_ERROR_ALREADY_EXISTS = 183

_WINDOW_TITLE_PREFIX = "Kuan POE Helper"

_SW_RESTORE = 9
_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32

# Held for the process lifetime. Module-level so it is never garbage
# collected -- dropping the handle would release the mutex and quietly let a
# second copy in.
_handle = None


def _raise_existing_window() -> bool:
    """Show and focus the already-running copy's window. True if found.

    Matched on the title prefix rather than the exact title, because the
    title carries the version number and the running copy may well be a
    different version from the one being launched.
    """
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        length = _user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value.startswith(_WINDOW_TITLE_PREFIX):
                found.append(hwnd)
                return False  # stop enumerating
        return True

    _user32.EnumWindows(visit, 0)
    if not found:
        return False
    hwnd = found[0]
    # SW_RESTORE also un-hides a window that was withdrawn to the tray, which
    # is the common case: the user could not see it, so they launched again.
    _user32.ShowWindow(hwnd, _SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)
    return True


def _try_take() -> bool | None:
    """Take the mutex. True = ours, False = someone else's, None = unusable."""
    global _handle
    _handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not _handle:
        return None
    if _kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(_handle)
        _handle = None
        return False
    return True


def acquire(handover: bool = False) -> bool:
    """True if this process may run; False if another copy already owns it.

    On False the caller must exit without touching hotkeys or the tray.

    ``handover`` waits for the lock rather than giving up on it, for the one
    case where another copy holding it is expected and temporary: a restart,
    where the copy being replaced is already shutting down.
    """
    taken = _try_take()
    if taken is None:
        # Nothing here is worth refusing to start over -- if the mutex cannot
        # be created at all, fall through and behave as before.
        logger.warning("could not create single-instance mutex; continuing")
        return True
    if not taken and handover:
        logger.info("waiting for the outgoing instance to release the lock")
        deadline = time.monotonic() + _HANDOVER_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(_HANDOVER_POLL)
            taken = _try_take()
            if taken is None:
                return True
            if taken:
                logger.info("handover complete")
                return True
        logger.warning("handover timed out; the previous instance is still running")
    if not taken:
        logger.info("another instance is already running; raising its window")
        if not _raise_existing_window():
            # Running but with no window we can find (starting up, or hidden
            # in a way EnumWindows missed). Say so, rather than exiting
            # silently and looking like the exe is broken.
            _user32.MessageBoxW(
                None,
                "Kuan POE Helper가 이미 실행 중입니다.\n"
                "작업 표시줄 오른쪽 아래 트레이 아이콘을 확인해주세요.",
                "Kuan POE Helper",
                0x40,  # MB_ICONINFORMATION
            )
        return False
    return True

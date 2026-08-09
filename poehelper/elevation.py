"""Admin-elevation helpers.

Elevation is not cosmetic here: Windows' UIPI blocks synthetic input
(``SendInput``, which every macro in ``input_io`` ends up calling) from
reaching a window that runs at a higher integrity level. Against an
elevated PoE client an unelevated helper still *sees* the hotkey via its
low-level keyboard hook but every key it sends afterwards is silently
dropped -- which looks exactly like "the hotkeys don't work".

The packaged EXE requests elevation via its embedded manifest
(``build.spec`` -> ``uac_admin=True``), so Windows itself shows the UAC
prompt before the process even starts. The relaunch path below only
matters when running from source (``python main.py``) without that
manifest, mirroring the original AHK's ``if not A_IsAdmin { Run *RunAs
... }`` guard.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from pathlib import Path

logger = logging.getLogger(__name__)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_INTEGRITY_LEVEL = 25

INTEGRITY_MEDIUM = 0x2000
INTEGRITY_HIGH = 0x3000

_INTEGRITY_NAMES = {
    0x0000: "UNTRUSTED",
    0x1000: "LOW",
    INTEGRITY_MEDIUM: "MEDIUM",
    0x2100: "MEDIUM_PLUS",
    INTEGRITY_HIGH: "HIGH (elevated)",
    0x4000: "SYSTEM",
}

_k32 = ctypes.windll.kernel32
_advapi = ctypes.windll.advapi32
# Without explicit types ctypes truncates returned pointers to 32 bits on x64.
_k32.OpenProcess.restype = wintypes.HANDLE
_advapi.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
_advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
_advapi.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_advapi.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)


def integrity_name(level: int | None) -> str:
    if level is None:
        return "unknown"
    return _INTEGRITY_NAMES.get(level, f"0x{level:04X}")


def process_integrity(pid: int) -> int | None:
    """Mandatory-label RID of a process token, or None if unreadable.

    Reading *up* is allowed (the default mandatory policy is NO_WRITE_UP), so
    this works from a normal-integrity process against an elevated one -- which
    is exactly the case that matters here.
    """
    handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    token = wintypes.HANDLE()
    try:
        if not _advapi.OpenProcessToken(handle, _TOKEN_QUERY, ctypes.byref(token)):
            return None
        size = wintypes.DWORD()
        _advapi.GetTokenInformation(token, _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not _advapi.GetTokenInformation(
            token, _TOKEN_INTEGRITY_LEVEL, buf, size, ctypes.byref(size)
        ):
            return None
        # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES Label } -> Label.Sid
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        count = _advapi.GetSidSubAuthorityCount(sid)[0]
        return int(_advapi.GetSidSubAuthority(sid, count - 1)[0])
    finally:
        if token:
            _k32.CloseHandle(token)
        _k32.CloseHandle(handle)


def current_integrity() -> int | None:
    return process_integrity(_k32.GetCurrentProcessId())


def process_image_path(pid: int) -> str | None:
    """Full path of a process's executable, or None if it cannot be read.

    Works against an elevated process from a normal one for the same reason
    process_integrity() does -- the default mandatory policy only blocks
    writing up, not querying.
    """
    handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        _k32.CloseHandle(handle)

# ShellExecuteW reports success as a fake HINSTANCE greater than 32; anything
# at or below that is an error code (1223 == the user dismissed the UAC dialog).
_SHELL_EXECUTE_MIN_SUCCESS = 32
_ERROR_CANCELLED = 1223

_MB_ICONWARNING = 0x30


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _alert(message: str, blocking: bool = True) -> None:
    """Last-resort user feedback. Tk isn't up yet at elevation time (and may
    never come up), so go straight to the Win32 message box.

    ``blocking=False`` shows it from a worker thread instead. MessageBoxW
    does not return until it is dismissed, so an alert raised on a path that
    keeps running (rather than exiting straight after) would otherwise hold
    up startup completely -- no main window at all until someone clicks OK.
    Paths that exit immediately must stay blocking, or the process would
    terminate before the message could be read.
    """
    def show() -> None:
        try:
            ctypes.windll.user32.MessageBoxW(None, message, "Kuan PoE Helper", _MB_ICONWARNING)
        except Exception:
            logger.warning("could not show message box: %s", message)

    if blocking:
        show()
    else:
        threading.Thread(target=show, daemon=True).start()


def relaunch_as_admin() -> bool:
    """Start an elevated copy of this script. Returns True if Windows
    accepted the request (this process should then exit)."""
    # argv[0] is whatever was typed -- for the usual `python main.py` that
    # is the bare relative name "main.py". An elevated process is started
    # by Windows' AppInfo service rather than inherited from this one, so
    # it does not reliably get this process's working directory: the
    # relaunched interpreter would be handed a relative path it cannot
    # resolve and die immediately, leaving nothing running at all. Resolving
    # the script and passing its folder as the working directory removes
    # that dependency.
    #
    # sys.executable is inherited as-is, so a run started from .venv relaunches
    # with that same interpreter and keeps its site-packages.
    script = Path(sys.argv[0] or (Path(__file__).resolve().parent.parent / "main.py")).resolve()
    args = [str(script), *sys.argv[1:]]
    params = " ".join(f'"{a}"' for a in args)

    shell_execute = ctypes.windll.shell32.ShellExecuteW
    shell_execute.restype = ctypes.c_void_p  # HINSTANCE: a pointer, not an int, on x64
    rc = shell_execute(None, "runas", sys.executable, params, str(script.parent), 1)
    rc = int(rc or 0)

    if rc > _SHELL_EXECUTE_MIN_SUCCESS:
        logger.info("relaunching elevated: %s %s", sys.executable, params)
        return True

    if rc == _ERROR_CANCELLED:
        _alert(
            "관리자 권한 요청이 취소되었습니다.\n\n"
            "관리자 권한 없이는 게임 창으로 키 입력이 전달되지 않아\n"
            "단축키가 동작하지 않습니다. UAC 창에서 '예'를 눌러주세요."
        )
    else:
        _alert(
            f"관리자 권한으로 다시 실행하지 못했습니다. (오류 코드 {rc})\n\n"
            "run_admin.bat 을 우클릭 -> '관리자 권한으로 실행' 으로 시작해 주세요."
        )
    logger.error("ShellExecuteW(runas) failed with code %s", rc)
    return False


def check_game_outranks_us(poe_window_class: str) -> bool:
    """Warn if the running game sits at a higher integrity level than we do.

    ``PathOfExile_KG.exe`` (the Daum/Korean client) launches elevated. Against
    a HIGH-integrity foreground window a MEDIUM-integrity helper is disabled in
    *both* directions at once: UIPI drops the input it sends, and its
    low-level keyboard hook never even sees the keys being pressed. Nothing
    errors -- hotkeys simply do nothing at all, which is impossible to tell
    apart from a broken binding without measuring the token. So measure it.

    Returns True if we are outranked (i.e. hotkeys cannot work).
    """
    if not poe_window_class:
        return False
    try:
        import win32gui
        import win32process
    except ImportError:
        return False

    hwnd = win32gui.FindWindow(poe_window_class, None)
    if not hwnd:
        return False  # game isn't running; nothing to compare against yet

    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    game = process_integrity(pid)
    ours = current_integrity()
    logger.info(
        "integrity: us=%s game(%s pid=%s)=%s",
        integrity_name(ours), poe_window_class, pid, integrity_name(game),
    )
    if game is None or ours is None or game <= ours:
        return False

    logger.error(
        "game outranks us (%s > %s) - UIPI will block every hotkey",
        integrity_name(game), integrity_name(ours),
    )
    _alert(
        "게임이 관리자 권한으로 실행 중입니다.\n\n"
        f"게임: {integrity_name(game)}\n"
        f"이 프로그램: {integrity_name(ours)}\n\n"
        "이 상태에서는 단축키가 하나도 동작하지 않습니다.\n"
        "키 입력이 게임에 전달되지 않고, 키를 눌러도 감지조차 되지 않습니다.\n\n"
        "run_admin.bat 으로 이 프로그램을 관리자 권한으로 실행해 주세요.",
        blocking=False,  # the app carries on starting; don't hold up the window
    )
    return True


def ensure_admin() -> bool:
    """Returns True if already elevated. If not and running from source,
    relaunches elevated and returns False (caller should exit)."""
    if is_admin():
        return True
    if getattr(sys, "frozen", False):
        # Frozen EXE should already be elevated via manifest; if we get
        # here the manifest is missing/was bypassed - don't loop-relaunch,
        # but say so, because the macros will misbehave from now on.
        logger.error("frozen build is running unelevated - the UAC manifest was bypassed")
        _alert(
            "관리자 권한 없이 실행 중입니다.\n\n"
            "게임 창으로 키 입력이 전달되지 않아 단축키가 동작하지 않을 수 있습니다.\n"
            "프로그램을 종료한 뒤 우클릭 -> '관리자 권한으로 실행' 으로 다시 시작해 주세요."
        )
        return False
    relaunch_as_admin()
    return False

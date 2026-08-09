"""Which zone the character is standing in, read from the game's own log.

The client appends a line to ``logs/KakaoClient.txt`` (``Client.txt`` on the
international build) every time a zone loads::

    2026/08/09 02:39:07 ... [INFO Client 19256] [SCENE] Set Source [(null)]
    2026/08/09 02:39:07 ... [INFO Client 19256] [SCENE] Set Source [밀림 계곡]
    2026/08/09 02:39:08 ... [INFO Client 19256] : 밀림 계곡에 진입했습니다.

``[SCENE] Set Source`` is the one used here rather than the "진입했습니다"
chat line: it is engine output rather than chat, so no other player can forge
it by typing, and it carries the bare zone name with nothing to strip. It
lands within a second of the loading screen ending, and unlike reading the
screen it does not care about resolution, gamma or UI scale.

Knowing this lets input be gated on where the character actually is -- macros
paused in town, and the cooldown overlays kept quiet in a hideout, where
"12345" or "qwert" is far more likely to be someone typing in chat than
someone using flasks.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SCENE_RE = re.compile(r"\[SCENE\] Set Source \[(.+?)\]\s*$")

# Emitted while a zone is loading; not a place, so it must not overwrite the
# last real zone (that would read as "unknown" for the whole loading screen).
_PLACEHOLDERS = {"(null)", "(unknown)", ""}

# Every hideout in both clients ends with this, which covers all 45 distinct
# hideouts seen in a real player's log without naming any of them.
_HIDEOUT_SUFFIXES = ("은신처", "Hideout")

# Towns have to be matched by *exact* name. Substring matching looks tempting
# ("항구", "초소", "부두" all appear in town names) and is wrong: 오리아스 부두
# is a town but 부두 is a map, 라이온아이 초소 is a town but 광기의 초소 is a
# map. All five of those appear in a real log.
TOWNS = {
    # PoE1 Korean
    "라이온아이 초소",
    "숲 야영지",
    "사안 야영지",
    "하이게이트",
    "다리 야영지",
    "오리아스 부두",
    "카루이의 바닷가",
    "킹스마치",
    "도적 항구",
    # PoE1 English, for anyone running the international client
    "Lioneye's Watch",
    "The Forest Encampment",
    "The Sarn Encampment",
    "Highgate",
    "The Bridge Encampment",
    "Oriath Docks",
    "Karui Shores",
    "Kingsmarch",
    "Rogue Harbour",
}

TOWN = "town"
HIDEOUT = "hideout"
COMBAT = "combat"
UNKNOWN = "unknown"

_KIND_LABELS = {
    TOWN: "마을",
    HIDEOUT: "은신처",
    COMBAT: "사냥터",
    UNKNOWN: "알 수 없음",
}


def classify(zone_name: str, extra_towns: set[str] | None = None) -> str:
    name = (zone_name or "").strip()
    if not name or name in _PLACEHOLDERS:
        return UNKNOWN
    if name.endswith(_HIDEOUT_SUFFIXES):
        return HIDEOUT
    if name in TOWNS or (extra_towns and name in extra_towns):
        return TOWN
    return COMBAT


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind)


# ---------------------------------------------------------------------------
# Locating the log
# ---------------------------------------------------------------------------
_LOG_NAMES = ("KakaoClient.txt", "Client.txt")

_FALLBACK_DIRS = (
    r"C:\Daum Games\POE\logs",
    r"C:\Program Files (x86)\Grinding Gear Games\Path of Exile\logs",
    r"C:\Program Files (x86)\Steam\steamapps\common\Path of Exile\logs",
)


def _log_in(directory: Path) -> Path | None:
    for name in _LOG_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def find_log_from_running_game(window_class: str = "POEWindowClass") -> Path | None:
    """Locate logs/ next to the running client's executable.

    Preferred over guessing install paths: it finds the build actually being
    played, which is what matters when PoE1 and PoE2 are both installed.
    """
    try:
        import win32gui
        import win32process
    except ImportError:
        return None

    hwnd = win32gui.FindWindow(window_class, None)
    if not hwnd:
        return None
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        from . import elevation

        exe = elevation.process_image_path(pid)
    except Exception:
        logger.debug("could not resolve game executable", exc_info=True)
        return None
    if not exe:
        return None
    return _log_in(Path(exe).parent / "logs")


def resolve_log_path(configured: str = "", window_class: str = "POEWindowClass") -> Path | None:
    """Configured path wins; otherwise auto-detect, then fall back to the
    usual install locations."""
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    found = find_log_from_running_game(window_class)
    if found:
        return found
    for directory in _FALLBACK_DIRS:
        found = _log_in(Path(directory))
        if found:
            return found
    return None


# ---------------------------------------------------------------------------
# Tailing
# ---------------------------------------------------------------------------
class ZoneTracker:
    """Follows the log from its end and keeps the current zone up to date."""

    _POLL_SEC = 0.4
    _RETRY_SEC = 5.0

    def __init__(self, config):
        self.config = config
        self.zone: str = ""
        self.kind: str = UNKNOWN
        self.log_path: Path | None = None
        self.error: str = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="zone-tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    # ---- settings -------------------------------------------------------
    def _settings(self) -> dict:
        return self.config.data.get("zone", {})

    def enabled(self) -> bool:
        return bool(self._settings().get("enabled", True))

    def _extra_towns(self) -> set[str]:
        return {t.strip() for t in self._settings().get("extra_town_names", []) if t.strip()}

    def _extra_safe(self) -> set[str]:
        """Anywhere else the user wants treated like a hideout."""
        return {t.strip() for t in self._settings().get("extra_safe_zones", []) if t.strip()}

    # ---- state ----------------------------------------------------------
    def is_safe(self) -> bool:
        """Hideout only -- the one place macros are suppressed.

        Towns are deliberately excluded even though nothing can be fought in
        them: a town is somewhere you pass through mid-run, and having flasks
        stop working there is its own kind of surprise. A hideout is where you
        stop and talk, so that is where a stray ``1``-``5`` is overwhelmingly
        likely to be chat rather than play.

        UNKNOWN is not safe either: if the zone cannot be read, leaving the
        macros working exactly as they always did beats silently disabling
        them.
        """
        return self.kind == HIDEOUT or self.zone in self._extra_safe()

    def describe(self) -> str:
        if not self.enabled():
            return "지역 감지 꺼짐"
        if self.error:
            return f"로그를 읽을 수 없음 ({self.error})"
        if not self.zone:
            return "지역 확인 중… (지역을 한 번 이동하면 인식됩니다)"
        return f"{self.zone}  ·  {kind_label(self.kind)}"

    def _set_zone(self, name: str) -> None:
        kind = classify(name, self._extra_towns())
        if kind is UNKNOWN and not name:
            return
        if name == self.zone:
            return
        self.zone, self.kind = name, kind
        logger.info("zone: %s (%s)", name, kind)
        _notify(name, kind)

    # ---- the tail loop --------------------------------------------------
    def _run(self) -> None:
        handle = None
        try:
            while not self._stop.is_set():
                if not self.enabled():
                    self._sleep(self._RETRY_SEC)
                    continue
                if handle is None:
                    handle = self._open()
                    if handle is None:
                        self._sleep(self._RETRY_SEC)
                        continue
                try:
                    handle, progressed = self._read_new(handle)
                except OSError as exc:
                    self.error = str(exc)
                    if handle:
                        handle.close()
                    handle = None
                    self._sleep(self._RETRY_SEC)
                    continue
                self._sleep(0.05 if progressed else self._POLL_SEC)
        finally:
            if handle:
                handle.close()

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def _open(self):
        path = resolve_log_path(
            self._settings().get("log_path", ""),
            self.config.data["misc"].get("poe_window_class", "POEWindowClass"),
        )
        if path is None:
            self.error = "로그 파일을 찾지 못했습니다"
            return None
        try:
            # Binary + errors="replace" on decode: the log is UTF-8 but a read
            # can land mid-character while the client is still writing.
            handle = open(path, "rb")
        except OSError as exc:
            self.error = str(exc)
            return None
        handle.seek(0, 2)  # only new lines matter; the file is tens of MB
        self.log_path = path
        self.error = ""
        self._buffer = b""
        logger.info("tailing client log: %s", path)
        return handle

    def _read_new(self, handle):
        position = handle.tell()
        size = self.log_path.stat().st_size if self.log_path else position
        if size < position:
            # Truncated or replaced (new session): start over from the top of
            # the new file rather than sitting past its end forever.
            handle.close()
            return self._open(), True

        chunk = handle.read()
        if not chunk:
            return handle, False

        data = getattr(self, "_buffer", b"") + chunk
        lines = data.split(b"\n")
        self._buffer = lines.pop()  # keep the partial trailing line
        for raw in lines:
            match = _SCENE_RE.search(raw.decode("utf-8", errors="replace"))
            if match:
                name = match.group(1).strip()
                if name not in _PLACEHOLDERS:
                    self._set_zone(name)
        return handle, True


# ---------------------------------------------------------------------------
# Module-level accessor
# ---------------------------------------------------------------------------
# Mirrors hotkeys/suspend.py: the gate is consulted from hook callbacks, GUI
# code and game actions alike, and threading one tracker instance through all
# of them would mean changing every constructor on the path.
_tracker: ZoneTracker | None = None


def set_tracker(tracker: ZoneTracker | None) -> None:
    global _tracker
    _tracker = tracker


def get_tracker() -> ZoneTracker | None:
    return _tracker


def _flag(config, key: str) -> bool:
    return bool(config.data.get("zone", {}).get(key, True))


def in_safe_zone() -> bool:
    """In a hideout (or a zone the user marked as one)."""
    return _tracker is not None and _tracker.enabled() and _tracker.is_safe()


def current_zone() -> str:
    return _tracker.zone if _tracker is not None else ""


def macros_paused_here(config) -> bool:
    """True when a combat macro should do nothing because we are parked."""
    return _flag(config, "pause_macros_in_hideout") and in_safe_zone()


def overlay_muted_here(config) -> bool:
    """True when a cooldown bar should not be raised.

    In a hideout a 1-5 or Q-T keypress is much more likely to be a chat
    message being typed than a flask being used, and an overlay popping up
    over the chat box every few characters is worse than no overlay at all.
    """
    return _flag(config, "hide_overlay_in_hideout") and in_safe_zone()


# ---------------------------------------------------------------------------
# Change notification (used by the levelling route to follow along)
# ---------------------------------------------------------------------------
_listeners: list = []


def on_zone_change(callback) -> None:
    """Register ``callback(zone_name, kind)``, fired on every real change.

    Callbacks run on the tracker's thread, so anything touching Tk must
    marshal itself back onto the main loop.
    """
    _listeners.append(callback)


def remove_listener(callback) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def _notify(name: str, kind: str) -> None:
    for callback in list(_listeners):
        try:
            callback(name, kind)
        except Exception:
            logger.exception("zone listener failed")

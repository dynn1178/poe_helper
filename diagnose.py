"""Live hotkey diagnostic -- run this INSTEAD of the app.

Everything is written to diagnose.log as well as the console, so you can just
play for a few seconds and read the file afterwards instead of trying to watch
a console window from inside a fullscreen game.

It separates the three things the app's own log cannot:
  1. does the low-level keyboard hook see keys while PoE has focus?
  2. does the PoE-window scope check pass at that moment?
  3. does a key we SEND actually arrive in the game?

Nothing here touches config.json.
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
import winsound
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import keyboard
import win32gui

from poehelper import elevation, foreground, input_io

LOG = ROOT / "diagnose.log"
_log_file = LOG.open("w", encoding="utf-8")
_lock = threading.Lock()


def log(msg: str = "") -> None:
    line = f"{datetime.now():%H:%M:%S.%f}"[:-3] + f" {msg}" if msg else ""
    with _lock:
        print(msg)
        _log_file.write(line + "\n")
        _log_file.flush()


cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
misc = cfg["misc"]
expected_class = misc.get("poe_window_class", "")
scoped = misc.get("scope_to_poe_window", True)

log("=" * 70)
log(" Kuan PoE Helper - hotkey diagnostic")
log("=" * 70)
log(f" admin              : {bool(ctypes.windll.shell32.IsUserAnAdmin())}")
log(f" python             : {sys.executable}")
log(f" scope_to_poe_window: {scoped}")
log(f" expected class     : {expected_class!r}")
log("")
_hwnd = win32gui.FindWindow(expected_class, None)
if _hwnd:
    import win32process

    _, _pid = win32process.GetWindowThreadProcessId(_hwnd)
    _game = elevation.process_integrity(_pid)
    _ours = elevation.current_integrity()
    log(f" our integrity      : {elevation.integrity_name(_ours)}")
    log(f" game integrity     : {elevation.integrity_name(_game)}  (pid {_pid})")
    if _game is not None and _ours is not None and _game > _ours:
        log("")
        log(" *** THE GAME OUTRANKS THIS PROCESS. ***")
        log(" *** Every result below will be a false negative: UIPI blocks the")
        log(" *** keys we send AND hides keys pressed while the game is focused.")
        log(" *** Re-run this elevated before drawing any conclusion.")
else:
    log(" game               : not running (start PoE first)")
log("")
log(" HOW TO USE:")
log("   1. Alt-tab into Path of Exile and just stand there.")
log("   2. Press a few hotkeys (`, Ctrl+D, F9). YOU WILL HEAR A BEEP for")
log("      every key this tool receives while the game is focused.")
log("         beeps  -> the hook works; the problem is on the SEND side")
log("         silence-> the hook is blind while the game is focused")
log("   3. Wait ~5s - this tool will SEND a '2' keypress at the game;")
log("      watch whether flask slot 2 actually fires.")
log("   4. Alt-tab back here and press Ctrl+Alt+Q, then read diagnose.log")
log("=" * 70)
log("")

by_class: Counter[str] = Counter()
poe_events = 0
seen = 0
_last_class = None
_stop = threading.Event()


def current() -> tuple[str, str]:
    hwnd = win32gui.GetForegroundWindow()
    cls = foreground.foreground_class_name()
    try:
        title = win32gui.GetWindowText(hwnd)
    except Exception:
        title = "?"
    return cls, title


def on_key(event) -> None:
    global seen, poe_events, _last_class
    if event.event_type != "down":
        return
    seen += 1
    cls, title = current()
    by_class[cls] += 1
    ok = (not scoped) or cls == expected_class
    if ok:
        poe_events += 1
    verdict = "PASS -> handler would run" if ok else "BLOCKED by scope check"
    log(
        f"[{seen:>3}] key={event.name!r:<12} scan={event.scan_code:<4} "
        f"focus={cls!r:<22} title={title[:24]!r:<26} {verdict}"
    )
    if ok:
        # You cannot read a console from inside a fullscreen game, and that is
        # exactly where the answer is needed: a beep says "the hook received
        # this key" without needing to alt-tab, so silence in game is real
        # evidence of a blocked hook rather than just an untested moment.
        threading.Thread(
            target=lambda: winsound.Beep(1200, 60), daemon=True
        ).start()


def focus_watcher() -> None:
    """Focus can change without any key being pressed -- notably if an overlay
    steals it the moment the game is clicked, which would silently block every
    hotkey while looking, to the player, like the game is focused."""
    global _last_class
    sent_test = False
    poe_since = None
    while not _stop.is_set():
        cls, title = current()
        if cls != _last_class:
            _last_class = cls
            match = "  <-- MATCHES config" if cls == expected_class else ""
            log(f"      [focus] {cls!r} / {title[:40]!r}{match}")
            poe_since = time.time() if cls == expected_class else None
            if cls != expected_class:
                sent_test = False  # re-arm, so leaving and re-entering retries
        elif cls == expected_class and poe_since and not sent_test:
            if time.time() - poe_since > 5:
                sent_test = True
                log("      [send-test] PoE focused 5s - sending '2' via pydirectinput NOW")
                winsound.Beep(600, 150)  # low double-beep = "watch your flask now"
                winsound.Beep(600, 150)
                try:
                    input_io.press("2")
                    log("      [send-test] sent. Did flask slot 2 fire in game?")
                except Exception as exc:
                    log(f"      [send-test] FAILED: {type(exc).__name__}: {exc}")
        time.sleep(0.25)


keyboard.hook(on_key)
watcher = threading.Thread(target=focus_watcher, daemon=True)
watcher.start()

log("Hook installed. Go into the game now...\n")
keyboard.wait("ctrl+alt+q")
_stop.set()

log("")
log("=" * 70)
log(" SUMMARY")
log("=" * 70)
log(f" total key events seen      : {seen}")
log(f" events while PoE focused   : {poe_events}")
log(" events per focused window  :")
for cls, n in by_class.most_common():
    mark = "  <-- PoE" if cls == expected_class else ""
    log(f"     {n:>4}  {cls}{mark}")
log("")
if seen == 0:
    log(" VERDICT: hook saw ZERO keys -> the hook itself is blocked.")
elif poe_events == 0:
    log(" VERDICT: hook works, but NOT ONE key was seen while the PoE window")
    log("          was focused. Either you never tested inside the game, or")
    log("          something else (an overlay) holds focus while you play.")
    log("          Look at the 'events per focused window' list above.")
else:
    log(f" VERDICT: {poe_events} keys passed the scope check - the receive side")
    log("          is fine. If nothing happened in game, the problem is the")
    log("          SEND side; check the [send-test] line above.")
log("=" * 70)
log(f" saved to: {LOG}")
_log_file.close()
input("\nPress Enter to close...")

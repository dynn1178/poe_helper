"""Entry point: elevation check, config load, wire every module together,
then run the Tk main loop.

Kept intentionally thin -- all behaviour lives in ``poehelper/``; this file
only does dependency wiring so it's easy to see the whole app's shape in
one place.
"""
from __future__ import annotations

import logging
import os
import sys
import threading

from poehelper import elevation, paths, single_instance, version, whispers, zone
from poehelper.config import Config
from poehelper.flask_timer import FlaskTimer
from poehelper.gui.app import App
from poehelper.gui.tray import TrayIcon
from poehelper.hotkeys.actions import GameActions
from poehelper.hotkeys.manager import HotkeyManager
from poehelper.hotkeys.toggles import HoldClicker, ImageKeyDisplay, MinefieldHold

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# How long an orderly shutdown gets before the process is killed outright.
# Generous: everything here is stopped explicitly and every thread this app
# starts is a daemon, so reaching this at all means something went wrong.
_EXIT_GRACE_SEC = 3.0


def _setup_logging() -> None:
    """Console plus a fresh poehelper.log next to the app.

    A hotkey that does nothing looks identical whether the app never started,
    started unelevated, or fired and got filtered out by the PoE-window scope
    check -- and none of that survives a windowed build with no console. The
    log file is what tells those apart after the fact.
    """
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # a windowed PyInstaller build has no stderr
        handlers.append(logging.StreamHandler())
    try:
        handlers.append(
            logging.FileHandler(paths.app_dir() / "poehelper.log", mode="w", encoding="utf-8")
        )
    except OSError:
        pass  # read-only install dir -- console logging (if any) still works
    logging.basicConfig(level=logging.DEBUG, format=_LOG_FORMAT, handlers=handlers)
    logging.getLogger("PIL").setLevel(logging.INFO)  # decoder spam at DEBUG


def main() -> None:
    _setup_logging()
    logging.info(
        "starting: version=%s frozen=%s admin=%s exe=%s",
        version.__version__,
        getattr(sys, "frozen", False),
        elevation.is_admin(),
        sys.executable,
    )

    # Elevation is not optional: unelevated, Windows' UIPI drops every
    # synthetic keystroke this app sends to the (elevated) game, so the
    # hotkeys appear to fire and nothing happens. ensure_admin() re-launches
    # elevated when it can; EXIT means that copy is starting and this one is
    # done. Anything else continues -- degraded and having said so -- rather
    # than leaving the user with a program that refuses to open at all.
    if elevation.ensure_admin() == elevation.EXIT:
        return

    # After the elevation hand-off, never before: the unelevated copy exits
    # immediately once it has launched the elevated one, and if it had taken
    # the mutex first the elevated copy could arrive while it still held it
    # and turn itself away.
    if not single_instance.acquire(
        handover=single_instance.RESTART_FLAG in sys.argv
    ):
        logging.info("exiting: another instance owns the single-instance lock")
        return

    config = Config()
    elevation.check_game_outranks_us(config.data["misc"].get("poe_window_class", ""))

    # Started before anything that consults it, and registered module-wide so
    # hook callbacks and game actions can ask "are we in town?" without having
    # the tracker threaded through their constructors.
    zone_tracker = zone.ZoneTracker(config)
    zone.set_tracker(zone_tracker)
    zone_tracker.start()

    # Rides on the zone tracker's tail of the same log rather than opening
    # it again, and only subscribes while the feature is on.
    whisper_monitor = whispers.WhisperMonitor(config)
    whispers.set_monitor(whisper_monitor)
    whisper_monitor.start()

    hotkey_manager = HotkeyManager(config)
    app = App(config, hotkey_manager, actions=None)  # actions wired below

    actions = GameActions(app, config)
    app.actions = actions

    hotkey_manager.set_handler("flask_macro", actions.flask_macro)
    hotkey_manager.set_handler("identify_scroll", actions.identify_scroll)
    hotkey_manager.set_handler("guild_chat", actions.guild_chat)
    hotkey_manager.set_handler("toggle_continuous_key", actions.toggle_continuous_key)
    hotkey_manager.set_handler("tujen_scroll", actions.tujen_scroll)
    hotkey_manager.set_handler("tujen_confirm", actions.tujen_confirm)
    hotkey_manager.set_handler("currency_cycle", actions.currency_cycle)
    hotkey_manager.set_handler("cwdt_macro", actions.cwdt_macro)
    hotkey_manager.set_handler("autoclick_toggle", actions.autoclick_toggle)
    hotkey_manager.set_handler("search_paste", actions.search_paste)
    hotkey_manager.set_handler("quad_stash_pull_filtered", actions.quad_stash_pull_filtered)
    hotkey_manager.set_handler("inventory_pull_filtered", actions.inventory_pull_filtered)
    hotkey_manager.set_handler("stash_dump_all", actions.stash_dump_all)
    hotkey_manager.set_handler("price_check", actions.price_check)
    # restart_app is deliberately not bound to a key any more -- it is a
    # button in the bottom bar (see gui/app.py _build_bottom_bar).

    def send_chat_macro(text: str, chat: bool = True) -> None:
        """Fire a saved phrase, the way that phrase says it wants to be fired.

        ``chat`` is the row's 채팅 tick: through the game's chat box (open,
        paste, Enter) when set, and a plain paste into whatever field the
        caret is in when not.
        """
        from poehelper import input_io

        if chat:
            input_io.send_chat_message(text)
        else:
            input_io.paste_text(text)

    hotkey_manager.set_handler("__send_chat_macro__", send_chat_macro)
    # Same shape as the chat macros: one config row per binding, so the
    # handler takes the payload rather than there being a fixed action id.
    hotkey_manager.set_handler("__paste_map_regex__", actions.paste_map_regex)

    # Hold/release gestures that bypass the simple edge-triggered hotkey model.
    minefield = MinefieldHold(config)
    hold_clicker = HoldClicker(app, config)
    image_display = ImageKeyDisplay(app, config)

    flask_timer = FlaskTimer(app, config)
    flask_timer.start()

    config.on_change(hotkey_manager.apply)
    config.on_change(image_display.rebuild)
    config.on_change(minefield.rebuild)
    config.on_change(hold_clicker.rebuild)
    config.on_change(whisper_monitor.refresh)
    hotkey_manager.apply()

    def do_exit() -> None:
        """Bring the whole program down. Reached from the window's X, its
        종료 button, and the tray menu."""
        logging.info("shutting down")
        app.close_child_windows()
        zone_tracker.stop()
        whisper_monitor.stop()
        flask_timer.stop()
        hotkey_manager.unregister_all()
        try:
            tray.stop()
        except Exception:
            pass

        # A last-resort watchdog, flagged daemon so it costs nothing when
        # shutdown goes to plan -- the interpreter exits and the timer goes
        # with it, never having fired. It exists for when it does not:
        # a low-level keyboard hook whose thread will not come down, or a
        # tray icon whose message loop is wedged, leaves a process with no
        # window that nothing can see and only Task Manager can end. That is
        # not just untidy -- it keeps the exe file locked, and the updater's
        # swap script gives up waiting for a release that never comes.
        watchdog = threading.Timer(_EXIT_GRACE_SEC, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()

        app.destroy()

    tray = TrayIcon(
        on_show=lambda: app.after(0, app.show_from_tray),
        on_exit=lambda: app.after(0, do_exit),
    )
    tray.run_detached()  # runs for the whole app lifetime; window hide/show
    app.on_request_exit = do_exit  # doesn't touch this icon at all

    # Deliberately late: the check is a network round trip, and startup is
    # already the slowest part of this app. 4s in, the window is up and
    # usable, and a dialog appearing then reads as "it found something"
    # rather than as part of loading.
    # Late enough that the main window is up: the panel is a topmost child
    # of it, and one created mid-startup lands under the window still being
    # revealed.
    app.after(1500, app.open_whisper_panel_on_start)

    app.after(4000, app.check_for_updates_on_start)

    # The price check's game data: several megabytes of modifier and base-type
    # tables that have to be in memory before the first item can be read.
    # Loaded here rather than on the hotkey, where the quarter-second it costs
    # would land on the keyboard hook's thread and delay the very press that
    # asked for it.
    app.after(6000, app.preload_game_data)

    app.mainloop()


if __name__ == "__main__":
    main()

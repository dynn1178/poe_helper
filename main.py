"""Entry point: elevation check, config load, wire every module together,
then run the Tk main loop.

Kept intentionally thin -- all behaviour lives in ``poehelper/``; this file
only does dependency wiring so it's easy to see the whole app's shape in
one place.
"""
from __future__ import annotations

import logging
import sys

from poehelper import elevation, paths, version, zone
from poehelper.config import Config
from poehelper.flask_timer import FlaskTimer
from poehelper.gui.app import App
from poehelper.gui.tray import TrayIcon
from poehelper.hotkeys.actions import GameActions
from poehelper.hotkeys.manager import HotkeyManager
from poehelper.hotkeys.toggles import HoldClicker, ImageKeyDisplay, MinefieldHold

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


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

    if not elevation.ensure_admin():
        # ensure_admin() already triggered a relaunch-as-admin when running
        # from source; the packaged EXE's manifest handles this instead.
        if not getattr(sys, "frozen", False):
            return

    config = Config()
    elevation.check_game_outranks_us(config.data["misc"].get("poe_window_class", ""))

    # Started before anything that consults it, and registered module-wide so
    # hook callbacks and game actions can ask "are we in town?" without having
    # the tracker threaded through their constructors.
    zone_tracker = zone.ZoneTracker(config)
    zone.set_tracker(zone_tracker)
    zone_tracker.start()

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
    # restart_app is deliberately not bound to a key any more -- it is a
    # button in the bottom bar (see gui/app.py _build_bottom_bar).

    def send_chat_macro(text: str) -> None:
        from poehelper import input_io

        input_io.send_chat_message(text)

    hotkey_manager.set_handler("__send_chat_macro__", send_chat_macro)

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
    hotkey_manager.apply()

    def do_exit() -> None:
        zone_tracker.stop()
        flask_timer.stop()
        hotkey_manager.unregister_all()
        try:
            tray.stop()
        except Exception:
            pass
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
    app.after(4000, app.check_for_updates_on_start)

    app.mainloop()


if __name__ == "__main__":
    main()

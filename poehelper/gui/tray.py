"""System tray icon (requirement #12): closing the main window minimizes to
tray instead of exiting, so hotkeys keep running while the player tabs back
into the game."""
from __future__ import annotations

import threading
from typing import Callable

import pystray
from PIL import Image

from .. import paths


class TrayIcon:
    def __init__(self, on_show: Callable[[], None], on_exit: Callable[[], None]):
        image = Image.open(paths.resource_path("icon.png"))
        menu = pystray.Menu(
            pystray.MenuItem("열기", lambda icon, item: on_show(), default=True),
            pystray.MenuItem("종료", lambda icon, item: on_exit()),
        )
        self.icon = pystray.Icon("PoeHelper", image, "PoE 도우미", menu)

    def run_detached(self) -> None:
        """Run the icon's message loop on a thread of our own.

        Not ``icon.run_detached()``: pystray starts that thread without
        ``daemon=True`` (``pystray/_win32.py`` -> ``_run_detached``), so the
        interpreter waits for it at exit. A tray icon that does not stop
        cleanly then leaves a process with no window that never ends -- which
        is invisible until something is waiting on the process to exit. The
        auto-updater is: its swap script polls for the old executable to be
        released, and sat there retrying for thirty seconds against a program
        that was never going to let go.

        ``_run()`` is what pystray's own detached thread targets, so this is
        the same work with the thread flagged so it cannot hold up shutdown.
        """
        threading.Thread(
            target=self.icon._run, name="tray-icon", daemon=True
        ).start()

    def stop(self) -> None:
        self.icon.stop()

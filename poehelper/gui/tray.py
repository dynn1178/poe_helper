"""System tray icon (requirement #12): closing the main window minimizes to
tray instead of exiting, so hotkeys keep running while the player tabs back
into the game."""
from __future__ import annotations

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
        self.icon.run_detached()

    def stop(self) -> None:
        self.icon.stop()

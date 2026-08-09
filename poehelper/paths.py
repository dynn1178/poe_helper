"""Resource/data/config path resolution.

When frozen by PyInstaller (onefile), bundled resources live under
``sys._MEIPASS`` while user-writable files (config.json, edited CSVs) must
live next to the executable so they persist across app updates and reruns.
"""
from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Directory the executable/script lives in (portable, writable)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """Directory bundled read-only assets are extracted to at runtime."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Path to a bundled template image (resources/)."""
    return bundle_dir().joinpath("resources", *parts)


def layout_dir() -> Path:
    """Directory of act-layout maps (act_layout/).

    Read from the app directory first so a user can drop in or correct a
    layout image next to the exe without rebuilding, falling back to the
    bundled copy.
    """
    local = app_dir() / "act_layout"
    if local.is_dir():
        return local
    return bundle_dir() / "act_layout"


def data_path(*parts: str) -> Path:
    """Path to a user-editable data file (data/), copied next to the exe
    on first run so edits persist across reinstalls/updates."""
    target = app_dir().joinpath("data", *parts)
    if not target.exists():
        bundled = bundle_dir().joinpath("data", *parts)
        if bundled.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundled.read_bytes())
    return target


def config_path() -> Path:
    return app_dir() / "config.json"

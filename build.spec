# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onefile build.

``uac_admin=True`` embeds an application manifest requesting elevation, so
Windows shows the UAC prompt itself before the process starts -- this
replaces the AHK original's manual ``RunAs`` relaunch, and is why
``elevation.ensure_admin()`` in main.py treats a frozen build as already
elevated rather than trying to relaunch itself.
"""
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

from pathlib import Path

datas = [
    ("resources", "resources"),
    ("act_layout", "act_layout"),  # per-zone map layouts for the overlay
]

# data/ listed file by file rather than as a folder, so the backups the app
# and its maintenance scripts leave behind (list.csv.bak-*) are not shipped
# to users. Naming the folder bundles whatever happens to be sitting in it,
# and one such backup was already inside a build before this was noticed.
datas += [
    (str(path), "data")
    for path in sorted(Path("data").iterdir())
    if path.is_file() and ".bak" not in path.name
]
datas += collect_data_files("customtkinter")  # bundles its theme JSON files

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "win32timezone",
        "win32com",
        "pythoncom",
        "pywintypes",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KuanPoeHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    icon="resources/icon.ico",
)

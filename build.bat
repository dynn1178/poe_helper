@echo off
REM Convenience build script - run from this folder.
REM Requires: py -3.11 -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt

pyinstaller --noconfirm build.spec
echo.
echo Build finished. EXE is at dist\KuanPoeHelper.exe

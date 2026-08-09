@echo off
setlocal

rem Always start Kuan PoE Helper elevated.
rem Windows' UIPI blocks synthetic key input from a normal-integrity process to
rem an elevated window, so without this the hotkeys fire but the game never
rem sees the keys they send.
rem
rem Deliberately ASCII-only with no `chcp`: switching codepage mid-batch makes
rem cmd lose its read position in this file and garbles every line after it.

cd /d "%~dp0"

rem fltmc only succeeds when elevated - cheap, reliable admin probe.
fltmc >nul 2>&1
if not errorlevel 1 goto :run

echo Restarting with administrator privileges...
rem Single quotes inside the outer pair: cmd passes them through untouched and
rem PowerShell takes them literally, so no backslash-escaping dance.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
exit /b

:run
rem Prefer the project venv: the machine-default python is a different version
rem without this project's dependencies installed.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0main.py"
if errorlevel 1 pause

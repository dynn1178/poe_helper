@echo off
setlocal

rem Publish the next version. Double-click it, or run it from a terminal:
rem
rem     release.bat              ask for the version and the notes, then publish
rem     release.bat --dry-run    everything except the push and the upload
rem     release.bat 1.3.0        skip the version question
rem
rem Deliberately ASCII-only with no `chcp`, for the same reason run_admin.bat
rem is: switching codepage mid-batch makes cmd lose its read position in this
rem file and garbles every line after it. Every Korean prompt lives in
rem release.py instead, where Python handles the console encoding properly --
rem which is also why the version and the release notes are asked for there
rem rather than with `set /p` here.

cd /d "%~dp0"

rem The project venv, not the machine default: release.py builds with
rem PyInstaller and only this interpreter has it installed.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo.
    echo   [!] .venv not found at:
    echo       %PY%
    echo.
    echo       Create it first:
    echo         py -3.11 -m venv .venv
    echo         .venv\Scripts\activate
    echo         pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

"%PY%" "%~dp0release.py" --ask %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo   Done.
) else (
    echo   Stopped. exit code %RC%
)

rem Always pause: double-clicked, the window would otherwise close on the
rem error message explaining why it stopped.
pause
exit /b %RC%

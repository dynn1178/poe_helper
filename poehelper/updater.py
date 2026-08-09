"""Check GitHub for a newer release, download it, and swap it in.

Three jobs, deliberately separated so the GUI can stop between any two of
them (check -> ask -> download -> install):

``check()``    one GitHub API request, safe to call from a worker thread.
``download()`` streams the release asset to a temp file with progress.
``install()``  hands the swap to a detached batch file and returns.

Why a batch file
----------------
Windows will not let a running executable be replaced, and this app *is* the
executable being replaced. So the last thing the process does is launch a
small script that outlives it: the script waits for the exe to become
deletable (i.e. for us to actually exit), moves the download over it, and
starts it again. That wait is what makes "update then restart" a single
click instead of a set of instructions.

Nothing here uses ``requests`` -- urllib does the job and the project ships
as a PyInstaller bundle where every added dependency is weight in the exe.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths, version

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds; a slow network must not hold up startup
_CHUNK = 128 * 1024

# GitHub rejects API requests with no User-Agent, and the explicit API
# version keeps the response shape stable if they change the default.
_HEADERS = {
    "User-Agent": f"KuanPoeHelper/{version.__version__}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Windows process-creation flags: the swap script must survive this process
# exiting, and must not flash a console window.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


class UpdateError(Exception):
    """Anything that stopped an update, with a message fit to show a user."""


@dataclass(frozen=True)
class Release:
    version: str        # tag with any leading "v" stripped
    tag: str
    notes: str
    asset_url: str      # "" when the release has no matching .exe attached
    asset_size: int
    page_url: str

    @property
    def label(self) -> str:
        return version.display(self.version)


def is_frozen() -> bool:
    """Running as the packaged exe, i.e. there is something to replace."""
    return bool(getattr(sys, "frozen", False))


# ---------------------------------------------------------------------------
# 1. check
# ---------------------------------------------------------------------------
def check(timeout: int = _TIMEOUT) -> Release | None:
    """The newest published release, or None if it is not newer than us.

    Raises UpdateError on anything the user can act on (offline, rate
    limited, repository not published yet).
    """
    request = urllib.request.Request(version.LATEST_API_URL, headers=_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("아직 등록된 릴리즈가 없습니다.") from exc
        if exc.code == 403:
            raise UpdateError("GitHub 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요.") from exc
        raise UpdateError(f"업데이트 확인 실패 (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("인터넷에 연결할 수 없습니다.") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError("업데이트 정보를 읽을 수 없습니다.") from exc

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("릴리즈 정보에 버전 태그가 없습니다.")

    asset_url, asset_size = "", 0
    for asset in payload.get("assets") or []:
        if asset.get("name") == version.ASSET_NAME:
            asset_url = asset.get("browser_download_url") or ""
            asset_size = int(asset.get("size") or 0)
            break

    release = Release(
        version=tag.lstrip("vV"),
        tag=tag,
        notes=(payload.get("body") or "").strip(),
        asset_url=asset_url,
        asset_size=asset_size,
        page_url=payload.get("html_url") or version.LATEST_RELEASE_URL,
    )
    logger.info(
        "latest release %s (current %s, asset=%s)",
        release.tag, version.__version__, bool(release.asset_url),
    )
    return release if version.is_newer(release.version) else None


# ---------------------------------------------------------------------------
# 2. download
# ---------------------------------------------------------------------------
def download(
    release: Release,
    on_progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Fetch the release exe to a temp file and return its path.

    ``on_progress(done_bytes, total_bytes)`` is called as it streams;
    ``total`` is 0 when the server does not say. ``cancelled()`` is polled
    between chunks so the dialog's cancel button takes effect immediately
    rather than after the whole file.
    """
    if not release.asset_url:
        raise UpdateError(
            f"이 릴리즈에는 {version.ASSET_NAME} 파일이 첨부되어 있지 않습니다.\n"
            "릴리즈 페이지에서 직접 내려받아 주세요."
        )

    target = Path(tempfile.gettempdir()) / f"KuanPoeHelper-{release.version}.exe"
    request = urllib.request.Request(release.asset_url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            total = int(response.headers.get("Content-Length") or release.asset_size or 0)
            done = 0
            with open(target, "wb") as handle:
                while True:
                    if cancelled is not None and cancelled():
                        raise UpdateError("사용자가 취소했습니다.")
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        target.unlink(missing_ok=True)
        raise UpdateError(f"내려받기에 실패했습니다.\n{exc}") from exc

    # A truncated download that still gets installed bricks the app, and the
    # only cheap check available is the size the API already told us.
    if release.asset_size and target.stat().st_size != release.asset_size:
        target.unlink(missing_ok=True)
        raise UpdateError("내려받은 파일이 손상되었습니다. 다시 시도해주세요.")

    logger.info("downloaded update to %s (%d bytes)", target, target.stat().st_size)
    return target


# ---------------------------------------------------------------------------
# 3. install
# ---------------------------------------------------------------------------
# Waits for the running exe to become deletable, swaps in the download, and
# starts it again. `del` in a loop rather than a fixed sleep: how long the
# process takes to release its own image file is not something to guess at,
# and the loop simply ends when the file is gone.
_SWAP_SCRIPT = """@echo off
setlocal
set "TARGET={target}"
set "SOURCE={source}"
set /a TRIES=0

:wait
ping -n 2 127.0.0.1 >nul
del "%TARGET%" >nul 2>&1
set /a TRIES+=1
if exist "%TARGET%" (
    if %TRIES% LSS 30 goto wait
    goto failed
)

move /y "%SOURCE%" "%TARGET%" >nul
if errorlevel 1 goto failed
start "" "%TARGET%"
goto done

:failed
rem Could not replace the running program -- leave the download where the
rem user can run it by hand rather than deleting the only copy of it.
start "" "%SOURCE%"

:done
(goto) 2>nul & del "%~f0"
"""


def install(downloaded: Path) -> None:
    """Launch the swap script and return; the caller then exits the app.

    The script cannot do anything until this process is gone, so the caller
    must shut down promptly after calling this -- see
    ``App.finish_update_and_restart``.
    """
    if not is_frozen():
        raise UpdateError(
            "소스에서 실행 중일 때는 자동 설치를 할 수 없습니다.\n"
            "릴리즈 페이지에서 새 버전을 받아주세요."
        )
    target = Path(sys.executable).resolve()
    script = Path(tempfile.gettempdir()) / "kuanpoehelper-update.bat"
    try:
        script.write_text(
            _SWAP_SCRIPT.format(target=target, source=downloaded.resolve()),
            encoding="cp949",  # cmd.exe reads batch files in the OEM/ANSI codepage
        )
    except (OSError, UnicodeEncodeError) as exc:
        raise UpdateError(f"업데이트 스크립트를 만들 수 없습니다.\n{exc}") from exc

    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(script)],
            cwd=tempfile.gettempdir(),
            close_fds=True,
            creationflags=(
                _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
            ),
        )
    except OSError as exc:
        raise UpdateError(f"업데이트를 시작할 수 없습니다.\n{exc}") from exc
    logger.info("update staged: %s -> %s", downloaded, target)


def backup_config_before_update() -> None:
    """Keep a copy of config.json next to it before the exe is replaced.

    The new build merges unknown keys forward on load, so an update should
    never lose settings -- but "should never" is not a reason to have no
    copy of the file the user spent an evening filling in.
    """
    source = paths.config_path()
    if not source.exists():
        return
    try:
        backup = source.with_name(f"{source.stem}.before-update-{version.__version__}.json")
        backup.write_bytes(source.read_bytes())
        logger.info("config backed up to %s", backup)
    except OSError:
        logger.warning("could not back up config before update", exc_info=True)


def open_release_page() -> None:
    import webbrowser

    webbrowser.open(version.LATEST_RELEASE_URL)


def human_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "?"
    mb = num_bytes / (1024 * 1024)
    return f"{mb:.1f} MB" if mb >= 1 else f"{num_bytes / 1024:.0f} KB"

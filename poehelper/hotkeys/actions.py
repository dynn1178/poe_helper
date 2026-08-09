"""Implementations behind every game-facing hotkey.

Each ``GameActions`` method is wired to a config hotkey id by ``main.py``
via ``HotkeyManager.set_handler``. Methods run on the ``keyboard`` library's
listener thread; anything that touches Tk widgets (calibration overlays) is
marshalled onto the main loop through ``calibration.py`` /
``region_picker.py`` themselves, so callers here don't need to worry about
threading beyond simple state.
"""
from __future__ import annotations

import logging
import random
import subprocess
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from typing import Callable

from .. import calibration, image_search, input_io, toast, zone
from ..config import GRID_SIZES, Config
from .toggles import AutoClickToggle, ContinuousKeyToggle

logger = logging.getLogger(__name__)

# Detached so the restarted copy outlives this process exiting.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


class _ClickPacer:
    """Irregular pacing for a run of automated clicks.

    Two levels of randomness, deliberately. A random gap between every click
    still produces a perfectly steady *rate*, and a steady rate held over
    sixty clicks is its own signature -- so every few clicks the run also
    takes a longer break, which is what someone actually emptying a tab
    does. The gaps are read from config so the pace can be changed without
    a rebuild (``clicking`` in config.json).
    """

    def __init__(self, config: Config):
        cfg = config.data.get("clicking", {})
        self._delay = _ms_range(cfg.get("delay_ms"), (100, 240))
        self._pause = _ms_range(cfg.get("pause_ms"), (400, 850))
        self._every = _ms_range(cfg.get("pause_every"), (6, 12))
        self._since = 0
        self._next = random.randint(*self._every)

    def wait(self) -> None:
        input_io.jitter_sleep(*self._delay)
        self._since += 1
        if self._since >= self._next:
            input_io.jitter_sleep(*self._pause)
            self._since = 0
            self._next = random.randint(*self._every)


def _ms_range(value, fallback: tuple[int, int]) -> tuple[int, int]:
    """A ``[low, high]`` config pair, or *fallback* if it is unusable."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            low, high = int(value[0]), int(value[1])
            if 0 <= low <= high:
                return low, high
        except (TypeError, ValueError):
            pass
    return fallback


def _region_ok(region: dict | None) -> bool:
    return bool(region and "x1" in region)


def _point_ok(point: dict | None) -> bool:
    return bool(point and "x" in point)


class GameActions:
    def __init__(self, root: tk.Misc, config: Config):
        self.root = root
        self.config = config
        self._continuous = ContinuousKeyToggle(root, config)
        self._autoclick = AutoClickToggle(root, config)

    # ---- 물약 매크로 (원본 `~` 키) ----------------------------------
    def flask_macro(self) -> None:
        # Nothing to drink for in town, and ` is a key that gets pressed by
        # accident while typing -- so the macro stays out of the way there.
        if zone.macros_paused_here(self.config):
            logger.debug("flask macro skipped: safe zone")
            return
        flask = self.config.data["flask"]
        checks = flask.get("checkboxes", [False] * 6)
        for i in range(5):
            if i < len(checks) and checks[i]:
                input_io.jitter_sleep(100, 200)
                input_io.press(str(i + 1))
        if len(checks) > 5 and checks[5]:
            input_io.jitter_sleep(100, 200)
            input_io.type_text(flask.get("custom_key", ""))

    # ---- 감정주문서 우클릭 (Ctrl+D) ----------------------------------
    def identify_scroll(self) -> None:
        point = self.config.data["scrolls"].get("identify_point")
        if not _point_ok(point):
            toast.show("감정주문서 위치가 설정되지 않았습니다. GUI에서 먼저 설정해주세요.")
            calibration.calibrate_identify_scroll_point(self.root, self.config)
            return
        input_io.key_up("ctrl")
        time.sleep(0.03)
        origin = input_io.get_cursor_pos()
        time.sleep(0.15)
        input_io.click(point["x"], point["y"], "right")
        time.sleep(0.01)
        input_io.move_to(*origin)

    # ---- 길드채팅 입력창 (\\) -----------------------------------------
    def guild_chat(self) -> None:
        input_io.press("enter")
        input_io.key_down("shift")
        input_io.press("7")
        input_io.key_up("shift")

    # ---- 연속사용키 토글 (Ctrl+1) --------------------------------
    def toggle_continuous_key(self) -> None:
        self._continuous.toggle()

    # ---- 오토클릭 토글 (F12) ------------------------------------------
    def autoclick_toggle(self) -> None:
        self._autoclick.toggle()

    # ---- 투겐 흥정: 스크롤 4회 (Ctrl+2) ------------------------------
    def tujen_scroll(self) -> None:
        input_io.click_current()
        for _ in range(4):
            input_io.scroll_wheel(-1)

    # ---- 투겐 흥정: 구입 확인 (Ctrl+3) --------------------------------
    def tujen_confirm(self) -> None:
        point = self.config.data["tujen"].get("confirm_point")
        if not _point_ok(point):
            toast.show("투겐 확인 버튼 위치가 설정되지 않았습니다.")
            calibration.calibrate_tujen_confirm_point(self.root, self.config)
            return
        input_io.click_restore(point["x"], point["y"], "left")

    # ---- 화폐 반복 사용: 기회/정제 사이클 (Ctrl+4) --------------------
    def currency_cycle(self) -> None:
        cc = self.config.data["currency_cycle"]
        chance, scouring, item = (
            cc.get("chance_point"),
            cc.get("scouring_point"),
            cc.get("item_point"),
        )
        if not (_point_ok(chance) and _point_ok(scouring) and _point_ok(item)):
            toast.show("기회/정제의 오브 좌표가 설정되지 않았습니다.")
            calibration.calibrate_currency_cycle_points(self.root, self.config)
            return
        cycles = int(cc.get("cycles", 1))
        for _ in range(cycles):
            input_io.click(chance["x"], chance["y"], "right")
            time.sleep(0.01)
            input_io.click(item["x"], item["y"], "left")
            time.sleep(0.01)
            input_io.click(scouring["x"], scouring["y"], "right")
            time.sleep(0.01)
            input_io.click(item["x"], item["y"], "left")
            time.sleep(0.01)

    # ---- CWDT 스압 매크로 (Ctrl+5) ------------------------------------
    def cwdt_macro(self) -> None:
        time.sleep(0.5)
        input_io.press("t")
        time.sleep(0.5)
        input_io.press("1")
        time.sleep(0.5)
        input_io.press("x")
        time.sleep(0.1)
        input_io.press("x")

    # ---- 검색창에 "위치:" 붙여넣기 (Alt+F) ------------------------------
    def search_paste(self) -> None:
        import pyperclip

        pyperclip.copy("위치:")
        input_io.key_down("ctrl")
        input_io.press("v")
        input_io.key_up("ctrl")

    # ---- 보관함 -> 인벤: 검색으로 강조된 아이템만 -----------------------
    _MAX_PASSES = 12
    # Clicks one run is allowed to send. Pulling out of a stash stops well
    # short of the inventory's 60 cells: a run that long is a lot of
    # uninterrupted automated input, and stopping early costs only a second
    # keypress, whereas not stopping costs whatever a flood looks like from
    # the other side.
    _MAX_PULL_CLICKS = 30
    # Emptying the inventory is bounded by the inventory itself (12x5), so
    # this is the natural full sweep rather than an early stop.
    _MAX_DUMP_CLICKS = 60

    def _pull_filtered(self, region_key: str, label: str, calibrate: Callable) -> None:
        """Ctrl+click only the items the search box has framed.

        A pass scans once and clicks everything it found, then the next pass
        rescans. Scanning per click would be the more cautious shape, but
        stash items do not reflow when one leaves -- the others stay exactly
        where they were -- so coordinates from one scan stay valid for that
        whole pass, and a rescan between every click costs about six times
        the wall clock for no extra correctness.

        What the passes *are* for: an item can only be recognised once its
        surroundings allow it (a cell boxed in on all four sides is skipped
        as ambiguous, see image_search._drop_enclosed), and a click that the
        client dropped simply gets found again. So the loop keeps going until
        a pass finds nothing new.

        Two things stop it besides that: a pass that finds exactly what the
        last one found (nothing is moving -- the inventory is full, or the
        clicks are not landing) and the inventory's own capacity. Without
        either, a full inventory meant re-clicking the same items every pass,
        twelve times over, which is precisely the input flood that gets an
        account looked at.
        """
        region_cfg = self.config.data["regions"].get(region_key)
        if not _region_ok(region_cfg):
            toast.show(f"{label} 영역이 설정되지 않았습니다. 최초 1회 영역을 지정해주세요.")
            calibrate(self.root, self.config)
            return

        cols, rows = GRID_SIZES[region_key]
        x1, y1, x2, y2 = region_cfg["x1"], region_cfg["y1"], region_cfg["x2"], region_cfg["y2"]
        cell_w = (x2 - x1) / cols
        cell_h = (y2 - y1) / rows
        region = (x1, y1, x2, y2)
        detect = self.config.data.get("detection", {})

        moved = 0
        stopped = ""   # "" | "stalled" | "limit" -- decides what to report
        previous: set[tuple[int, int, int, int]] | None = None
        pacer = _ClickPacer(self.config)
        input_io.key_down("ctrl")
        try:
            for _ in range(self._MAX_PASSES):
                items = image_search.scan(
                    region, cols, rows, **image_search.options_from_config(detect)
                ).items
                if not items:
                    break

                signature = {(i.col, i.row, i.width, i.height) for i in items}
                if signature == previous:
                    stopped = "stalled"
                    logger.info(
                        "%s: %d items unchanged after a pass, stopping",
                        region_key, len(items),
                    )
                    break
                previous = signature

                for item in items:
                    if moved >= self._MAX_PULL_CLICKS:
                        stopped = "limit"
                        break
                    # The middle of the whole item, not of its top-left cell:
                    # a 1x3 amulet clicked at its top cell is fine, but the
                    # centre is the point least sensitive to the region being
                    # a few pixels out.
                    centre_col, centre_row = item.centre
                    cx = int(x1 + cell_w * centre_col)
                    cy = int(y1 + cell_h * centre_row)
                    input_io.click(cx, cy, "left")
                    moved += 1
                    pacer.wait()
                if stopped:
                    break
                # Let the client redraw before looking again, or the next scan
                # sees the frame of an item that has already gone.
                input_io.jitter_sleep(*_ms_range(
                    self.config.data.get("clicking", {}).get("pass_gap_ms"),
                    (260, 480)))
        finally:
            input_io.key_up("ctrl")
        logger.info("%s: moved %d items (stopped=%s)", region_key, moved, stopped or "-")
        if moved and stopped == "limit":
            toast.show(
                f"{label}: {moved}개를 옮기고 멈췄습니다.\n"
                f"한 번에 옮기는 최대 개수({self._MAX_PULL_CLICKS}개)에 도달했습니다.\n"
                "인벤토리를 정리한 뒤 단축키를 다시 눌러주세요.",
                duration_ms=4200,
            )
        elif moved and stopped:
            toast.show(
                f"{label}: {moved}개를 옮겼습니다.\n"
                "더 이상 옮겨지지 않아 중단했습니다 — 인벤토리가 가득 찼는지 확인해주세요.",
                duration_ms=4000,
            )
        elif moved:
            toast.show(f"{label}: 검색된 아이템 {moved}개를 인벤토리로 옮겼습니다.")
        else:
            # "0개를 옮겼습니다" reads as success and leaves the user with no
            # idea whether nothing matched or nothing was detected. The test
            # window is the thing that tells those apart.
            toast.show(
                f"{label}: 검색으로 강조된 아이템을 찾지 못했습니다.\n"
                "검색어가 맞는지 확인하고, 계속 안 되면 '좌표 캘리브레이션' 탭의\n"
                "'검색 인식 테스트'로 인식 상태를 확인해주세요.",
                duration_ms=4200,
            )

    def quad_stash_pull_filtered(self) -> None:
        self._pull_filtered("quad_stash", "쿼드 보관함", calibration.calibrate_quad_stash_region)

    def inventory_pull_filtered(self) -> None:
        self._pull_filtered("stash", "일반 보관함", calibration.calibrate_stash_region)

    # ---- 인벤토리 전체 아이템 창고로 (F10) ------------------------------
    def stash_dump_all(self) -> None:
        inv = self.config.data["regions"].get("inventory")
        if not _region_ok(inv):
            toast.show("인벤토리 영역이 설정되지 않았습니다. 최초 1회 영역을 지정해주세요.")
            calibration.calibrate_inventory_region(self.root, self.config)
            return
        cols, rows = GRID_SIZES["inventory"]
        x1, y1, x2, y2 = inv["x1"], inv["y1"], inv["x2"], inv["y2"]
        cell_w = (x2 - x1) / cols
        cell_h = (y2 - y1) / rows
        pacer = _ClickPacer(self.config)
        clicked = 0
        input_io.key_down("ctrl")
        try:
            for col in range(cols):
                cx = int(x1 + cell_w * (col + 0.5))
                for row in range(rows):
                    if clicked >= self._MAX_DUMP_CLICKS:
                        break
                    cy = int(y1 + cell_h * (row + 0.5))
                    input_io.click(cx, cy, "left")
                    clicked += 1
                    pacer.wait()
                if clicked >= self._MAX_DUMP_CLICKS:
                    break
        finally:
            input_io.key_up("ctrl")
        logger.info("stash_dump_all: %d cells clicked", clicked)
        toast.show(f"인벤토리 아이템을 창고로 보냈습니다. ({clicked}칸)")

    # ---- 앱 재시작 (F11, 문제 발생시 수동 복구용) -----------------------
    def restart_app(self) -> None:
        """Relaunch the program.

        A packaged build starts a fresh process and lets this one exit,
        rather than ``os.execv``-ing over itself. A onefile exe hands its
        unpack folder to anything it spawns -- and to anything that replaces
        its image -- through ``_PYI_*`` environment variables, so a restart
        that keeps them ends up looking for its DLLs in a folder belonging to
        the process it replaced. See ``updater.child_environment``.

        Running from source has no unpack folder and no such problem, and
        execv there keeps the restart to a single process.
        """
        import os
        import subprocess
        import sys

        from .. import updater

        self.config.save()

        if not getattr(sys, "frozen", False):
            os.execv(sys.executable, [sys.executable] + sys.argv)
            return

        from ..single_instance import RESTART_FLAG

        # The new copy starts while this one is still tearing down, so it has
        # to be told to wait for the single-instance lock instead of seeing a
        # duplicate and refusing to run. Filtered first so the flag does not
        # accumulate across repeated restarts.
        passthrough = [arg for arg in sys.argv[1:] if arg != RESTART_FLAG]
        subprocess.Popen(
            [sys.executable, *passthrough, RESTART_FLAG],
            cwd=str(Path(sys.executable).resolve().parent),
            env=updater.child_environment(),
            close_fds=True,
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        )
        # Hand back to the app so the tray icon, hooks and zone tracker are
        # torn down properly instead of the process just vanishing.
        exit_app = getattr(self.root, "on_request_exit", None)
        if callable(exit_app):
            self.root.after(0, exit_app)
        else:
            self.root.after(0, self.root.destroy)

    # ---- POE 실행 / 프로그램 경로 실행 ---------------------------------
    def launch_path(self, path: str) -> None:
        if not path:
            return
        try:
            if path.startswith("http://") or path.startswith("https://"):
                webbrowser.open(path)
            else:
                subprocess.Popen([path])
        except OSError:
            logger.exception("failed to launch %s", path)

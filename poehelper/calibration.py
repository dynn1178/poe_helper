"""One-shot screen-point/region calibration flows.

Each function is safe to call from any thread (it marshals the actual
overlay creation onto the Tk main loop via ``root.after``). They're used
both by explicit "재설정"(reset) buttons in the GUI and by game actions that
auto-trigger calibration the first time they're used without one yet
(requirement: inventory/stash regions must be picked once before F9/F10
work, since the original's hardcoded 2560x1440 pixel grid doesn't
generalize across resolutions).
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import region_picker, toast
from .config import GRID_SIZES, Config


def _on_ui(root: tk.Misc, fn: Callable[[], None]) -> None:
    root.after(0, fn)


def _calibrate_grid_region(
    root: tk.Misc,
    config: Config,
    region_key: str,
    label: str,
    on_done: Callable[[], None],
) -> None:
    """Shared flow for every cell-grid area. The grid size comes from
    ``config.GRID_SIZES`` so the dashed guide the user lines up is literally
    the same subdivision the click code will use afterwards."""
    cols, rows = GRID_SIZES[region_key]

    def go() -> None:
        def saved(rect: dict) -> None:
            config.data["regions"][region_key] = rect
            config.save()
            toast.show(f"{label} 영역 설정 완료. ({cols}x{rows}칸)")
            on_done()

        region_picker.pick_grid_rect(
            root,
            label,
            cols,
            rows,
            saved,
            initial=config.data["regions"].get(region_key),
        )

    _on_ui(root, go)


def calibrate_inventory_region(
    root: tk.Misc, config: Config, on_done: Callable[[], None] = lambda: None
) -> None:
    _calibrate_grid_region(root, config, "inventory", "인벤토리", on_done)


def calibrate_stash_region(
    root: tk.Misc, config: Config, on_done: Callable[[], None] = lambda: None
) -> None:
    _calibrate_grid_region(root, config, "stash", "일반 보관함", on_done)


def calibrate_quad_stash_region(
    root: tk.Misc, config: Config, on_done: Callable[[], None] = lambda: None
) -> None:
    _calibrate_grid_region(root, config, "quad_stash", "쿼드 보관함", on_done)


def calibrate_flask_overlay(
    root: tk.Misc, config: Config, on_done: Callable[[], None] = lambda: None
) -> None:
    """Bar-graph area for flask slots 1-5. Five columns of dashed guide, one
    per slot, so the bars can be lined up with the actual flask icons."""

    def go() -> None:
        def saved(rect: dict) -> None:
            config.data["flask"]["overlay_rect"] = rect
            config.save()
            toast.show("물약 쿨다운 막대 위치 설정 완료.")
            on_done()

        region_picker.pick_grid_rect(
            root, "물약 쿨다운 막대 (1~5번)", 5, 1, saved,
            initial=config.data["flask"].get("overlay_rect"),
        )

    _on_ui(root, go)


def calibrate_skill_overlay(
    root: tk.Misc, config: Config, on_done: Callable[[], None] = lambda: None
) -> None:
    """Same idea as the flask bars, for the Q/W/E/R/T skill row."""

    def go() -> None:
        def saved(rect: dict) -> None:
            config.data["skills"]["overlay_rect"] = rect
            config.save()
            toast.show("스킬 쿨다운 막대 위치 설정 완료.")
            on_done()

        region_picker.pick_grid_rect(
            root, "스킬 쿨다운 막대 (Q W E R T)", 5, 1, saved,
            initial=config.data["skills"].get("overlay_rect"),
        )

    _on_ui(root, go)


def calibrate_identify_scroll_point(
    root: tk.Misc, config: Config, on_done: Callable[[], None] = lambda: None
) -> None:
    def go() -> None:
        def saved(points: list[tuple[int, int]]) -> None:
            x, y = points[0]
            config.data["scrolls"]["identify_point"] = {"x": x, "y": y}
            config.save()
            toast.show("감정주문서 위치 설정 완료.")
            on_done()

        region_picker.pick_points(root, ["감정주문서를 놓을 인벤토리 칸을 클릭하세요"], saved)

    _on_ui(root, go)


def calibrate_tujen_confirm_point(
    root: tk.Misc, config: Config, on_done: Callable[[], None] = lambda: None
) -> None:
    def go() -> None:
        def saved(points: list[tuple[int, int]]) -> None:
            x, y = points[0]
            config.data["tujen"]["confirm_point"] = {"x": x, "y": y}
            config.save()
            toast.show("투겐 흥정 확인 버튼 위치 설정 완료.")
            on_done()

        region_picker.pick_points(root, ["투겐 흥정 창의 확인 버튼을 클릭하세요"], saved)

    _on_ui(root, go)


def calibrate_currency_cycle_points(
    root: tk.Misc, config: Config, on_done: Callable[[], None] = lambda: None
) -> None:
    def go() -> None:
        def saved(points: list[tuple[int, int]]) -> None:
            (cx, cy), (sx, sy), (ix, iy) = points
            cc = config.data["currency_cycle"]
            cc["chance_point"] = {"x": cx, "y": cy}
            cc["scouring_point"] = {"x": sx, "y": sy}
            cc["item_point"] = {"x": ix, "y": iy}
            config.save()
            toast.show("기회/정제의 오브 좌표 설정 완료.")
            on_done()

        region_picker.pick_points(
            root,
            [
                "기회의 오브 위치를 클릭하세요",
                "정제의 오브 위치를 클릭하세요",
                "찬스질 할 아이템 위치를 클릭하세요",
            ],
            saved,
        )

    _on_ui(root, go)

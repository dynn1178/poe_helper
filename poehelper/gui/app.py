"""Main application window: a tabbed layout replacing the original's
scattered fixed-coordinate GUI with clearly grouped, organized sections
(requirement #1)."""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import colorchooser, filedialog, messagebox
from typing import Callable

import customtkinter as ctk

from .. import calibration, map_layout, paths, toast, updater, version, zone
from ..config import (
    CONTINUOUS_KEY_CHOICES,
    CONTINUOUS_KEY_SLOTS,
    SKILL_KEYS,
    Config,
)
from ..hotkeys import suspend as hotkey_suspend
from ..hotkeys.actions import GameActions
from ..hotkeys.manager import HotkeyManager
from . import render, theme
from .detect_test import DetectTestWindow
from .layout_window import LayoutWindow
from .memo_tab import MemoTab
from .route_window import RouteWindow
from .update_dialog import UpdateDialog
from .widgets.hotkey_picker import HotkeyPicker
from .widgets.link_list_editor import LinkListEditor
from .widgets.macro_table import MacroTableEditor
from .widgets.tooltip import HelpMark, Tooltip, labelled_row

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("light")
ctk.set_default_color_theme(str(theme.theme_path()))
# CustomTkinter's ThemeManager opens theme JSON files without an explicit
# encoding, so under a Korean-locale Windows install it decodes as cp949
# and a Hangul font name baked into the JSON crashes on load. Setting the
# resolved font family here instead -- after the theme file (pure ASCII)
# has already loaded -- sidesteps that entirely.
ctk.ThemeManager.theme["CTkFont"]["family"] = theme.FONT_FAMILY
render.install()


# (action_id, title, hover tooltip -- tooltip text is a plain-language
# description of what the underlying GameActions method in
# hotkeys/actions.py actually does when this hotkey fires).
_GAME_HOTKEY_DESCRIPTIONS: list[tuple[str, str, str]] = [
    (
        "flask_macro",
        "물약 매크로 실행",
        "물약 탭에서 체크한 슬롯을 1→5 순서로 입력하고, #6이 체크되어 있으면 마지막으로 6번 키를 "
        "입력합니다. 어떤 슬롯을 쓸지는 '물약' 탭에서 정합니다.",
    ),
    (
        "identify_scroll",
        "감정주문서 우클릭",
        "설정된 인벤토리 칸의 감정의 주문서를 우클릭해 사용하고, 마우스 커서를 원래 위치로 되돌립니다. "
        "좌표가 설정되지 않았으면 자동으로 설정 화면이 열립니다.",
    ),
    (
        "guild_chat",
        "길드채팅 입력창 열기",
        "Enter로 채팅창을 연 뒤 Shift+7('&')을 입력해 길드채팅 입력 상태로 바로 전환합니다.",
    ),
    (
        "toggle_continuous_key",
        "연속사용키 토글 시작/종료",
        "아래 연속사용키 슬롯 5개에 지정한 키를 왼쪽부터 순서대로 한 번씩 누릅니다 "
        "(키 사이 0.1~0.3초 랜덤). 한 바퀴를 다 돌면 설정한 간격만큼 쉬었다가 다시 반복하며, "
        "그 간격도 지정값~지정값+0.3초 사이에서 매번 랜덤하게 정해집니다. "
        "같은 단축키를 다시 누르면 정지합니다.",
    ),
    (
        "tujen_scroll",
        "투겐 흥정: 스크롤 4회",
        "현재 마우스 위치를 클릭한 뒤 마우스 휠을 아래로 4번 굴려 투겐 흥정 수량을 조절합니다.",
    ),
    (
        "tujen_confirm",
        "투겐 흥정: 구입 확인",
        "설정된 투겐 흥정 확인 버튼 좌표를 좌클릭해 구매를 확정하고, 마우스 커서를 원래 위치로 되돌립니다.",
    ),
    (
        "currency_cycle",
        "화폐 반복 사용 (기회/정제 오브)",
        "설정된 반복 횟수만큼 '기회의 오브 우클릭 → 아이템 좌클릭 → 정제의 오브 우클릭 → 아이템 좌클릭' "
        "순서를 자동으로 반복합니다.",
    ),
    (
        "cwdt_macro",
        "CWDT 스압 매크로",
        "0.5초 간격으로 T → 1 → X → X 키를 순서대로 눌러 CWDT(적중 시 시전) 스크린샷 매크로를 실행합니다.",
    ),
    (
        "autoclick_toggle",
        "오토클릭 토글 (On/Off)",
        "켜져있는 동안 100ms 간격으로 현재 마우스 위치를 좌클릭합니다. 같은 단축키를 다시 누르면 정지합니다.",
    ),
    (
        "search_paste",
        "검색창에 '위치:' 붙여넣기",
        "클립보드에 '위치:' 문구를 복사한 뒤 Ctrl+V로 검색창에 붙여넣습니다.",
    ),
    (
        "quad_stash_pull_filtered",
        "쿼드 보관함 → 인벤토리 (검색 아이템)",
        "쿼드 보관함(24×24칸) 영역에서 검색으로 강조된 아이템을 찾아 Ctrl+클릭으로 인벤토리에 가져옵니다. "
        "찾은 위치는 저장된 24×24 격자의 칸 중앙으로 보정해서 클릭하므로 칸을 빗나가지 않습니다. "
        "더 이상 찾을 것이 없으면 자동으로 멈춥니다. 영역이 설정되지 않았으면 설정 화면이 열립니다.",
    ),
    (
        "inventory_pull_filtered",
        "일반 보관함 → 인벤토리 (검색 아이템)",
        "일반 보관함(12×12칸) 영역에서 검색으로 강조된 아이템을 찾아 Ctrl+클릭으로 인벤토리에 가져옵니다. "
        "찾은 위치는 저장된 12×12 격자의 칸 중앙으로 보정해서 클릭합니다. "
        "더 이상 찾을 것이 없으면 자동으로 멈춥니다. 영역이 설정되지 않았으면 설정 화면이 열립니다.",
    ),
    (
        "stash_dump_all",
        "인벤토리 → 창고 (전체 정리)",
        "설정된 인벤토리 영역을 12×5칸으로 나눠 각 칸을 Ctrl+클릭해 인벤토리의 아이템을 모두 창고로 보냅니다. "
        "영역이 설정되지 않았으면 설정 화면이 열립니다.",
    ),
    (
        "price_check",
        "아이템 가격 확인 (거래소 검색)",
        "마우스를 올려둔 아이템을 게임에서 복사(Ctrl+C)해 거래소에서 같은 조건의 매물을 찾아 "
        "가격을 보여줍니다. 고유 아이템은 이름으로, 레어 아이템은 베이스와 잘 뜬 속성 2개로 "
        "검색하며, 창에서 속성 체크를 바꾸면 즉시 다시 찾습니다. "
        "게임 UI 설정에서 '고급 속성 설명'을 켜두면 각 속성이 얼마나 잘 떴는지(%)까지 표시됩니다.",
    ),
    (
        "show_image_1",
        "참고 이미지 1 표시 (홀드)",
        "키를 누르고 있는 동안 화면 중앙에 참고 이미지 1(ctrl1.png)을 표시합니다.",
    ),
    (
        "show_image_2",
        "참고 이미지 2 표시 (홀드)",
        "키를 누르고 있는 동안 화면 중앙에 참고 이미지 2(ctrl2.png)을 표시합니다.",
    ),
    (
        "show_image_3",
        "참고 이미지 3 표시 (홀드)",
        "키를 누르고 있는 동안 화면 중앙에 참고 이미지 3(ctrl3.png)을 표시합니다.",
    ),
]

# The actions used constantly in play, pinned to the top of the hotkey list
# in this order. Everything else follows in declaration order.
_HOTKEY_PRIORITY = [
    "flask_macro",
    "identify_scroll",
    "guild_chat",
    "toggle_continuous_key",
    "autoclick_toggle",
    "quad_stash_pull_filtered",
    "inventory_pull_filtered",
    "stash_dump_all",
]

# action_id -> calibration.calibrate_* function to jump straight to for
# hotkeys whose behaviour depends on a screen coordinate/region the user
# must set up first.
_CALIBRATION_SHORTCUTS: dict[str, Callable] = {
    "identify_scroll": calibration.calibrate_identify_scroll_point,
    "tujen_confirm": calibration.calibrate_tujen_confirm_point,
    "currency_cycle": calibration.calibrate_currency_cycle_points,
    "quad_stash_pull_filtered": calibration.calibrate_quad_stash_region,
    "inventory_pull_filtered": calibration.calibrate_stash_region,
    "stash_dump_all": calibration.calibrate_inventory_region,
}

def _normalise_combo(combo: str) -> str:
    """"Ctrl+D" / "d+ctrl" -> "ctrl+d". Modifier order carries no meaning."""
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    mods = sorted(p for p in parts if p in {"ctrl", "alt", "shift", "win"})
    base = [p for p in parts if p not in {"ctrl", "alt", "shift", "win"}]
    return "+".join(mods + base)


def _readable_on(hex_colour: str) -> str:
    """Black or white label text, whichever stays legible on *hex_colour*.
    A swatch button is user-coloured, so a fixed text colour disappears on
    half the palette."""
    try:
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return theme.ON_PRIMARY
    # Rec. 601 luma; >150 reads as a light background.
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


_AUTOCLICK_HELP = (
    "지정한 키를 누르고 있는 동안 15ms 간격으로 좌클릭을 반복합니다.\n\n"
    "• 반드시 인벤토리/보관함 안에서만 사용하세요. 일반 필드에서 쓰면 입력 과다로 "
    "접속이 차단될 수 있습니다.\n"
    "• 오작동 방지를 위해 Ctrl 또는 Alt 조합만 지정할 수 있습니다. 단일 키로 두면 "
    "키보드로 글자를 칠 때마다 클릭이 발생합니다.\n"
    "• Shift를 함께 누르고 있으면 창고 ↔ 인벤토리 아이템 이동에도 그대로 사용할 수 있습니다."
)


class App(ctk.CTk):
    def __init__(self, config: Config, hotkey_manager: HotkeyManager, actions: GameActions):
        super().__init__()
        # Fully transparent (not withdrawn!) until every tab is fully built
        # and laid out -- see the attributes("-alpha", 1.0) call at the end
        # of __init__. withdraw() was tried here first, but an unmapped
        # window never gets real screen space negotiated for it, so the
        # CTkScrollableFrame canvases inside it size themselves to 0x0
        # while hidden; the chat-macro tab (the one tab that's already
        # "current" from the very start, so it never goes through the
        # tab-cycling re-grid pass below) never got a chance to recompute
        # afterwards and came up with an empty list on first launch.
        # Alpha 0 keeps the window mapped -- geometry is computed for real
        # -- while still being invisible until we're ready to reveal it.
        self.attributes("-alpha", 0.0)
        # Before any widget is built: plain Tk widgets read their family from
        # the named fonts, and CustomTkinter ones from ThemeManager. Both have
        # to say 맑은 고딕 or the two families appear side by side.
        theme.apply_tk_fonts(self)
        self.config = config
        self.hotkey_manager = hotkey_manager
        self.actions = actions
        self.on_request_exit: Callable[[], None] | None = None
        self.on_request_minimize: Callable[[], None] | None = None
        self._route_windows: dict[str, RouteWindow] = {}
        self._layout_windows: dict[str, LayoutWindow] = {}
        self._detect_windows: dict[str, DetectTestWindow] = {}
        self._price_window = None
        self._whisper_panel = None
        self._trade_api = None

        toast.bind_root(self)

        self.title(f"Kuan POE Helper {version.display()}")
        try:
            self.iconbitmap(str(paths.resource_path("icon.ico")))
        except tk.TclError:
            pass
        pos = config.data["windows"]["main"]
        # Wider than the original 620: the settings rows are now "? badge |
        # name | control", and at the old width the hotkey pickers and the
        # five continuous-key dropdowns ran off the right edge.
        self.geometry(f"720x680+{pos.get('x', 100)}+{pos.get('y', 100)}")

        self.tabs = ctk.CTkTabview(self, command=self._on_tab_selected)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        # Only the first tab is built here; the rest are filled in from the
        # event loop once the window is up (see the after() at the end of
        # __init__). Building all seven eagerly cost ~5s of startup --
        # CustomTkinter draws every widget onto its own canvas, and the two
        # hotkey-heavy tabs alone account for ~34 HotkeyPicker instances,
        # 3 checkboxes + a combo box each -- all of it spent before the
        # window could be shown at all. Now the window is up in ~2.5s and
        # the remaining tabs are ready long before they can be clicked.
        # Tab order is the order they are used in, left to right, ending with
        # the two that are set up once and then left alone: 좌표 설정 (done at
        # install time and after a resolution change) and 설정 (the app's own
        # options). Those two sit on the right for the same reason a
        # preferences menu does.
        self._tab_builders: dict[str, Callable[[ctk.CTkFrame], None]] = {
            "채팅 매크로": self._build_macro_tab,
            "물약": self._build_flask_tab,
            "게임 단축키": self._build_hotkeys_tab,
            "링크": self._build_links_tab,
            "메모": self._build_memo_tab,
            "레벨업": self._build_levelup_tab,
            "좌표 설정": self._build_calibration_tab,
            "설정": self._build_misc_tab,
        }
        for name in self._tab_builders:
            self.tabs.add(name)
        self._built_tabs: set[str] = set()

        self._build_bottom_bar()

        self._ensure_tab(next(iter(self._tab_builders)))

        self.protocol("WM_DELETE_WINDOW", self._on_minimize)
        self.bind("<Configure>", self._on_configure)

        # Reveal the window only once it's actually finished painting.
        # Widget construction above is synchronous Python, but the GDI
        # painting it queues up (~16 saved macro rows worth of widgets,
        # plus everything else) is flushed by the Windows message pump only
        # once the event loop actually runs -- confirmed by screenshotting
        # real runs of this window: revealing on a fixed short delay
        # (tried 10ms, then 150ms) still caught that backlog mid-flush and
        # showed a half-drawn window for hundreds of ms after becoming
        # visible. update() (unlike update_idletasks()) blocks and keeps
        # pumping the event loop until the queue is genuinely empty, so it
        # drains that entire backlog deterministically before the window
        # is shown, regardless of machine speed or how many rows/tabs exist.
        self.update()
        self.attributes("-alpha", 1.0)

        # The other six tabs get built one at a time from the event loop,
        # now that the window is up and interactive. Building them on click
        # instead would leave the tab blank for as long as the build takes
        # (the game-hotkey tab alone is >2s), so they are quietly filled in
        # ahead of time; _ensure_tab() still covers a tab the user reaches
        # before its turn comes up.
        self.after(120, self._build_next_pending_tab)

    # ---- lazy tab construction --------------------------------------------
    def _build_next_pending_tab(self) -> None:
        pending = [name for name in self._tab_builders if name not in self._built_tabs]
        if not pending:
            return
        self._ensure_tab(pending[0])
        # One tab per callback, so the app stays responsive to clicks and
        # hotkeys in between instead of freezing for the whole batch.
        self.after(60, self._build_next_pending_tab)

    def _on_tab_selected(self) -> None:
        self._reveal_tab(self.tabs.get())

    def _reveal_tab(self, name: str) -> None:
        """``CTkTabview.set()`` leaves the previously shown tab gridded in
        the same cell for another 100ms (it defers the ``grid_forget`` via
        ``after``), so for that window two tabs overlap and which one you
        see comes down to stacking order -- which pre-building tabs
        underneath the visible one (see ``_tab_sized_for_layout``)
        reshuffles. Dropping the others right away makes the switch a
        single clean repaint instead."""
        for other in self._tab_builders:
            if other != name:
                self.tabs.tab(other).grid_forget()
        self._ensure_tab(name)

    def _ensure_tab(self, name: str) -> None:
        """Build ``name``'s content the first time it is needed.

        The content is assembled inside a container that is *not* mapped
        yet, then packed in one go. Tk does no layout or painting for an
        unmapped subtree, so a tab's whole contents land in a single paint
        instead of the rows visibly filling in one by one as they're
        created.
        """
        if name in self._built_tabs:
            return
        self._built_tabs.add(name)

        tab = self.tabs.tab(name)
        with self._tab_sized_for_layout(tab):
            # Tab geometry has to be real *while* the content is built:
            # a CTkScrollableFrame sizes its inner canvas from the space it
            # is given, and one built inside an un-gridded (1x1) tab frame
            # comes out permanently mislaid -- rows scattered at the wrong
            # offsets, most of the list not drawn at all. Confirmed by
            # screenshotting the game-hotkey tab built this way.
            self.tabs.update_idletasks()
            container = ctk.CTkFrame(tab, fg_color="transparent")
            self._tab_builders[name](container)
            # Packed only now: Tk lays out and paints nothing for an
            # unmapped subtree, so the whole tab lands in one paint instead
            # of its rows visibly filling in one by one as they're created.
            container.pack(fill="both", expand=True)
            self.tabs.update_idletasks()

    @contextlib.contextmanager
    def _tab_sized_for_layout(self, tab: ctk.CTkFrame):
        """Temporarily give a not-currently-selected tab frame real
        dimensions, by gridding it into the same cell as the visible tab and
        stacking it underneath. The visible tab covers the same cell exactly,
        so nothing of the one being built is ever on screen."""
        current = self.tabs.tab(self.tabs.get())
        if tab is current:
            yield
            return
        info = current.grid_info()
        tab.grid(
            row=info["row"], column=info["column"], sticky=info["sticky"],
            padx=info["padx"], pady=info["pady"],
        )
        tab.lower(current)
        try:
            yield
            # Pay this tab's first paint here, while it's still covered.
            # Building the widgets isn't the whole cost -- CustomTkinter
            # renders each one onto its own canvas, and that only happens
            # the first time the tab is actually mapped. Without this the
            # cost just moved to the first click on the tab (~0.6s for the
            # game-hotkey list, visibly filling in), instead of being spent
            # invisibly up front. update_idletasks() rather than update():
            # it flushes the paint without processing user input, so a
            # click landing mid-prebuild can't re-enter this.
            self.update_idletasks()
        finally:
            tab.grid_forget()

    def _show_tab(self, name: str) -> None:
        """Switch to a tab from code. ``CTkTabview.set()`` bypasses the
        segmented button's callback, so the lazy build has to be triggered
        explicitly here."""
        self.tabs.set(name)
        self._reveal_tab(name)

    # ---- tab: 채팅 매크로 -----------------------------------------------
    def _build_macro_tab(self, tab: ctk.CTkFrame) -> None:
        def on_change(macros: list[dict]) -> None:
            self.config.data["chat_macros"] = macros
            self.config.save()

        def on_save() -> None:
            # Sinking the keyless rows is part of what 저장 means here, so it
            # happens before the write rather than as a separate button.
            self.macro_editor.sort_unbound_last()
            self.config.save()

        self._build_save_row(tab, on_save)
        self.macro_editor = MacroTableEditor(
            tab, self.config.data["chat_macros"], on_change=on_change
        )
        self.macro_editor.pack(fill="both", expand=True, padx=8, pady=8)

    # ---- tab: 물약 -----------------------------------------------------
    def _build_flask_tab(self, tab: ctk.CTkFrame) -> None:
        self._build_save_row(tab)
        flask = self.config.data["flask"]

        flask_title = ctk.CTkLabel(
            tab, text="체크된 물약을 순서대로 사용 (기본 단축키: `)", anchor="w"
        )
        flask_title.pack(fill="x", padx=8, pady=(8, 4))
        Tooltip(
            flask_title,
            "물약 매크로 단축키(기본 `)를 누르면 아래에서 체크된 슬롯을 1→5 순서로 입력하고, "
            "#6이 체크되어 있으면 마지막으로 6번 키를 입력합니다. 각 입력 사이에는 자연스러운 랜덤 딜레이가 들어갑니다.",
        )

        check_row = ctk.CTkFrame(tab, fg_color="transparent")
        check_row.pack(fill="x", padx=8)
        self.flask_vars = []
        checks = flask.get("checkboxes", [False] * 6)
        for i in range(5):
            var = ctk.BooleanVar(value=checks[i] if i < len(checks) else False)
            ctk.CTkCheckBox(
                check_row, text=str(i + 1), variable=var, width=40,
                command=self._save_flask_checkboxes,
            ).pack(side="left", padx=4)
            self.flask_vars.append(var)
        var6 = ctk.BooleanVar(value=checks[5] if len(checks) > 5 else False)
        ctk.CTkCheckBox(
            check_row, text="#6", variable=var6, width=40, command=self._save_flask_checkboxes
        ).pack(side="left", padx=4)
        self.flask_vars.append(var6)

        self.flask_custom_key = ctk.StringVar(value=flask.get("custom_key", ""))
        entry = ctk.CTkEntry(check_row, textvariable=self.flask_custom_key, width=80, placeholder_text="6번 키")
        entry.pack(side="left", padx=8)
        self.flask_custom_key.trace_add("write", lambda *_: self._save_flask_custom_key())

        cd_row, _cd_label = labelled_row(
            tab,
            "물약 쿨다운 (초) — 막대그래프로 표시",
            "해당 슬롯의 물약을 사용하면 여기 입력한 초 동안 줄어드는 막대를 화면에 표시합니다. "
            "0이면 표시하지 않습니다. 막대는 남은 시간에 비례해 줄어들기 때문에 숫자를 읽지 않고도 "
            "곁눈질로 파악할 수 있습니다.",
        )
        cd_row.pack(fill="x", padx=8, pady=(16, 4))
        self.cooldown_vars = self._build_cooldown_entries(
            tab, flask.get("cooldowns_ms", [0] * 5),
            [str(i + 1) for i in range(5)], self._save_flask_cooldowns,
        )

        opt_row = ctk.CTkFrame(tab, fg_color="transparent")
        opt_row.pack(fill="x", padx=8, pady=(10, 4))
        self.overlay_enabled_var = ctk.BooleanVar(value=flask.get("overlay_enabled", True))
        ctk.CTkCheckBox(
            opt_row, text="물약 쿨다운 막대 표시", variable=self.overlay_enabled_var,
            command=self._save_flask_overlay_enabled,
        ).pack(side="left")
        self.flask_color_btn = self._build_color_button(
            opt_row, "flask", "물약 막대 색상", theme_default="#39b6ff"
        )
        self._build_alpha_slider(tab, "flask", "물약 막대 투명도")
        ctk.CTkButton(
            tab, text="물약 막대 위치/크기 설정 (드래그)",
            command=lambda: calibration.calibrate_flask_overlay(self, self.config),
        ).pack(anchor="w", padx=8, pady=(0, 4), fill="x")

        ctk.CTkLabel(tab, text="─" * 46).pack(fill="x", padx=8, pady=(8, 4))

        skills = self.config.data["skills"]
        sk_row, _sk_label = labelled_row(
            tab,
            "스킬 쿨다운 (초) — Q W E R T",
            "Q/W/E/R/T 키를 누르면 해당 스킬의 쿨다운 막대를 표시합니다. 물약과는 별개의 막대이고 "
            "화면 위치도 따로 지정합니다 (물약과 스킬은 HUD에서 서로 다른 위치에 있기 때문입니다). "
            "0이면 표시하지 않습니다.",
        )
        sk_row.pack(fill="x", padx=8, pady=(4, 4))
        self.skill_cooldown_vars = self._build_cooldown_entries(
            tab, skills.get("cooldowns_ms", [0] * 5),
            [k.upper() for k in SKILL_KEYS], self._save_skill_cooldowns,
        )

        sk_opt_row = ctk.CTkFrame(tab, fg_color="transparent")
        sk_opt_row.pack(fill="x", padx=8, pady=(10, 4))
        self.skill_overlay_enabled_var = ctk.BooleanVar(value=skills.get("overlay_enabled", False))
        ctk.CTkCheckBox(
            sk_opt_row, text="스킬 쿨다운 막대 표시", variable=self.skill_overlay_enabled_var,
            command=self._save_skill_overlay_enabled,
        ).pack(side="left")
        self.skill_color_btn = self._build_color_button(
            sk_opt_row, "skills", "스킬 막대 색상", theme_default="#ffb03a"
        )
        self._build_alpha_slider(tab, "skills", "스킬 막대 투명도")
        ctk.CTkButton(
            tab, text="스킬 막대 위치/크기 설정 (드래그)",
            command=lambda: calibration.calibrate_skill_overlay(self, self.config),
        ).pack(anchor="w", padx=8, pady=(0, 8), fill="x")

    def _build_alpha_slider(self, parent: ctk.CTkFrame, section: str, label: str) -> None:
        """Overlay opacity, applied live as the slider moves."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0, 4))
        HelpMark(
            row,
            f"{label}를 조절합니다. 낮출수록 막대가 흐려져 게임 화면이 비쳐 보입니다. "
            "슬라이더를 움직이면 화면의 막대에 바로 반영됩니다.",
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(row, text=f"{label}:", font=theme.FONT_CAPTION).pack(side="left")

        current = float(self.config.data[section].get("overlay_alpha", 0.85))
        value_label = ctk.CTkLabel(
            row, text=f"{int(current * 100)}%", width=42, font=theme.FONT_CAPTION
        )

        def on_move(value: float) -> None:
            self.config.data[section]["overlay_alpha"] = round(float(value), 2)
            value_label.configure(text=f"{int(float(value) * 100)}%")

        slider = ctk.CTkSlider(
            row, from_=0.15, to=1.0, number_of_steps=17, width=170, command=on_move
        )
        slider.set(current)
        slider.pack(side="left", padx=(8, 6))
        value_label.pack(side="left")
        # Saved on release, not on every pixel of travel: dragging the slider
        # would otherwise rewrite config.json dozens of times and fan out a
        # full hotkey re-registration on each one.
        slider.bind("<ButtonRelease-1>", lambda _e: self.config.save())

    def _build_color_button(
        self, parent: ctk.CTkFrame, section: str, label: str, theme_default: str
    ) -> ctk.CTkButton:
        """Swatch button that opens the OS colour chooser.

        The button *is* the swatch -- its own fill shows the colour the bars
        will be drawn in, so the setting is legible without opening anything.
        """
        current = self.config.data[section].get("bar_color") or theme_default

        btn = ctk.CTkButton(parent, text=label, width=140, fg_color=current, hover=False)

        def choose() -> None:
            colour = colorchooser.askcolor(
                color=self.config.data[section].get("bar_color") or theme_default,
                title=label, parent=self,
            )[1]
            if not colour:
                return
            self.config.data[section]["bar_color"] = colour
            self.config.save()
            btn.configure(fg_color=colour, text_color=_readable_on(colour))

        btn.configure(command=choose, text_color=_readable_on(current))
        btn.pack(side="left", padx=(12, 0))
        Tooltip(btn, f"{label}을 고릅니다. 버튼 색이 실제 막대 색상입니다.")
        return btn

    def _build_cooldown_entries(
        self, parent: ctk.CTkFrame, values_ms: list[int], labels: list[str],
        on_save: Callable[[], None],
    ) -> list[ctk.StringVar]:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=8)
        out: list[ctk.StringVar] = []
        for i, name in enumerate(labels):
            seconds = (values_ms[i] if i < len(values_ms) else 0) / 1000.0
            var = ctk.StringVar(value=str(seconds) if seconds else "0")
            ctk.CTkLabel(row, text=name, width=22).pack(side="left", padx=(8 if i else 0, 2))
            ctk.CTkEntry(row, textvariable=var, width=50).pack(side="left")
            var.trace_add("write", lambda *_: on_save())
            out.append(var)
        return out

    def _save_skill_cooldowns(self) -> None:
        try:
            ms = [int(float(v.get() or 0) * 1000) for v in self.skill_cooldown_vars]
        except ValueError:
            return
        self.config.data["skills"]["cooldowns_ms"] = ms
        self.config.save()

    def _save_skill_overlay_enabled(self) -> None:
        self.config.data["skills"]["overlay_enabled"] = self.skill_overlay_enabled_var.get()
        self.config.save()

    def _save_flask_checkboxes(self) -> None:
        self.config.data["flask"]["checkboxes"] = [v.get() for v in self.flask_vars]
        self.config.save()

    def _save_flask_custom_key(self) -> None:
        self.config.data["flask"]["custom_key"] = self.flask_custom_key.get()
        self.config.save()

    def _save_flask_cooldowns(self) -> None:
        try:
            ms = [int(float(v.get() or 0) * 1000) for v in self.cooldown_vars]
        except ValueError:
            return
        self.config.data["flask"]["cooldowns_ms"] = ms
        self.config.save()

    def _save_flask_overlay_enabled(self) -> None:
        self.config.data["flask"]["overlay_enabled"] = self.overlay_enabled_var.get()
        self.config.save()

    # ---- tab: 게임 단축키 -------------------------------------------------
    def _build_hotkeys_tab(self, tab: ctk.CTkFrame) -> None:
        self._build_save_row(tab, self._save_all_hotkeys)
        # fill="both"/expand=True, like every other tab's content area: the
        # old fixed height=380 packed with fill="x" only left ~150px of dead
        # space below the list while the rows above stayed needlessly
        # cramped.
        # A holder packed up front, with the label packed/unpacked inside it,
        # rather than packing the banner itself with before=scroll: a
        # CTkScrollableFrame packs an internal parent, so it is not the
        # sibling that "before" needs and Tk rejects it outright.
        #
        # height=0 is load-bearing: a CTkFrame defaults to 200x200, and with
        # no conflict to report this holder has no children to shrink it, so
        # it was reserving a 200px band of blank space above the list and
        # pushing every row of the tab down by that much.
        # ``refresh_conflict_banner`` re-asserts it when the banner is taken
        # back down, since the frame otherwise keeps whatever height the
        # banner grew it to.
        self.conflict_holder = ctk.CTkFrame(tab, fg_color="transparent", height=0)
        self.conflict_holder.pack(fill="x", padx=8, pady=(0, 0))
        self.conflict_banner = ctk.CTkLabel(
            self.conflict_holder, text="", anchor="w", justify="left",
            font=theme.FONT_CAPTION, text_color=theme.DANGER, wraplength=660,
        )

        scroll = ctk.CTkScrollableFrame(tab)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)
        self.hotkey_scroll = scroll

        # Autoclick first, not buried at the bottom: it is the one binding
        # here that can get an account disconnected if it is set carelessly,
        # so it should be read before the list rather than found after it.
        self._build_autoclick_row(scroll)

        # Fixed order, everyday actions first, rather than the old "whatever
        # happens to be bound floats to the top" sort -- that reshuffled the
        # list as keys were assigned, so the row you wanted was never twice in
        # the same place. Anything not named here keeps its declaration order
        # below, with unbound entries last.
        priority = {aid: i for i, aid in enumerate(_HOTKEY_PRIORITY)}
        declared = {aid: i for i, (aid, _t, _h) in enumerate(_GAME_HOTKEY_DESCRIPTIONS)}
        sorted_hotkeys = sorted(
            _GAME_HOTKEY_DESCRIPTIONS,
            key=lambda item: (
                priority.get(item[0], len(priority)),
                0 if self.config.data["hotkeys"].get(item[0], "") else 1,
                declared[item[0]],
            ),
        )
        for action_id, title, hint in sorted_hotkeys:
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            HelpMark(row, hint).pack(side="left", padx=(0, 6))
            label = ctk.CTkLabel(row, text=title, anchor="w")
            label.pack(side="left", fill="x", expand=True)
            Tooltip(label, hint)
            picker = HotkeyPicker(
                row,
                on_change=lambda combo, aid=action_id: self._save_hotkey(aid, combo),
                show_capture=False,
            )
            picker.set_combo(self.config.data["hotkeys"].get(action_id, ""))
            picker.pack(side="right")

            calib_fn = _CALIBRATION_SHORTCUTS.get(action_id)
            if calib_fn is not None:
                calib_btn = ctk.CTkButton(
                    row, text="📍좌표설정", width=64, height=22, font=theme.FONT_CAPTION,
                    command=lambda fn=calib_fn: self._jump_to_calibration(fn),
                )
                calib_btn.pack(side="right", padx=(0, 6))
                Tooltip(calib_btn, "이 기능에 필요한 화면 좌표를 지금 바로 설정합니다 (좌표 설정 탭으로 이동).")

            if action_id == "toggle_continuous_key":
                # Which key the toggle actually spams, right under the
                # toggle itself. It used to live in its own section far
                # below the divider, where it read as an unrelated setting
                # and was easy to miss entirely.
                self._build_continuous_key_detail(scroll)

            if action_id == _HOTKEY_PRIORITY[-1]:
                # Closes the everyday-actions group (autoclick through
                # 인벤토리 → 창고). The divider used to sit directly under the
                # autoclick row, which fenced autoclick off on its own and
                # left the eight actions people actually use every session
                # reading as leftovers of the long tail below them.
                ctk.CTkLabel(scroll, text="─" * 40).pack(fill="x", pady=(6, 8))

        ctk.CTkLabel(scroll, text="─" * 40).pack(fill="x", pady=8)

        mine_row, _mine_label = labelled_row(
            scroll,
            "지뢰 자동시전/격발 홀드키:",
            "체크 후 이 홀드키를 누르고 있으면 '지뢰 스킬키'를 계속 눌러 자동 시전하고, "
            "키를 떼면 스킬키를 놓은 뒤 D키를 눌러 격발합니다.",
        )
        mine_row.pack(fill="x", pady=3)
        self.minefield_enabled_var = ctk.BooleanVar(
            value=self.config.data["misc"].get("minefield_enabled", False)
        )
        ctk.CTkCheckBox(
            mine_row, text="", width=24, variable=self.minefield_enabled_var,
            command=self._save_minefield_enabled,
        ).pack(side="left", padx=(4, 4))
        mine_trigger_picker = HotkeyPicker(
            mine_row, on_change=lambda combo: self._save_hotkey("minefield_hold", combo),
            show_capture=False,
        )
        mine_trigger_picker.set_combo(self.config.data["hotkeys"].get("minefield_hold", "tab"))
        mine_trigger_picker.pack(side="left", padx=(6, 10))
        HelpMark(mine_row, "지뢰 자동시전 중 계속 눌러줄 스킬 키입니다 (예: q).").pack(side="left", padx=(0, 4))
        ctk.CTkLabel(mine_row, text="지뢰 스킬키:").pack(side="left")
        self.holdkey_var = ctk.StringVar(value=self.config.data["misc"].get("holdkey", ""))
        ctk.CTkEntry(mine_row, textvariable=self.holdkey_var, width=60, placeholder_text="예: q").pack(
            side="left", padx=8
        )
        self.holdkey_var.trace_add("write", lambda *_: self._save_holdkey())

        # Surface any duplicates that were already in the saved config, not
        # only ones created during this session.
        self.refresh_conflict_banner()

    def _build_autoclick_row(self, parent: ctk.CTkFrame) -> None:
        row, _label = labelled_row(
            parent, "오토클릭 홀드키 (Ctrl 또는 Alt 필수):", _AUTOCLICK_HELP,
            font=theme.FONT_BODY_STRONG,
        )
        row.pack(fill="x", pady=(2, 2))

        self.autoclick_picker = HotkeyPicker(
            row, on_change=self._save_autoclick_hold, show_capture=False
        )
        self.autoclick_picker.set_combo(
            self.config.data["hotkeys"].get("autoclick_hold", "ctrl+capslock")
        )
        self.autoclick_picker.pack(side="left", padx=(6, 0))

        # Only the danger stays on screen. The mechanics (Ctrl/Alt required,
        # Shift for transfers) are useful once and then just noise, so they
        # live in the ? tooltip; the account-risk line is the one thing that
        # has to be readable without hovering anything.
        warn = ctk.CTkLabel(
            parent,
            text="⚠ 반드시 인벤토리/보관함 안에서만 사용하세요. 일반 필드에서 사용하면 "
                 "입력 과다로 접속이 차단될 수 있습니다.",
            anchor="w", justify="left", font=theme.FONT_CAPTION, text_color=theme.DANGER,
            wraplength=640,
        )
        # Bottom padding stands in for the divider that used to close this
        # block off; without either, the first hotkey row crowded the warning.
        warn.pack(fill="x", padx=(24, 0), pady=(2, 8))
        Tooltip(warn, _AUTOCLICK_HELP)

    def _save_autoclick_hold(self, combo: str) -> None:
        """Reject a binding without Ctrl/Alt instead of saving it.

        A bare key here means every press of that letter -- including while
        typing in chat -- starts machine-gun clicking, which is both useless
        and the fastest way to look like an input bot.
        """
        if combo:
            parts = {p.strip().lower() for p in combo.split("+")}
            if not parts & {"ctrl", "alt"}:
                toast.show("오토클릭 홀드키는 Ctrl 또는 Alt를 반드시 포함해야 합니다.")
                self.autoclick_picker.set_combo(
                    self.config.data["hotkeys"].get("autoclick_hold", "ctrl+capslock")
                )
                return
        self._save_hotkey("autoclick_hold", combo)

    _CONTINUOUS_HELP = (
        "위 '연속사용키 토글' 단축키를 누르면 아래 5개 슬롯에 지정한 키를 왼쪽부터 순서대로 한 번씩 "
        "누릅니다.\n\n"
        "• 키와 키 사이는 0.1~0.3초 랜덤 간격입니다.\n"
        "• 모든 키를 다 누른 뒤 아래 '간격'만큼 쉬었다가 다시 처음부터 반복합니다.\n"
        "• 간격은 정확히 같은 값이 아니라 지정값 ~ 지정값+0.3초 사이에서 매번 랜덤하게 정해집니다 "
        "(자동 입력 티가 나지 않도록).\n"
        "• '없음'으로 둔 슬롯은 건너뜁니다. 같은 단축키를 다시 누르면 정지합니다."
    )

    def _build_continuous_key_detail(self, parent: ctk.CTkFrame) -> None:
        """Sub-rows of the '연속사용키 토글' hotkey: which keys get pressed,
        in what order, and how long the cycle waits before repeating."""
        row, _label = labelled_row(
            parent, "└ 연속으로 누를 키 (순서대로):", self._CONTINUOUS_HELP,
            font=theme.FONT_CAPTION,
        )
        row.pack(fill="x", padx=(20, 0), pady=(0, 2))

        slots = self.config.data["misc"].get("ckeys", ["없음"] * CONTINUOUS_KEY_SLOTS)
        self.ckey_vars: list[ctk.StringVar] = []
        for i in range(CONTINUOUS_KEY_SLOTS):
            value = slots[i] if i < len(slots) else "없음"
            var = ctk.StringVar(value=value if value in CONTINUOUS_KEY_CHOICES else "없음")
            ctk.CTkOptionMenu(
                row, values=CONTINUOUS_KEY_CHOICES, variable=var, width=72,
                command=lambda _v: self._save_ckey(),
            ).pack(side="left", padx=(6 if i == 0 else 3, 0))
            self.ckey_vars.append(var)

        delay_row = ctk.CTkFrame(parent, fg_color="transparent")
        delay_row.pack(fill="x", padx=(20, 0), pady=(0, 6))
        HelpMark(
            delay_row,
            "모든 키를 한 번씩 누른 뒤 다음 반복까지 쉬는 시간입니다. 실제로는 이 값 ~ +0.3초 "
            "사이에서 매번 랜덤하게 정해집니다.",
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(delay_row, text="└ 반복 간격(초):", font=theme.FONT_CAPTION).pack(side="left")
        self.ckey_delay_var = ctk.StringVar(
            value=str(self.config.data["misc"].get("ckey_delay_ms", 3000) / 1000)
        )
        ctk.CTkEntry(delay_row, textvariable=self.ckey_delay_var, width=60).pack(side="left", padx=6)
        ctk.CTkLabel(
            delay_row, text="(예: 3 → 3.0~3.3초 랜덤)", font=theme.FONT_SMALL,
            text_color=theme.PRIMARY_ON_DARK,
        ).pack(side="left")
        self.ckey_delay_var.trace_add("write", lambda *_: self._save_ckey())

    # ---- duplicate detection ---------------------------------------------
    def _all_bound_combos(self) -> dict[str, list[str]]:
        """Normalised combo -> the human names using it.

        Normalised so "Ctrl+D" and "ctrl+d", or alt+shift+x and shift+alt+x,
        are recognised as the same binding -- the manager matches on the
        modifier *set*, so those really do collide.
        """
        titles = {aid: title for aid, title, _hint in _GAME_HOTKEY_DESCRIPTIONS}
        titles.update({
            "minefield_hold": "지뢰 자동시전/격발 홀드키",
            "autoclick_hold": "오토클릭 홀드키",
        })

        used: dict[str, list[str]] = {}
        for action_id, combo in self.config.data["hotkeys"].items():
            if combo:
                used.setdefault(_normalise_combo(combo), []).append(
                    titles.get(action_id, action_id)
                )
        for i, macro in enumerate(self.config.data["chat_macros"]):
            combo = macro.get("hotkey")
            if combo:
                name = macro.get("text", "").strip()[:14] or f"채팅 매크로 {i + 1}"
                used.setdefault(_normalise_combo(combo), []).append(f"채팅: {name}")
        return used

    def find_hotkey_conflicts(self) -> dict[str, list[str]]:
        """Only the combos claimed by more than one action."""
        return {c: names for c, names in self._all_bound_combos().items() if len(names) > 1}

    def refresh_conflict_banner(self) -> None:
        """Keep the warning strip on the hotkey tab in step with the config.

        Duplicates are not rejected -- sometimes two things on one key is
        deliberate -- but they are never silently accepted either: with a raw
        keyboard hook every binding on a key fires, so an unnoticed duplicate
        means one press quietly doing two jobs.
        """
        banner = getattr(self, "conflict_banner", None)
        if banner is None or not banner.winfo_exists():
            return
        conflicts = self.find_hotkey_conflicts()
        if not conflicts:
            banner.pack_forget()
            # Un-packing the only child does not shrink the holder back on
            # its own -- it keeps the height the banner gave it, which would
            # leave a permanent blank band once a conflict has been resolved.
            self.conflict_holder.configure(height=0)
            return
        lines = [
            f"• {combo.upper()} → {' / '.join(names)}"
            for combo, names in sorted(conflicts.items())
        ]
        banner.configure(
            text="⚠ 중복된 단축키가 있습니다. 한 번 누르면 아래 기능이 모두 실행됩니다.\n"
                 + "\n".join(lines)
        )
        banner.pack(fill="x", pady=(2, 4))

    def _save_all_hotkeys(self) -> None:
        self.config.save()
        self.refresh_conflict_banner()

    def _jump_to_calibration(self, calibrate_fn: Callable) -> None:
        self._show_tab("좌표 설정")
        calibrate_fn(self, self.config)

    def _save_hotkey(self, action_id: str, combo: str) -> None:
        self.config.data["hotkeys"][action_id] = combo
        self.config.save()
        self.refresh_conflict_banner()

    def _save_minefield_enabled(self) -> None:
        self.config.data["misc"]["minefield_enabled"] = self.minefield_enabled_var.get()
        self.config.save()

    def _save_holdkey(self) -> None:
        self.config.data["misc"]["holdkey"] = self.holdkey_var.get()
        self.config.save()

    def _save_ckey(self) -> None:
        try:
            delay_ms = int(float(self.ckey_delay_var.get() or 0) * 1000)
        except ValueError:
            return
        self.config.data["misc"]["ckeys"] = [v.get() for v in self.ckey_vars]
        self.config.data["misc"]["ckey_delay_ms"] = delay_ms
        self.config.save()

    # ---- tab: 링크 --------------------------------------------------------
    def _build_links_tab(self, tab: ctk.CTkFrame) -> None:
        self._build_save_row(tab)

        def on_change(links: list[dict]) -> None:
            self.config.data["links"] = links
            self.config.save()

        self.link_editor = LinkListEditor(tab, self.config.data["links"], on_change=on_change)
        self.link_editor.pack(fill="both", expand=True, padx=8, pady=8)

    def refresh_links(self) -> None:
        """Called after the memo window adds a favorite link, so the links
        tab reflects it without needing a manual reopen."""
        if "링크" not in self._built_tabs:
            # Not built yet -- it will read the (already saved) links from
            # config when it is first opened, so there is nothing to sync.
            return
        # Rebuilt silently, then saved once at the end. Every _remove_row()
        # otherwise fires the editor's on_change, which writes the
        # now-shorter list straight back to config and saves -- so by the
        # time the clear loop finished, config.data["links"] was already []
        # and the repopulate below read back nothing, permanently wiping
        # every saved link. Snapshotting the list first is what actually
        # fixes that; the silent flag also keeps this from writing
        # config.json once per row.
        links = [dict(link) for link in self.config.data["links"]]
        self.link_editor._loading = True
        try:
            for row in list(self.link_editor.rows):
                self.link_editor._remove_row(row)
            for link in links:
                self.link_editor._add_row(link.get("name", ""), link.get("url", ""))
        finally:
            self.link_editor._loading = False
        self.link_editor._notify()

    # ---- tab: 좌표 설정 -----------------------------------------------------
    def _build_calibration_tab(self, tab: ctk.CTkFrame) -> None:
        self._build_save_row(tab)
        scroll = ctk.CTkScrollableFrame(tab)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        grid_help = (
            "화면에 반투명 상자가 나타납니다.\n\n"
            "① 드래그해서 크기를 맞추고 ② 상자 안쪽을 끌어 위치를 옮긴 뒤 ③ '확인'을 누르면 저장됩니다.\n"
            "상자 안의 점선이 실제 게임 칸과 겹치도록 맞추세요. 방향키로 1px씩, Shift+방향키로 크기를 "
            "미세 조정할 수 있습니다. Esc로 취소합니다."
        )
        for label, cols_rows, fn in (
            ("쿼드 보관함 영역 설정", "24×24칸", calibration.calibrate_quad_stash_region),
            ("일반 보관함 영역 설정", "12×12칸", calibration.calibrate_stash_region),
            ("인벤토리 영역 설정", "12×5칸", calibration.calibrate_inventory_region),
        ):
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=4)
            HelpMark(row, f"{label} ({cols_rows}).\n\n{grid_help}").pack(side="left", padx=(0, 6))
            ctk.CTkButton(
                row, text=f"{label}  ({cols_rows})",
                command=lambda f=fn: f(self, self.config),
            ).pack(side="left", fill="x", expand=True)

        # Right under the region buttons: a region that looks correctly placed
        # can still detect nothing (wrong colour, too-thin frame at this
        # resolution), and that is invisible until something is actually
        # measured against a real screenshot.
        test_help = (
            "게임에서 보관함을 열고 검색창에 아이템을 검색한 상태에서 눌러주세요.\n\n"
            "지정한 영역을 그대로 캡처해서, 프로그램이 어떤 칸을 '검색된 아이템'으로 인식하는지 "
            "화면에 그려서 보여줍니다.\n"
            "• 아무것도 인식되지 않으면 → 강조색 인식 범위나 인식 강도 문제입니다.\n"
            "• 엉뚱한 칸이 잡히면 → 인식 강도를 올리면 됩니다.\n"
            "모니터·해상도·감마에 따라 테두리 두께와 색이 달라지기 때문에, 값을 추측하지 않고 "
            "실제 화면으로 맞출 수 있게 만든 창입니다."
        )
        for label, region_key in (("쿼드 보관함", "quad_stash"), ("일반 보관함", "stash")):
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=4)
            HelpMark(row, test_help).pack(side="left", padx=(0, 6))
            ctk.CTkButton(
                row, text=f"🔍 {label} 검색 인식 테스트",
                fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_FOCUS,
                command=lambda k=region_key, l=label: self._open_detect_test(k, l),
            ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(scroll, text="─" * 46).pack(fill="x", pady=(8, 4))

        for label, help_text, fn in (
            ("감정주문서 위치 설정 (Ctrl+D)",
             "감정의 주문서를 놓아둘 인벤토리 칸을 클릭해 지정합니다.",
             calibration.calibrate_identify_scroll_point),
            ("투겐 흥정 확인버튼 위치 설정 (Ctrl+3)",
             "투겐 흥정 창의 '확인' 버튼 위치를 클릭해 지정합니다.",
             calibration.calibrate_tujen_confirm_point),
            ("기회/정제의 오브 좌표 설정 (Ctrl+4)",
             "기회의 오브 → 정제의 오브 → 찬스질할 아이템 순서로 3곳을 클릭해 지정합니다.",
             calibration.calibrate_currency_cycle_points),
        ):
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=4)
            HelpMark(row, help_text).pack(side="left", padx=(0, 6))
            ctk.CTkButton(
                row, text=label, command=lambda f=fn: f(self, self.config)
            ).pack(side="left", fill="x", expand=True)

        cycle_row, _cycle_label = labelled_row(
            scroll, "기회/정제 반복 횟수:",
            "Ctrl+4를 한 번 누를 때 '기회의 오브 → 아이템 → 정제의 오브 → 아이템' 순서를 몇 번 반복할지 정합니다.",
        )
        cycle_row.pack(fill="x", pady=(4, 0))
        self.cycle_var = ctk.StringVar(value=str(self.config.data["currency_cycle"].get("cycles", 1)))
        ctk.CTkEntry(cycle_row, textvariable=self.cycle_var, width=50).pack(side="left", padx=6)
        self.cycle_var.trace_add("write", lambda *_: self._save_cycles())

    def _open_detect_test(self, region_key: str, label: str) -> None:
        existing = self._detect_windows.get(region_key)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.capture()
            return
        self._detect_windows[region_key] = DetectTestWindow(
            self, self.config, region_key, label
        )

    def _save_cycles(self) -> None:
        try:
            cycles = int(self.cycle_var.get() or 1)
        except ValueError:
            return
        self.config.data["currency_cycle"]["cycles"] = cycles
        self.config.save()

    # ---- tab: 메모 --------------------------------------------------------
    def _build_memo_tab(self, tab: ctk.CTkFrame) -> None:
        MemoTab(tab, self.config, on_favorite_added=lambda _u: self.refresh_links()).pack(
            fill="both", expand=True
        )

    # ---- tab: 레벨업 -------------------------------------------------------
    def _build_levelup_tab(self, tab: ctk.CTkFrame) -> None:
        """Levelling routes only. Split out of the old combined tab: routes
        are read while playing, the launcher/backup settings underneath them
        are touched once and never again -- two different jobs sharing one
        scroll area meant scrolling past one to reach the other."""
        self._build_save_row(tab)
        scroll = ctk.CTkScrollableFrame(tab)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        # One block per game, stacked: PoE1 and PoE2 have separate routes and
        # separate layout support, and side-by-side buttons made it look like
        # one feature with a version switch rather than two independent sets.
        self._build_levelup_section(scroll, "poe1", layouts=True)
        ctk.CTkLabel(scroll, text="─" * 46).pack(fill="x", pady=(14, 10))
        self._build_levelup_section(scroll, "poe2", layouts=False)

    _LAYOUT_HELP = (
        "현재 있는 지역의 지형(레이아웃) 그림을 화면 위에 띄웁니다.\n\n"
        "• 지역 이름은 게임 클라이언트 로그의 엔진 출력에서 읽어오므로, 다른 지역으로 이동하면 "
        "그림도 자동으로 바뀝니다.\n"
        "• 창 위에 마우스를 올리면 조절 막대와 닫기 버튼이 나타나고, 마우스를 치우면 그림만 "
        "남습니다.\n"
        "• 이동: 창의 아무 곳이나 잡고 끌면 됩니다. 크기: 오른쪽 아래 모서리의 ◢ 를 잡고 "
        "끌면 비율을 유지한 채 커지고 작아집니다. 투명도는 ◐ 막대로 조절합니다.\n"
        "• 액트 1~5와 6~10에 같은 이름의 지역이 있어 두 가지 지형이 있을 때는, 창 위쪽의 "
        "◀ 액트N ▶ 버튼으로 바꿀 수 있습니다.\n"
        "• 마을·은신처처럼 그림이 없는 지역에서는 작은 안내 문구만 표시됩니다."
    )

    def _build_levelup_section(
        self, parent: ctk.CTkFrame, mode: str, layouts: bool
    ) -> None:
        title = "POE1" if mode == "poe1" else "POE2"
        ctk.CTkLabel(parent, text=title, font=theme.FONT_SECTION, anchor="w").pack(fill="x")

        route_row, _route_title = labelled_row(
            parent, "레벨업 루트",
            "레벨업 루트 참고 창을 엽니다. 마지막으로 보던 위치를 기억해 다시 열면 이어서 보여줍니다. "
            "창은 항상 위에 표시되고, 투명도와 크기를 조절할 수 있습니다.",
        )
        route_row.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(
            route_row, text=f"{title} 루트 열기", width=150,
            command=lambda: self._open_route(mode),
        ).pack(side="right")

        layout_row, _layout_title = labelled_row(parent, "맵 레이아웃", self._LAYOUT_HELP)
        layout_row.pack(fill="x", pady=(6, 0))
        button = ctk.CTkButton(
            layout_row, text=f"{title} 맵 레이아웃 열기", width=150,
            command=lambda: self._open_layout(mode),
        )
        button.pack(side="right")

        if layouts:
            note = "루트 창에서 방향키/버튼으로 단계를 넘길 수 있고, 마지막으로 보던 줄은 자동 저장됩니다."
        else:
            # Disabled rather than hidden: the row is what says the feature is
            # planned for POE2 too, and a button that silently isn't there
            # reads as something broken in the build.
            button.configure(state="disabled", text="POE2 맵 레이아웃 (미구현)")
            note = "POE2 맵 레이아웃은 아직 준비 중입니다. 루트 창은 정상적으로 사용할 수 있습니다."
        ctk.CTkLabel(
            parent, text=note, anchor="w", justify="left", font=theme.FONT_CAPTION,
            text_color=theme.PRIMARY, wraplength=620,
        ).pack(fill="x", pady=(6, 0))

    # ---- tab: 설정 ---------------------------------------------------------
    def _build_misc_tab(self, tab: ctk.CTkFrame) -> None:
        self._build_save_row(tab)
        scroll = ctk.CTkScrollableFrame(tab)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_zone_section(scroll)

        ctk.CTkLabel(scroll, text="프로그램/게임 실행", font=theme.FONT_SECTION).pack(
            anchor="w", pady=(16, 2)
        )
        self.poe_url_var = self._build_poe_launch_row(
            scroll, "POE1 실행", "poe_launch_url", pady=(2, 2)
        )
        self.poe2_url_var = self._build_poe_launch_row(
            scroll, "POE2 실행", "poe2_launch_url", pady=(0, 4)
        )

        self.path_vars: list[ctk.StringVar] = []
        paths_cfg = self.config.data["paths"]
        for i in range(5):
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            var = ctk.StringVar(value=paths_cfg[i] if i < len(paths_cfg) else "")
            ctk.CTkEntry(row, textvariable=var, width=260, placeholder_text=f"프로그램 #{i+1} 경로").pack(
                side="left", padx=(0, 4)
            )
            ctk.CTkButton(row, text="찾아보기", width=70, command=lambda v=var: self._browse_path(v)).pack(
                side="left", padx=2
            )
            ctk.CTkButton(row, text="실행", width=50, command=lambda v=var: self.actions.launch_path(v.get())).pack(
                side="left", padx=2
            )
            var.trace_add("write", lambda *_: self._save_paths())
            self.path_vars.append(var)

        ctk.CTkLabel(scroll, text="설정 백업/복원", font=theme.FONT_SECTION).pack(
            anchor="w", pady=(16, 2)
        )
        backup_row = ctk.CTkFrame(scroll, fg_color="transparent")
        backup_row.pack(fill="x")
        ctk.CTkButton(backup_row, text="내보내기", command=self._export_config).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(backup_row, text="가져오기", command=self._import_config).pack(side="left")

        self._build_whisper_section(scroll)
        self._build_trade_section(scroll)
        self._build_update_section(scroll)

    # ---- 설정 탭: 귓속말 알림 ------------------------------------------------
    _WHISPER_HELP = (
        "귓속말이 오면 화면에 알림 카드를 띄웁니다. 게임 클라이언트가 남기는 로그에서 "
        "읽어오므로 게임 메모리나 화면을 건드리지 않습니다.\n\n"
        "• 구매 귓속말이면 아이템 이름·제시 금액·창고 위치를 뽑아 강조해서 보여줍니다.\n"
        "• 카드의 초대/귓속말/은신처 버튼은 게임 채팅창에 명령을 대신 입력합니다.\n"
        "• 차단 버튼은 오른쪽 끝에 따로 떨어뜨려 두었습니다. 되돌릴 수 없는 동작이라 "
        "자주 누르는 버튼 옆에 두지 않았습니다.\n"
        "• 카드는 설정한 시간 뒤 숨겨지고 얇은 막대만 남습니다. 막대에 마우스를 올리면 "
        "다시 나타납니다. 최대 3개가 보이고 그 이상은 스크롤됩니다.\n"
        "• 내가 보낸 귓속말은 게임이 로그에 남기지 않아 표시할 수 없습니다."
    )
    def _build_whisper_section(self, parent: ctk.CTkFrame) -> None:
        title_row, _label = labelled_row(
            parent, "귓속말 알림", self._WHISPER_HELP, font=theme.FONT_SECTION
        )
        title_row.pack(fill="x", pady=(18, 2))

        cfg = self.config.data.setdefault("whisper", {})
        self.whisper_enabled_var = ctk.BooleanVar(value=cfg.get("enabled", True))
        ctk.CTkCheckBox(
            parent, text="귓속말 알림 사용", variable=self.whisper_enabled_var,
            command=self._save_whisper,
        ).pack(anchor="w", pady=(2, 0))

        self.whisper_start_var = ctk.BooleanVar(value=cfg.get("open_on_start", True))
        ctk.CTkCheckBox(
            parent, text="프로그램 시작할 때 알림창 열기", variable=self.whisper_start_var,
            command=self._save_whisper,
        ).pack(anchor="w", pady=(4, 0))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(row, text="숨김까지:", font=theme.FONT_CAPTION).pack(side="left")
        self.whisper_hide_var = ctk.StringVar(value=str(cfg.get("hide_after_sec", 5)))
        ctk.CTkEntry(row, textvariable=self.whisper_hide_var, width=46).pack(side="left", padx=(4, 2))
        ctk.CTkLabel(row, text="초", font=theme.FONT_CAPTION).pack(side="left")
        ctk.CTkLabel(row, text="보관:", font=theme.FONT_CAPTION).pack(side="left", padx=(16, 4))
        self.whisper_keep_var = ctk.StringVar(value=str(cfg.get("keep_minutes", 30)))
        ctk.CTkEntry(row, textvariable=self.whisper_keep_var, width=46).pack(side="left", padx=(0, 2))
        ctk.CTkLabel(row, text="분", font=theme.FONT_CAPTION).pack(side="left")
        for var in (self.whisper_hide_var, self.whisper_keep_var):
            var.trace_add("write", lambda *_: self._save_whisper())

        self.whisper_reply_vars: dict[str, ctk.StringVar] = {}
        for key, label in (("thanks", "감사"), ("wait", "잠시만"), ("sold", "품절")):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=(4, 0))
            ctk.CTkLabel(row, text=f"{label}:", font=theme.FONT_CAPTION, width=44, anchor="e").pack(
                side="left"
            )
            var = ctk.StringVar(
                value=self.config.data.get("whisper", {}).get("replies", {}).get(key, "")
            )
            ctk.CTkEntry(row, textvariable=var, width=430).pack(side="left", padx=6)
            var.trace_add("write", lambda *_: self._save_whisper())
            self.whisper_reply_vars[key] = var

        buttons = ctk.CTkFrame(parent, fg_color="transparent")
        buttons.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(
            buttons, text="알림창 열기 / 닫기", width=140, command=self.toggle_whisper_panel
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons, text="테스트 알림", width=100, command=self.test_whisper
        ).pack(side="left")

    def _save_whisper(self) -> None:
        cfg = self.config.data.setdefault("whisper", {})
        cfg["enabled"] = self.whisper_enabled_var.get()
        cfg["open_on_start"] = self.whisper_start_var.get()
        cfg["replies"] = {k: v.get() for k, v in self.whisper_reply_vars.items()}
        for key, var, low, high in (
            ("hide_after_sec", self.whisper_hide_var, 1, 120),
            ("keep_minutes", self.whisper_keep_var, 1, 720),
        ):
            try:
                cfg[key] = max(low, min(high, int(float(var.get()))))
            except ValueError:
                pass  # mid-edit; keep the last good value
        self.config.save()
        if not cfg["enabled"]:
            panel = self.whisper_panel(create=False)
            if panel is not None:
                panel.close()
                self._whisper_panel = None

    # ---- 설정 탭: 아이템 가격 확인 ------------------------------------------
    _TRADE_HELP = (
        "아이템에 마우스를 올리고 단축키(기본 Alt+D)를 누르면 거래소에서 같은 조건의 "
        "매물을 찾아 가격을 보여줍니다.\n\n"
        "• 게임이 Ctrl+C로 아이템 정보를 클립보드에 넣어주는 기능을 그대로 이용합니다. "
        "게임 메모리를 읽거나 화면을 인식하지 않습니다.\n"
        "• 고유 아이템은 이름으로, 레어 아이템은 베이스와 잘 뜬 속성 2개로 검색합니다. "
        "창에서 속성 체크를 바꾸면 바로 다시 찾습니다.\n"
        "• 게임 UI 설정에서 '고급 속성 설명'을 켜두시면 각 속성이 가능 범위 안에서 "
        "얼마나 잘 떴는지 %로 표시되고, 여러 줄짜리 속성도 정확히 인식됩니다.\n"
        "• 거래소는 요청 횟수 제한이 있어 잠깐 기다렸다 보내는 경우가 있습니다. "
        "제한을 어기면 일시적으로 차단되기 때문입니다."
    )

    def _build_trade_section(self, parent: ctk.CTkFrame) -> None:
        title_row, _label = labelled_row(
            parent, "아이템 가격 확인", self._TRADE_HELP, font=theme.FONT_SECTION
        )
        title_row.pack(fill="x", pady=(18, 2))

        cfg = self.config.data.setdefault("trade", {})

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(row, text="리그:", font=theme.FONT_CAPTION).pack(side="left")
        self.trade_league_var = ctk.StringVar(value=cfg.get("league", ""))
        ctk.CTkEntry(
            row, textvariable=self.trade_league_var, width=170,
            placeholder_text="비워두면 자동 (현재 리그)",
        ).pack(side="left", padx=6)
        self.trade_league_var.trace_add("write", lambda *_: self._save_trade())

        ctk.CTkLabel(row, text="거래소:", font=theme.FONT_CAPTION).pack(side="left", padx=(14, 4))
        self.trade_realm_var = ctk.StringVar(
            value="한국 (카카오)" if cfg.get("realm", "kakao") == "kakao" else "글로벌"
        )
        ctk.CTkOptionMenu(
            row, variable=self.trade_realm_var, values=["한국 (카카오)", "글로벌"],
            width=130, command=lambda _v: self._save_trade(),
        ).pack(side="left")

        ctk.CTkLabel(row, text="글씨 크기:", font=theme.FONT_CAPTION).pack(side="left", padx=(14, 4))
        self.trade_font_var = ctk.StringVar(value=str(cfg.get("font_size", 13)))
        ctk.CTkEntry(row, textvariable=self.trade_font_var, width=46).pack(side="left")
        self.trade_font_var.trace_add("write", lambda *_: self._save_trade())

        opts = ctk.CTkFrame(parent, fg_color="transparent")
        opts.pack(fill="x", pady=(6, 0))
        self.trade_auto_var = ctk.BooleanVar(value=cfg.get("auto_search", True))
        ctk.CTkCheckBox(
            opts, text="창을 열면 바로 검색", variable=self.trade_auto_var,
            command=self._save_trade,
        ).pack(side="left")

        ctk.CTkLabel(
            parent,
            text="리그를 비워두면 프로그램이 거래소에서 현재 리그를 알아내 채웁니다.",
            anchor="w", font=theme.FONT_CAPTION, text_color=theme.PRIMARY, wraplength=620,
        ).pack(fill="x", pady=(4, 0))

    def _save_trade(self) -> None:
        cfg = self.config.data.setdefault("trade", {})
        cfg["league"] = self.trade_league_var.get().strip()
        cfg["realm"] = "kakao" if self.trade_realm_var.get().startswith("한국") else "official"
        cfg["auto_search"] = self.trade_auto_var.get()
        try:
            cfg["font_size"] = max(9, min(20, int(self.trade_font_var.get())))
        except ValueError:
            pass  # mid-edit; keep the last good value
        self.config.save()
        # The client caches per realm and holds a loaded stat table; switching
        # realms has to build a new one rather than search Korean mods against
        # the English site.
        self._trade_api = None

    # ---- 설정 탭: 자동 업데이트 -------------------------------------------
    _UPDATE_HELP = (
        "GitHub에 올라온 최신 릴리즈와 현재 버전을 비교합니다.\n\n"
        "• 새 버전이 있으면 변경 내용을 보여주고, '지금 업데이트'를 누르면 내려받아 "
        "설치한 뒤 프로그램을 자동으로 다시 시작합니다.\n"
        "• 설치 전에 config.json을 따로 복사해 두므로 설정은 그대로 유지됩니다.\n"
        "• 실행 중인 프로그램 파일은 스스로 교체할 수 없기 때문에, 종료 직후 교체 후 "
        "다시 실행하는 작은 스크립트가 대신 처리합니다. 잠시 창이 닫혔다가 다시 뜹니다.\n"
        "• '이 버전 건너뛰기'를 누른 버전은 다시 알리지 않습니다. 그 다음 버전부터 "
        "다시 알림이 옵니다."
    )

    def _build_update_section(self, parent: ctk.CTkFrame) -> None:
        title_row, _label = labelled_row(
            parent, "자동 업데이트", self._UPDATE_HELP, font=theme.FONT_SECTION
        )
        title_row.pack(fill="x", pady=(18, 2))

        cfg = self.config.data["update"]

        info_row = ctk.CTkFrame(parent, fg_color="transparent")
        info_row.pack(fill="x", padx=(24, 0), pady=(2, 4))
        ctk.CTkLabel(
            info_row, text=f"현재 버전  {version.display()}", anchor="w",
            font=theme.FONT_BODY_STRONG,
        ).pack(side="left")
        self.update_check_button = ctk.CTkButton(
            info_row, text="지금 확인", width=90, command=self.check_for_updates
        )
        self.update_check_button.pack(side="right")
        release_button = ctk.CTkButton(
            info_row, text="릴리즈 페이지", width=100, fg_color=theme.PRIMARY,
            hover_color=theme.PRIMARY_FOCUS, command=updater.open_release_page,
        )
        release_button.pack(side="right", padx=(0, 8))
        Tooltip(release_button, f"{version.RELEASES_URL} 을(를) 브라우저에서 엽니다.")

        self.update_status = ctk.CTkLabel(
            parent, text="", anchor="w", justify="left", font=theme.FONT_CAPTION,
            text_color=theme.PRIMARY, wraplength=620,
        )
        self.update_status.pack(fill="x", padx=(24, 0))
        self._set_update_status(self._last_checked_text())

        self.update_on_start_var = ctk.BooleanVar(value=cfg.get("check_on_start", True))
        ctk.CTkCheckBox(
            parent, text="프로그램 시작할 때 새 버전 확인",
            variable=self.update_on_start_var, command=self._save_update_settings,
        ).pack(anchor="w", padx=(24, 0), pady=(6, 0))

        self.update_auto_var = ctk.BooleanVar(value=cfg.get("auto_install", False))
        auto_cb = ctk.CTkCheckBox(
            parent, text="새 버전을 묻지 않고 바로 내려받아 설치 (설치 후 자동 재시작)",
            variable=self.update_auto_var, command=self._save_update_settings,
        )
        auto_cb.pack(anchor="w", padx=(24, 0), pady=(4, 0))
        Tooltip(
            auto_cb,
            "켜 두면 시작할 때 새 버전을 찾는 즉시 내려받아 설치하고 프로그램을 다시 "
            "시작합니다.\n\n게임 중에 창이 닫혔다 열리는 것이 곤란하다면 꺼 두세요 — "
            "꺼져 있으면 변경 내용을 먼저 보여주고 물어봅니다.",
        )

        if not updater.is_frozen():
            ctk.CTkLabel(
                parent,
                text="※ 지금은 소스에서 실행 중이라 자동 설치는 동작하지 않습니다 "
                     "(확인과 알림은 정상 동작).",
                anchor="w", justify="left", font=theme.FONT_SMALL,
                text_color=theme.PRIMARY_ON_DARK, wraplength=620,
            ).pack(fill="x", padx=(24, 0), pady=(6, 0))

        skipped = cfg.get("skipped_version", "")
        if skipped:
            skip_row = ctk.CTkFrame(parent, fg_color="transparent")
            skip_row.pack(fill="x", padx=(24, 0), pady=(6, 0))
            ctk.CTkLabel(
                skip_row, text=f"건너뛴 버전: {version.display(skipped)}",
                font=theme.FONT_CAPTION, text_color=theme.PRIMARY_ON_DARK,
            ).pack(side="left")
            ctk.CTkButton(
                skip_row, text="다시 알림", width=80, height=22, font=theme.FONT_CAPTION,
                command=self._clear_skipped_version,
            ).pack(side="left", padx=8)

    def _last_checked_text(self) -> str:
        stamp = self.config.data["update"].get("last_checked", "")
        return f"마지막 확인: {stamp}" if stamp else "아직 확인한 적이 없습니다."

    def _set_update_status(self, text: str, danger: bool = False) -> None:
        label = getattr(self, "update_status", None)
        if label is not None and label.winfo_exists():
            label.configure(text=text, text_color=theme.DANGER if danger else theme.PRIMARY)

    def _save_update_settings(self) -> None:
        self.config.data["update"]["check_on_start"] = self.update_on_start_var.get()
        self.config.data["update"]["auto_install"] = self.update_auto_var.get()
        self.config.save()

    def _clear_skipped_version(self) -> None:
        self.config.data["update"]["skipped_version"] = ""
        self.config.save()
        toast.show("건너뛴 버전을 해제했습니다. 다음 확인부터 다시 알립니다.")

    # ---- update check -----------------------------------------------------
    def check_for_updates_on_start(self) -> None:
        """Startup check: silent unless there is something to report."""
        if not self.config.data["update"].get("check_on_start", True):
            return
        self.check_for_updates(silent=True)

    def check_for_updates(self, silent: bool = False) -> None:
        """Ask GitHub, on a worker thread.

        ``silent`` suppresses "you are up to date" and network errors -- at
        startup those are noise, but when the user pressed 지금 확인 they are
        the answer to a question they just asked.
        """
        button = getattr(self, "update_check_button", None)
        if button is not None and button.winfo_exists():
            button.configure(state="disabled", text="확인 중…")
        if not silent:
            self._set_update_status("새 버전을 확인하는 중…")

        def work() -> None:
            try:
                release = updater.check()
                self.after(0, self._on_check_done, release, None, silent)
            except updater.UpdateError as exc:
                self.after(0, self._on_check_done, None, str(exc), silent)
            except Exception as exc:  # noqa: BLE001 - never kill the thread silently
                logger.exception("update check failed")
                self.after(0, self._on_check_done, None, f"확인 실패: {exc}", silent)

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _on_check_done(self, release, error: str | None, silent: bool) -> None:
        button = getattr(self, "update_check_button", None)
        if button is not None and button.winfo_exists():
            button.configure(state="normal", text="지금 확인")

        self.config.data["update"]["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.config.save()

        if error is not None:
            self._set_update_status(error, danger=True)
            if not silent:
                toast.show(error)
            return

        if release is None:
            self._set_update_status(f"최신 버전입니다. ({version.display()})")
            if not silent:
                toast.show(f"이미 최신 버전입니다. {version.display()}")
            return

        self._set_update_status(f"새 버전 {release.label} 이(가) 있습니다.")

        # A skipped version stays skipped for the automatic check only; asking
        # explicitly means you want to see it again.
        if silent and release.version == self.config.data["update"].get("skipped_version"):
            logger.debug("update %s skipped by user", release.version)
            return

        if silent and self.config.data["update"].get("auto_install", False):
            dialog = UpdateDialog(self, self.config, release)
            dialog.start_update()
            return
        UpdateDialog(self, self.config, release)

    # How long the clean shutdown gets before the process is ended outright.
    _UPDATE_EXIT_GRACE_SEC = 1.5

    def finish_update_and_restart(self) -> None:
        """Shut down so the waiting swap script can replace the exe.

        Called by the update dialog once the installer is staged. The script
        is already polling for this process to let go of its own executable,
        so exiting is not optional here -- if it does not happen the update
        silently never occurs while a hidden script retries for half a minute
        and then gives up.

        And it can fail to happen: pystray starts its tray thread *without*
        ``daemon=True`` (pystray/_win32.py), so a tray icon that does not come
        down cleanly leaves the interpreter waiting on that thread after the
        Tk loop has ended -- a process with no window that never exits. The
        timer below makes that impossible. It is a real thread rather than
        ``after()`` because the event loop is about to stop.
        """
        self.config.save()
        threading.Timer(self._UPDATE_EXIT_GRACE_SEC, lambda: os._exit(0)).start()
        if self.on_request_exit is not None:
            self.on_request_exit()
        else:
            self.destroy()

    _ZONE_HELP = (
        "게임이 로그 파일에 기록하는 지역 이름을 읽어 지금 있는 곳을 판별합니다.\n\n"
        "• 화면을 인식하는 방식이 아니라서 해상도·감마·UI 설정에 영향을 받지 않습니다.\n"
        "• 지역을 옮기면 1초 안에 반영됩니다.\n"
        "• 매크로 정지는 '은신처'에서만 적용됩니다. 이름이 '은신처'로 끝나면 모두 "
        "인식되므로 목록을 따로 관리할 필요가 없습니다.\n"
        "• 마을·사냥터는 표시만 할 뿐 동작에는 영향을 주지 않습니다.\n"
        "• 지역을 알 수 없을 때는 사냥터로 간주해 단축키를 평소대로 동작시킵니다."
    )

    def _build_zone_section(self, parent: ctk.CTkFrame) -> None:
        title_row, _label = labelled_row(
            parent, "현재 지역 감지 (마을/사냥터 구분)", self._ZONE_HELP,
            font=theme.FONT_SECTION,
        )
        title_row.pack(fill="x")

        cfg = self.config.data["zone"]

        self.zone_status = ctk.CTkLabel(
            parent, text="", anchor="w", justify="left",
            font=theme.FONT_CAPTION, text_color=theme.PRIMARY, wraplength=640,
        )
        self.zone_status.pack(fill="x", padx=(24, 0), pady=(2, 4))

        self.zone_enabled_var = ctk.BooleanVar(value=cfg.get("enabled", True))
        ctk.CTkCheckBox(
            parent, text="지역 감지 사용", variable=self.zone_enabled_var,
            command=self._save_zone,
        ).pack(anchor="w", padx=(24, 0))

        self.zone_pause_var = ctk.BooleanVar(value=cfg.get("pause_macros_in_hideout", True))
        pause_cb = ctk.CTkCheckBox(
            parent, text="은신처에서는 물약 매크로 / 연속사용키 자동 정지",
            variable=self.zone_pause_var, command=self._save_zone,
        )
        pause_cb.pack(anchor="w", padx=(24, 0), pady=(4, 0))
        Tooltip(
            pause_cb,
            "은신처에 있는 동안 물약 매크로와 연속사용키가 키를 보내지 않습니다. "
            "연속사용키 토글은 켜진 채로 유지되므로, 은신처를 나가면 다시 자동으로 동작합니다.\n\n"
            "마을은 해당되지 않습니다 — 마을은 사냥 도중 잠깐 들르는 곳이라 거기서 물약이 "
            "멈추면 그것대로 당황스럽기 때문입니다.",
        )

        self.zone_overlay_var = ctk.BooleanVar(value=cfg.get("hide_overlay_in_hideout", True))
        overlay_cb = ctk.CTkCheckBox(
            parent, text="은신처에서는 쿨다운 막대 표시 안 함 (채팅 입력 오작동 방지)",
            variable=self.zone_overlay_var, command=self._save_zone,
        )
        overlay_cb.pack(anchor="w", padx=(24, 0), pady=(4, 0))
        Tooltip(
            overlay_cb,
            "은신처에서 채팅으로 '12345'나 'qwert'를 치면 그 키들이 물약/스킬 사용으로 "
            "인식되어 쿨다운 막대가 떠버립니다. 이 옵션을 켜두면 은신처에서는 막대가 "
            "뜨지 않습니다.",
        )

        self.zone_route_var = ctk.BooleanVar(value=cfg.get("route_follow", True))
        route_cb = ctk.CTkCheckBox(
            parent, text="레벨업 루트를 현재 지역에 맞춰 자동 이동",
            variable=self.zone_route_var, command=self._save_zone,
        )
        route_cb.pack(anchor="w", padx=(24, 0), pady=(4, 0))
        Tooltip(
            route_cb,
            "지역을 옮기면 레벨업 루트 창이 그 지역을 언급한 줄로 알아서 넘어갑니다.\n\n"
            "보조 기능일 뿐이라 ◀ ▶ 버튼·액트 선택·검색은 그대로 쓸 수 있고, 루트 창의 "
            "'자동' 체크를 끄면 완전히 수동으로만 동작합니다. 지역 이름이 적혀 있지 않은 "
            "줄(보스 처치, 젬 수령 등)은 직접 넘기셔야 합니다.",
        )

        path_row = ctk.CTkFrame(parent, fg_color="transparent")
        path_row.pack(fill="x", padx=(24, 0), pady=(8, 0))
        HelpMark(
            path_row,
            "비워두면 실행 중인 게임의 설치 경로에서 자동으로 찾습니다. "
            "자동 탐지가 실패할 때만 직접 지정하세요 (예: C:\\Daum Games\\POE\\logs\\KakaoClient.txt).",
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(path_row, text="로그 경로:", font=theme.FONT_CAPTION).pack(side="left")
        self.zone_path_var = ctk.StringVar(value=cfg.get("log_path", ""))
        ctk.CTkEntry(
            path_row, textvariable=self.zone_path_var, width=300,
            placeholder_text="비워두면 자동 탐지",
        ).pack(side="left", padx=6)
        ctk.CTkButton(path_row, text="찾아보기", width=70, command=self._browse_zone_log).pack(
            side="left", padx=(0, 4)
        )
        ctk.CTkButton(path_row, text="자동", width=50, command=self._autodetect_zone_log).pack(
            side="left"
        )
        self.zone_path_var.trace_add("write", lambda *_: self._save_zone())

        self._refresh_zone_status()

    def _refresh_zone_status(self) -> None:
        """Poll the tracker so the label follows the character around."""
        label = getattr(self, "zone_status", None)
        if label is None or not label.winfo_exists():
            return
        tracker = zone.get_tracker()
        if tracker is None:
            label.configure(text="현재 지역: (감지 기능이 실행되지 않았습니다)")
        else:
            path = tracker.log_path.name if tracker.log_path else "로그 미확인"
            label.configure(text=f"현재 지역: {tracker.describe()}      [{path}]")
        # 1s: this only drives a text label, and the zone itself cannot change
        # faster than a loading screen.
        self.after(1000, self._refresh_zone_status)

    def _save_zone(self) -> None:
        cfg = self.config.data["zone"]
        cfg["enabled"] = self.zone_enabled_var.get()
        cfg["pause_macros_in_hideout"] = self.zone_pause_var.get()
        cfg["hide_overlay_in_hideout"] = self.zone_overlay_var.get()
        cfg["route_follow"] = self.zone_route_var.get()
        cfg["log_path"] = self.zone_path_var.get().strip()
        self.config.save()

    def _browse_zone_log(self) -> None:
        chosen = filedialog.askopenfilename(
            title="클라이언트 로그 선택", filetypes=[("로그 파일", "*.txt"), ("모든 파일", "*.*")]
        )
        if chosen:
            self.zone_path_var.set(chosen)

    def _autodetect_zone_log(self) -> None:
        found = zone.resolve_log_path(
            "", self.config.data["misc"].get("poe_window_class", "POEWindowClass")
        )
        if found:
            self.zone_path_var.set("")  # blank = keep auto-detecting each run
            toast.show(f"로그를 찾았습니다:\n{found}")
        else:
            toast.show("로그를 찾지 못했습니다.\n게임을 실행한 뒤 다시 시도하거나 직접 지정해주세요.")

    # ---- 귓속말 알림 패널 ----------------------------------------------------
    def whisper_panel(self, create: bool = True):
        existing = self._whisper_panel
        if existing is not None and existing.winfo_exists():
            return existing
        if not create:
            return None
        from .whisper_panel import WhisperPanel

        self._whisper_panel = WhisperPanel(
            self, self.config, on_open_settings=lambda: self._show_tab("설정")
        )
        return self._whisper_panel

    def toggle_whisper_panel(self) -> None:
        existing = self._whisper_panel
        if existing is not None and existing.winfo_exists():
            existing.close()
            self._whisper_panel = None
            return
        self.whisper_panel()

    def open_whisper_panel_on_start(self) -> None:
        cfg = self.config.data.get("whisper", {})
        if cfg.get("enabled", True) and cfg.get("open_on_start", True):
            self.whisper_panel()

    def place_whisper_panel(self) -> None:
        """Open the panel pinned open so it can be dragged into place."""
        panel = self.whisper_panel()
        if panel is not None:
            panel.begin_placing()

    def test_whisper(self) -> None:
        """Push a sample whisper through the panel.

        Whispers are not something you can arrange on demand, so without this
        there is no way to see where the panel sits or whether it works.
        """
        from .whisper_panel import _sample, _sample_plain

        panel = self.whisper_panel()
        if panel is not None:
            # One of each: a buy whisper and an ordinary one, since they draw
            # quite differently and both are worth seeing before trusting it.
            panel.add(_sample_plain())
            panel.add(_sample())

    # ---- 아이템 가격 확인 ---------------------------------------------------
    def trade_api(self):
        """One API client for the app, built on first use.

        Not in __init__: constructing it is cheap but the first call loads a
        multi-megabyte stat table, and startup is already the slowest part of
        this app. Someone who never price-checks never pays for it.
        """
        if self._trade_api is None:
            from ..trade.api import TradeAPI

            self._trade_api = TradeAPI(self.config)
        return self._trade_api

    def open_price_check(self, clipboard_text: str) -> None:
        from ..trade import item as trade_item
        from .price_window import PriceCheckWindow

        parsed = trade_item.parse(clipboard_text)
        if parsed is None:
            toast.show("아이템 정보를 읽지 못했습니다.\n아이템 위에서 다시 시도해주세요.")
            return

        # One window, reused: price checking is a rapid-fire activity (hover,
        # press, glance, move on) and leaving a trail of windows behind would
        # bury the game within a dozen items.
        existing = self._price_window
        if existing is not None and existing.winfo_exists():
            existing.close()
        self._price_window = PriceCheckWindow(self, self.config, self.trade_api(), parsed)
        self._ensure_league()

    def _ensure_league(self) -> None:
        """Fill in a blank league setting from the site's own list.

        Done in the background on first use so a new league costs the user no
        settings change: the temporary league is the first entry the site
        returns that is not a permanent one.
        """
        trade = self.config.data.setdefault("trade", {})
        if trade.get("league"):
            return

        def resolve() -> None:
            try:
                leagues = self.trade_api().leagues()
            except Exception:
                logger.debug("could not resolve the current league", exc_info=True)
                return
            permanent = {"Standard", "Hardcore", "Ruthless", "Hardcore Ruthless"}
            for entry in leagues:
                name = entry.get("id", "")
                if name and name not in permanent and "SSF" not in name:
                    trade["league"] = name
                    self.after(0, self.config.save)
                    logger.info("trade league resolved: %s", name)
                    return

        threading.Thread(target=resolve, name="league-lookup", daemon=True).start()

    def _open_route(self, mode: str) -> None:
        existing = self._route_windows.get(mode)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        window = RouteWindow(self, self.config, mode)
        self._route_windows[mode] = window

    def _open_layout(self, mode: str) -> None:
        """Toggle the map-layout overlay for *mode*.

        Toggling rather than lifting (as _open_route does): the overlay has no
        titlebar and is deliberately invisible-until-hovered, so the button
        that opened it is the reliable way to get rid of it again.
        """
        existing = self._layout_windows.get(mode)
        if existing is not None and existing.winfo_exists():
            existing.close()
            self._layout_windows.pop(mode, None)
            return
        if not map_layout.index():
            toast.show(
                "맵 레이아웃 이미지를 찾지 못했습니다.\n"
                f"'{map_layout.layout_dir()}' 폴더를 확인해주세요."
            )
            return
        self._layout_windows[mode] = LayoutWindow(self, self.config, mode)

    def _build_poe_launch_row(
        self, parent: ctk.CTkFrame, label: str, config_key: str, pady: tuple[int, int]
    ) -> ctk.StringVar:
        """One editable launch URL/path + its run button. POE1 and POE2 are
        separate entries because the Korean editions are published by
        different portals (Daum vs Kakao Games) with unrelated URLs."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=pady)
        var = ctk.StringVar(value=self.config.data["misc"].get(config_key, ""))
        ctk.CTkEntry(row, textvariable=var, width=300).pack(side="left", padx=(0, 6))
        var.trace_add("write", lambda *_: self._save_poe_url(config_key, var))
        ctk.CTkButton(
            row, text=label, width=80, command=lambda: self.actions.launch_path(var.get())
        ).pack(side="left")
        return var

    def _save_poe_url(self, config_key: str, var: ctk.StringVar) -> None:
        self.config.data["misc"][config_key] = var.get()
        self.config.save()

    def _browse_path(self, var: ctk.StringVar) -> None:
        chosen = filedialog.askopenfilename()
        if chosen:
            var.set(chosen)

    def _save_paths(self) -> None:
        self.config.data["paths"] = [v.get() for v in self.path_vars]
        self.config.save()

    def _export_config(self) -> None:
        target = filedialog.asksaveasfilename(defaultextension=".json", initialfile="poehelper_backup.json")
        if target:
            from pathlib import Path

            self.config.export_to(Path(target))
            toast.show("설정을 내보냈습니다.")

    def _import_config(self) -> None:
        source = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if source:
            from pathlib import Path

            self.config.import_from(Path(source))
            toast.show("설정을 가져왔습니다. 앱을 재시작하면 완전히 반영됩니다.")

    # ---- shared: per-tab explicit save button ------------------------------
    def _build_save_row(self, tab: ctk.CTkFrame, on_save: Callable[[], None] | None = None) -> None:
        """Every tab gets its own visible 저장 button so the user has a
        definite confirmation the current settings were written to disk,
        instead of only trusting the silent autosave-on-change already
        wired into each field."""
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=8, pady=(4, 8))

        def do_save() -> None:
            if on_save:
                on_save()
            else:
                self.config.save()
            toast.show("저장되었습니다.")

        ctk.CTkButton(row, text="저장", width=90, command=do_save).pack(side="right")

    # ---- bottom bar / window chrome ----------------------------------------
    def _build_bottom_bar(self) -> None:
        """단축키 정지 / 재시작 / 종료.

        Restart used to be a hotkey (F11). Nothing else in this app is
        destructive on a single keypress, and a mis-hit during a map wiped
        every overlay and hold-state mid-fight -- so it lives here, where it
        takes a deliberate click.
        """
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=8)
        ctk.CTkButton(bar, text="트레이로 최소화", command=self._on_minimize).pack(side="left")

        ctk.CTkButton(
            bar, text="종료", width=90,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER, command=self._on_exit,
        ).pack(side="right")

        restart_btn = ctk.CTkButton(bar, text="재시작", width=90, command=self._on_restart)
        restart_btn.pack(side="right", padx=(0, 8))
        Tooltip(
            restart_btn,
            "현재 설정을 저장한 뒤 앱을 즉시 다시 시작합니다. 단축키가 먹통이 되는 등 "
            "문제가 생겼을 때 사용하세요.",
        )

        self.suspend_btn = ctk.CTkButton(
            bar, text="단축키 정지", width=110,
            fg_color=theme.SURFACE_LIGHT_2, hover_color=theme.HAIRLINE_LIGHT, text_color=theme.INK,
            command=self._toggle_hotkey_suspend,
        )
        self.suspend_btn.pack(side="right", padx=(0, 8))
        Tooltip(
            self.suspend_btn,
            "모든 단축키/홀드키 감지를 잠시 켜고 끕니다. 다른 창에서 타이핑할 때처럼 단축키가 잠깐 방해되면 안 될 때 사용하세요.",
        )

    def _on_restart(self) -> None:
        if not messagebox.askokcancel("재시작", "앱을 다시 시작할까요?\n현재 설정은 저장됩니다.", parent=self):
            return
        self.config.save()
        self.actions.restart_app()

    def _toggle_hotkey_suspend(self) -> None:
        paused = hotkey_suspend.toggle()
        if paused:
            self.suspend_btn.configure(text="단축키 재개", fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER, text_color=theme.ON_PRIMARY)
            toast.show("단축키 입력 감지를 정지했습니다.")
        else:
            self.suspend_btn.configure(
                text="단축키 정지", fg_color=theme.SURFACE_LIGHT_2, hover_color=theme.HAIRLINE_LIGHT, text_color=theme.INK
            )
            toast.show("단축키 입력 감지를 재개했습니다.")

    def _on_minimize(self) -> None:
        self.config.save()
        self.withdraw()
        if self.on_request_minimize:
            self.on_request_minimize()

    def close_child_windows(self) -> None:
        """Shut every window this one opened, in an orderly way.

        destroy() on the root would take them down regardless, but each of
        these has something to do first -- saving its geometry, unsubscribing
        from the whisper feed or the zone tracker -- and a listener bound to a
        destroyed widget keeps firing for the rest of the session.
        """
        panel = self._whisper_panel
        if panel is not None and panel.winfo_exists():
            panel.close()
        self._whisper_panel = None

        price = self._price_window
        if price is not None and price.winfo_exists():
            price.close()
        self._price_window = None

        for group in (self._route_windows, self._layout_windows, self._detect_windows):
            for window in list(group.values()):
                try:
                    if window.winfo_exists():
                        closer = getattr(window, "close", window.destroy)
                        closer()
                except tk.TclError:
                    pass
            group.clear()

    def _on_exit(self) -> None:
        self.config.save()
        self.close_child_windows()
        if self.on_request_exit:
            self.on_request_exit()

    def _on_configure(self, _event: tk.Event) -> None:
        pos = self.config.data["windows"]["main"]
        pos["x"], pos["y"] = self.winfo_x(), self.winfo_y()

    def show_from_tray(self) -> None:
        # Same reveal-once-painted trick as startup: withdraw() unmaps every
        # canvas in the window, so deiconify() has to lay out and repaint
        # the whole current tab again. Doing that behind alpha 0 and only
        # then revealing keeps the window from showing itself mid-repaint.
        self.attributes("-alpha", 0.0)
        self.deiconify()
        self.update()
        self.attributes("-alpha", 1.0)
        self.lift()

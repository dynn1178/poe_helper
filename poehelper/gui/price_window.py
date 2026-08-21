"""The price-check window: what the trade site says an item is worth.

Opened by a hotkey pressed over an item in game, and closed a few seconds
later by Esc, by space, or simply by clicking somewhere else -- back into
the game, most of the time. Everything about it is shaped by that: it comes
up already holding an answer rather than a form to fill in, and the controls
exist to adjust a search that missed rather than to build one from scratch.

What identifies an item depends on what it is:

* currency and cards -- the printed name is the site's *type*, and there is
  nothing else to go on. (Sending it as "name" is why currency never found
  anything: on the trade site only uniques have a name.)
* uniques -- the name says which unique, the base pins the variant, and any
  ticked mods narrow it to copies that rolled as well as this one.
* everything else -- the name is random, so the base type plus the ticked
  mods are all there is.

Searching happens on 다시 검색, never on a checkbox. Every tick used to fire
a request, and a few seconds of adjusting could put a dozen searches at a
rate-limited API; the first search runs automatically and after that the
button is the trigger.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timezone

import customtkinter as ctk

from ..config import Config
from ..trade import query as querymod
from ..trade.api import TradeError
from ..trade.item import ItemMod, ParsedItem
from . import theme

logger = logging.getLogger(__name__)

_RARITY_COLOR = {
    "rare": "#f0e64c", "unique": "#cf863c", "magic": "#8888ff",
    "currency": "#d4b98a", "gem": "#71bfb6", "card": "#d8d8dd", "normal": "#e8e8ea",
}

_KIND_LABEL = {
    "implicit": "고정", "explicit": "", "crafted": "제작",
    "fractured": "분열", "enchant": "인챈트",
}

_DEFAULT_FONT_PT = 13
_MIN_FONT_PT, _MAX_FONT_PT = 9, 20

# One press of 범위 넓히기/좁히기 moves each bound by this share of the mod's
# *best possible* roll -- not of the rolled value. A mod that can reach 200
# and one that can reach 3 need very different absolute steps, and the
# achievable maximum is what puts them on a common scale. Mods whose range
# the game did not print are skipped: without it there is nothing to take a
# percentage of.
_RANGE_STEP = 0.05

# The site's own status options (see /api/trade/data/filters). "available"
# is the default because a buyout-only search hides everything listed for
# direct trade -- which is most currency, and why currency looked unsearchable.
STATUS_OPTIONS = [
    ("available", "즉시 구입 및 직접 거래"),
    ("securable", "즉시 구입만"),
    ("online", "접속 중 (직접 거래)"),
    ("any", "모두"),
]

# The site offers no five-day bucket, so these are its real options rather
# than an approximation presented as one.
INDEXED_OPTIONS = [
    ("", "언제든"), ("1day", "1일 이내"), ("3days", "3일 이내"),
    ("1week", "1주일 이내"), ("2weeks", "2주일 이내"), ("1month", "1달 이내"),
]

# Item state the user may want pinned. Ids and labels come from the site's
# misc_filters, so "삿된" is mutated because that is what the site calls it.
TRISTATE_FILTERS = [
    ("corrupted", "타락"),
    ("mutated", "삿된"),
    ("fractured_item", "분열"),
    ("synthesised_item", "결합"),
    ("mirrored", "복제"),
    ("crucible_item", "시련"),
]
TRISTATE_CHOICES = [("", "모두"), ("true", "예"), ("false", "아니요")]

SORT_OPTIONS = [("indexed", "최근 등록순"), ("price", "가격 낮은순")]

# ---------------------------------------------------------------------------
# "click anywhere else and it goes away"
# ---------------------------------------------------------------------------
# Asked of Windows on a timer rather than answered from Tk's own <FocusOut>.
# This window is override-redirect (no titlebar), and such a window cannot
# hold focus reliably on Windows -- the dropdown popup in
# widgets/hotkey_picker.py hit exactly that and had to abandon <FocusOut> for
# the same reason. On top of that the click that dismisses this window is
# usually a click *into the game*, which is a different process entirely and
# never reaches our event loop at all.
#
# 110ms: fast enough that the window is gone before the click it reacts to
# has finished registering in game, cheap enough to run for the seconds this
# window is on screen (two GetAsyncKeyState calls and, only on a fresh press,
# one WindowFromPoint).
_OUTSIDE_POLL_MS = 110
_VK_LBUTTON, _VK_RBUTTON = 0x01, 0x02
_DOWN_BIT = 0x8000

if sys.platform == "win32":
    _async_key_state = ctypes.windll.user32.GetAsyncKeyState
    _async_key_state.argtypes = [ctypes.c_int]
    _async_key_state.restype = ctypes.c_short
else:  # tests only
    _async_key_state = None


def _mouse_button_down() -> bool:
    if _async_key_state is None:
        return False
    return any(
        _async_key_state(vk) & _DOWN_BIT for vk in (_VK_LBUTTON, _VK_RBUTTON)
    )


def _point_is_ours(x: int, y: int) -> bool:
    """Does the window under (x, y) belong to this process?

    Not "is it inside the price window": CustomTkinter's option menus drop
    their list into a separate popup window that extends past this window's
    edges, and treating a click on one of those as "outside" would close the
    window in the middle of choosing a filter. Every such popup is ours, so
    ownership -- rather than geometry -- is the question worth asking.
    """
    try:
        import win32gui
        import win32process

        hwnd = win32gui.WindowFromPoint((x, y))
        if not hwnd:
            return False
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid == os.getpid()
    except Exception:
        # Better to leave the window open than to close it on a guess.
        logger.debug("could not identify the window under the cursor", exc_info=True)
        return True


class _ModRow:
    """One mod: whether it is searched, and over what range.

    The lower bound starts at the value on the item, so the opening search is
    "this or better". The upper box starts empty: capping it at your own roll
    would hide every item better than yours, which is usually the thing you
    most want the price of.
    """

    def __init__(self, mod: ItemMod, checked: bool, current: float | None):
        self.mod = mod
        self.current = current
        self.initial_checked = checked
        self.enabled = ctk.BooleanVar(value=checked)
        self.minimum = ctk.StringVar(value=_fmt(current))
        self.maximum = ctk.StringVar(value="")

    @property
    def span(self) -> float | None:
        """The best this mod can roll, averaged over its numbers."""
        if not self.mod.ranges:
            return None
        highs = [high for _low, high in self.mod.ranges]
        return sum(highs) / len(highs)

    def nudge(self, direction: int) -> bool:
        """Move both bounds by 5% of the achievable maximum.

        Returns whether anything moved, so the window can say when a mod was
        skipped for having no printed range.
        """
        span = self.span
        if span is None:
            return False
        delta = span * _RANGE_STEP * direction
        moved = False
        for var in (self.minimum, self.maximum):
            value = _parse_number(var.get())
            if value is None:
                continue
            var.set(_fmt(_round(max(0.0, value + delta))))
            moved = True
        return moved

    def reset(self) -> None:
        """Back to how the window opened -- ticked as it was, bounds at the
        roll on the item, no upper limit."""
        self.enabled.set(self.initial_checked)
        self.minimum.set(_fmt(self.current))
        self.maximum.set("")

    def selection(self) -> querymod.Selection | None:
        if not self.enabled.get():
            return None
        return querymod.Selection(
            self.mod, _parse_number(self.minimum.get()), _parse_number(self.maximum.get())
        )


class PriceCheckWindow(ctk.CTkToplevel):
    def __init__(self, master, config: Config, api, item: ParsedItem):
        super().__init__(master)
        self.app_config = config
        self.api = api
        self.item = item
        self.geo = config.data["windows"].setdefault(
            "price_check", {"x": 300, "y": 140, "w": 600, "h": 820}
        )
        self._search_id = ""
        self._busy = False
        self._closed = False
        self._answered = False
        self._status_option = "available"
        self._rows: list[_ModRow] = []
        self._drag_origin: tuple[int, int] | None = None
        self._resize_origin: tuple[int, int, int, int] | None = None
        self._pending_move: tuple[int, int] | None = None
        self._move_scheduled = False
        # Seeded from the real button state, not from False: the window opens
        # on a hotkey that may well be pressed with a mouse button already
        # held, and starting at False would read that hold as a fresh click
        # and close the window before it had finished appearing.
        self._mouse_was_down = _mouse_button_down()
        self._outside_watch: str | None = None

        size = self.font_size
        self.font = (theme.FONT_FAMILY, size)
        self.font_small = (theme.FONT_FAMILY, max(_MIN_FONT_PT, size - 1))
        self.font_bold = (theme.FONT_FAMILY, size + 2, "bold")

        self.title("가격 확인")
        self.geometry(
            f"{max(560, int(self.geo.get('w', 600)))}x{max(460, int(self.geo.get('h', 820)))}"
            f"+{int(self.geo.get('x', 300))}+{int(self.geo.get('y', 180))}"
        )
        # No OS titlebar: this is a panel over a game, and the grey Windows
        # chrome made it read as a stray dialog. Drag and close are rebuilt
        # on the header.
        self.overrideredirect(True)
        self.configure(fg_color=theme.OVERLAY_BG)
        self.attributes("-topmost", True)

        self.card = ctk.CTkFrame(
            self, corner_radius=14, fg_color=theme.OVERLAY_BG,
            border_width=1, border_color=theme.OVERLAY_BORDER,
        )
        self.card.pack(fill="both", expand=True, padx=2, pady=2)

        self._build()
        self._build_grip()
        self.after(60, self._take_focus)
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<space>", self._on_space)
        self.bind("<Return>", lambda _e: self.search())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Configure>", self._on_configure)
        self._outside_watch = self.after(_OUTSIDE_POLL_MS, self._watch_outside_click)

        if config.data.get("trade", {}).get("auto_search", True):
            self.after(80, self.search)

    @property
    def font_size(self) -> int:
        raw = self.app_config.data.get("trade", {}).get("font_size", _DEFAULT_FONT_PT)
        try:
            return max(_MIN_FONT_PT, min(_MAX_FONT_PT, int(raw)))
        except (TypeError, ValueError):
            return _DEFAULT_FONT_PT

    # ---- widgets ----------------------------------------------------------
    def _build(self) -> None:
        self._build_header()
        if self.item.searchable_mods:
            self._build_mods()
        else:
            ctk.CTkLabel(
                self.card, text="이 아이템은 이름으로 가격을 찾습니다.", font=self.font_small,
                anchor="w", text_color=theme.OVERLAY_TEXT_DIM,
            ).pack(fill="x", padx=14, pady=(10, 0))
        self._build_filters()
        self._build_actions()
        self._build_results()
        self._build_results_list()

    def _build_header(self) -> None:
        head = ctk.CTkFrame(self.card, fg_color=theme.OVERLAY_SURFACE, corner_radius=12)
        head.pack(fill="x", padx=3, pady=(3, 0))

        top = ctk.CTkFrame(head, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(9, 0))
        ctk.CTkButton(
            top, text="×", width=24, height=24, font=self.font_bold,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER, command=self.close,
        ).pack(side="right")
        title = ctk.CTkLabel(
            top, text=self.item.name or self.item.base, font=self.font_bold, anchor="w",
            text_color=_RARITY_COLOR.get(self.item.rarity, theme.OVERLAY_TEXT),
        )
        title.pack(side="left", fill="x", expand=True)
        for widget in (head, top, title):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)

        bits = [self.item.base] if self.item.name else []
        if self.item.item_level:
            bits.append(f"아이템 레벨 {self.item.item_level}")
        if self.item.sockets:
            bits.append(f"{self.item.links}링크")
        if self.item.quality:
            bits.append(f"퀄리티 {self.item.quality}%")
        ctk.CTkLabel(
            head, text="  ·  ".join(bits), font=self.font_small, anchor="w",
            text_color=theme.OVERLAY_TEXT_DIM,
        ).pack(fill="x", padx=12, pady=(0, 0 if self.item.is_weapon else 9))

        if self.item.is_weapon:
            # A weapon's printed damage range and attack speed are not
            # comparable between weapons; DPS is the number anyone actually
            # price-checks on, so it is computed here rather than left to be
            # done in the player's head.
            breakdown = [
                f"{label} {_pretty(value)}"
                for value, label in (
                    (self.item.physical_dps, "물리"),
                    (self.item.elemental_dps, "원소"),
                    (self.item.chaos_dps, "카오스"),
                )
                if value
            ]
            # Total first and largest, because that is the number being
            # compared; the split is there to explain where it came from.
            text = f"DPS {_pretty(self.item.total_dps)}"
            if len(breakdown) > 1:
                text += "   =   " + "  +  ".join(breakdown)
            ctk.CTkLabel(
                head, text=text, font=self.font, anchor="w", text_color="#ffd75e",
            ).pack(fill="x", padx=12, pady=(2, 9))

    def _build_mods(self) -> None:
        caption = ctk.CTkFrame(self.card, fg_color="transparent")
        caption.pack(fill="x", padx=14, pady=(9, 2))
        ctk.CTkLabel(
            caption, text="검색에 사용할 속성", font=self.font, anchor="w",
            text_color=theme.OVERLAY_TEXT_DIM,
        ).pack(side="left")
        ctk.CTkLabel(
            caption, text="점수", font=self.font_small, width=38, anchor="e",
            text_color=theme.OVERLAY_TEXT_DIM,
        ).pack(side="right", padx=(4, 2))
        for heading in ("최대", "최소"):
            ctk.CTkLabel(
                caption, text=heading, font=self.font_small, width=62,
                text_color=theme.OVERLAY_TEXT_DIM,
            ).pack(side="right", padx=(3, 0))

        box = ctk.CTkScrollableFrame(self.card, fg_color=theme.OVERLAY_SURFACE)
        box.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        # One row per *searchable* stat rather than per { ... } block: a single
        # header can cover two stats the site indexes separately, and shown as
        # one row they were one unusable filter with the second line hidden.
        self.item.mods = querymod.expand_mods(self.api, self.item.mods)

        # Best tier first, and the top three ticked. Tier is the game's own
        # verdict on how good a roll is -- T1 is the best band the mod has --
        # so it beats percentage-through-range for deciding which mods are
        # the ones worth pricing on.
        ranked = sorted(self.item.searchable_mods, key=_tier_rank)
        # Ticked by roll quality, not by tier: what a mod is worth depends on
        # where it landed in its own range, and "T1 at the bottom of its band"
        # is a worse line than "T2 near the top of its own".
        #
        # 고정(implicit) and 제작(crafted) are excluded whatever their score.
        # An implicit is on every copy of the base and a crafted mod can be
        # added to any item, so neither narrows a search to comparable items --
        # they only make it return less.
        scored = [
            m for m in ranked
            if m.kind in ("explicit", "fractured") and m.quality is not None
        ]
        best = sorted(scored, key=lambda m: -m.quality)[:3]
        default_on = {id(m) for m in best}
        if not default_on and not self.item.priced_by_name:
            # No roll ranges at all (advanced mod descriptions off). Fall back
            # to tier so the window still opens with a real search.
            rollable = [m for m in ranked if m.kind in ("explicit", "fractured")]
            default_on = {id(m) for m in sorted(rollable, key=_tier_only)[:3]}
        for mod in ranked:
            self._build_mod_row(box, mod, id(mod) in default_on)

    def _build_mod_row(self, parent, mod: ItemMod, checked: bool) -> None:
        resolved = querymod.resolve(self.api, mod, self.item)
        placeholders = resolved[1] if resolved else 0
        row_data = _ModRow(mod, checked, querymod.current_value(mod, placeholders))
        self._rows.append(row_data)

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=2)

        # Right-hand boxes first, expanding label last: pack hands out space
        # in *packing* order regardless of side, so a long mod name packed
        # first shoves the boxes out of line with every other row.
        # How well this line rolled, right next to the numbers it is about:
        # 38 in a 30-40 range is 80% of the way up its band, and that -- not
        # the raw 38 -- is what says whether the line is worth pricing on.
        quality = mod.quality
        ctk.CTkLabel(
            row, text=f"{quality * 100:.0f}%" if quality is not None else "",
            font=self.font_small, width=38, anchor="e",
            text_color=_quality_colour(quality) if quality is not None else theme.OVERLAY_TEXT_DIM,
        ).pack(side="right", padx=(4, 2))

        # A mod with no number in it ("번개 피해만 줄 수 있음") has nothing to
        # bound, and an editable box there invites a value the site will
        # reject. Disabled rather than absent so the columns stay aligned.
        numeric = row_data.current is not None or bool(mod.values)
        for var in (row_data.maximum, row_data.minimum):
            entry = ctk.CTkEntry(
                row, textvariable=var, width=58, height=26, font=self.font_small,
                justify="center", fg_color=theme.OVERLAY_SURFACE_2,
                border_color=theme.OVERLAY_BORDER, text_color=theme.OVERLAY_TEXT,
            )
            entry.pack(side="right", padx=(3, 0))
            if numeric:
                entry.bind("<Return>", lambda _e: self.search())
            else:
                entry.configure(state="disabled", fg_color=theme.OVERLAY_BG)

        # Tier in front, where it can be read straight down the column,
        # rather than trailing the mod text at a different offset every row.
        tier = f"T{mod.tier}" if mod.tier else ("고정" if mod.kind == "implicit" else "")
        ctk.CTkLabel(
            row, text=tier, font=self.font_small, width=34, anchor="w",
            text_color=_tier_colour(mod.tier),
        ).pack(side="left", padx=(4, 2))

        note = _KIND_LABEL.get(mod.kind, "")
        label = mod.text.splitlines()[0] + (f"  [{note}]" if note else "")
        ctk.CTkCheckBox(
            row, text=label, variable=row_data.enabled,
            font=self.font_small, checkbox_width=16, checkbox_height=16,
            fg_color=theme.OVERLAY_ACCENT, hover_color=theme.OVERLAY_ACCENT_HOVER,
            border_color=theme.OVERLAY_BORDER, text_color=theme.OVERLAY_TEXT,
            checkmark_color=theme.OVERLAY_TEXT,
        ).pack(side="left", fill="x", expand=True)

    def _build_filters(self) -> None:
        """Item state and listing age, chosen rather than inferred."""
        self.filter_vars: dict[str, ctk.StringVar] = {}
        self.num_vars: dict[str, ctk.StringVar] = {}
        wrap = ctk.CTkFrame(self.card, fg_color="transparent")
        wrap.pack(fill="x", padx=12, pady=(0, 4))

        # grid, not pack: six dropdowns and their labels do not fit one row at
        # this width, and pack answered that by silently shaving the labels
        # off until every control read "모두" with no way to tell which was
        # which. A grid wraps them onto rows instead of hiding them.
        for column in range(3):
            wrap.grid_columnconfigure(column * 2 + 1, weight=1)

        row = 0
        for index, (key, label, initial) in enumerate((
            ("links", "링크", str(self.item.links) if self.item.links >= 5 else ""),
            ("quality", "퀄리티", str(self.item.quality) if self.item.quality else ""),
            ("ilvl", "아이템 레벨", ""),
        )):
            var = ctk.StringVar(value=initial)
            self._cell(wrap, row, index, label)
            ctk.CTkEntry(
                wrap, textvariable=var, width=64, height=26, font=self.font_small,
                justify="center", fg_color=theme.OVERLAY_SURFACE_2,
                border_color=theme.OVERLAY_BORDER, text_color=theme.OVERLAY_TEXT,
                placeholder_text="이상",
            ).grid(row=row, column=index * 2 + 1, sticky="w", pady=2)
            self.num_vars[key] = var

        for index, (key, label) in enumerate(TRISTATE_FILTERS):
            row = 1 + index // 3
            initial = "true" if key in self.item.flags else ""
            self.filter_vars[key] = self._option(
                wrap, row, index % 3, label, TRISTATE_CHOICES, initial, 82
            )

        trade = self.app_config.data.get("trade", {})
        self.status_var = self._option(
            wrap, 3, 0, "거래", STATUS_OPTIONS, trade.get("status", "available"), 176
        )
        self.indexed_var = self._option(
            wrap, 3, 2, "등록", INDEXED_OPTIONS, trade.get("indexed", "1week"), 110
        )
        # Taken after every widget exists, so 초기화 restores exactly what the
        # window opened with rather than a second copy of the defaults.
        # A list of pairs, not a dict: tkinter Variables define __eq__ and
        # are therefore unhashable, so they cannot be dict keys.
        self._initial_state = [
            (var, var.get())
            for var in (
                *self.num_vars.values(), *self.filter_vars.values(),
                self.status_var, self.indexed_var,
            )
        ]

    def _cell(self, parent, row: int, index: int, label: str) -> None:
        ctk.CTkLabel(
            parent, text=label, font=self.font_small, anchor="e", width=64,
            text_color=theme.OVERLAY_TEXT_DIM,
        ).grid(row=row, column=index * 2, sticky="e", padx=(0, 5), pady=2)

    def _option(self, parent, row, index, label, choices, initial, width) -> ctk.StringVar:
        self._cell(parent, row, index, label)
        labels = [text for _value, text in choices]
        current = next((t for v, t in choices if v == initial), labels[0])
        var = ctk.StringVar(value=current)
        ctk.CTkOptionMenu(
            parent, variable=var, values=labels, width=width, height=26, font=self.font_small,
            fg_color=theme.OVERLAY_SURFACE_2, button_color=theme.OVERLAY_ACCENT,
            button_hover_color=theme.OVERLAY_ACCENT_HOVER, text_color=theme.OVERLAY_TEXT,
            dropdown_fg_color=theme.OVERLAY_SURFACE, dropdown_hover_color=theme.OVERLAY_SURFACE_2,
            dropdown_text_color=theme.OVERLAY_TEXT, dropdown_font=self.font_small,
        ).grid(row=row, column=index * 2 + 1, sticky="w", pady=2)
        # Stashed on the variable so _chosen can map the shown label back to
        # the id the site expects, without a second dict to keep in step.
        var._choices = choices  # type: ignore[attr-defined]
        return var

    def _build_actions(self) -> None:
        bar = ctk.CTkFrame(self.card, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=(4, 8))
        button = dict(
            height=30, font=self.font, fg_color=theme.OVERLAY_ACCENT,
            hover_color=theme.OVERLAY_ACCENT_HOVER, text_color=theme.OVERLAY_TEXT,
        )
        # The one button that does the thing, coloured so it is not one of
        # four identical grey rectangles.
        self.search_button = ctk.CTkButton(
            bar, text="다시 검색", width=90, command=self.search,
            **{**button, "fg_color": "#3d6ea5", "hover_color": "#4a82c0"},
        )
        self.search_button.pack(side="left")
        if self.item.searchable_mods:
            ctk.CTkButton(
                bar, text="범위 넓히기", width=96, command=lambda: self._nudge(-1), **button
            ).pack(side="left", padx=(6, 0))
            ctk.CTkButton(
                bar, text="범위 좁히기", width=96, command=lambda: self._nudge(1), **button
            ).pack(side="left", padx=(6, 0))
        ctk.CTkButton(
            bar, text="초기화", width=68, command=self.reset_filters, **button
        ).pack(side="left", padx=(6, 0))
        self.browser_button = ctk.CTkButton(
            bar, text="거래소에서 열기", width=116, command=self._open_browser,
            state="disabled", **button,
        )
        self.browser_button.pack(side="left", padx=(6, 0))

    def _build_results(self) -> None:
        bar = ctk.CTkFrame(self.card, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=(0, 2))
        self.sort_var = self._sort_menu(bar)
        self.status = ctk.CTkLabel(
            bar, text="", font=self.font_small, anchor="w", justify="left",
            text_color=theme.OVERLAY_TEXT_DIM,
        )
        self.status.pack(side="left", fill="x", expand=True, padx=(2, 8))
    def _sort_menu(self, parent) -> ctk.StringVar:
        """Ordering sits with the list it orders, not down in the filters."""
        stored = self.app_config.data.get("trade", {}).get("sort", "price")
        labels = [text for _value, text in SORT_OPTIONS]
        var = ctk.StringVar(
            value=next((t for v, t in SORT_OPTIONS if v == stored), labels[0])
        )
        var._choices = SORT_OPTIONS  # type: ignore[attr-defined]
        ctk.CTkOptionMenu(
            parent, variable=var, values=labels, width=118, height=26,
            font=self.font_small, command=lambda _v: self.search(),
            fg_color=theme.OVERLAY_SURFACE_2, button_color=theme.OVERLAY_ACCENT,
            button_hover_color=theme.OVERLAY_ACCENT_HOVER, text_color=theme.OVERLAY_TEXT,
            dropdown_fg_color=theme.OVERLAY_SURFACE, dropdown_hover_color=theme.OVERLAY_SURFACE_2,
            dropdown_text_color=theme.OVERLAY_TEXT, dropdown_font=self.font_small,
        ).pack(side="right")
        return var

    def _build_results_list(self) -> None:
        self.results = ctk.CTkScrollableFrame(self.card, fg_color=theme.OVERLAY_SURFACE, height=170)
        self.results.pack(fill="both", expand=True, padx=12, pady=(0, 14))

    def _sort(self) -> str:
        var = getattr(self, "sort_var", None)
        return (_chosen(var) if var is not None else "") or "price"

    # ---- searching --------------------------------------------------------
    def selections(self) -> list[querymod.Selection]:
        picked = [row.selection() for row in self._rows]
        return [s for s in picked if s is not None]

    def reset_filters(self) -> None:
        """Undo every adjustment and search again.

        Widening a search is a few clicks; getting back to a known state after
        it went wrong was a matter of remembering what each box started at.
        """
        for row in self._rows:
            row.reset()
        for var, value in getattr(self, "_initial_state", []):
            var.set(value)
        self._set_status("검색 조건을 처음 상태로 되돌렸습니다.")
        self.search()

    def _nudge(self, direction: int) -> None:
        """Widen or tighten every ticked mod by 5% of its best possible roll."""
        rows = [r for r in self._rows if r.enabled.get()]
        moved = sum(1 for row in rows if row.nudge(direction))
        skipped = len(rows) - moved
        if not rows:
            self._set_status("체크된 속성이 없습니다.", danger=True)
        elif skipped:
            self._set_status(
                f"{moved}개 조정 · {skipped}개는 게임이 값 범위를 알려주지 않아 건너뜀. "
                "'다시 검색'을 눌러주세요."
            )
        else:
            self._set_status(f"{moved}개 속성 범위를 조정했습니다. '다시 검색'을 눌러주세요.")

    def _extra_filters(self) -> dict:
        socket, misc, trade = {}, {}, {}
        for key, var in self.num_vars.items():
            value = _parse_number(var.get())
            if value is not None:
                # Links, quality and item level are counts, and the site
                # rejects the query outright for a float ("Link min must be
                # an integer") rather than rounding it itself.
                (socket if key == "links" else misc)[key] = {"min": int(value)}
        for key, var in self.filter_vars.items():
            chosen = _chosen(var)
            if chosen:
                misc[key] = {"option": chosen}
        indexed = _chosen(self.indexed_var)
        if indexed:
            trade["indexed"] = {"option": indexed}
        self._status_option = _chosen(self.status_var) or "available"

        out: dict[str, dict] = {}
        if socket:
            out["socket_filters"] = socket
        if misc:
            out["misc_filters"] = misc
        if trade:
            out["trade_filters"] = trade
        return out

    def search(self) -> None:
        if self._busy or self._closed:
            return
        extra = self._extra_filters()
        self._busy = True
        self.search_button.configure(state="disabled")
        self._set_status("거래소에서 찾는 중…")
        payload = querymod.build(
            self.api, self.item, self.selections(),
            extra_filters=extra, sort=self._sort(),
        )
        payload["query"]["status"] = {"option": self._status_option}
        # Off the Tk loop: a search is two round trips plus whatever the rate
        # limiter decides to wait, and doing that in a button callback would
        # freeze every window this app owns.
        threading.Thread(
            target=self._search_worker, args=(payload,), name="price-check", daemon=True
        ).start()

    def _search_worker(self, payload: dict) -> None:
        try:
            result = self.api.search(payload)
            listings = self.api.fetch(
                result["ids"][: int(self.app_config.data.get("trade", {}).get("result_count", 10))],
                result["id"],
            )
        except TradeError as exc:
            self._marshal(self._show_error, str(exc))
        except Exception:
            logger.exception("price check failed")
            self._marshal(self._show_error, "가격 확인 중 오류가 발생했습니다. 로그를 확인해주세요.")
        else:
            self._marshal(self._show_results, result, listings)

    def _marshal(self, fn, *args) -> None:
        try:
            self.after(0, fn, *args)
        except tk.TclError:
            pass  # window closed while the search was in flight

    def _done(self) -> None:
        self._busy = False
        self._answered = True
        if not self._closed:
            self.search_button.configure(state="normal")

    def _show_error(self, message: str) -> None:
        self._done()
        self._set_status(message, danger=True)

    def _show_results(self, result: dict, listings: list[dict]) -> None:
        self._done()
        self._search_id = result["id"]
        self.browser_button.configure(state="normal" if self._search_id else "disabled")
        for child in self.results.winfo_children():
            child.destroy()

        if not listings:
            self._set_status(
                "조건에 맞는 매물이 없습니다. '범위 넓히기'를 누르거나 체크를 줄이고 다시 검색해보세요.",
                danger=True,
            )
            return

        # "Cheapest" can no longer be read off the front of the list: that
        # only held while the site was sorting by price, and the default is
        # now newest-first. It cannot be a plain min() either, because the
        # amounts are in different currencies -- min(420 chaos, 2 divine) is
        # 2. So: take the currency most of the listings are priced in, and
        # compare only within it.
        summary = f"매물 {result['total']}건"
        prices: dict[str, list[float]] = {}
        for entry in listings:
            amount = _amount(entry)
            if amount is not None:
                prices.setdefault(_currency(entry), []).append(amount)
        if prices:
            currency = max(prices, key=lambda c: len(prices[c]))
            values = sorted(prices[currency])
            name = querymod.CURRENCY_NAMES.get(currency, currency)
            summary += f"   ·   최저 {_pretty(values[0])} {name}"
            if len(values) > 2:
                summary += f"   ·   중앙값 {_pretty(values[len(values) // 2])} {name}"
            if len(prices) > 1:
                # Say so rather than quietly dropping the others.
                summary += f"   (다른 통화 {len(prices) - 1}종 제외)"
        self._set_status(summary)

        for entry in listings:
            self._build_listing_row(entry)

    def _build_listing_row(self, entry: dict) -> None:
        row = ctk.CTkFrame(self.results, fg_color="transparent")
        row.pack(fill="x", pady=2)
        ctk.CTkLabel(
            row, text=querymod.summarise_price(entry), font=self.font, width=126, anchor="w",
            text_color="#ffd75e",
        ).pack(side="left", padx=(6, 8))
        # When it was listed, not what it is called: every result is the same
        # item you are holding, so the name never varied. How stale a listing
        # is, on the other hand, decides whether its price means anything.
        ctk.CTkLabel(
            row, text=_listed_ago((entry.get("listing") or {}).get("indexed", "")),
            font=self.font_small, anchor="e", width=82, text_color=theme.OVERLAY_TEXT_DIM,
        ).pack(side="right", padx=(6, 8))
        ctk.CTkLabel(
            row, text=querymod.seller_of(entry), font=self.font_small, anchor="w",
            text_color=theme.OVERLAY_TEXT,
        ).pack(side="left", fill="x", expand=True)

    def _set_status(self, text: str, danger: bool = False) -> None:
        if not self._closed:
            self.status.configure(
                text=text, text_color=theme.DANGER if danger else theme.OVERLAY_TEXT_DIM
            )

    # ---- window chrome (no titlebar -> drag, resize and close by hand) -----
    def _build_grip(self) -> None:
        self.grip = tk.Label(
            self, text="◢", bd=0, cursor="size_nw_se",
            bg=theme.OVERLAY_BG, fg=theme.OVERLAY_TEXT_DIM, font=self.font_small,
        )
        self.grip.place(relx=1.0, rely=1.0, x=-6, y=-4, anchor="se")
        self.grip.bind("<ButtonPress-1>", self._start_resize)
        self.grip.bind("<B1-Motion>", self._do_resize)

    def _take_focus(self) -> None:
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root, event.y_root)

    def _do_drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        self._drag_origin = (event.x_root, event.y_root)
        self._pending_move = (self.winfo_x() + dx, self.winfo_y() + dy)
        # Motion events arrive far faster than a window can be moved, and
        # calling geometry() on each one queues up work the window then has
        # to catch up on -- which is what the lag and tearing were. Coalesce
        # them: keep only the latest position and apply it once the event
        # loop is free.
        if self._move_scheduled:
            return
        self._move_scheduled = True
        self.after_idle(self._apply_move)

    def _apply_move(self) -> None:
        self._move_scheduled = False
        if self._pending_move is None or self._closed:
            return
        x, y = self._pending_move
        self._pending_move = None
        try:
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _start_resize(self, event: tk.Event) -> None:
        self._resize_origin = (
            event.x_root, event.y_root, self.winfo_width(), self.winfo_height()
        )

    def _do_resize(self, event: tk.Event) -> None:
        if self._resize_origin is None:
            return
        ox, oy, ow, oh = self._resize_origin
        self.geometry(
            f"{max(560, ow + event.x_root - ox)}x{max(460, oh + event.y_root - oy)}"
        )

    def _on_space(self, event: tk.Event) -> str | None:
        """Space closes the window once an answer is on screen.

        Not while a search is still running -- that would throw away a request
        already in flight -- and not while a box is being typed into, where
        space is just a keystroke.
        """
        if not self._answered:
            return None
        if isinstance(self.focus_get(), (ctk.CTkEntry, tk.Entry)):
            return None
        self.close()
        return "break"

    # ---- dismissal on a click elsewhere ------------------------------------
    def _watch_outside_click(self) -> None:
        """Close when a mouse button goes down over something that isn't ours.

        Only the press *edge* counts, and only where the cursor was at that
        moment: dragging the window by its header ends with the button being
        released well outside it, and a position sampled any later than the
        press would read that as a click on the game.
        """
        if self._closed:
            return
        self._outside_watch = None
        if not self.app_config.data.get("trade", {}).get("close_on_outside_click", True):
            self._mouse_was_down = _mouse_button_down()
        else:
            down = _mouse_button_down()
            pressed = down and not self._mouse_was_down
            self._mouse_was_down = down
            if pressed and not self._cursor_over_us():
                logger.debug("price check closed by a click outside the window")
                self.close()
                return
        self._outside_watch = self.after(_OUTSIDE_POLL_MS, self._watch_outside_click)

    def _cursor_over_us(self) -> bool:
        try:
            x, y = self.winfo_pointerxy()
        except tk.TclError:
            return True
        try:
            left, top = self.winfo_rootx(), self.winfo_rooty()
            if left <= x < left + self.winfo_width() and top <= y < top + self.winfo_height():
                return True
        except tk.TclError:
            return True
        return _point_is_ours(x, y)

    def _open_browser(self) -> None:
        if self._search_id:
            webbrowser.open(self.api.search_url(self._search_id))

    def _on_configure(self, event: tk.Event) -> None:
        # Only the window's own geometry is worth recording. Every child
        # widget fires Configure too, and CustomTkinter rebuilds a lot of
        # canvases while a window is being dragged -- doing this work on
        # each of those events is most of why dragging felt heavy.
        if event.widget is not self:
            return
        try:
            self.geo["x"], self.geo["y"] = self.winfo_x(), self.winfo_y()
            self.geo["w"], self.geo["h"] = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            pass

    def close(self) -> None:
        self._closed = True
        if self._outside_watch is not None:
            try:
                self.after_cancel(self._outside_watch)
            except tk.TclError:
                pass
            self._outside_watch = None
        trade = self.app_config.data.setdefault("trade", {})
        trade["status"] = self._status_option
        indexed = getattr(self, "indexed_var", None)
        if indexed is not None:
            trade["indexed"] = _chosen(indexed)
        trade["sort"] = self._sort()
        self.app_config.save()
        self.destroy()


# The order the game itself shows them in, which is the order anyone reading
# an item already expects: what the base grants, what was added at a bench,
# then the rolled prefixes and suffixes as two separate groups.
_GROUP_ORDER = {"implicit": 0, "crafted": 1, "prefix": 2, "suffix": 3}


def _group_of(mod: ItemMod) -> int:
    if mod.kind in ("implicit", "crafted"):
        return _GROUP_ORDER[mod.kind]
    return _GROUP_ORDER.get(mod.affix, 4)


def _tier_only(mod: ItemMod) -> tuple[int, float]:
    """Best tier first, ignoring which affix slot it sits in.

    Used for *what to tick*, where the prefix/suffix grouping that governs
    display order would only get in the way of picking the three strongest.
    """
    tier = mod.tier if mod.tier is not None else 99
    return tier, -(mod.quality if mod.quality is not None else 0.0)


def _tier_rank(mod: ItemMod) -> tuple[int, int, float]:
    """Sort key: group first, then best tier, then best roll.

    T1 is the top band, so a plain ascending sort on the number is right.
    Mods with no tier sort last within their group rather than first -- an
    unknown tier is not evidence of a good one.
    """
    tier = mod.tier if mod.tier is not None else 99
    return _group_of(mod), tier, -(mod.quality if mod.quality is not None else 0.0)


def _quality_colour(quality: float) -> str:
    if quality >= 0.85:
        return "#7fd6a4"
    if quality >= 0.5:
        return "#e8c07a"
    return theme.OVERLAY_TEXT_DIM


def _tier_colour(tier: int | None) -> str:
    if tier is None:
        return theme.OVERLAY_TEXT_DIM
    if tier == 1:
        return "#7fd6a4"
    if tier <= 3:
        return "#e8c07a"
    return theme.OVERLAY_TEXT_DIM


def _chosen(var: ctk.StringVar) -> str:
    """The site's id behind the label currently shown in a dropdown."""
    choices = getattr(var, "_choices", [])
    label = var.get()
    return next((value for value, text in choices if text == label), "")


def _currency(entry: dict) -> str:
    return ((entry.get("listing") or {}).get("price") or {}).get("currency", "")


def _amount(entry: dict) -> float | None:
    price = (entry.get("listing") or {}).get("price") or {}
    try:
        return float(price.get("amount"))
    except (TypeError, ValueError):
        return None


def _listed_ago(indexed: str) -> str:
    """"3시간 전" from the ISO timestamp the site returns."""
    if not indexed:
        return ""
    try:
        when = datetime.fromisoformat(indexed.replace("Z", "+00:00"))
    except ValueError:
        return ""
    seconds = (datetime.now(timezone.utc) - when).total_seconds()
    if seconds < 90:
        return "방금"
    for limit, divisor, unit in ((3600, 60, "분"), (86400, 3600, "시간"), (2592000, 86400, "일")):
        if seconds < limit:
            return f"{int(seconds // divisor)}{unit} 전"
    return f"{int(seconds // 2592000)}개월 전"


def _round(value: float) -> float:
    return round(value, 1) if abs(value) < 10 else float(int(value))


def _pretty(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _fmt(value: float | None) -> str:
    return "" if value is None else _pretty(value)


def _parse_number(raw: str) -> float | None:
    """A blank or half-typed box means "no bound", not an error."""
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return None

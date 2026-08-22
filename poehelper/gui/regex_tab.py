"""정규식 tab: tick the map mods you care about, get a search string.

The job this does is small and the reason it exists is entirely about where
the answer ends up. The game's map search box takes a regex and holds 50
characters; a useful filter is a dozen mods' worth of short fragments joined
by ``|``, and assembling that by hand -- in the right order, without a stray
space, under the limit -- is the whole difficulty. So: tick, and the string
appears; save it, and it comes back next league.

Two shapes, one for each thing anyone actually asks of a stack of maps:

* 포함 -- ``"수량|규모"``, keep the maps that have any of these.
* 제외 -- ``"!반사|관통"``, throw away the maps that have any of these.

The generated string stays editable. The fragments in ``map_mods.py`` are
picked to be short and distinctive rather than exhaustive, and the box is
where anyone who wants something they aren't offered can just type it.

A preset can carry a hotkey. Pressed in game it copies the pattern and
pastes it, which is the only thing anybody was ever going to do with it --
the alternative being alt-tab, this tab, a copy button, and alt-tab back,
in the middle of sorting a stash tab.
"""
from __future__ import annotations

import tkinter as tk
import uuid

import customtkinter as ctk

from .. import map_mods, toast
from ..config import Config
from ..map_mods import EXCLUDE, INCLUDE, INCLUDE_ALL, MapMod
from . import theme
from .widgets.hotkey_picker import HotkeyPicker
from .widgets.tooltip import Tooltip

_check_window = None


def open_map_check(master, pattern: str, item_text: str):
    """Show one map against *pattern*, reusing the window if one is up.

    Module-level so the 맵모드 tab's button and the global hotkey both reach
    the same window. One at a time, for the same reason the price check keeps
    one: this is opened repeatedly while sorting a stash tab, and a trail of
    windows would bury the game.
    """
    global _check_window
    from .map_check_window import MapCheckWindow

    if _check_window is not None:
        try:
            _check_window.close()
        except Exception:  # noqa: BLE001 - already gone is fine
            pass
        _check_window = None

    def forget() -> None:
        global _check_window
        _check_window = None

    _check_window = MapCheckWindow(master, pattern, item_text, on_closed=forget)
    return _check_window

_MODE_LABELS = {INCLUDE: "포함(하나)", INCLUDE_ALL: "포함(모두)", EXCLUDE: "제외"}
_MODE_BY_LABEL = {label: mode for mode, label in _MODE_LABELS.items()}
_ALL_CATEGORIES = "전체 분류"

_MODE_HELP = (
    "포함(하나): 체크한 조건이 하나라도 맞는 지도만 남깁니다.\n"
    "포함(모두): 체크한 조건을 전부 만족하는 지도만 남깁니다.\n"
    "                 (조건마다 따옴표로 나눠 적습니다 — 게임이 이걸 AND로 읽습니다)\n"
    "제외:          체크한 조건이 하나라도 맞는 지도를 걸러냅니다 (맨 앞에 ! 이 붙습니다)."
)

# Body text for the 검사 window, which inherits whatever appearance mode is
# in force rather than painting its own surface.
_TEXT = ("#1d1d1f", "#f2f2f5")


def _explain(mod: MapMod, catches: list[str] | None) -> str:
    """Tooltip for one option: the fragment, and what it really catches.

    The list of matched modifiers is the useful half. These fragments are
    short on purpose and it is never obvious from the text alone whether
    "반사" means the three reflect mods or also half a reminder paragraph --
    so the game's own answer is shown instead of asking anyone to trust the
    wording.
    """
    lines = [f"정규식 조각: {mod.pattern}"]
    if mod.numeric:
        lines += [
            "",
            "숫자를 적으면 그 값 이상인 지도만 찾습니다.",
            f"예) 100 → {map_mods.fragment_for(mod.id, 100)}",
        ]
    if catches is None:
        return "\n".join(lines)
    if not catches:
        lines += [
            "",
            "⚠ 게임 데이터에서 이 조각과 맞는 지도 옵션을 찾지 못했습니다.",
            "체크하면 모든 지도가 걸러질 수 있습니다.",
        ]
    elif catches == ["(지도 속성 줄)"]:
        lines += ["", "지도 옵션이 아니라 지도에 항상 찍히는 속성 줄입니다."]
    else:
        lines += ["", f"실제로 잡는 지도 옵션 {len(catches)}개:"]
        lines += [f"  · {ref}" for ref in catches[:8]]
        if len(catches) > 8:
            lines.append(f"  … 외 {len(catches) - 8}개")
    return "\n".join(lines)


class RegexTab(ctk.CTkFrame):
    def __init__(self, master, config: Config, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app_config = config
        # Set while a preset is being loaded into the widgets. Every control
        # here saves on change, and without this the act of *showing* a
        # preset would write it back a field at a time -- with the mode and
        # mod list still belonging to the previous one.
        self._loading = False
        self._rows: list[tuple[MapMod, ctk.BooleanVar, ctk.CTkCheckBox]] = []

        self._build_preset_bar()
        self._build_editor()
        self._build_mod_list()
        self._build_output()

        self._refresh_preset_menu()
        self._load(self._current_preset())

    # ---- config access -----------------------------------------------------
    def _store(self) -> dict:
        return self.app_config.data.setdefault("map_regex", {})

    def _presets(self) -> list[dict]:
        return self._store().setdefault("presets", [])

    def _current_preset(self) -> dict | None:
        presets = self._presets()
        if not presets:
            return None
        active = self._store().get("active_id", "")
        for preset in presets:
            if preset.get("id") == active:
                return preset
        return presets[0]

    # ---- preset bar --------------------------------------------------------
    def _build_preset_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=(8, 2))

        ctk.CTkLabel(bar, text="정규식:", font=theme.FONT_BODY_STRONG).pack(side="left")
        self.preset_var = ctk.StringVar()
        self.preset_menu = ctk.CTkOptionMenu(
            bar, variable=self.preset_var, values=[""], width=190,
            command=self._on_preset_chosen,
        )
        self.preset_menu.pack(side="left", padx=(6, 8))

        ctk.CTkButton(bar, text="+ 새로 만들기", width=110, command=self._new_preset).pack(
            side="left"
        )
        ctk.CTkButton(
            bar, text="삭제", width=54, fg_color=theme.DANGER,
            hover_color=theme.DANGER_HOVER, command=self._delete_preset,
        ).pack(side="left", padx=(6, 0))

    # ---- name / mode / hotkey ---------------------------------------------
    def _build_editor(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(6, 2))

        ctk.CTkLabel(row, text="이름", font=theme.FONT_CAPTION, width=32).pack(side="left")
        self.name_var = ctk.StringVar()
        ctk.CTkEntry(row, textvariable=self.name_var, width=150).pack(side="left", padx=(4, 12))
        self.name_var.trace_add("write", lambda *_: self._save())

        # The tooltip hangs off the label, not off the segmented button:
        # CTkSegmentedButton raises NotImplementedError from bind().
        mode_label = ctk.CTkLabel(row, text="방식", font=theme.FONT_CAPTION, width=32)
        mode_label.pack(side="left")
        Tooltip(mode_label, _MODE_HELP)
        self.mode_var = ctk.StringVar(value=_MODE_LABELS[EXCLUDE])
        ctk.CTkSegmentedButton(
            row,
            values=[_MODE_LABELS[INCLUDE], _MODE_LABELS[INCLUDE_ALL], _MODE_LABELS[EXCLUDE]],
            variable=self.mode_var, width=230, command=lambda _v: self._regenerate(),
        ).pack(side="left", padx=(4, 0))

        hotkey_row = ctk.CTkFrame(self, fg_color="transparent")
        hotkey_row.pack(fill="x", padx=8, pady=(2, 2))
        ctk.CTkLabel(hotkey_row, text="단축키", font=theme.FONT_CAPTION, width=42).pack(side="left")
        self.hotkey_picker = HotkeyPicker(hotkey_row, on_change=lambda _c: self._save())
        self.hotkey_picker.pack(side="left")
        ctk.CTkLabel(
            hotkey_row,
            text="게임에서 누르면 이 정규식을 복사해 검색창에 붙여넣습니다.",
            font=theme.FONT_CAPTION, text_color=theme.PRIMARY,
        ).pack(side="left", padx=(8, 0))

    # ---- the mod checkboxes ------------------------------------------------
    def _build_mod_list(self) -> None:
        filter_row = ctk.CTkFrame(self, fg_color="transparent")
        filter_row.pack(fill="x", padx=8, pady=(8, 2))

        ctk.CTkLabel(filter_row, text="검색", font=theme.FONT_CAPTION, width=32).pack(side="left")
        self.search_var = ctk.StringVar()
        ctk.CTkEntry(
            filter_row, textvariable=self.search_var, width=170,
            placeholder_text="옵션 이름 일부",
        ).pack(side="left", padx=(4, 8))
        self.search_var.trace_add("write", lambda *_: self._apply_filter())

        self.category_var = ctk.StringVar(value=_ALL_CATEGORIES)
        ctk.CTkOptionMenu(
            filter_row, variable=self.category_var,
            values=[_ALL_CATEGORIES, *map_mods.categories()], width=130,
            command=lambda _v: self._apply_filter(),
        ).pack(side="left")
        ctk.CTkButton(
            filter_row, text="체크 모두 해제", width=110, command=self._clear_checks
        ).pack(side="right")

        # Fixed height: the output box below is the point of this tab and has
        # to stay on screen, so the list gets what is left over rather than
        # growing to fit 60-odd rows and pushing it off the bottom.
        self.list_frame = ctk.CTkScrollableFrame(self, height=210)
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # Built once and hidden/shown by the filter. Rebuilding on every
        # keystroke would mean re-reading which boxes were ticked out of 60
        # freshly created widgets, and CustomTkinter draws each one onto its
        # own canvas -- the cost is all in construction, not in packing.
        current_category = None
        for mod in map_mods.MAP_MODS:
            if mod.category != current_category:
                current_category = mod.category
                header = ctk.CTkLabel(
                    self.list_frame, text=mod.category, font=theme.FONT_BODY_STRONG,
                    anchor="w",
                )
                self._rows.append((None, None, header, None))  # type: ignore[arg-type]
            self._rows.append(self._build_mod_row(mod))
        self._apply_filter()

    def _build_mod_row(self, mod: MapMod):
        """``(mod, ticked, widget, threshold)`` for one option.

        Only the mods carrying a number get the extra box, and only those get
        a frame to hold it -- the other sixty stay a bare checkbox, since
        CustomTkinter draws every widget onto a canvas of its own and a
        wrapper each would be sixty canvases bought for nothing.
        """
        var = ctk.BooleanVar(value=False)
        threshold: ctk.StringVar | None = None
        parent = self.list_frame

        if mod.numeric:
            parent = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            threshold = ctk.StringVar(value="")
            ctk.CTkLabel(
                parent, text="% 이상", font=theme.FONT_CAPTION,
                text_color=theme.PRIMARY, width=42, anchor="w",
            ).pack(side="right")
            ctk.CTkEntry(
                parent, textvariable=threshold, width=50, height=24,
                font=theme.FONT_CAPTION, justify="center", placeholder_text="전체",
            ).pack(side="right", padx=(4, 2))
            threshold.trace_add("write", lambda *_: self._regenerate())

        # What this fragment catches, checked against the game's own list of
        # the modifiers a map can roll. A fragment that catches nothing is
        # the dangerous case -- ticking it filters out every map, and an
        # empty result looks exactly like a bad batch -- so it is said out
        # loud on the row rather than left to the tooltip.
        catches = map_mods.coverage().get(mod.id)
        warn = catches is not None and not catches
        box = ctk.CTkCheckBox(
            parent,
            text=f"{mod.label}   ·   {mod.pattern}" + ("   ⚠" if warn else ""),
            variable=var, font=theme.FONT_CAPTION, checkbox_width=17,
            checkbox_height=17, command=self._regenerate,
            **({"text_color": theme.DANGER} if warn else {}),
        )
        box.pack(side="left", fill="x", expand=True) if mod.numeric else None
        Tooltip(box, _explain(mod, catches))
        return (mod, var, parent if mod.numeric else box, threshold)

    def _apply_filter(self) -> None:
        needle = self.search_var.get().strip().lower()
        category = self.category_var.get()
        for mod, _var, widget, _threshold in self._rows:
            if mod is None:  # a category heading
                visible = not needle and category == _ALL_CATEGORIES
            else:
                visible = (
                    (category == _ALL_CATEGORIES or mod.category == category)
                    and (
                        not needle
                        or needle in mod.label.lower()
                        or needle in mod.pattern.lower()
                    )
                )
            if visible:
                widget.pack(fill="x", anchor="w", padx=(2, 0), pady=1)
            else:
                widget.pack_forget()

    def _clear_checks(self) -> None:
        for mod, var, _widget, threshold in self._rows:
            if mod is not None:
                var.set(False)
                if threshold is not None:
                    threshold.set("")
        self._regenerate()

    # ---- the answer --------------------------------------------------------
    def _build_output(self) -> None:
        extra_row = ctk.CTkFrame(self, fg_color="transparent")
        extra_row.pack(fill="x", padx=8, pady=(2, 2))
        ctk.CTkLabel(extra_row, text="추가 키워드", font=theme.FONT_CAPTION, width=72).pack(
            side="left"
        )
        self.extra_var = ctk.StringVar()
        entry = ctk.CTkEntry(
            extra_row, textvariable=self.extra_var,
            placeholder_text="목록에 없는 단어를 쉼표로 구분해서 (예: 야마, 지도제작)",
        )
        entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.extra_var.trace_add("write", lambda *_: self._regenerate())

        out_row = ctk.CTkFrame(self, fg_color="transparent")
        out_row.pack(fill="x", padx=8, pady=(4, 2))
        self.output_var = ctk.StringVar()
        ctk.CTkEntry(
            out_row, textvariable=self.output_var, font=theme.FONT_PATTERN, height=32,
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text="복사", width=54, command=self._copy).pack(
            side="left", padx=(6, 0)
        )
        check = ctk.CTkButton(
            out_row, text="아이템 검사", width=88, command=self._check_item
        )
        check.pack(side="left", padx=(6, 0))
        Tooltip(
            check,
            "게임에서 지도에 마우스를 올리고 Ctrl+C 를 누른 뒤 이 버튼을 누르면,\n"
            "조각 하나하나가 그 지도의 어느 줄에 걸렸는지 보여줍니다.\n"
            "속성이 아니라 설명·구분 줄에 걸렸다면 그 조각이 잘못된 것입니다.",
        )
        # Edits here are kept: the fragment list is a starting point, and
        # anyone who knows the exact wording they want should be able to just
        # type it. Ticking a box regenerates and therefore discards it, which
        # is what the hint below says out loud.
        self.output_var.trace_add("write", lambda *_: self._on_output_changed())

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=8, pady=(0, 6))
        self.count_label = ctk.CTkLabel(foot, text="", font=theme.FONT_CAPTION, anchor="w")
        self.count_label.pack(side="left")
        ctk.CTkLabel(
            foot,
            text="직접 고쳐 써도 저장됩니다. 체크를 바꾸면 다시 만들어집니다.",
            font=theme.FONT_CAPTION, text_color=theme.PRIMARY, anchor="e",
        ).pack(side="right")

    def _regenerate(self) -> None:
        """Rebuild the pattern from what is ticked, then save."""
        if self._loading:
            return
        self.output_var.set(
            map_mods.build_pattern(
                self._checked_ids(),
                mode=_MODE_BY_LABEL.get(self.mode_var.get(), EXCLUDE),
                extra=self.extra_var.get(),
                quote=self._store().get("quote", True),
                thresholds=self._thresholds(),
            )
        )  # which saves via the output trace

    def _checked_ids(self) -> list[str]:
        return [
            mod.id for mod, var, _w, _t in self._rows if mod is not None and var.get()
        ]

    def _thresholds(self) -> dict[str, str]:
        """Only the ones actually filled in -- an empty box is "any value",
        not zero, and writing zeros into the preset would make every reward
        row look like it had a condition on it."""
        return {
            mod.id: threshold.get().strip()
            for mod, _var, _w, threshold in self._rows
            if mod is not None and threshold is not None and threshold.get().strip()
        }

    def _on_output_changed(self) -> None:
        self._update_counter()
        self._save()

    def _update_counter(self) -> None:
        length = len(self.output_var.get())
        over = length > map_mods.SEARCH_LIMIT
        self.count_label.configure(
            text=f"{length}/{map_mods.SEARCH_LIMIT}자"
            + ("  · 게임 검색창에 다 들어가지 않습니다" if over else ""),
            text_color=theme.DANGER if over else theme.PRIMARY,
        )

    def _copy(self) -> None:
        pattern = self.output_var.get()
        if not pattern:
            toast.show("복사할 정규식이 없습니다.")
            return
        self.clipboard_clear()
        self.clipboard_append(pattern)
        toast.show("정규식을 복사했습니다.")

    # ---- checking against a real map ---------------------------------------
    def _check_item(self) -> None:
        """Answer "why did this map match?" against the item on the clipboard.

        A fragment is a few characters of Korean and a map carries far more
        text than its mods -- affix headers, the rules blurb under a curse,
        the property lines every map has. A fragment landing on one of those
        looks exactly like a real hit from the outside, so the only honest
        way to trust this list is to be able to see where each piece landed.
        """
        pattern = self.output_var.get().strip()
        if not pattern:
            toast.show("먼저 정규식을 만들어주세요.")
            return
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ""
        if "아이템 희귀도" not in text and "아이템 종류" not in text:
            toast.show(
                "클립보드에 아이템이 없습니다.\n"
                "게임에서 지도에 마우스를 올리고 Ctrl+C 를 누른 뒤 다시 눌러주세요."
            )
            return
        open_map_check(self, pattern, text)

    def _refresh_preset_menu(self) -> None:
        names = [p.get("name", "") or "(이름 없음)" for p in self._presets()]
        self.preset_menu.configure(values=names or [""])
        current = self._current_preset()
        self.preset_var.set(
            (current.get("name", "") or "(이름 없음)") if current else ""
        )

    def _on_preset_chosen(self, label: str) -> None:
        for preset in self._presets():
            if (preset.get("name", "") or "(이름 없음)") == label:
                self._store()["active_id"] = preset.get("id", "")
                self.app_config.save()
                self._load(preset)
                return

    def _new_preset(self) -> None:
        preset = {
            "id": uuid.uuid4().hex[:8],
            "name": f"정규식 {len(self._presets()) + 1}",
            "mode": EXCLUDE,
            "mods": [],
            "thresholds": {},
            "extra": "",
            "pattern": "",
            "hotkey": "",
        }
        self._presets().append(preset)
        self._store()["active_id"] = preset["id"]
        self.app_config.save()
        self._refresh_preset_menu()
        self._load(preset)

    def _delete_preset(self) -> None:
        current = self._current_preset()
        # The last one stays: an empty tab has no editable anything, and the
        # way out of that state ("+ 새로 만들기") is not obviously the way
        # back to a working tab. Same rule as the memo tab.
        if current is None or len(self._presets()) <= 1:
            toast.show("정규식은 최소 1개는 남아 있어야 합니다.")
            return
        self._presets().remove(current)
        remaining = self._presets()
        self._store()["active_id"] = remaining[0]["id"] if remaining else ""
        self.app_config.save()
        self._refresh_preset_menu()
        self._load(self._current_preset())

    def _load(self, preset: dict | None) -> None:
        self._loading = True
        try:
            self.name_var.set(preset.get("name", "") if preset else "")
            self.mode_var.set(
                _MODE_LABELS.get((preset or {}).get("mode", EXCLUDE), _MODE_LABELS[EXCLUDE])
            )
            self.extra_var.set((preset or {}).get("extra", ""))
            self.hotkey_picker.set_combo((preset or {}).get("hotkey", ""))
            chosen = set((preset or {}).get("mods", []))
            saved_thresholds = (preset or {}).get("thresholds", {}) or {}
            for mod, var, _widget, threshold in self._rows:
                if mod is None:
                    continue
                var.set(mod.id in chosen)
                if threshold is not None:
                    threshold.set(str(saved_thresholds.get(mod.id, "")))
            # Fires the output trace, which is why this has to happen inside
            # the guard: the counter should follow the preset being shown,
            # but nothing may be written back to config while loading.
            self.output_var.set((preset or {}).get("pattern", ""))
        finally:
            self._loading = False
        self._update_counter()

    def _save(self) -> None:
        if self._loading:
            return
        preset = self._current_preset()
        if preset is None:
            return
        preset["name"] = self.name_var.get()
        preset["mode"] = _MODE_BY_LABEL.get(self.mode_var.get(), EXCLUDE)
        preset["mods"] = self._checked_ids()
        preset["thresholds"] = self._thresholds()
        preset["extra"] = self.extra_var.get()
        preset["pattern"] = self.output_var.get()
        preset["hotkey"] = self.hotkey_picker.get_combo()
        # Saving re-registers hotkeys through the config listener in main.py,
        # so a preset's key is live the moment it is picked.
        self.app_config.save()
        self._refresh_preset_menu()

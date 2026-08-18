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

import uuid

import customtkinter as ctk

from .. import map_mods, toast
from ..config import Config
from ..map_mods import EXCLUDE, INCLUDE, MapMod
from . import theme
from .widgets.hotkey_picker import HotkeyPicker
from .widgets.tooltip import Tooltip

_MODE_LABELS = {INCLUDE: "포함", EXCLUDE: "제외"}
_MODE_BY_LABEL = {label: mode for mode, label in _MODE_LABELS.items()}
_ALL_CATEGORIES = "전체 분류"


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
        Tooltip(
            mode_label,
            "포함: 체크한 옵션이 하나라도 있는 지도만 남깁니다.\n"
            "제외: 체크한 옵션이 하나라도 있는 지도를 걸러냅니다 (맨 앞에 ! 이 붙습니다).",
        )
        self.mode_var = ctk.StringVar(value=_MODE_LABELS[EXCLUDE])
        ctk.CTkSegmentedButton(
            row, values=[_MODE_LABELS[INCLUDE], _MODE_LABELS[EXCLUDE]],
            variable=self.mode_var, width=120, command=lambda _v: self._regenerate(),
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
            values=[_ALL_CATEGORIES, *map_mods.CATEGORIES], width=130,
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
                self._rows.append((None, None, header))  # type: ignore[arg-type]
            var = ctk.BooleanVar(value=False)
            box = ctk.CTkCheckBox(
                self.list_frame, text=f"{mod.label}   ·   {mod.pattern}",
                variable=var, font=theme.FONT_CAPTION, checkbox_width=17,
                checkbox_height=17, command=self._regenerate,
            )
            Tooltip(box, f"정규식 조각: {mod.pattern}")
            self._rows.append((mod, var, box))
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self.search_var.get().strip().lower()
        category = self.category_var.get()
        for mod, _var, widget in self._rows:
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
        for mod, var, _widget in self._rows:
            if mod is not None:
                var.set(False)
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
            out_row, textvariable=self.output_var, font=theme.FONT_MONO,
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text="복사", width=54, command=self._copy).pack(
            side="left", padx=(6, 0)
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
            )
        )  # which saves via the output trace

    def _checked_ids(self) -> list[str]:
        return [mod.id for mod, var, _w in self._rows if mod is not None and var.get()]

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

    # ---- preset lifecycle --------------------------------------------------
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
            for mod, var, _widget in self._rows:
                if mod is not None:
                    var.set(mod.id in chosen)
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
        preset["extra"] = self.extra_var.get()
        preset["pattern"] = self.output_var.get()
        preset["hotkey"] = self.hotkey_picker.get_combo()
        # Saving re-registers hotkeys through the config listener in main.py,
        # so a preset's key is live the moment it is picked.
        self.app_config.save()
        self._refresh_preset_menu()

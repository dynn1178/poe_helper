"""Single JSON config store replacing the old multi-section setting.ini.

Everything the GUI edits (links, hotkeys, macros, calibration points, memos,
window geometry) lives in one dict tree and is persisted atomically. Callers
mutate ``Config.data`` in place and call ``save()``; ``on_change`` listeners
let the hotkey manager re-register bindings immediately after a save, so
there is no AHK-style "reload required" step.
"""
from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from . import paths

# Default chat macros. Bound phrases first, unbound after -- the same order the 저장 button now
# enforces (MacroTableEditor.sort_unbound_last). No "name" field: the editor
# only ever reads and writes hotkey/text, so a name here was never shown
# anywhere and was dropped the first time the table was saved.
DEFAULT_MACROS = [
    {"hotkey": "alt+f1", "text": "&Hello " * 18},
    {"hotkey": "alt+f2", "text": "&Bye " * 28},
    {"hotkey": "alt+5", "text": "@last ty"},
    {"hotkey": "alt+6", "text": "/invite @last"},
    {"hotkey": "alt+7", "text": "/tradewith @last"},
    {"hotkey": "alt+8", "text": "/hideout @last"},
    {"hotkey": "f3", "text": "Thank you Exile."},
    {"hotkey": "f4", "text": "/leave"},
    {"hotkey": "f5", "text": "/hideout"},
    {"hotkey": "f6", "text": "/kingsmarch"},
    {"hotkey": "", "text": "/exit"},
    {"hotkey": "", "text": "/remaining"},
    {"hotkey": "", "text": "/deaths"},
    {"hotkey": "", "text": "dnd"},
    {"hotkey": "", "text": "/played"},
    {"hotkey": "", "text": "/passives"},
]

DEFAULT_LINKS = [
    {"name": "POE인벤", "url": "http://poe.inven.co.kr"},
    {"name": "POE카페", "url": "https://cafe.naver.com/POEkorea"},
    {"name": "POE DB", "url": "https://poedb.tw/kr/"},
    {"name": "Planner", "url": "https://poeplanner.com/"},
    {"name": "NINJA", "url": "https://poe.ninja/"},
    {"name": "미궁", "url": "https://www.poelab.com/"},
    {"name": "크래프팅", "url": "https://www.craftofexile.com/"},
    {"name": "정규식", "url": "https://poeregexkr.web.app/"},
    {"name": "맵거래", "url": "https://poemap.trade/"},
    {"name": "Stack", "url": "https://poestack.com/"},
]

# Non-macro action hotkeys (rebindable the same way as macros).
#
# ``restart_app`` deliberately has no default binding any more: an app restart
# is not something to risk triggering with a stray keypress mid-fight, so it
# lives on an explicit button in the bottom bar instead (see gui/app.py).
DEFAULT_HOTKEYS = {
    "flask_macro": "`",
    "identify_scroll": "ctrl+d",
    "guild_chat": "\\",
    "toggle_continuous_key": "ctrl+1",
    "tujen_scroll": "ctrl+2",
    "tujen_confirm": "ctrl+3",
    "currency_cycle": "ctrl+4",
    "cwdt_macro": "ctrl+5",
    "autoclick_toggle": "f12",
    "minefield_hold": "tab",
    "autoclick_hold": "ctrl+capslock",
    "search_paste": "alt+f",
    # f9/f10/f11 in stash-order (쿼드 -> 일반 -> 인벤토리 비우기) so the three
    # transfer keys sit next to each other in the order they are used.
    "quad_stash_pull_filtered": "f9",
    "inventory_pull_filtered": "f10",
    "stash_dump_all": "f11",
    "show_image_1": "ctrl+num 1",
    "show_image_2": "ctrl+num 2",
    "show_image_3": "ctrl+num 3",
    # Not ctrl+d, which this app already uses for the identify scroll -- the
    # collision would price-check every time a scroll was used.
    "price_check": "alt+d",
    # Next to the price check, and for the same kind of moment: the item is
    # under the cursor and the question is whether to keep it.
    "map_regex_check": "alt+a",
}

# Grid dimensions of each clickable game area, in cells. The pickers draw
# these as dashed guides so a saved region can be lined up against the real
# grid instead of guessed at from two corner clicks.
GRID_SIZES = {
    "quad_stash": (24, 24),
    "stash": (12, 12),
    "inventory": (12, 5),
}

# Keys offerable in the continuous-key slots. "없음" leaves the slot unused.
CONTINUOUS_KEY_CHOICES = ["없음", "q", "w", "e", "r", "t", "1", "2", "3", "4", "5"]
CONTINUOUS_KEY_SLOTS = 5

SKILL_KEYS = ["q", "w", "e", "r", "t"]

DEFAULTS: dict[str, Any] = {
    "windows": {
        "main": {"x": 100, "y": 100},
        # Wider than the 560 the toolbar needs, and short enough to show
        # about three route lines -- the shape it actually gets used in.
        # (560 is the floor: below that the one-row toolbar starts losing
        # controls off the right edge. See route_window._MIN_W.)
        "route_poe1": {"x": 120, "y": 120, "w": 789, "h": 120, "alpha": 1.0},
        "route_poe2": {"x": 140, "y": 140, "w": 560, "h": 300, "alpha": 1.0},
        # Map-layout overlay: sized from the picture it shows, so it stores a
        # scale factor rather than a width/height.
        "layout_poe1": {"x": 200, "y": 200, "scale": 1.0, "alpha": 0.9},
        "memo": {"x": 160, "y": 160, "w": 480, "h": 520},
        "price_check": {"x": 300, "y": 180, "w": 640, "h": 660},
    },
    "links": deepcopy(DEFAULT_LINKS),
    "hotkeys": deepcopy(DEFAULT_HOTKEYS),
    "chat_macros": deepcopy(DEFAULT_MACROS),
    "flask": {
        "checkboxes": [True, True, True, True, True, False],
        "custom_key": "w",
        "cooldowns_ms": [2000, 2000, 2000, 2000, 2000],
        "anchor_left": None,   # {"x":.., "y":..} - legacy 2-point placement
        "anchor_right": None,  # kept so an old config still upgrades cleanly
        "overlay_enabled": True,
        # Drag-sized bar-graph area; supersedes anchor_left/right above.
        "overlay_rect": None,  # {"x1":.., "y1":.., "x2":.., "y2":..}
        "bar_color": "#009900",
        "overlay_alpha": 0.85,
    },
    # Q/W/E/R/T skill cooldowns, drawn as their own bar graph in their own
    # place on screen -- flasks and skills sit in different parts of the HUD,
    # so one shared overlay position could never suit both.
    "skills": {
        "cooldowns_ms": [20000, 30000, 0, 0, 0],  # q, w, e, r, t
        "overlay_enabled": True,
        "overlay_rect": None,
        "bar_color": "#ffb03a",
        "overlay_alpha": 0.85,
    },
    "regions": {
        "inventory": None,     # {"x1":.., "y1":.., "x2":.., "y2":..}  12x5
        "stash": None,         # normal stash tab                      12x12
        "quad_stash": None,    # quad stash tab                        24x24
    },
    # Where the character is, read from the client's own log (see zone.py).
    "zone": {
        "enabled": True,
        "log_path": "",  # blank = auto-detect from the running client
        # Hideouts only -- towns are somewhere you pass through mid-run, so
        # having flasks stop working there would be its own surprise.
        "pause_macros_in_hideout": True,
        "hide_overlay_in_hideout": True,
        "extra_safe_zones": [],   # anywhere else to treat like a hideout
        "extra_town_names": [],   # affects the status label only
        "route_follow": True,     # levelling route follows the zone
    },
    # Incoming whisper notifications (see whispers.py, gui/whisper_panel.py).
    "whisper": {
        "enabled": True,
        "open_on_start": True,
        "hide_after_sec": 5,     # cards hide, and the bar fades out with them
        # How visible the bar is with nothing to say. 0 hides it outright;
        # raise it slightly (0.15 or so) to keep a faint marker of where it
        # lives, since at 0 the only way to find it is to remember.
        "idle_opacity": 0.0,
        "keep_minutes": 30,      # older cards are swept up
        # What the three reply buttons send. Editable because the wording is
        # a matter of taste, and because a seller may want Korean instead.
        "replies": {
            "thanks": "Thank you very much! Have a great day.",
            "wait": "Sorry, could you please wait a moment? I will invite you shortly.",
            "sold": "I am sorry, that item is already sold. Thank you for your interest!",
        },
    },
    # Item price checking against the trade site (see trade/).
    "trade": {
        # "kakao" (한국 카카오 거래소) or "official" (pathofexile.com).
        "realm": "kakao",
        # Blank means "whatever the site currently calls the temp league",
        # resolved on first use so a new league needs no setting change.
        "league": "",
        # The site's status option. "available" (즉시 구입 및 직접 거래) rather
        # than a buyout-only search: most currency is listed for direct trade
        # and a buyout filter hides all of it.
        "status": "available",
        # Cheapest first, and only listings put up in the last week: an old
        # listing is usually a seller who has stopped answering, and its
        # price drags the whole comparison down.
        "sort": "price",
        "indexed": "1week",
        "auto_search": True,      # search as soon as the window opens
        # Clicking anywhere that isn't ours -- the game, usually -- closes the
        # price window. On by default because that is what every other
        # transient panel over a game does; off for anyone who wants it to
        # stay put while they click around.
        "close_on_outside_click": True,
        "result_count": 10,
        # Point size for the price window. A setting rather than a constant
        # because how big "readable" is depends on the monitor it lands on.
        "font_size": 13,
        # Currency, fragments, essences and fossils are exchanged in bulk
        # rather than listed one at a time, so they are priced through the
        # site's exchange endpoint instead of a search. Off turns that back
        # into an ordinary search, which is what to do when you want to see
        # who is selling a single one.
        "bulk_exchange": True,
        # The game's own modifier and base-type data, refreshed from the
        # project it is generated by (see trade/gamedata.py). Off leaves the
        # copy that shipped with the program in place, which goes gradually
        # out of date as a league adds modifiers.
        "auto_update_data": True,
    },
    # Tuning for "which cells did the search box highlight". Exposed because
    # the exact colour depends on the monitor/gamma/HDR profile, and a
    # detector that cannot be adjusted is one that silently picks the wrong
    # items -- or nothing at all -- on somebody else's PC. The 좌표 설정
    # tab's 검색 인식 테스트 window edits these against a real screenshot
    # rather than by guesswork. See image_search.py.
    "detection": {
        "edge_coverage": 0.85,   # how much of a cell edge must be highlight
        "min_edges": 2,          # how many of the 4 edges must qualify
        "scan_px": 4,            # +-px to sweep, absorbs calibration drift
        # Sides of the *item* rectangle that must be framed. 4 is ideal
        # but unreachable against the panel border, where one side is
        # clipped; 3 still needs three whole framed sides.
        "min_sides": 3,
        # A single cell boxed in by four highlighted items is
        # indistinguishable from a highlighted one; skipping it is the
        # safe way to be wrong, and it resolves itself on the next pass.
        "skip_enclosed": True,
        "hsv_low": [8, 60, 120],
        "hsv_high": [40, 255, 255],
    },
    # Pacing for the click loops (보관함/인벤토리 이동). Two levels: a random
    # gap between clicks, plus a longer break every few clicks -- a steady
    # rate is a signature even when each individual gap is randomised. Raise
    # these if you want it gentler still.
    "clicking": {
        "delay_ms": [80, 112],      # between clicks -- a narrow band, so
                                    # the rhythm is even but never exact
        "pause_every": [5, 9],      # clicks between the longer breaks
        "pause_ms": [140, 210],     # length of a longer break
        "pass_gap_ms": [220, 340],  # after a pass, before rescanning
    },
    "scrolls": {"identify_point": None},  # fixed inventory slot to place ID scrolls
    "tujen": {"confirm_point": None},
    "currency_cycle": {
        "chance_point": None,
        "scouring_point": None,
        "item_point": None,
        "cycles": 1,
    },
    # Saved map-search regexes (see map_mods.py, gui/regex_tab.py). Each
    # preset keeps both the ticked mod ids and the string they produced: the
    # string is what gets pasted into the game and what the user may have
    # edited by hand, and the ids are what puts the checkboxes back.
    "map_regex": {
        "quote": True,           # wrap the whole pattern in "..."
        "active_id": "danger",
        "presets": [
            {
                "id": "danger",
                "name": "위험 옵션 제외",
                "mode": "exclude",
                # Reflect and no-regen are the two that end a map run outright
                # rather than making it slower; the rest are the usual
                # can't-be-bothered list.
                "mods": ["reflect", "no_regen", "no_leech", "max_res"],
                "extra": "",
                "pattern": '"!반사|재생|흡수|저항 최대치"',
                "hotkey": "",
            },
            {
                "id": "reward",
                "name": "보상 옵션 포함",
                "mode": "include",
                "mods": ["quantity", "pack_size"],
                "extra": "",
                "pattern": r'"수량: \+|무리 규모"',
                "hotkey": "",
            },
        ],
    },
    "memos": [{"id": "memo1", "name": "메모 1", "text": ""}],
    "active_memo_id": "memo1",
    "paths": ["", "", "", "", ""],
    "route": {
        "poe1": {"csv": "list.csv", "last_row": 1},
        "poe2": {"csv": "list2.csv", "last_row": 1},
    },
    # Auto-update against the GitHub releases of this project (see updater.py).
    "update": {
        "check_on_start": True,
        # Off by default: downloading and restarting the app on its own, with
        # no warning, is not something to opt someone into -- especially when
        # the app might be sitting behind a game they are mid-map in.
        "auto_install": False,
        "skipped_version": "",   # "이 버전 건너뛰기" -- one version, not forever
        "last_checked": "",
    },
    "misc": {
        "holdkey": "",
        "minefield_enabled": False,
        "ckey": "q",  # legacy single key; migrated into ckeys[0] on load
        "ckeys": ["없음"] * CONTINUOUS_KEY_SLOTS,
        "ckey_delay_ms": 3000,
        "poe_window_class": "POEWindowClass",
        "scope_to_poe_window": True,
        "start_minimized_to_tray": False,
        # POE1 (Daum) and POE2 (Kakao Games) are separate Korean portals.
        # The POE1 key keeps its original name so existing configs don't
        # lose a customised URL on upgrade.
        "poe_launch_url": "https://poe.game.daum.net/",
        "poe2_launch_url": "https://pathofexile2.kakaogames.com",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.config_path()
        self.data: dict[str, Any] = deepcopy(DEFAULTS)
        self._listeners: list[Callable[[], None]] = []
        self.load()

    def _migrate_map_mod_ids(self) -> None:
        """Saved presets, moved from the grouped map options to the real ones.

        The 맵모드 tab used to offer about sixty hand-grouped options and now
        offers one per modifier the game can actually roll, so the ids a
        preset stores no longer all exist. Dropping the unknown ones would
        turn "위험 옵션 제외" into a filter that quietly stops excluding
        anything, so each old id is replaced by the individual options its
        fragment used to catch -- the same maps, now separately tickable.

        Skipped entirely when the game data is unavailable, because then the
        tab is still showing the old grouped list and the old ids are correct.
        """
        presets = self.data.get("map_regex", {}).get("presets", [])
        if not presets:
            return
        try:
            from . import map_mods

            known = {mod.id for mod in map_mods.all_mods()}
            legacy = map_mods.legacy_ids()
        except Exception:  # noqa: BLE001 - a config load must not fail on this
            return
        if not legacy:
            return
        for preset in presets:
            saved = preset.get("mods")
            if not isinstance(saved, list) or all(i in known for i in saved):
                continue
            expanded: list[str] = []
            for old in saved:
                for new in [old] if old in known else legacy.get(old, []):
                    if new not in expanded:
                        expanded.append(new)
            preset["mods"] = expanded

    def _migrate_paths_to_links(self) -> None:
        """The five 프로그램 경로 slots, folded into the shortcut list.

        Those slots and the shortcut list did the same job -- run a program,
        open a folder -- so the slots were dropped and their tab now points
        at the list. Anything already saved in them has to come with it:
        removing the boxes without this would leave five configured programs
        in config.json with nothing left in the app able to show or run them.

        Emptied afterwards so a second load does not add them again, and
        skipped for anything the list already holds, because a path is very
        likely to have been in both places at once -- which is why the slots
        were redundant in the first place.
        """
        slots = self.data.get("paths")
        if not isinstance(slots, list) or not any(
            isinstance(s, str) and s.strip() for s in slots
        ):
            return
        links = self.data.setdefault("links", [])
        known = {
            (link.get("url") or "").strip().lower()
            for link in links
            if isinstance(link, dict)
        }
        for slot in slots:
            target = (slot or "").strip() if isinstance(slot, str) else ""
            if not target or target.lower() in known:
                continue
            # Named after the file, which is what the shortcut list shows on
            # its button -- the slots themselves had no name to carry over.
            links.append({"name": Path(target).name or target, "url": target})
            known.add(target.lower())
        self.data["paths"] = ["", "", "", "", ""]

    def _migrate(self) -> None:
        """Fold settings from older layouts into their current homes.

        Done on every load rather than once, because a config can also arrive
        through import_from() or be hand-edited between runs.
        """
        misc = self.data["misc"]
        self._migrate_paths_to_links()

        # Single 연속사용키 -> the five-slot list.
        slots = misc.get("ckeys") or []
        if not isinstance(slots, list):
            slots = []
        slots = [s for s in slots if isinstance(s, str)][:CONTINUOUS_KEY_SLOTS]
        slots += ["없음"] * (CONTINUOUS_KEY_SLOTS - len(slots))
        legacy = (misc.get("ckey") or "").strip().lower()
        if legacy and legacy in CONTINUOUS_KEY_CHOICES and all(s == "없음" for s in slots):
            slots[0] = legacy
        misc["ckeys"] = slots

        # Two-point flask overlay anchors -> a real rectangle. The old points
        # marked slot centres, so pad outwards to get a box with height.
        flask = self.data["flask"]
        if not flask.get("overlay_rect"):
            left, right = flask.get("anchor_left"), flask.get("anchor_right")
            if left and right and "x" in left and "x" in right:
                x1, x2 = sorted((int(left["x"]), int(right["x"])))
                y1, y2 = sorted((int(left["y"]), int(right["y"])))
                flask["overlay_rect"] = {
                    "x1": x1 - 20, "y1": y1 - 24, "x2": x2 + 20, "y2": y2 + 24,
                }

        # restart_app moved from a hotkey to a bottom-bar button; leaving the
        # old binding in place would keep F11 restarting the app mid-game.
        self.data["hotkeys"].pop("restart_app", None)

        # The highlight detector moved from "fraction of a fixed-width ring
        # around the cell" to "coverage of each cell edge" (image_search.py).
        # The old keys tuned a measurement that no longer exists, so they are
        # dropped rather than left in the file looking adjustable.
        detection = self.data["detection"]
        for dead in ("min_border_fraction", "border_px"):
            detection.pop(dead, None)

        # The click pacing shipped faster than it should have. A config
        # still carrying those exact values was never a choice anyone made
        # -- it is just the old default written out on first run -- so it is
        # moved up to the current one. A value that differs from the old
        # default was deliberate and is kept.
        superseded = {
            "delay_ms": ([100, 240], [220, 480]),
            "pause_every": ([6, 12],),
            "pause_ms": ([400, 850], [800, 1600]),
            "pass_gap_ms": ([260, 480], [350, 600]),
        }
        clicking = self.data.setdefault("clicking", {})
        for key, old_defaults in superseded.items():
            if any(clicking.get(key) == list(old) for old in old_defaults):
                clicking[key] = list(DEFAULTS["clicking"][key])

        # Map-regex presets shipped in 1.2.2 with fragments that matched more
        # than they named: 최대치 also hit "몬스터의 격분 충전 최대치", 수량 and
        # 희귀도 hit the property lines every map prints, and the pair of them
        # made both starter presets quietly wrong. A preset still carrying the
        # exact string 1.2.2 wrote is not a choice anyone made -- it is the old
        # default sitting where it was put -- so it moves up to the corrected
        # one. Anything edited since differs, and is left alone.
        #
        # Same treatment, and the same reasoning, as the click-pacing defaults
        # above.
        superseded_patterns = {
            '"!반사|재생|흡수|최대치"': '"!반사|재생|흡수|저항 최대치"',
            '"수량|규모"': r'"수량: \+|무리 규모"',
        }
        for preset in self.data.get("map_regex", {}).get("presets", []):
            replacement = superseded_patterns.get(preset.get("pattern", ""))
            if replacement:
                preset["pattern"] = replacement
        self._migrate_map_mod_ids()

        # Zone gating narrowed from "town or hideout" to hideouts only.
        zone_cfg = self.data["zone"]
        for old, new in (
            ("pause_macros_in_town", "pause_macros_in_hideout"),
            ("hide_overlay_in_town", "hide_overlay_in_hideout"),
        ):
            if old in zone_cfg:
                zone_cfg.setdefault(new, zone_cfg[old])
                zone_cfg.pop(old, None)

    def load(self) -> None:
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.data = _deep_merge(DEFAULTS, loaded)
                self._migrate()
            except (json.JSONDecodeError, OSError):
                # Corrupt config: keep defaults but preserve the bad file
                # for inspection instead of silently overwriting it.
                backup = self.path.with_suffix(".json.bak")
                shutil.copy(self.path, backup)
        else:
            self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)
        for listener in list(self._listeners):
            listener()

    def on_change(self, callback: Callable[[], None]) -> None:
        """Register a callback fired after every save() (e.g. hotkey re-registration)."""
        self._listeners.append(callback)

    def export_to(self, target: Path) -> None:
        target.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def import_from(self, source: Path) -> None:
        loaded = json.loads(source.read_text(encoding="utf-8"))
        self.data = _deep_merge(DEFAULTS, loaded)
        self._migrate()
        self.save()

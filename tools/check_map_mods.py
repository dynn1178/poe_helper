"""Check every 맵모드 search fragment against the mods a map can actually roll.

    python tools/check_map_mods.py            # 문제만
    python tools/check_map_mods.py --all      # 조각별 매칭 전부

The fragments in ``poehelper/map_mods.py`` are short on purpose -- the game's
search box holds 50 characters -- and being short is exactly what makes them
easy to get wrong in two opposite ways:

*Too narrow.* The fragment matches no real mod at all, so ticking it filters
out every map. Silent: an empty result looks like "no map has that", which is
also what a correct fragment looks like on a bad batch.

*Too broad.* The fragment matches mods nobody meant, so a map is rejected for
carrying something harmless. Also silent, and worse, because the maps it
throws away were the ones worth running.

Neither can be found by reading the fragment. Both can be found by running it
against the game's own list of what a map can roll, which is what this does:
``fromAreaMods`` in the bundled game data marks every such modifier, with the
Korean text the client prints. See ``trade/gamedata.py``.

A fragment matching several mods is normal and often the point -- one 반사
deliberately covers physical, elemental and hex reflect. What this flags is a
fragment matching *nothing*, and a fragment matching mods from parts of the
game it has no business touching.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from poehelper import map_mods  # noqa: E402
from poehelper.trade import gamedata  # noqa: E402

# Above this, a fragment is almost certainly catching things it did not mean
# to. Deliberately generous: 저항 legitimately appears in a dozen mods.
_BROAD = 12


def corpus(data) -> list[tuple[str, list[str]]]:
    """Every map modifier, with the wordings a search would see.

    ``#`` is what the data writes where a rolled number goes; on the item it
    is a digit, and a fragment must match either way. Replaced with a digit
    rather than stripped, because a fragment may legitimately anchor onto the
    text right after a number.
    """
    out = []
    for ref, wordings in data.area_mods():
        rendered = [w.replace("#", "7") for w in wordings]
        if rendered:
            out.append((ref, rendered))
    return out


def matches(pattern: str, wordings: list[str]) -> bool:
    try:
        return any(re.search(pattern, w) for w in wordings)
    except re.error:
        return False


def report(show_all: bool) -> int:
    data = gamedata.load()
    if data is None:
        print("게임 데이터를 읽지 못했습니다. tools/update_trade_data.py 를 먼저 실행하세요.")
        return 1
    mods = corpus(data)
    print(f"게임이 정의한 맵 모드: {len(mods)}개")
    print(f"정규식 조각: {len(map_mods.MAP_MODS)}개\n")

    dead: list[map_mods.MapMod] = []
    broad: list[tuple[map_mods.MapMod, int]] = []
    covered: set[str] = set()

    # Same answer the 맵모드 tab shows, so the tool and the tab can never
    # disagree about whether an option works.
    found = map_mods.coverage()
    for mod in map_mods.MAP_MODS:
        hits = found.get(mod.id, [])
        if mod.numeric:
            # A property line, printed on every map. Its fragment is checked
            # by the threshold rather than against the modifier list, so it
            # is not a modifier that can be missing.
            if show_all:
                print(f"[{mod.category}] {mod.label}  ← 지도 속성 줄")
            continue
        covered.update(hits)
        if not hits:
            dead.append(mod)
        elif len(hits) > _BROAD:
            broad.append((mod, len(hits)))
        if show_all:
            print(f"[{mod.category}] {mod.label}  ← /{mod.pattern}/  ({len(hits)}개)")
            for ref in hits[:6]:
                print(f"      {ref[:78]}")
            if len(hits) > 6:
                print(f"      … 외 {len(hits) - 6}개")

    if dead:
        print(f"\n■ 실제 맵 모드를 하나도 잡지 못하는 조각 ({len(dead)}개)")
        print("  (해당 옵션을 체크하면 모든 지도가 걸러집니다)")
        for mod in dead:
            print(f"   {mod.id:18} /{mod.pattern}/   {mod.label}")

    if broad:
        print(f"\n■ 지나치게 많이 잡는 조각 ({len(broad)}개, 기준 {_BROAD}개 초과)")
        for mod, count in sorted(broad, key=lambda p: -p[1]):
            print(f"   {mod.id:18} /{mod.pattern}/   {count}개   {mod.label}")

    missing = [ref for ref, _w in mods if ref not in covered]
    print(f"\n■ 어떤 조각으로도 잡히지 않는 맵 모드: {len(missing)}개 / {len(mods)}개")
    if show_all:
        for ref in missing:
            print(f"   {ref[:88]}")

    return 1 if dead else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="조각별 매칭을 모두 출력")
    sys.exit(report(parser.parse_args().all))

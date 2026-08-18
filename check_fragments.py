"""Check every map-mod fragment against real copied maps.

    python check_fragments.py            # check, and say what failed
    python check_fragments.py --verbose  # ...and list every match, not just bad ones

A fragment in ``poehelper/map_mods.py`` is a few characters of Korean, and a
copied map carries far more text than its mods: the affix headers
(``{ 접두어 속성 부여 "뚫리지 않는" — 물리, 카오스, 공격, 상태 이상 }``), the
rules blurb printed under a curse (``(취약성은 받는 물리 피해를...)``), and the
property lines every map has (``몬스터 레벨: 83``). A short fragment lands on
those just as happily as on the mod it was named after, and from the outside
the two are indistinguishable -- the map is simply filtered, or not, for a
reason nobody can see.

That is not hypothetical: 1.2.2 shipped with 카오스 as the fragment for
"monsters gain extra chaos damage", and it matched an affix's damage-type tag
and a 중독 explanation on maps whose monsters gain no chaos damage at all.

So: **a fragment may only land on 속성 (mod) or 정보 (property) lines.** A hit
on 설명 or 구분 is a failure, and the fix is to anchor the fragment further --
취약성 저주 rather than 취약 -- not to accept it.

Add samples by copying a map in game (Ctrl+C over it) and saving the text into
``data/map_samples/``. The more varied the affixes, the more this catches; a
map with curses and ailments on it is worth more here than three plain ones.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "data" / "map_samples"

sys.path.insert(0, str(ROOT))
from poehelper.map_mods import (  # noqa: E402  (needs ROOT on the path first)
    MAP_MODS,
    MOD_LINE,
    PROPERTY_LINE,
    classify_line,
)

# The kinds a fragment is allowed to land on. 속성 is the mod itself; 정보 is
# a property line, which is a legitimate target for the reward fragments
# (아이템 수량 is only ever printed as a property).
ALLOWED = {MOD_LINE, PROPERTY_LINE}

_GREEN, _RED, _DIM, _OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


def samples() -> list[tuple[str, list[str]]]:
    if not SAMPLES.is_dir():
        return []
    found = []
    for path in sorted(SAMPLES.glob("*.txt")):
        lines = [
            line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        found.append((path.name, lines))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", action="store_true",
                        help="정상적으로 걸린 것도 전부 출력")
    args = parser.parse_args()

    found = samples()
    if not found:
        print(f"{_RED}샘플이 없습니다:{_OFF} {SAMPLES}")
        print("게임에서 지도를 Ctrl+C 로 복사해 이 폴더에 .txt 로 저장하세요.")
        return 1

    failures = 0
    for name, lines in found:
        print(f"\n{_DIM}== {name} ({len(lines)}줄) =={_OFF}")
        for mod in MAP_MODS:
            try:
                hits = [
                    (classify_line(line), line.strip())
                    for line in lines
                    if re.search(mod.pattern, line)
                ]
            except re.error as exc:
                print(f"  {_RED}정규식 오류{_OFF} {mod.id}: {mod.pattern!r} ({exc})")
                failures += 1
                continue

            bad = [hit for hit in hits if hit[0] not in ALLOWED]
            if not hits and not args.verbose:
                continue
            if bad:
                failures += 1
                print(f"  {_RED}실패{_OFF} {mod.id} {mod.pattern!r} — {mod.label}")
            elif args.verbose:
                print(f"  {_GREEN}정상{_OFF} {mod.id} {mod.pattern!r}")
            else:
                continue
            for kind, line in hits:
                mark = _RED if (kind, line) in bad else _DIM
                print(f"        {mark}[{kind}]{_OFF} {line[:88]}")

    print()
    if failures:
        print(f"{_RED}{failures}개 조각이 실제 옵션이 아닌 줄에 걸립니다.{_OFF}")
        print("map_mods.py 에서 해당 조각을 더 좁게 (예: 취약 -> 취약성 저주) 고치세요.")
        return 1
    print(f"{_GREEN}모든 조각이 실제 옵션 줄에만 걸립니다.{_OFF} "
          f"({len(MAP_MODS)}개 조각 × 샘플 {len(found)}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

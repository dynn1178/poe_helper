"""Level-up route CSV loading/saving.

Each row is ``index,text`` (POE1's ``list.csv``) or ``index,text,`` with a
trailing empty field (POE2's ``list2.csv`` -- both are handled the same way
since we only ever look at the first two comma-separated fields).

Act boundaries are detected from the text itself (``A01-02`` / ``(A3_89%)``
style prefixes already present in the source data) instead of the original
AHK's hardcoded row numbers, so this works unmodified for either CSV and
survives rows being added/removed later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ACT_PATTERN = re.compile(r"^\(?A(\d+)[-_]")


@dataclass
class RouteStep:
    index: int
    text: str


def read_text_auto(path: Path) -> str:
    """UTF-8 first (this project's own files), CP949 fallback (in case a
    legacy AHK-era CSV is dropped in as-is)."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949")


def load_route(path: Path) -> list[RouteStep]:
    text = read_text_auto(path)
    steps: list[RouteStep] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        idx_str, _, rest = line.partition(",")
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if rest.endswith(","):
            rest = rest[:-1]
        steps.append(RouteStep(index=idx, text=rest))
    return steps


def save_route(path: Path, steps: list[RouteStep]) -> None:
    lines = [f"{s.index},{s.text}" for s in steps]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_raw_text(path: Path) -> str:
    return read_text_auto(path)


def save_raw_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


_BRACKET_RE = re.compile(r"\[(.+?)\]")


def step_places(step: RouteStep) -> set[str]:
    """Bracketed names in a route line.

    The route text already marks every place, boss, item and action in square
    brackets -- "[해안 지대]에 들어가", "[힐록] 처치". No attempt is made here
    to tell which is which: callers match these against a real zone name, and
    a boss or item name simply never equals one, so the wrong kind of token
    can't produce a false match.
    """
    return {t.strip() for t in _BRACKET_RE.findall(step.text) if t.strip()}


def leading_place(step: RouteStep) -> str:
    """The first bracketed name in a line, or "".

    Route lines that *happen in* a zone lead with it ("[물에 잠긴 길]에 들어가
    …"), whereas lines that merely mention one are pointing at it from
    somewhere else ("… [물에 잠긴 길]을 엽니다"). That ordering is the only
    hint the text gives about which step you are actually standing in.
    """
    match = _BRACKET_RE.search(step.text)
    return match.group(1).strip() if match else ""


# How far past the first mention to look for a step that *leads* with the
# zone. Deliberately small. Entering 물에 잠긴 길 should skip the "open the
# way to it" line and land on the "enter it" line one row later -- but 갯벌 is
# named in passing in Act 1 and not led with until Act 6, 191 rows away, and
# jumping there would be far worse than staying put.
_LEAD_LOOKAHEAD = 3


def find_step_for_zone(
    steps: list[RouteStep], zone_name: str, from_pos: int = 0
) -> int | None:
    """Position of the step to show for *zone_name*, or None.

    Searches forward from ``from_pos`` and only then wraps: a levelling route
    revisits zones (해안 지대 is at both step 3 and step 6) and the one wanted
    is essentially always the next ahead, not the first in the file.
    """
    zone_name = (zone_name or "").strip()
    if not zone_name or not steps:
        return None

    order = list(range(from_pos, len(steps))) + list(range(0, from_pos))
    for i, pos in enumerate(order):
        if zone_name not in step_places(steps[pos]):
            continue
        # Found a mention. Prefer a step that leads with this zone if one sits
        # just after it -- that is the step you are standing in, rather than
        # the one that told you how to get here.
        for lookahead in order[i:i + 1 + _LEAD_LOOKAHEAD]:
            if leading_place(steps[lookahead]) == zone_name:
                return lookahead
        return pos
    return None


def act_boundaries(steps: list[RouteStep]) -> list[tuple[str, int]]:
    """[(label, position_in_steps), ...] for the first row of each act found,
    in the order they first appear."""
    boundaries: list[tuple[str, int]] = []
    seen: set[str] = set()
    for pos, step in enumerate(steps):
        m = _ACT_PATTERN.match(step.text)
        if not m:
            continue
        act_num = m.group(1)
        if act_num in seen:
            continue
        seen.add(act_num)
        boundaries.append((f"Act {int(act_num)}", pos))
    return boundaries

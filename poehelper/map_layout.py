"""Zone name (as the client logs it) -> act-layout image in ``act_layout/``.

The files are named ``액트<n>_<zone with spaces as underscores>.png``, e.g.
``액트1_해안_지대.png`` for the zone ``zone.py`` reads out of the client log as
"해안 지대". This module is the whole mapping between those two worlds, kept
separate from the overlay window so the matching can be checked without a
screen.

The act half of the filename is not decoration -- PoE1's acts 6-10 revisit
the act 1-5 areas under the *same zone names* with different layouts, and 21
of the 100 zones here collide that way (해안 지대 is act 1 and act 6, 루나리스
사원 1층 is act 3 and act 8, ...). The log line carries only the bare zone
name, so a collision cannot be resolved from the current line alone.

It can be resolved from the ones before it: most zones are unique to one act,
so entering any of them pins which half of the campaign the character is in,
and that answer stays good until the next unambiguous zone. ``choose()``
takes that remembered act as a hint. When the hint is missing or wrong the
lookup still returns something sensible (the earliest act), and the overlay
puts a manual act switch next to the picture -- a guess the user can see and
override beats a guess presented as fact.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import paths

logger = logging.getLogger(__name__)

_ACT_RE = re.compile(r"(\d+)")


@dataclass(frozen=True)
class Layout:
    """One layout image: which act it belongs to and what it shows."""

    act: int
    zone: str
    path: Path

    @property
    def act_label(self) -> str:
        return f"액트{self.act}"


def layout_dir() -> Path:
    return paths.layout_dir()


def _key(name: str) -> str:
    """Match key for a zone name.

    Whitespace and underscores are dropped rather than normalised, so the
    log's "해안 지대", a filename's "해안_지대" and a hand-typed "해안지대"
    are all the same key -- the spacing of a Korean zone name is exactly the
    kind of detail that differs between two sources for no useful reason.
    """
    return re.sub(r"[\s_]+", "", (name or "")).strip().lower()


@lru_cache(maxsize=1)
def index() -> dict[str, tuple[Layout, ...]]:
    """Match key -> every layout with that zone name, earliest act first."""
    directory = layout_dir()
    found: dict[str, list[Layout]] = {}
    try:
        files = sorted(directory.glob("*.png"))
    except OSError:
        logger.warning("could not read layout directory %s", directory, exc_info=True)
        return {}
    for path in files:
        act_part, _, zone_part = path.stem.partition("_")
        match = _ACT_RE.search(act_part)
        if not match or not zone_part:
            # Not "액트N_지역" -- leave it alone rather than guessing.
            logger.debug("skipping unrecognised layout filename: %s", path.name)
            continue
        zone = zone_part.replace("_", " ")
        found.setdefault(_key(zone), []).append(
            Layout(act=int(match.group(1)), zone=zone, path=path)
        )
    logger.info("act layouts: %d images, %d zones (%s)", len(files), len(found), directory)
    return {key: tuple(sorted(v, key=lambda item: item.act)) for key, v in found.items()}


def refresh() -> None:
    """Re-scan the directory (images can be added without restarting)."""
    index.cache_clear()


def candidates(zone: str) -> tuple[Layout, ...]:
    """Every layout matching *zone*, earliest act first. Empty if unknown."""
    if not zone:
        return ()
    return index().get(_key(zone), ())


def choose(zone: str, act_hint: int | None = None) -> Layout | None:
    """The best layout for *zone*, biased towards *act_hint*.

    Nearest act rather than exact match: the colliding pairs are always one
    early act and one late one, so a hint of act 6 should land on 루나리스
    사원's act 8 copy even though act 6 is not one of the choices -- an exact
    match test would fall through to the earliest act and show the act 3
    layout to someone three quarters of the way through the campaign. Ties
    go to the earlier act, which is where a fresh character starts.
    """
    options = candidates(zone)
    if not options:
        return None
    if act_hint is None:
        return options[0]
    return min(options, key=lambda layout: (abs(layout.act - act_hint), layout.act))


def act_hint_from(zone: str) -> int | None:
    """The act *zone* pins down, or None if it is shared between acts.

    Only unambiguous zones answer, which is the point: a zone that exists in
    two acts teaches nothing about which one we are in, and letting it
    overwrite the hint would make the hint follow the guess rather than the
    evidence.
    """
    options = candidates(zone)
    return options[0].act if len(options) == 1 else None

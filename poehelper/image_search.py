"""Screen search for search-highlighted stash cells.

Replaces AHK's ``ImageSearch``. The original hunted for the four yellow
corner bitmaps (``line5.bmp``..``line8.bmp``) around a highlighted item, and
template matching on a corner sprite is a weak test: ``matchTemplate``
reports a best match with a similarity score, and plenty of ordinary item art
(gold rings, brass fittings, amulet chains -- a jewellery tab is full of
them) scores above any threshold low enough to still catch real highlights.

What actually distinguishes a highlighted item is not a corner sprite but a
shape: the search box draws one continuous bright frame around the item's
whole extent, running along the cell edges. Item art can be gold in the
middle of a cell; it does not draw an unbroken line down a cell's edge.

Measuring that frame across machines
------------------------------------
Two things differ from PC to PC and both used to break this:

*The frame's thickness.* It scales with resolution and UI scale -- 1px on a
1080p window, 3px at 4K. The previous version measured "what fraction of a
3px-deep ring around the cell is highlight-coloured", which is really a
measurement of thickness/depth: a 1px frame scored 0.33 against a 0.35
threshold and vanished, while a 3px one scored 1.0. Here each cell edge is
probed with a *1px line*, swept across a few offsets, keeping the best. A
frame of any thickness >= 1px scores ~1.0 on its own line, so the test stops
depending on how thick the game drew it.

*Where the grid actually is.* A calibrated region is a hand-dragged
rectangle, so cell boundaries land a pixel or two off the drawn ones -- and
at the far corner of a 24x24 quad tab, rounding has accumulated. Sweeping
the probe line over +-``scan_px`` absorbs that, which a fixed ring could not.
The capture is also taken with a margin around the region (see
``grab_with_margin``), because the outermost cells otherwise have nothing on
their outer side to sweep into: a region dragged 2px inside the grid leaves
every first-column cell unable to score on its left edge at all.

And what is measured along that line is the longest *unbroken* run, not the
count of matching pixels. That is the difference between a frame and item
art: the search box draws one continuous line down the whole edge, whereas a
gold ring or a brass fitting scatters gold pixels near it. Counting pixels
rates "a solid line over 60% of the edge" and "gold speckle over 60% of the
edge" identically, and in a jewellery tab the speckle alone was enough to
mark 178 unhighlighted cells as matches.

The colour band is centred on the highlight colour measured out of the
original AHK sprites (BGR 119,180,231 -> HSV hue 16). The band this module
shipped with started at hue 18, i.e. it excluded the very colour it was
looking for, and matched nothing at all.
"""
from __future__ import annotations

import logging

import cv2
import mss
import numpy as np

logger = logging.getLogger(__name__)

# OpenCV HSV: hue is 0-179 (not 0-359). resources/line5.bmp -- the highlight
# sprite the original script searched for -- is HSV (16, 124, 231). The band
# is centred on that with room either side for the game's own antialiasing
# and for monitors/HDR profiles that shift it slightly; the stash chrome it
# has to be told apart from is desaturated blue-grey, nowhere near this hue.
HIGHLIGHT_HSV_LOW = (8, 60, 120)
HIGHLIGHT_HSV_HIGH = (40, 255, 255)

# How much of one cell edge must be one unbroken highlight-coloured run for
# that edge to count. A real frame covers effectively all of it; the slack is
# for corner rounding and for a cell edge clipped by the region rectangle.
EDGE_COVERAGE = 0.60

# How many of a cell's four edges must qualify. Two, not three, because an
# item bigger than 1x1 is framed once around the whole block: every cell of a
# 2x2 item has exactly two framed edges. Cells that pick up two edges from
# *neighbouring* highlights are always adjacent to a real hit, so
# ``group_cells`` folds them into that hit rather than producing a stray
# click.
MIN_EDGES = 2

# Pixels either side of a cell edge to sweep the probe line across. This is
# the detector's whole tolerance for a mis-dragged region, and it is a cliff
# rather than a slope: at exactly this far out everything still matches, one
# pixel further and nothing does. 4 covers the drag error a person actually
# makes; measured against the simulator, going from 2 to 4 buys +-2px of
# slack and produced no false positives at any cell size.
SCAN_PX = 4

# ...but capped to a fraction of the cell, so that on a small grid the sweep
# cannot reach so far past the edge that it starts finding the neighbouring
# cell's art. 15% of a 22px cell is 3px; of a 64px cell, the cap never binds.
SCAN_MAX_CELL_FRACTION = 0.15


def grab_region(x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    with mss.mss() as sct:
        shot = sct.grab({"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1})
        return np.array(shot)[:, :, :3]  # drop alpha, BGR order matches cv2


def highlight_mask(
    image: np.ndarray,
    hsv_low: tuple[int, int, int] = HIGHLIGHT_HSV_LOW,
    hsv_high: tuple[int, int, int] = HIGHLIGHT_HSV_HIGH,
) -> np.ndarray:
    """Binary mask (0/1) of pixels that are highlight-coloured."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_low, np.uint8), np.array(hsv_high, np.uint8))
    return (mask > 0).astype(np.uint8)


def _longest_run(line: np.ndarray) -> int:
    """Length of the longest unbroken stretch of set pixels in a 1-D mask."""
    if line.size == 0 or not line.any():
        return 0
    padded = np.concatenate(([0], line, [0])).astype(np.int8)
    change = np.diff(padded)
    starts = np.flatnonzero(change == 1)
    ends = np.flatnonzero(change == -1)
    if starts.size == 0:
        return 0
    return int((ends - starts).max())


def _edge_score(
    mask: np.ndarray, x0: int, x1: int, y0: int, y1: int,
    horizontal: bool, scan: int,
) -> float:
    """Best *unbroken* highlight run along a 1px line, swept +-``scan`` px.

    The run length, not the total count, is what separates a frame from item
    art. The search box draws one continuous line down the whole cell edge;
    a gold ring or a brass fitting scatters gold pixels that happen to lie
    near that edge. Counting pixels treats "60% of the edge is one solid
    line" and "60% of the edge is gold speckle" as the same thing, and in a
    jewellery tab the speckle wins often enough to drag unhighlighted items
    out of the stash.

    Sweeping is what makes this tolerant of a slightly misplaced region: the
    frame only has to be found *somewhere near* the cell edge, not exactly on
    the pixel the grid maths landed on.
    """
    height, width = mask.shape[:2]
    best = 0.0
    if horizontal:
        length = x1 - x0
        if length <= 0:
            return 0.0
        for offset in range(-scan, scan + 1):
            y = y0 + offset
            if y < 0 or y >= height:
                continue
            best = max(best, _longest_run(mask[y, x0:x1]) / length)
    else:
        length = y1 - y0
        if length <= 0:
            return 0.0
        for offset in range(-scan, scan + 1):
            x = x0 + offset
            if x < 0 or x >= width:
                continue
            best = max(best, _longest_run(mask[y0:y1, x]) / length)
    return best


def cell_edge_scores(
    image: np.ndarray, cols: int, rows: int, *,
    grid: tuple[int, int, int, int] | None = None,
    scan_px: int = SCAN_PX,
    hsv_low: tuple[int, int, int] = HIGHLIGHT_HSV_LOW,
    hsv_high: tuple[int, int, int] = HIGHLIGHT_HSV_HIGH,
) -> np.ndarray:
    """``(rows, cols, 4)`` coverage per cell edge, ordered top/bottom/left/right.

    ``grid`` is ``(x, y, w, h)`` locating the cell grid inside *image*, and
    defaults to the whole image. It exists so *image* can be captured with a
    margin around the grid: without one, the outermost cells have no pixels
    on their outer side to sweep into, so a first-column cell can never score
    on its left edge no matter how the region was dragged, and the whole
    border of the grid silently measures one edge short.

    Split out from the decision so the diagnostics window can show the raw
    measurements rather than just a yes/no per cell.
    """
    height, width = image.shape[:2]
    scores = np.zeros((rows, cols, 4), np.float32)
    if height < 4 or width < 4:
        return scores
    gx, gy, gw, gh = grid if grid is not None else (0, 0, width, height)

    mask = highlight_mask(image, hsv_low, hsv_high)

    cell_w = gw / cols
    cell_h = gh / rows
    scan_px = max(1, min(scan_px, int(min(cell_w, cell_h) * SCAN_MAX_CELL_FRACTION)))
    for r in range(rows):
        cy0, cy1 = gy + int(round(r * cell_h)), gy + int(round((r + 1) * cell_h))
        for c in range(cols):
            cx0, cx1 = gx + int(round(c * cell_w)), gx + int(round((c + 1) * cell_w))
            scores[r, c] = (
                _edge_score(mask, cx0, cx1, cy0, cy1, True, scan_px),
                _edge_score(mask, cx0, cx1, cy1 - 1, cy1, True, scan_px),
                _edge_score(mask, cx0, cx0, cy0, cy1, False, scan_px),
                _edge_score(mask, cx1 - 1, cx1, cy0, cy1, False, scan_px),
            )
    return scores


def find_highlighted_cells(
    region: tuple[int, int, int, int],
    cols: int,
    rows: int,
    *,
    edge_coverage: float = EDGE_COVERAGE,
    min_edges: int = MIN_EDGES,
    scan_px: int = SCAN_PX,
    hsv_low: tuple[int, int, int] = HIGHLIGHT_HSV_LOW,
    hsv_high: tuple[int, int, int] = HIGHLIGHT_HSV_HIGH,
    image: np.ndarray | None = None,
) -> list[tuple[int, int]]:
    """``(col, row)`` of every cell the search box has framed.

    One screenshot covers the whole grid, so this replaces the old "search
    the region again for each of four templates, ten times over" loop with a
    single measurement pass.
    """
    return scan(
        region, cols, rows, edge_coverage=edge_coverage, min_edges=min_edges,
        scan_px=scan_px, hsv_low=hsv_low, hsv_high=hsv_high, image=image,
    ).hits


TOP, BOTTOM, LEFT, RIGHT = 0, 1, 2, 3

# Path of Exile items are at most 2 cells wide and 4 tall (a two-handed
# weapon). Bounding the search keeps two separate items from ever being read
# as one big rectangle, and keeps the scan cheap.
MAX_ITEM_W, MAX_ITEM_H = 2, 4

# How many of an item's four sides must be framed. Four is the honest test and
# is always preferred where it holds, but against the stash panel's own border
# -- the leftmost column above all -- one side is clipped or drawn over and
# never appears, so requiring all four drops those items every single time.
# Three whole sides of an item is still something no item art produces.
MIN_ITEM_SIDES = 3


class Item:
    """One highlighted item: the whole rectangle its frame encloses."""

    __slots__ = ("col", "row", "width", "height")

    def __init__(self, col: int, row: int, width: int, height: int):
        self.col, self.row = col, row
        self.width, self.height = width, height

    @property
    def cells(self) -> list[tuple[int, int]]:
        return [(self.col + c, self.row + r)
                for c in range(self.width) for r in range(self.height)]

    @property
    def centre(self) -> tuple[float, float]:
        """Cell coordinates of the item's middle, for the click."""
        return (self.col + self.width / 2, self.row + self.height / 2)

    def __repr__(self) -> str:
        return f"Item({self.col},{self.row},{self.width}x{self.height})"


def _rect_sides(scores: np.ndarray, c: int, r: int, w: int, h: int, cov: float) -> int:
    """How many of this block's four sides are framed along their whole length."""
    sides = 0
    if all(scores[r, cc, TOP] >= cov for cc in range(c, c + w)):
        sides += 1
    if all(scores[r + h - 1, cc, BOTTOM] >= cov for cc in range(c, c + w)):
        sides += 1
    if all(scores[rr, c, LEFT] >= cov for rr in range(r, r + h)):
        sides += 1
    if all(scores[rr, c + w - 1, RIGHT] >= cov for rr in range(r, r + h)):
        sides += 1
    return sides


def find_items(
    scores: np.ndarray,
    edge_coverage: float = EDGE_COVERAGE,
    *,
    min_sides: int = MIN_ITEM_SIDES,
    skip_enclosed: bool = True,
) -> list[Item]:
    """Every closed frame in the grid, as items rather than cells.

    This is "all four sides must be framed" applied where it actually holds
    -- around the item -- instead of around each cell. Per cell it cannot
    hold: an item bigger than 1x1 is framed once around the whole block, so
    every cell of a 1x3 has at most three framed edges and a 2x2 has exactly
    two. Demanding four per cell therefore threw away every multi-cell item,
    while demanding two let in any cell that merely had framed neighbours.
    Matching the rectangle gets both: nothing is accepted without a complete
    frame, and the frame is allowed to be the size of the item.

    Smallest rectangle first, and cells are consumed once claimed, so two
    stacked items are never merged into the taller rectangle that would also
    technically be closed.

    ``min_sides`` is how many of the four have to be there. Four is the ideal
    and is always preferred when it is available, but it is not always
    achievable: against the stash panel's own edge -- the leftmost column
    especially -- one side of the frame is drawn over, clipped by the panel
    border, or simply outside anything that can be captured, and demanding it
    loses those items every time. Three still needs a frame on three whole
    sides of the item, which no item art produces.
    """
    rows, cols = scores.shape[:2]
    sizes = sorted(
        ((w, h) for w in range(1, MAX_ITEM_W + 1) for h in range(1, MAX_ITEM_H + 1)),
        key=lambda wh: (wh[0] * wh[1], wh[1], wh[0]),
    )
    min_sides = max(1, min(4, int(min_sides)))
    used: set[tuple[int, int]] = set()
    items: list[Item] = []
    for r in range(rows):
        for c in range(cols):
            if (c, r) in used:
                continue
            # A cheap gate before any rectangle is tried. Not "top *and* left"
            # any more: an item against the panel edge can be missing one of
            # them entirely, which is the whole reason min_sides exists.
            if (scores[r, c, TOP] < edge_coverage
                    and scores[r, c, LEFT] < edge_coverage):
                continue

            fallback: tuple[int, int] | None = None
            chosen: tuple[int, int] | None = None
            for w, h in sizes:
                if c + w > cols or r + h > rows:
                    continue
                if any((c + dc, r + dr) in used
                       for dc in range(w) for dr in range(h)):
                    continue
                sides = _rect_sides(scores, c, r, w, h, edge_coverage)
                if sides == 4:
                    chosen = (w, h)  # a complete frame always wins
                    break
                if sides >= min_sides and fallback is None:
                    # Remembered, but keep looking: a 1x2 item's top cell
                    # shows three sides on its own, and taking that would
                    # report the item as half its real size.
                    fallback = (w, h)
            chosen = chosen or fallback
            if chosen is not None:
                item = Item(c, r, *chosen)
                used.update(item.cells)
                items.append(item)
    if skip_enclosed:
        items = _drop_enclosed(items, cols, rows, min_sides)
    return items


def _drop_enclosed(
    items: list[Item], cols: int, rows: int, min_sides: int = MIN_ITEM_SIDES
) -> list[Item]:
    """Discard 1x1 items that are boxed in on all four sides by other items.

    A single cell surrounded by highlighted neighbours is *pixel-identical*
    to a highlighted cell: its four edges are the neighbours' frame lines,
    the very same pixels. Nothing in the image can separate the two cases, so
    the choice is which way to be wrong. Dropping it is the safe way -- a
    wrongly moved item has to be found and put back, a missed one does not --
    and it is self-correcting: once the neighbours have been pulled out, the
    cell is no longer enclosed, and the next pass sees it for what it is.
    """
    owned: dict[tuple[int, int], Item] = {}
    for item in items:
        for cell in item.cells:
            owned[cell] = item
    kept = []
    for item in items:
        if item.width == 1 and item.height == 1:
            c, r = item.col, item.row
            neighbours = [(c, r - 1), (c, r + 1), (c - 1, r), (c + 1, r)]
            # Counted against min_sides, not against four: if three framed
            # sides are enough to accept a cell, then three framed neighbours
            # are enough to have produced those sides without the cell being
            # highlighted at all.
            borrowed = sum(
                1 for n in neighbours
                if owned.get(n) is not None and owned[n] is not item
            )
            if borrowed >= min_sides:
                logger.debug("skipping enclosed cell %s (%d framed neighbours)",
                             item, borrowed)
                continue
        kept.append(item)
    return kept


def group_cells(
    cells: list[tuple[int, int]],
    scores: np.ndarray | None = None,
    edge_coverage: float = EDGE_COVERAGE,
) -> list[tuple[int, int]]:
    """One representative cell per contiguous block of matched cells.

    An item larger than 1x1 is framed as a single rectangle, so every cell
    along that frame matches. Clicking each of them would move the item on
    the first click and then click empty space for the rest, so collapse each
    connected group to one cell.

    Which cell matters. Taking the plain top-left of the group picks the
    wrong one whenever an *unhighlighted* cell has highlighted neighbours to
    its right and below: those two frames light two of its edges, it matches,
    and it sorts ahead of the real items -- so the click landed on the empty
    cell between them and moved whatever was sitting there, while the two
    actual matches were left behind. Given ``scores``, the representative is
    instead the top-left cell whose *own* top and left edges are lit, which
    is the corner of a real frame and never that in-between cell.
    """
    remaining = set(cells)
    reps: list[tuple[int, int]] = []
    while remaining:
        seed = min(remaining)
        stack = [seed]
        group = []
        remaining.discard(seed)
        while stack:
            c, r = stack.pop()
            group.append((c, r))
            for neighbour in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if neighbour in remaining:
                    remaining.discard(neighbour)
                    stack.append(neighbour)

        if scores is not None:
            corners = [
                (c, r) for (c, r) in group
                if scores[r, c, TOP] >= edge_coverage
                and scores[r, c, LEFT] >= edge_coverage
            ]
            reps.append(min(corners) if corners else min(group))
        else:
            reps.append(min(group))
    return sorted(reps)


class ScanResult:
    """What one look at the grid found.

    ``hits`` is every matching cell (what the test window draws), ``targets``
    is the cells to actually click, and ``scores`` is the raw per-edge
    measurement behind both. ``image``/``grid`` are what was actually
    captured and where the grid sits inside it, so a caller that wants to
    draw the result does not have to recreate the capture.
    """

    __slots__ = ("hits", "targets", "scores", "image", "grid", "items")

    def __init__(self, hits, targets, scores, image=None, grid=(0, 0, 0, 0), items=()):
        self.hits = hits
        self.targets = targets
        self.scores = scores
        self.image = image
        self.grid = grid
        self.items = list(items)


def grab_with_margin(
    region: tuple[int, int, int, int], margin: int
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Capture *region* plus a margin, clamped to the virtual desktop.

    Returns the image and where the original region landed inside it. The
    margin is what gives the outermost cells something to sweep into; the
    clamp matters because a stash flush against the left edge of the screen
    cannot be over-captured, and asking for negative coordinates would grab
    the wrong pixels rather than fail.
    """
    x1, y1, x2, y2 = region
    with mss.mss() as sct:
        desktop = sct.monitors[0]  # union of all monitors
        left = max(desktop["left"], x1 - margin)
        top = max(desktop["top"], y1 - margin)
        right = min(desktop["left"] + desktop["width"], x2 + margin)
        bottom = min(desktop["top"] + desktop["height"], y2 + margin)
        shot = sct.grab({
            "left": left, "top": top,
            "width": max(1, right - left), "height": max(1, bottom - top),
        })
        image = np.array(shot)[:, :, :3]
    return image, (x1 - left, y1 - top, x2 - x1, y2 - y1)


def scan(
    region: tuple[int, int, int, int],
    cols: int,
    rows: int,
    *,
    edge_coverage: float = EDGE_COVERAGE,
    min_edges: int = MIN_EDGES,
    scan_px: int = SCAN_PX,
    hsv_low: tuple[int, int, int] = HIGHLIGHT_HSV_LOW,
    hsv_high: tuple[int, int, int] = HIGHLIGHT_HSV_HIGH,
    image: np.ndarray | None = None,
    grid: tuple[int, int, int, int] | None = None,
    min_sides: int = MIN_ITEM_SIDES,
    skip_enclosed: bool = True,
) -> ScanResult:
    """Measure the grid once and derive the lit cells and the items."""
    if image is None:
        image, grid = grab_with_margin(region, max(1, scan_px))
    if grid is None:
        grid = (0, 0, image.shape[1], image.shape[0])
    if image.shape[0] < 4 or image.shape[1] < 4:
        return ScanResult([], [], np.zeros((rows, cols, 4), np.float32), image, grid)

    scores = cell_edge_scores(
        image, cols, rows, grid=grid, scan_px=scan_px,
        hsv_low=hsv_low, hsv_high=hsv_high,
    )
    # Cells with *some* frame on them: what the test window shows, and the
    # answer to "is anything being seen at all".
    qualifying = (scores >= edge_coverage).sum(axis=2)
    hits = [(int(c), int(r)) for r, c in zip(*np.nonzero(qualifying >= min_edges))]
    # What actually gets clicked: complete frames only.
    items = find_items(scores, edge_coverage, min_sides=min_sides,
                       skip_enclosed=skip_enclosed)
    targets = sorted((item.col, item.row) for item in items)
    logger.debug(
        "highlight scan: %d/%d cells lit, %d items",
        len(hits), cols * rows, len(items),
    )
    return ScanResult(hits, targets, scores, image, grid, items)


def options_from_config(detection: dict | None) -> dict:
    """Detector keyword arguments from the ``detection`` config section.

    One place that knows the key names and the fallbacks, so a config written
    by an older build (or hand-edited into nonsense) degrades to the shipped
    defaults instead of raising inside a hotkey handler.
    """
    detection = detection or {}

    def _hsv(key: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        value = detection.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 3:
            try:
                return (int(value[0]), int(value[1]), int(value[2]))
            except (TypeError, ValueError):
                pass
        return fallback

    def _num(key: str, fallback, cast):
        try:
            return cast(detection.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    return {
        "edge_coverage": _num("edge_coverage", EDGE_COVERAGE, float),
        "min_edges": _num("min_edges", MIN_EDGES, int),
        "scan_px": _num("scan_px", SCAN_PX, int),
        "hsv_low": _hsv("hsv_low", HIGHLIGHT_HSV_LOW),
        "hsv_high": _hsv("hsv_high", HIGHLIGHT_HSV_HIGH),
        "min_sides": _num("min_sides", MIN_ITEM_SIDES, int),
        "skip_enclosed": bool(detection.get("skip_enclosed", True)),
    }


def annotate(
    image: np.ndarray, cols: int, rows: int, hits: list[tuple[int, int]],
    targets: list[tuple[int, int]] | None = None,
    grid: tuple[int, int, int, int] | None = None,
    items: list[Item] | None = None,
) -> np.ndarray:
    """Copy of *image* with the grid drawn and detected cells marked.

    For the 검색 인식 테스트 window: "it found nothing" and "it found the
    wrong things" need completely different fixes, and only a picture tells
    them apart on someone else's machine.
    """
    out = image.copy()
    height, width = out.shape[:2]
    gx, gy, gw, gh = grid if grid is not None else (0, 0, width, height)
    cell_w, cell_h = gw / cols, gh / rows
    hit_set, target_set = set(hits), set(targets or [])

    for c in range(cols + 1):
        x = min(width - 1, gx + int(round(c * cell_w)))
        cv2.line(out, (x, gy), (x, gy + gh - 1), (70, 70, 70), 1)
    for r in range(rows + 1):
        y = min(height - 1, gy + int(round(r * cell_h)))
        cv2.line(out, (gx, y), (gx + gw - 1, y), (70, 70, 70), 1)

    # Lit cells that are not part of a complete frame: yellow, so "something
    # is here but it was not accepted" stays visible.
    claimed = {cell for item in (items or []) for cell in item.cells}
    for (c, r) in hit_set - claimed:
        x0, y0 = gx + int(round(c * cell_w)), gy + int(round(r * cell_h))
        x1 = gx + int(round((c + 1) * cell_w)) - 1
        y1 = gy + int(round((r + 1) * cell_h)) - 1
        cv2.rectangle(out, (x0, y0), (x1, y1), (60, 210, 235), 1)  # BGR yellow

    # Accepted items: one green rectangle around the item's whole extent,
    # plus a dot where the click will land.
    for item in (items or []):
        x0 = gx + int(round(item.col * cell_w))
        y0 = gy + int(round(item.row * cell_h))
        x1 = gx + int(round((item.col + item.width) * cell_w)) - 1
        y1 = gy + int(round((item.row + item.height) * cell_h)) - 1
        cv2.rectangle(out, (x0, y0), (x1, y1), (60, 220, 60), 2)
        ccol, crow = item.centre
        cv2.circle(
            out,
            (gx + int(round(ccol * cell_w)), gy + int(round(crow * cell_h))),
            2, (60, 220, 60), -1,
        )

    # Callers that pass no items fall back to marking target cells directly.
    if items is None:
        for (c, r) in target_set:
            x0, y0 = gx + int(round(c * cell_w)), gy + int(round(r * cell_h))
            x1 = gx + int(round((c + 1) * cell_w)) - 1
            y1 = gy + int(round((r + 1) * cell_h)) - 1
            cv2.rectangle(out, (x0, y0), (x1, y1), (60, 220, 60), 2)
    return out

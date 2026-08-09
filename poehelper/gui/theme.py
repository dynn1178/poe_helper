"""Central palette/typography constants translating DESIGN-apple.md into
this desktop app: a single dark-gray accent, near-black/parchment
surfaces, and pill-radius buttons, applied app-wide via
``resources/apple_theme.json`` (see ``theme_path()``). Originally an Apple
"Action Blue" accent, swapped for a quieter dark gray (plus a muted red for
destructive actions) since blue/red together read as too flashy against
the white canvas. Korean UI text keeps a Korean-capable family (맑은 고딕)
rather than the doc's SF Pro/Inter, since none of those ship Hangul glyphs
-- the JSON theme's ``CTkFont.Windows`` entry does the same substitution
for every widget that doesn't pass its own ``font=``.

Widgets that need an explicit font (headings, monospace editors, small
utility rows) should pull from the tuples below instead of hard-coding
sizes, so the whole app tracks one type scale.
"""
from __future__ import annotations

from pathlib import Path

from .. import paths

PRIMARY = "#5a5a5e"
PRIMARY_FOCUS = "#48484a"
PRIMARY_ON_DARK = "#8e8e93"
INK = "#1d1d1f"
CANVAS = "#ffffff"
CANVAS_PARCHMENT = "#f5f5f7"
SURFACE_TILE_1 = "#272729"
SURFACE_TILE_2 = "#2a2a2c"
SURFACE_TILE_3 = "#252527"
SURFACE_LIGHT = "#f5f5f7"
SURFACE_LIGHT_2 = "#e5e5ea"
HAIRLINE = "#3a3a3c"
HAIRLINE_LIGHT = "#e0e0e0"
ON_PRIMARY = "#ffffff"

# Semantic destructive-action red -- kept distinct from the gray accent so
# delete/exit stays visually unambiguous, but muted/desaturated rather than
# a bright alarm red so it doesn't fight with the rest of the palette.
DANGER = "#b3524a"
DANGER_HOVER = "#96423b"

# ---------------------------------------------------------------------------
# Overlay palette (route window, map-layout window)
# ---------------------------------------------------------------------------
# The settings window is a normal desktop window and follows the light theme
# above. The overlays are not: they float on top of the game for a whole
# session, and a white panel there is a lit rectangle sitting in peripheral
# vision the entire time -- exactly the attention the game is competing for.
# These are their own dark tokens rather than the SURFACE_TILE_* values,
# because those were picked as accents inside a light layout, not as a
# self-contained dark surface with text on it.
OVERLAY_BG = "#1c1c20"           # the window itself
OVERLAY_SURFACE = "#26262b"      # bars and panels sitting on OVERLAY_BG
OVERLAY_SURFACE_2 = "#303036"    # inputs, and the raised/hover state
OVERLAY_TEXT = "#f2f2f5"         # off-white; pure white glares at low alpha
OVERLAY_TEXT_DIM = "#9a9aa2"     # handles, hints, slider tracks
OVERLAY_BORDER = "#3a3a42"
OVERLAY_ACCENT = "#4a4a52"       # buttons
OVERLAY_ACCENT_HOVER = "#5c5c66"
OVERLAY_HIGHLIGHT = "#2f5f8f"    # the current route step

FONT_FAMILY = "맑은 고딕"
FONT_TITLE = (FONT_FAMILY, 14, "bold")
FONT_SECTION = (FONT_FAMILY, 13, "bold")
FONT_BODY_STRONG = (FONT_FAMILY, 12, "bold")
FONT_BODY = (FONT_FAMILY, 12)
# Bumped 1pt (10/9 -> 11/10): the small scale carries the hint and warning
# lines, which are the text most worth reading and were the hardest to.
FONT_CAPTION = (FONT_FAMILY, 11)
FONT_SMALL = (FONT_FAMILY, 10)
FONT_MONO = ("Consolas", 10)


def theme_path() -> Path:
    return paths.resource_path("apple_theme.json")

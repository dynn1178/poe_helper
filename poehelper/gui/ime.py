"""Make the Korean IME compose in the same typeface as the box it types into.

Windows draws the *composing* syllable -- the one still being assembled, the
one with the box around it, before it is committed by a space or the next
consonant -- itself, in the IME's own composition window, not in the widget.
Which font it uses comes from the input context, and the default there is a
system fallback (굴림/Gulim, at the system size), so on every entry in this
app the letter being typed appeared in a visibly different face and weight
from the letters already typed, and then changed shape the moment it was
committed.

Tk never sets that font. ``Tk_SetCaretPos`` tells the IME *where* to put the
composition (``ImmSetCompositionWindow``, tkWinX.c) so the syllable at least
lands on the caret, but there is no matching ``ImmSetCompositionFont`` call
anywhere in Tk, so the context keeps whatever the system gave it. This module
makes that call: on every focus into an Entry or a Text, it reads the font
that widget is actually drawing with and hands the same family, size, weight
and slant to the input context.

Pure Win32 through ctypes, and every failure path is a no-op -- the worst
case is the mismatched composition font that was there before.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

logger = logging.getLogger(__name__)

_LF_FACESIZE = 32
# Ask for the Hangul codepage explicitly. DEFAULT_CHARSET resolves against the
# thread locale, which is what produced the fallback face in the first place.
_HANGEUL_CHARSET = 129
_installed = False


class _LOGFONTW(ctypes.Structure):
    _fields_ = [
        ("lfHeight", wintypes.LONG),
        ("lfWidth", wintypes.LONG),
        ("lfEscapement", wintypes.LONG),
        ("lfOrientation", wintypes.LONG),
        ("lfWeight", wintypes.LONG),
        # c_ubyte, not wintypes.BYTE: Python declares that one as a *signed*
        # char, and HANGEUL_CHARSET (129) does not fit in one.
        ("lfItalic", ctypes.c_ubyte),
        ("lfUnderline", ctypes.c_ubyte),
        ("lfStrikeOut", ctypes.c_ubyte),
        ("lfCharSet", ctypes.c_ubyte),
        ("lfOutPrecision", ctypes.c_ubyte),
        ("lfClipPrecision", ctypes.c_ubyte),
        ("lfQuality", ctypes.c_ubyte),
        ("lfPitchAndFamily", ctypes.c_ubyte),
        ("lfFaceName", wintypes.WCHAR * _LF_FACESIZE),
    ]


def _imm32():
    if sys.platform != "win32":
        return None
    try:
        imm = ctypes.WinDLL("imm32", use_last_error=True)
    except OSError:
        return None
    imm.ImmGetContext.argtypes = [wintypes.HWND]
    imm.ImmGetContext.restype = wintypes.HANDLE
    imm.ImmSetCompositionFontW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_LOGFONTW)]
    imm.ImmSetCompositionFontW.restype = wintypes.BOOL
    imm.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
    imm.ImmReleaseContext.restype = wintypes.BOOL
    return imm


_IMM = _imm32()


def _logfont_for(widget: tk.Misc) -> _LOGFONTW | None:
    """The widget's own font, as a Win32 LOGFONT.

    Read off the widget rather than off the theme: the price window and the
    memo editor are both user-resizable in points, and the composing syllable
    has to follow whatever they are currently at, not one fixed size.
    """
    try:
        spec = widget.cget("font")
    except tk.TclError:
        return None
    if not spec:
        return None
    try:
        font = tkfont.Font(root=widget, font=spec)
        actual = font.actual()
        metrics = font.metrics()
    except (tk.TclError, RuntimeError):
        return None

    # Measured, not converted. The size on a font spec can be points or
    # pixels (Tk reads a negative size as pixels) and ``actual()`` reports it
    # back in whichever unit it settled on, rounded -- CustomTkinter asks for
    # -14px, actual() says "10pt", and converting that back lands on 13px.
    # Ascent + descent is the same quantity GDI calls tmHeight, which is what
    # a *positive* lfHeight asks for, so this matches exactly and needs to
    # know nothing about DPI.
    height = int(metrics.get("ascent", 0)) + int(metrics.get("descent", 0))
    if height <= 0:
        return None

    lf = _LOGFONTW()
    lf.lfHeight = height
    lf.lfWeight = 700 if actual.get("weight") == "bold" else 400
    lf.lfItalic = 1 if actual.get("slant") == "italic" else 0
    lf.lfCharSet = _HANGEUL_CHARSET
    lf.lfFaceName = str(actual.get("family", ""))[:_LF_FACESIZE - 1]
    return lf


def _apply(hwnd: int, lf: _LOGFONTW) -> None:
    himc = _IMM.ImmGetContext(hwnd)
    if not himc:
        return
    try:
        _IMM.ImmSetCompositionFontW(himc, ctypes.byref(lf))
    finally:
        _IMM.ImmReleaseContext(hwnd, himc)


def _on_focus(event: tk.Event) -> None:
    widget = event.widget
    lf = _logfont_for(widget)
    if lf is None:
        return
    try:
        # The input context lives on whichever window Windows considers
        # focused, which for Tk is the toplevel; the widget's own HWND is set
        # too, since Tk does give every widget one and which of the two the
        # IME reads back has never been documented.
        targets = {widget.winfo_toplevel().winfo_id(), widget.winfo_id()}
    except tk.TclError:
        return
    for hwnd in targets:
        try:
            _apply(hwnd, lf)
        except OSError:
            logger.debug("could not set the IME composition font", exc_info=True)


def install(root: tk.Misc) -> None:
    """Idempotent; bound by widget class, so it covers every window.

    ``bind_class`` registers against the interpreter rather than the window,
    so the overlays and the price window -- built long after this runs, as
    separate toplevels -- are covered by this one call.
    """
    global _installed
    if _installed or _IMM is None:
        return
    for widget_class in ("Entry", "Text", "TEntry"):
        root.bind_class(widget_class, "<FocusIn>", _on_focus, add="+")
    _installed = True

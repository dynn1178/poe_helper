"""검색 인식 테스트: show what the highlight detector actually sees.

The detector has to work on someone else's monitor, at their resolution, with
their gamma/HDR profile -- none of which can be reasoned about from here. So
rather than shipping thresholds and hoping, this window screenshots the real
stash region, draws the grid and every detected cell on top of it, and lets
the two thresholds be dragged until the picture is right.

It also answers the question a failure otherwise cannot: "found nothing" and
"found the wrong cells" look identical from the outside and need opposite
fixes. Here you can see which one you have -- and the colour-mask view shows
whether the frame is even the colour the detector is looking for, which is
the failure that is impossible to guess at.
"""
from __future__ import annotations

import logging
import tkinter as tk

import customtkinter as ctk
import numpy as np

from .. import image_search
from ..config import GRID_SIZES, Config
from . import theme
from .widgets.tooltip import Tooltip

logger = logging.getLogger(__name__)

_PREVIEW_MAX = (620, 430)   # the captured region is scaled to fit this


class DetectTestWindow(ctk.CTkToplevel):
    """Capture the stash region, run detection, and show the result."""

    def __init__(self, master, config: Config, region_key: str, label: str):
        super().__init__(master)
        self.app_config = config
        self.region_key = region_key
        self.label = label
        self.cols, self.rows = GRID_SIZES[region_key]
        self._image: np.ndarray | None = None
        self._photo = None
        self._scores = None
        self._grid = None
        self._render_scale = 1.0

        self.title(f"검색 인식 테스트 — {label}")
        # Width only. The height is fitted to the content by _fit_window():
        # the preview is as tall as the captured region's aspect makes it, and
        # the controls under it have grown as this window gained features, so
        # a hard-coded height pushed the buttons off the bottom edge.
        self.geometry("700x700")
        self.transient(master)
        self.after(150, self._grab_focus)

        detection = config.data.get("detection", {})
        options = image_search.options_from_config(detection)
        self.coverage_var = ctk.DoubleVar(value=options["edge_coverage"])
        # Not user-editable any more: complete frames are required around
        # the item now, and this only picks which partially-lit cells get
        # the faint yellow outline in the preview.
        self._min_edges = int(options["min_edges"])
        self.skip_enclosed_var = ctk.BooleanVar(value=options["skip_enclosed"])
        self.sides_var = ctk.StringVar(value=f'{options["min_sides"]}면')

        self._build()
        self.capture()
        # Also fitted here, so the buttons are reachable even when the region
        # is unset and no preview was ever drawn.
        self._fit_window()

    def _grab_focus(self) -> None:
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _fit_window(self) -> None:
        """Grow the window to whatever its contents need.

        Grow-only, so a window the user has dragged larger is left alone, and
        clamped to the screen because a stash region with an extreme aspect
        ratio could otherwise ask for a preview taller than the display.
        """
        try:
            self.update_idletasks()
            needed = self.winfo_reqheight()
            limit = self.winfo_screenheight() - 90  # taskbar + title bar
            target = min(needed, limit)
            if target > self.winfo_height():
                self.geometry(f"{max(self.winfo_width(), 700)}x{target}")
        except tk.TclError:
            pass

    # ---- layout -----------------------------------------------------------
    def _build(self) -> None:
        ctk.CTkLabel(
            self,
            text=f"게임에서 보관함을 열고 검색창에 아이템을 검색한 상태로 '다시 촬영'을 누르세요.\n"
                 f"초록색 사각형이 실제로 가져올 아이템입니다.",
            anchor="w", justify="left", font=theme.FONT_CAPTION,
            text_color=theme.PRIMARY, wraplength=650,
        ).pack(fill="x", padx=14, pady=(12, 8))

        self.preview = tk.Label(self, bd=1, relief="solid", bg="#101014", cursor="hand2")
        self.preview.pack(padx=14)
        # Click a cell to see the four numbers the decision was made from.
        # "Why was this cell picked up?" cannot be answered from the coloured
        # boxes alone, and it is the only question left once detection is
        # broadly working.
        self.preview.bind("<Button-1>", self._inspect_cell)

        self.inspect = ctk.CTkLabel(
            self, text="칸을 클릭하면 그 칸의 네 변 측정값을 볼 수 있습니다.",
            anchor="w", justify="left", font=theme.FONT_CAPTION,
            text_color=theme.PRIMARY_ON_DARK, wraplength=650,
        )
        self.inspect.pack(fill="x", padx=14, pady=(6, 0))

        self.summary = ctk.CTkLabel(
            self, text="", anchor="w", justify="left", font=theme.FONT_BODY_STRONG,
            wraplength=650,
        )
        self.summary.pack(fill="x", padx=14, pady=(10, 2))

        self.hint = ctk.CTkLabel(
            self, text="", anchor="w", justify="left", font=theme.FONT_CAPTION,
            text_color=theme.PRIMARY, wraplength=650,
        )
        self.hint.pack(fill="x", padx=14)

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.pack(fill="x", padx=14, pady=(10, 0))

        self._slider_row(
            controls, "테두리 인식 강도", self.coverage_var, 0.20, 0.95, 15,
            "칸의 한 변에 강조색 선이 얼마나 길게 끊기지 않고 이어져야 인식할지.\n\n"
            "실제 테두리는 0.9 이상이 나오므로 기본값 0.60이면 충분합니다. 아이템 그림의 "
            "금색은 점점이 흩어져 있어 길게 이어지지 않으므로, 값을 낮춰도 잘 잡히지 "
            "않습니다. 인식이 안 되면 낮추고, 엉뚱한 칸이 잡히면 올리세요.",
        )
        # No "필요한 변의 수" any more: four framed sides is now required
        # around the *item*, whatever size it is, which is what that setting
        # was reaching for. Per cell it could never be right -- see
        # image_search.find_items.
        sides_row = ctk.CTkFrame(controls, fg_color="transparent")
        sides_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(sides_row, text="필요한 테두리 변 수", width=110, anchor="w",
                     font=theme.FONT_CAPTION).pack(side="left")
        sides_seg = ctk.CTkSegmentedButton(
            sides_row, values=["3면", "4면"], variable=self.sides_var,
            width=140, height=24, font=theme.FONT_CAPTION,
            command=lambda _v: self.rerun(),
        )
        sides_seg.pack(side="left", padx=8)
        Tooltip(
            sides_row,
            "칸이 아니라 '아이템 전체'의 네 변 중 몇 개가 테두리로 둘러싸여야 하는지입니다.\n\n"
            "4면이 가장 엄격하지만, 보관함 패널 가장자리(특히 맨 왼쪽 열)에서는 한 변이 "
            "패널 테두리에 가려지거나 잘려서 아예 잡히지 않습니다. 그래서 기본값은 3면입니다 — "
            "아이템 그림의 금색으로는 세 변을 통째로 채울 수 없으므로 3면도 충분히 엄격합니다.\n\n"
            "4면이 가능한 아이템은 3면 설정에서도 항상 4면으로 먼저 인식되므로, 크기를 "
            "잘못 잡는 일은 없습니다.",
        )

        enclosed_cb = ctk.CTkCheckBox(
            controls, text="사방이 둘러싸인 한 칸은 건너뛰기 (권장)",
            variable=self.skip_enclosed_var, command=self.rerun,
        )
        enclosed_cb.pack(anchor="w", pady=(8, 0))
        Tooltip(
            enclosed_cb,
            "강조된 아이템 네 개에 둘러싸인 한 칸짜리 자리는, 화면상으로 강조된 칸과 "
            "완전히 똑같이 보입니다 — 그 칸의 네 변이 곧 이웃들의 테두리 선 자체라서 "
            "구별할 방법이 없습니다.\n\n"
            "그래서 기본값은 건너뛰기입니다. 잘못 가져온 아이템은 다시 찾아 넣어야 하지만, "
            "못 가져온 아이템은 그냥 한 번 더 실행하면 됩니다. 게다가 주변 아이템을 "
            "먼저 빼내고 나면 더 이상 둘러싸인 상태가 아니므로, 다음 반복에서 정상적으로 "
            "인식됩니다.",
        )

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(12, 14))
        ctk.CTkButton(buttons, text="다시 촬영", width=100, command=self.capture).pack(side="left")
        self.mask_button = ctk.CTkButton(
            buttons, text="색상 마스크 보기", width=130, fg_color=theme.PRIMARY,
            hover_color=theme.PRIMARY_FOCUS, command=self._toggle_mask,
        )
        self.mask_button.pack(side="left", padx=8)
        ctk.CTkButton(buttons, text="닫기", width=70, fg_color=theme.PRIMARY,
                      hover_color=theme.PRIMARY_FOCUS, command=self.destroy).pack(side="right")
        ctk.CTkButton(buttons, text="이 설정 저장", width=110, command=self._save).pack(
            side="right", padx=8
        )
        self._show_mask = False

    def _slider_row(self, parent, title, variable, lo, hi, steps, help_text) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text=title, width=110, anchor="w",
                     font=theme.FONT_CAPTION).pack(side="left")
        value = ctk.CTkLabel(row, text="", width=42, font=theme.FONT_CAPTION)

        def on_move(_v=None) -> None:
            raw = variable.get()
            value.configure(text=f"{raw:.2f}" if isinstance(raw, float) else str(int(raw)))
            self.rerun()

        slider = ctk.CTkSlider(
            row, from_=lo, to=hi, number_of_steps=steps, variable=variable,
            width=280, command=on_move,
        )
        slider.pack(side="left", padx=8)
        value.pack(side="left")
        ctk.CTkLabel(row, text="?", width=18, font=theme.FONT_CAPTION,
                     text_color=theme.PRIMARY).pack(side="left", padx=(6, 0))
        Tooltip(row, help_text)
        on_move()

    # ---- work -------------------------------------------------------------
    def _region(self):
        cfg = self.app_config.data["regions"].get(self.region_key)
        if not cfg or not all(k in cfg for k in ("x1", "y1", "x2", "y2")):
            return None
        return int(cfg["x1"]), int(cfg["y1"]), int(cfg["x2"]), int(cfg["y2"])

    def capture(self) -> None:
        region = self._region()
        if region is None:
            self.summary.configure(
                text=f"{self.label} 영역이 아직 설정되지 않았습니다.",
                text_color=theme.DANGER,
            )
            return
        # Hidden during the grab, or the window ends up photographing itself
        # when it happens to overlap the stash.
        self.withdraw()
        self.update_idletasks()
        try:
            self._image, self._grid = image_search.grab_with_margin(
                region, max(1, image_search.options_from_config(
                    self.app_config.data.get('detection'))['scan_px'])
            )
        except Exception as exc:  # noqa: BLE001 - a screenshot can fail for many reasons
            logger.exception("region capture failed")
            self._image = None
            self.summary.configure(text=f"화면 캡처 실패: {exc}", text_color=theme.DANGER)
        finally:
            self.deiconify()
        self.rerun()

    def rerun(self) -> None:
        if self._image is None:
            return
        options = image_search.options_from_config(self.app_config.data.get("detection"))
        # Live overrides from the controls, on top of the saved settings.
        options["edge_coverage"] = float(self.coverage_var.get())
        options["min_edges"] = self._min_edges
        options["skip_enclosed"] = bool(self.skip_enclosed_var.get())
        options["min_sides"] = int(self.sides_var.get()[0])

        result = image_search.scan(
            (0, 0, 0, 0), self.cols, self.rows, image=self._image,
            grid=self._grid, **options
        )
        hits, targets, items = result.hits, result.targets, result.items
        self._scores = result.scores
        self._options = options

        if self._show_mask:
            # Mask view answers two questions the annotated view cannot:
            # is the frame the colour we are filtering for at all, and does it
            # line up with the grid? Both are hidden if the detection
            # rectangles are drawn on top of the very pixels being inspected,
            # so this view gets the grid only.
            mask = image_search.highlight_mask(
                self._image, options["hsv_low"], options["hsv_high"]
            )
            view = np.zeros_like(self._image)
            view[mask > 0] = (60, 220, 255)
            annotated = image_search.annotate(
                view, self.cols, self.rows, [], grid=self._grid)
        else:
            annotated = image_search.annotate(
                self._image, self.cols, self.rows, hits, targets,
                grid=self._grid, items=items,
            )
        self._render(annotated)

        self.summary.configure(
            text=f"가져올 아이템 {len(items)}개  ·  테두리가 보인 칸 {len(hits)}개",
            text_color=theme.DANGER if not hits else theme.INK,
        )
        if not items:
            self.hint.configure(
                text="가져올 아이템이 없습니다. 노란 칸이 보인다면 테두리가 일부만 인식된 것이니 "
                     "'테두리 인식 강도'를 낮춰보세요. 아무것도 없다면 '색상 마스크 보기'로 "
                     "테두리가 색으로 잡히는지 먼저 확인하세요."
            )
        elif len(items) > self.cols * self.rows * 0.4:
            self.hint.configure(
                text="너무 많은 아이템이 잡혔습니다. '테두리 인식 강도'를 올려보세요."
            )
        else:
            self.hint.configure(text="초록색 = 실제로 클릭할 칸, 노란색 = 같은 아이템의 나머지 칸.")

    def _inspect_cell(self, event: tk.Event) -> None:
        """Report the four edge measurements for the clicked cell."""
        scores = getattr(self, "_scores", None)
        if scores is None or self._image is None or not self._render_scale:
            return
        gx, gy, gw, gh = self._grid or (0, 0, self._image.shape[1], self._image.shape[0])
        col = int((event.x / self._render_scale - gx) / (gw / self.cols))
        row = int((event.y / self._render_scale - gy) / (gh / self.rows))
        if not (0 <= col < self.cols and 0 <= row < self.rows):
            return
        coverage = float(self.coverage_var.get())
        needed = self._min_edges
        values = scores[row, col]
        names = ("위", "아래", "왼쪽", "오른쪽")
        parts = [
            f"{name} {values[i]:.2f}{'✓' if values[i] >= coverage else '·'}"
            for i, name in enumerate(names)
        ]
        passed = int((values >= coverage).sum())
        self.inspect.configure(
            text=f"[{col + 1}열 {row + 1}행]  " + "   ".join(parts)
                 + f"   →  이 칸의 테두리 {passed}/4변"
                 + ("  ⇒ 완전히 닫힘" if passed == 4 else "  ⇒ 일부만 보임"),
            text_color=theme.INK,
        )

    def _toggle_mask(self) -> None:
        self._show_mask = not self._show_mask
        self.mask_button.configure(
            text="원본 보기" if self._show_mask else "색상 마스크 보기"
        )
        self.rerun()
        if self._show_mask:
            self.hint.configure(
                text="강조색으로 인식된 픽셀만 표시합니다. 아이템 테두리가 여기 선으로 보이지 않으면 "
                     "색상 범위가 맞지 않는 것이고, 보이더라도 회색 격자선과 어긋나 있으면 "
                     "보관함 영역 설정을 다시 맞춰야 합니다."
            )

    def _render(self, image: np.ndarray) -> None:
        try:
            import cv2
            from PIL import Image, ImageTk
        except ImportError:
            return
        height, width = image.shape[:2]
        scale = min(_PREVIEW_MAX[0] / width, _PREVIEW_MAX[1] / height, 1.0)
        self._render_scale = scale  # so a click can be mapped back to a cell
        if scale < 1.0:
            image = cv2.resize(
                image, (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        rgb = image[:, :, ::-1]  # the pipeline is BGR; PIL wants RGB
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.preview.configure(image=self._photo)
        # The preview's height depends on the region's aspect, so the window
        # can only be sized once there is actually a picture in it.
        self._fit_window()

    def _save(self) -> None:
        detection = self.app_config.data.setdefault("detection", {})
        detection["edge_coverage"] = round(float(self.coverage_var.get()), 2)
        detection["skip_enclosed"] = bool(self.skip_enclosed_var.get())
        detection["min_sides"] = int(self.sides_var.get()[0])
        self.app_config.save()
        self.summary.configure(text="설정을 저장했습니다.", text_color=theme.PRIMARY)

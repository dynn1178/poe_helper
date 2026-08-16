"""Whisper notifications: a card per incoming whisper, over the game.

Shaped entirely by when it appears -- mid-map, while the player is busy, and
usually because someone wants to buy something. So:

*It never takes the foreground.* PoE only receives keyboard input while it is
the active window, so a panel that activated itself would stop the game
responding to keys the moment a whisper arrived (see ``overlay_win``). It gets
``WS_EX_NOACTIVATE`` without click-through: mouse clicks arrive, activation
never does, and the buttons still work.

*It gets out of the way on its own.* Cards hide after a few seconds, leaving
only a thin bar. Putting the pointer on that bar brings the list back, so
nothing is lost by not reading it immediately.

*The dangerous button is somewhere else.* 차단 sits in its own corner, away
from the three buttons pressed constantly, because a misplaced click there is
not something you can undo from here.

A trade whisper is broken into what was asked for, what is being paid and
where the item is, because those are the parts worth acting on and the parts
that are hardest to pick out of the sentence the game composes.
"""
from __future__ import annotations

import logging
import tkinter as tk

import customtkinter as ctk

from .. import overlay_win, whispers
from ..config import Config
from ..whispers import Whisper
from . import theme

logger = logging.getLogger(__name__)

_MAX_VISIBLE = 3          # cards on screen; older ones scroll
_HOVER_POLL_MS = 140
_PURGE_MS = 20_000        # how often expired cards are swept up
_BAR_HEIGHT = 22

# The game's own item colours, so a card reads the same way the item does
# in the stash. Only the rarities a whisper can actually be told apart by
# are listed -- see Whisper.rarity; anything else falls back to plain text
# rather than being coloured as a guess.
_RARITY_COLOR = {
    "rare": "#f0e64c",
    "gem": "#71bfb6",
    "": "#e8e8ea",
}


class WhisperPanel(tk.Toplevel):
    def __init__(self, master: tk.Misc, config: Config, on_open_settings=None):
        super().__init__(master)
        self.withdraw()  # revealed by overlay_win once the styles are on
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        # NOT self.config: CustomTkinter replaces a plain Toplevel's
        # .config/.configure the first time a CTk child is built inside it,
        # which would silently overwrite the settings object stored there.
        self.app_config = config
        self.cfg = config.data.setdefault("whisper", {})
        self.geo = config.data["windows"].setdefault(
            "whisper_panel", {"x": 40, "y": 120, "w": 360}
        )
        self.on_open_settings = on_open_settings

        self._cards: list[tuple[Whisper, ctk.CTkFrame]] = []
        self._drag_origin: tuple[int, int] | None = None
        self._hide_after: str | None = None
        self._hover = False
        self._poll_id: str | None = None
        self._closed = False
        self._placing = False
        self._list_shown = False
        self._elapsed_labels: list = []

        self.configure(bg=theme.OVERLAY_BG)
        self.geometry(f"+{int(self.geo.get('x', 40))}+{int(self.geo.get('y', 120))}")

        self._build()
        overlay_win.show_passive(self, click_through=False)
        self._poll_hover()
        self.after(_PURGE_MS, self._purge)
        self._tick_elapsed()

        monitor = whispers.get_monitor()
        if monitor is not None:
            monitor.add_listener(self._on_whisper)
        self._monitor = monitor

    # ---- widgets ----------------------------------------------------------
    @property
    def width(self) -> int:
        try:
            return max(340, int(self.geo.get("w", 360)))
        except (TypeError, ValueError):
            return 360

    def _build(self) -> None:
        # The bar is the only thing left once the cards hide, so it is also
        # the handle for everything: dragging, re-showing, settings, close.
        self.bar = ctk.CTkFrame(
            self, fg_color=theme.OVERLAY_SURFACE, corner_radius=8, height=_BAR_HEIGHT
        )
        self.bar.pack(fill="x")
        self.bar.pack_propagate(False)

        handle = tk.Label(
            self.bar, text="⠿ 귓속말", bd=0, cursor="fleur",
            bg=theme.OVERLAY_SURFACE, fg=theme.OVERLAY_TEXT_DIM,
            font=(theme.FONT_FAMILY, 9),
        )
        handle.pack(side="left", padx=(6, 4))
        for widget in (self.bar, handle):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)
            widget.bind("<ButtonRelease-1>", lambda _e: self._save_position())

        small = dict(
            width=22, height=17, font=(theme.FONT_FAMILY, 9),
            fg_color=theme.OVERLAY_ACCENT, hover_color=theme.OVERLAY_ACCENT_HOVER,
            text_color=theme.OVERLAY_TEXT,
        )
        ctk.CTkButton(self.bar, text="×", command=self.close, **{
            **small, "fg_color": theme.DANGER, "hover_color": theme.DANGER_HOVER,
        }).pack(side="right", padx=(2, 5))
        ctk.CTkButton(self.bar, text="지움", command=self.clear, **{**small, "width": 32}).pack(
            side="right", padx=2
        )
        if self.on_open_settings is not None:
            ctk.CTkButton(
                self.bar, text="설정", command=self.on_open_settings, **{**small, "width": 32}
            ).pack(side="right", padx=2)

        self.count_label = ctk.CTkLabel(
            self.bar, text="", font=(theme.FONT_FAMILY, 9), text_color=theme.OVERLAY_TEXT_DIM
        )
        self.count_label.pack(side="left")

        # Height is set from the cards in it, so an empty panel is just the bar.
        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color=theme.OVERLAY_BG, width=self.width, height=300,
        )

    # ---- receiving --------------------------------------------------------
    def _on_whisper(self, whisper: Whisper) -> None:
        """Called on the log tracker's thread -- hop onto the Tk loop."""
        try:
            self.after(0, self.add, whisper)
        except tk.TclError:
            pass  # panel already closed

    def add(self, whisper: Whisper) -> None:
        if self._closed:
            return
        card = self._build_card(whisper)
        self._cards.insert(0, (whisper, card))
        self._refresh_count()
        self._show_list()
        self._schedule_hide()

    def _build_card(self, whisper: Whisper) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            self.list_frame, fg_color=theme.OVERLAY_SURFACE, corner_radius=10,
            border_width=0,
        )
        # Newest on top, so the whisper that just arrived is already on screen
        # and nothing has to be scrolled to be read.
        first = self._cards[0][1] if self._cards else None
        if first is not None and first.winfo_exists():
            card.pack(fill="x", pady=3, padx=3, before=first)
        else:
            card.pack(fill="x", pady=3, padx=3)

        # The stripe says what kind of whisper this is at a glance, in the
        # width a border used to take around the whole card.
        accent = (
            _RARITY_COLOR.get(whisper.rarity, "#c98a3c") if whisper.is_trade else "#8a6fbf"
        )
        # height=1: a CTkFrame defaults to 200px tall, and with fill="y" that
        # default became the card's minimum height -- three cards of mostly
        # empty space. fill="y" still stretches it to the real content.
        ctk.CTkFrame(card, fg_color=accent, corner_radius=3, width=4, height=1).pack(
            side="left", fill="y", padx=(4, 0), pady=6
        )
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True)

        head = ctk.CTkFrame(body, fg_color="transparent")
        head.pack(fill="x", padx=(8, 6), pady=(5, 0))
        ctk.CTkLabel(
            head, text=whisper.sender, font=(theme.FONT_FAMILY, 13, "bold"),
            text_color="#d191ff", anchor="w",
        ).pack(side="left")
        ctk.CTkButton(
            head, text="\u00d7", width=18, height=18, font=(theme.FONT_FAMILY, 10),
            fg_color="transparent", hover_color=theme.DANGER,
            text_color=theme.OVERLAY_TEXT_DIM,
            command=lambda: self._dismiss(card),
        ).pack(side="right", padx=(6, 0))
        elapsed = ctk.CTkLabel(
            head, text=whisper.elapsed, font=(theme.FONT_FAMILY, 11),
            text_color=theme.OVERLAY_TEXT_DIM,
        )
        elapsed.pack(side="right")
        self._elapsed_labels.append((whisper, elapsed))

        if whisper.is_trade:
            self._build_trade_body(body, whisper)
        else:
            ctk.CTkLabel(
                body, text=whisper.text, font=(theme.FONT_FAMILY, 13),
                text_color=theme.OVERLAY_TEXT, anchor="w", justify="left",
                wraplength=self.width - 40,
            ).pack(fill="x", padx=(8, 6), pady=(3, 1))

        self._build_actions(body, whisper)
        return card

    def _build_trade_body(self, body, whisper: Whisper) -> None:
        """Price, item, location on one row.

        The price sits in a filled pill rather than as loose text: it is the
        one thing on the card the eye should land on first, and a colour alone
        was not enough to separate it from the item name beside it.
        """
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", padx=(8, 6), pady=(4, 2))

        # Packed before the name so it keeps its full width: pack allocates in
        # call order regardless of side, and a long item name packed first
        # would squeeze the location off the row.
        if whisper.stash_location:
            ctk.CTkLabel(
                row, text=whisper.stash_location, font=(theme.FONT_FAMILY, 10),
                text_color="#79b8ff", anchor="e",
            ).pack(side="right", padx=(6, 0))

        badge = ctk.CTkFrame(row, fg_color="#3a3320", corner_radius=6)
        badge.pack(side="left")
        ctk.CTkLabel(
            badge, text=whisper.price, font=(theme.FONT_FAMILY, 13, "bold"),
            text_color="#ffd75e",
        ).pack(padx=7, pady=1)

        ctk.CTkLabel(
            row, text=_shorten(whisper.wanted, self._name_budget()),
            font=(theme.FONT_FAMILY, 12, "bold"),
            text_color=_RARITY_COLOR.get(whisper.rarity, _RARITY_COLOR[""]),
            anchor="w",
        ).pack(side="left", padx=(10, 0))

    def _name_budget(self) -> int:
        """Roughly how many characters of item name fit beside the price.

        A Tk label does not ellipsize and does not clip -- it makes its parent
        wider instead -- so a long name on a shared row would quietly stretch
        the whole panel. Trimming it here is what keeps the card the width it
        was asked to be.
        """
        return max(8, int((self.width - 205) / 11))

    def _build_actions(self, body, whisper: Whisper) -> None:
        """Every button on one row, still colour-coded by what it does.

        Blue acts on the *game* -- it moves the buyer around. Grey only says
        something. Labels are as short as they can be and still be read at a
        glance, because seven buttons on a 320px card is what the row has to
        hold; the colour is what carries the grouping now that the two rows
        do not.
        """
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", padx=(8, 6), pady=(0, 6))
        name = whisper.sender

        common = dict(height=22, font=(theme.FONT_FAMILY, 10), text_color=theme.OVERLAY_TEXT)
        game = dict(fg_color="#3d6ea5", hover_color="#4a82c0", **common)
        reply = dict(
            fg_color=theme.OVERLAY_ACCENT, hover_color=theme.OVERLAY_ACCENT_HOVER, **common
        )

        # 차단 first, so it keeps its width and the gap in front of it: pack
        # hands out space in call order regardless of side, and six buttons
        # packed ahead of it would push it off the row.
        ctk.CTkButton(
            row, text="차단", width=34, height=22, font=(theme.FONT_FAMILY, 10),
            fg_color=theme.OVERLAY_SURFACE_2, hover_color=theme.DANGER,
            text_color=theme.OVERLAY_TEXT_DIM,
            command=lambda n=name: self._block(n),
        ).pack(side="right", padx=(5, 0))

        buttons = (
            ("초대", 32, game, lambda n=name: whispers.send_command(self.app_config, "invite", n)),
            ("은신", 32, game, lambda n=name: whispers.send_command(self.app_config, "hideout", n)),
            ("귓", 24, game, lambda n=name: whispers.whisper_to(self.app_config, n)),
            ("감사", 32, reply, lambda n=name: whispers.quick_reply(self.app_config, n, "thanks")),
            ("잠시", 32, reply, lambda n=name: whispers.quick_reply(self.app_config, n, "wait")),
            ("품절", 36, reply, lambda n=name: whispers.quick_reply(self.app_config, n, "sold")),
        )
        # expand=True on every button rather than six fixed widths: pack
        # gives each its requested size in call order and hands the last one
        # only what is left, so 품절 was being crushed whenever the card was
        # a few pixels narrower than the sum. Sharing the row evenly means
        # the panel can be any width and no button is ever the one clipped.
        for label, width, style, command in buttons:
            ctk.CTkButton(row, text=label, width=width, command=command, **style).pack(
                side="left", padx=(0, 2), fill="x", expand=True
            )

    def _dismiss(self, card) -> None:
        """Drop one card by hand, for a whisper already dealt with."""
        self._cards = [(w, c) for w, c in self._cards if c is not card]
        card.destroy()
        self._refresh_count()
        if not self._cards:
            self._hide_list()

    def _block(self, name: str) -> None:
        whispers.send_command(self.app_config, "ignore", name)
        self._drop_by_sender(name)

    # ---- showing and hiding -----------------------------------------------
    def _show_list(self) -> None:
        # A boolean, not winfo_ismapped(): CTkScrollableFrame is an inner
        # frame managed by a canvas, so its ismapped() is 0 even when the
        # widget is plainly on screen -- which meant _hide_list never fired
        # and the cards never went away.
        if not self._list_shown:
            self._list_shown = True
            self.list_frame.pack(fill="both", expand=True)
        # Height is fixed rather than computed per card: CTkScrollableFrame
        # forwards configure(height=) to its inner frame, and resizing that
        # after the cards are in it left the viewport showing empty space
        # below them.
        self.after_idle(self._scroll_to_start)

    def _scroll_to_start(self) -> None:
        canvas = getattr(self.list_frame, "_parent_canvas", None)
        try:
            if canvas is not None:
                canvas.yview_moveto(0.0)
        except Exception:
            logger.debug("could not scroll the whisper list", exc_info=True)

    def _hide_list(self) -> None:
        if self._hover or self._placing:
            return  # never yank it away from under the pointer
        if self._list_shown:
            self._list_shown = False
            self.list_frame.pack_forget()

    def _schedule_hide(self) -> None:
        if self._hide_after is not None:
            try:
                self.after_cancel(self._hide_after)
            except tk.TclError:
                pass
        seconds = self.cfg.get("hide_after_sec", 5)
        try:
            delay = int(max(1.0, float(seconds)) * 1000)
        except (TypeError, ValueError):
            delay = 5000
        self._hide_after = self.after(delay, self._hide_list)

    def _poll_hover(self) -> None:
        """Polling rather than <Enter>/<Leave>: those fire again for every
        child widget the pointer crosses, so the list flickered as the mouse
        moved onto the very buttons it had just revealed."""
        if self._closed:
            return
        try:
            px, py = self.winfo_pointerxy()
            x, y = self.winfo_rootx(), self.winfo_rooty()
            inside = (
                x <= px < x + max(self.winfo_width(), self.width)
                and y <= py < y + self.winfo_height()
            )
        except tk.TclError:
            return
        if inside != self._hover:
            self._hover = inside
            if inside and self._cards:
                self._show_list()
            elif not inside:
                self._schedule_hide()
        self._poll_id = self.after(_HOVER_POLL_MS, self._poll_hover)

    def _tick_elapsed(self) -> None:
        """Keep the "N분 전" labels honest as the cards sit there.

        Every 20 seconds rather than every second: the text only changes once
        a minute, and a panel that repaints constantly over a game is exactly
        what this one is trying not to be.
        """
        if self._closed:
            return
        alive = []
        for whisper, label in self._elapsed_labels:
            try:
                if not label.winfo_exists():
                    continue
                label.configure(text=whisper.elapsed)
                alive.append((whisper, label))
            except tk.TclError:
                continue
        self._elapsed_labels = alive
        self.after(20_000, self._tick_elapsed)

    # ---- housekeeping -----------------------------------------------------
    def _purge(self) -> None:
        """Drop whispers older than the configured age.

        Without this the panel is an ever-growing pile: a busy evening leaves
        dozens of cards for sales long since made, and the one that matters is
        buried under them.
        """
        if self._closed:
            return
        monitor = whispers.get_monitor()
        limit = (monitor.keep_minutes() if monitor else whispers.DEFAULT_KEEP_MINUTES) * 60
        keep: list[tuple[Whisper, ctk.CTkFrame]] = []
        for whisper, card in self._cards:
            if whisper.age_seconds() > limit:
                card.destroy()
            else:
                keep.append((whisper, card))
        if len(keep) != len(self._cards):
            self._cards = keep
            self._refresh_count()
            if not keep:
                self._hide_list()
        self.after(_PURGE_MS, self._purge)

    def _drop_by_sender(self, name: str) -> None:
        keep = []
        for whisper, card in self._cards:
            if whisper.sender == name:
                card.destroy()
            else:
                keep.append((whisper, card))
        self._cards = keep
        self._refresh_count()

    def _refresh_count(self) -> None:
        total = len(self._cards)
        if total > _MAX_VISIBLE:
            self.count_label.configure(text=f"{total}건 (스크롤)")
        else:
            self.count_label.configure(text=f"{total}건" if total else "")

    def clear(self) -> None:
        for _whisper, card in self._cards:
            card.destroy()
        self._cards.clear()
        self._refresh_count()
        self._hide_list()

    # ---- placement --------------------------------------------------------
    def begin_placing(self) -> None:
        """Pin the panel open with a sample card so it can be positioned.

        Dragging a panel that vanishes after five seconds is not something
        anyone can do, so placement mode holds it open until 확인 is pressed.
        """
        self._placing = True
        if not self._cards:
            self.add(_sample())
        self._show_list()
        if getattr(self, "_place_bar", None) is None:
            self._place_bar = ctk.CTkFrame(self, fg_color=theme.OVERLAY_SURFACE)
            ctk.CTkLabel(
                self._place_bar, text="⠿ 막대를 끌어 위치를 정하세요",
                font=(theme.FONT_FAMILY, 10), text_color=theme.OVERLAY_TEXT,
            ).pack(side="left", padx=8, pady=4)
            ctk.CTkButton(
                self._place_bar, text="확인", width=56, height=22,
                font=(theme.FONT_FAMILY, 10), command=self.finish_placing,
                fg_color=theme.OVERLAY_ACCENT, hover_color=theme.OVERLAY_ACCENT_HOVER,
                text_color=theme.OVERLAY_TEXT,
            ).pack(side="right", padx=8, pady=4)
        self._place_bar.pack(fill="x", before=self.bar)

    def finish_placing(self) -> None:
        self._placing = False
        if getattr(self, "_place_bar", None) is not None:
            self._place_bar.pack_forget()
        self._save_position()
        self._schedule_hide()

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root, event.y_root)

    def _do_drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        self._drag_origin = (event.x_root, event.y_root)
        self.geometry(f"+{self.winfo_x() + dx}+{self.winfo_y() + dy}")

    def _save_position(self) -> None:
        self.geo["x"], self.geo["y"] = self.winfo_x(), self.winfo_y()
        self.app_config.save()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        if self._monitor is not None:
            self._monitor.remove_listener(self._on_whisper)
        self._save_position()
        self.destroy()


def _shorten(text: str, budget: int) -> str:
    text = text.strip()
    return text if len(text) <= budget else text[: budget - 1].rstrip() + "…"


def _sample_plain() -> Whisper:
    from datetime import datetime

    return Whisper(
        sender="테스트친구",
        text="형 지금 접속했어요? 같이 맵 돌아요",
        at=datetime.now(),
    )


def _sample() -> Whisper:
    from datetime import datetime

    return Whisper(
        sender="테스트구매자",
        text="Hi, I would like to buy your 보석상의 징조 listed for 5 chaos",
        at=datetime.now(),
        item="보석상의 징조", price="5 chaos",
        stash_tab="판매 #1", stash_left=12, stash_top=1,
    )

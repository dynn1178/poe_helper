"""Incoming whispers, read from the client's own log.

Same source and the same reasoning as ``zone.py``: the client writes every
chat line it displays into ``logs/KakaoClient.txt``, so whispers can be read
without touching the game's memory or the screen. This subscribes to that
tailer rather than opening the file again -- see ``zone.on_log_line``.

Only *received* whispers matter here::

    2026/08/16 02:40:10 ... [INFO Client 22860] @수신 MaxBlack: 안녕하세요

The client writes ``@수신`` for received and ``@발신`` for sent (``@From`` /
``@To`` on the international build). Note that nothing you type is ever
logged: the only ``@발신`` lines the game writes are the server's own "이
플레이어는 자리비움 상태입니다" notices, so there is no way to show your own
side of a conversation, and no attempt is made to.

A trade whisper is picked apart further. Every one the game generates has the
same shape, and the parts of it worth acting on -- what they want, what they
are paying, and where the item is sitting -- are exactly the parts that are
tedious to read out of a wall of text mid-map::

    Hi, I would like to buy your 보석상의 징조 listed for 5 chaos in Allflame
    (stash tab "판매 #1"; position: left 12, top 1)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from . import zone

logger = logging.getLogger(__name__)

_LINE_RE = re.compile(
    r"^(?P<date>\d{4}/\d{2}/\d{2}) (?P<time>\d{2}:\d{2}:\d{2}) "
    r".*?\[INFO Client \d+\] @(?P<direction>수신|발신|From|To) +"
    r"(?:<(?P<guild>[^>]*)> *)?(?P<name>[^:]*?): ?(?P<text>.*)$"
)
_INCOMING = {"수신", "From"}

# The buy message the game itself composes. English is what the client emits
# even for Korean players (verified across a season's worth of real whispers);
# the Korean wording is accepted too in case a Korean-client buyer produces it.
_TRADE_RES = (
    re.compile(
        r"like to buy your (?P<item>.+?) listed for (?P<price>.+?) in (?P<league>[^(]+)"
        r"(?:\(stash tab \"(?P<tab>[^\"]*)\"; position: left (?P<left>\d+), top (?P<top>\d+)\))?"
    ),
    re.compile(
        r"(?P<league>\S+)(?:에|에서) 올려놓은 (?P<item>.+?)(?:을|를|을\(를\)) "
        r"(?P<price>.+?)(?:에|으로) (?:구매|구입|사)"
        r"(?:.*?보관함 탭 \"(?P<tab>[^\"]*)\".*?왼쪽 (?P<left>\d+).*?(?:상단|위) (?P<top>\d+))?"
    ),
)

# A whisper older than this is not worth keeping on screen: the buyer has
# moved on, and a panel that accumulates all evening becomes something to
# clear out rather than something to read.
DEFAULT_KEEP_MINUTES = 30

# "level 1 0% <name>" -- how the game writes a gem into a buy message.
_GEM_PREFIX_RE = re.compile(r"^(?:level|레벨)\s+\d+", re.IGNORECASE)


@dataclass
class Whisper:
    sender: str
    text: str
    at: datetime
    guild: str = ""
    # Trade details, when the message is one the game composed.
    item: str = ""
    price: str = ""
    league: str = ""
    stash_tab: str = ""
    stash_left: int = 0
    stash_top: int = 0

    @property
    def is_trade(self) -> bool:
        return bool(self.item)

    @property
    def rarity(self) -> str:
        """What kind of thing was asked for, as far as it can be told.

        The whisper the game composes never states rarity, so this reads the
        shape of the item name instead -- reliable for two cases and honestly
        ambiguous for the rest:

        * a rare is written "<rolled name>, <base type>", the only kind with
          a comma in it;
        * a gem carries its level and quality up front ("level 1 0% 바알 면죄");
        * everything else is a single bare name, where a unique looks exactly
          like a currency. Those are left uncoloured rather than guessed at.
        """
        text = self.item.strip()
        if not text:
            return ""
        if _GEM_PREFIX_RE.match(text):
            return "gem"
        if ", " in text:
            return "rare"
        return ""

    @property
    def elapsed(self) -> str:
        """How long ago this arrived, for the card to keep showing."""
        seconds = self.age_seconds()
        if seconds < 60:
            return "방금"
        if seconds < 3600:
            return f"{int(seconds // 60)}분 전"
        return f"{int(seconds // 3600)}시간 전"

    @property
    def stash_location(self) -> str:
        if not self.stash_tab:
            return ""
        if self.stash_left or self.stash_top:
            return f'{self.stash_tab}  ({self.stash_left}, {self.stash_top})'
        return self.stash_tab

    def age_seconds(self) -> float:
        return max(0.0, (datetime.now() - self.at).total_seconds())


def parse(line: str) -> Whisper | None:
    """One log line -> a received :class:`Whisper`, or ``None``."""
    match = _LINE_RE.match(line)
    if match is None or match.group("direction") not in _INCOMING:
        return None
    try:
        at = datetime.strptime(
            f"{match.group('date')} {match.group('time')}", "%Y/%m/%d %H:%M:%S"
        )
    except ValueError:
        at = datetime.now()

    whisper = Whisper(
        sender=(match.group("name") or "").strip(),
        text=(match.group("text") or "").strip(),
        at=at,
        guild=(match.group("guild") or "").strip(),
    )
    _add_trade_details(whisper)
    return whisper


def _add_trade_details(whisper: Whisper) -> None:
    for pattern in _TRADE_RES:
        found = pattern.search(whisper.text)
        if not found:
            continue
        parts = found.groupdict()
        whisper.item = (parts.get("item") or "").strip()
        whisper.price = (parts.get("price") or "").strip()
        whisper.league = (parts.get("league") or "").strip()
        whisper.stash_tab = (parts.get("tab") or "").strip()
        whisper.stash_left = int(parts.get("left") or 0)
        whisper.stash_top = int(parts.get("top") or 0)
        return


class WhisperMonitor:
    """Turns the client log's ``@수신`` lines into :class:`Whisper` events.

    Runs on the log tracker's thread, so listeners are called off the Tk main
    loop and must marshal themselves back onto it.
    """

    def __init__(self, config):
        self.config = config
        self._listeners: list = []
        self._subscribed = False
        self.seen_count = 0

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self.refresh()

    def stop(self) -> None:
        self._unsubscribe()

    def refresh(self) -> None:
        """Follow the ``whisper.enabled`` setting, so switching the feature
        off actually stops the reading rather than reading and discarding."""
        if self.enabled():
            self._subscribe()
        else:
            self._unsubscribe()

    def _subscribe(self) -> None:
        if not self._subscribed:
            zone.on_log_line(self._on_line)
            self._subscribed = True

    def _unsubscribe(self) -> None:
        if self._subscribed:
            zone.remove_log_line_listener(self._on_line)
            self._subscribed = False

    # ---- settings ---------------------------------------------------------
    def _settings(self) -> dict:
        return self.config.data.get("whisper", {})

    def enabled(self) -> bool:
        return bool(self._settings().get("enabled", True))

    def keep_minutes(self) -> float:
        try:
            return max(1.0, float(self._settings().get("keep_minutes", DEFAULT_KEEP_MINUTES)))
        except (TypeError, ValueError):
            return DEFAULT_KEEP_MINUTES

    # ---- listeners --------------------------------------------------------
    def add_listener(self, callback) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _on_line(self, line: str) -> None:
        # Cheapest possible rejection first: this runs on every line the
        # client writes, and almost none of them are whispers.
        if "@" not in line:
            return
        whisper = parse(line)
        if whisper is None or not whisper.sender:
            return
        self.seen_count += 1
        for callback in list(self._listeners):
            try:
                callback(whisper)
            except Exception:
                logger.exception("whisper listener failed")


# ---------------------------------------------------------------------------
# Talking back
# ---------------------------------------------------------------------------
# What each button on a whisper card sends. All of them are ordinary chat
# commands typed into the game's own chat box -- the same thing the chat
# macros in input_io.py do.
COMMANDS = {
    "invite": "/invite {name}",
    "trade": "/tradewith {name}",
    "hideout": "/hideout {name}",
    "kick": "/kick {name}",
    "ignore": "/ignore {name}",
}


def send_command(config, kind: str, name: str) -> None:
    """Run one of :data:`COMMANDS` against *name*, off the Tk loop.

    Always on a thread: the sequence is a fifth of a second of deliberate
    sleeps between keystrokes (the game's chat box needs them), and spending
    that inside a button callback would freeze every window this app owns.
    """
    template = COMMANDS.get(kind)
    name = (name or "").strip()
    if not template or not name:
        return
    threading.Thread(
        target=_command_worker, args=(config, template.format(name=name)),
        name="whisper-command", daemon=True,
    ).start()


# The three things a seller actually says back, in English because the buyer
# usually is not Korean -- across a season of real whispers the buy messages
# were English without exception. Polite and complete sentences: these go to a
# stranger who is waiting, and a bare "no" reads badly.
DEFAULT_REPLIES = {
    "thanks": "Thank you very much! Have a great day.",
    "wait": "Sorry, could you please wait a moment? I will invite you shortly.",
    "sold": "I am sorry, that item is already sold. Thank you for your interest!",
}


def quick_reply(config, name: str, kind: str) -> None:
    """Send one of the stock answers to a whisper.

    Distinct from the 귓속말 button, which only opens the chat box: these are
    the sentences said every single time, and typing them out mid-map is the
    part worth removing.
    """
    replies = config.data.get("whisper", {}).get("replies") or {}
    text = str(replies.get(kind) or DEFAULT_REPLIES.get(kind, "")).strip()
    if text:
        whisper_to(config, name, text)


def whisper_to(config, name: str, text: str = "") -> None:
    """Open the game's chat addressed to *name*; send it if *text* is given."""
    name = (name or "").strip()
    if not name:
        return
    message = f"@{name} {text}".rstrip()
    threading.Thread(
        target=_command_worker, args=(config, message, bool(text)),
        name="whisper-reply", daemon=True,
    ).start()


def _command_worker(config, message: str, submit: bool = True) -> None:
    from . import foreground, input_io, toast

    window_class = config.data.get("misc", {}).get("poe_window_class", "POEWindowClass")
    if not foreground.focus_game(window_class):
        toast.show("게임 창을 찾지 못했습니다.")
        return
    # The game needs a moment after being raised before its chat box will
    # take the Enter that opens it.
    time.sleep(0.12)
    try:
        if submit:
            input_io.send_chat_message(message)
        else:
            input_io.open_chat_box()
            input_io.paste_into_chat(message)
    except Exception:
        logger.exception("could not send %r", message)


# ---------------------------------------------------------------------------
# Module-level accessor
# ---------------------------------------------------------------------------
_monitor: WhisperMonitor | None = None


def set_monitor(monitor: WhisperMonitor | None) -> None:
    global _monitor
    _monitor = monitor


def get_monitor() -> WhisperMonitor | None:
    return _monitor

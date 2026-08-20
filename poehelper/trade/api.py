"""Client for the official trade site's JSON API.

The Korean (Kakao) realm serves the same API as pathofexile.com at its own
host, and -- crucially -- serves the *Korean* text for all 18,000-odd
modifiers alongside the same stat ids the search takes. That is what makes
price checking possible for this client at all: the hard part of a localised
price checker is normally hand-maintaining a translation table for every mod
in the game, and here the site simply hands it over.

    GET  /api/trade/data/leagues   -> current leagues
    GET  /api/trade/data/stats     -> every searchable modifier, localised
    POST /api/trade/search/<league> -> a search id + up to 100 result ids
    GET  /api/trade/fetch/<ids>    -> the actual listings for those ids

Two things this module exists to get right:

*Rate limits.* GGG publishes the policy in response headers and enforces it
with temporary IP bans. :class:`_RateLimiter` reads that policy and blocks
*before* sending a request that would breach it, rather than reacting to a
429 after the damage is done.

*Not re-downloading the world.* The stats payload is several megabytes and
changes only when the game does, so it is cached next to the executable and
refreshed weekly.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

from .. import paths, version

logger = logging.getLogger(__name__)

# Korean realm first -- this app is built for the Kakao client. The official
# host is offered for anyone running the international build; the two speak
# the same API, so nothing else in this package cares which is in use.
REALMS: dict[str, str] = {
    "kakao": "https://poe.kakaogames.com",
    "official": "https://www.pathofexile.com",
}
DEFAULT_REALM = "kakao"

# GGG asks third-party tools to identify themselves rather than pretend to be
# a browser, so that a misbehaving one can be contacted instead of blocked.
_USER_AGENT = f"KuanPoeHelper/{version.__version__} (+https://github.com/dynn1178)"

_CACHE_DAYS = 7
_TIMEOUT = 25


class TradeError(Exception):
    """Anything that stops a price check, in a form the GUI can print."""


class RateLimited(TradeError):
    def __init__(self, retry_after: float):
        super().__init__(
            f"거래소 요청이 잠시 제한되었습니다. {int(retry_after)}초 후 다시 시도하세요."
        )
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class _RateLimiter:
    """Honours the ``X-Rate-Limit-*`` policy the site returns.

    The header is a comma-separated list of ``hits:period:ban`` rules, e.g.
    ``8:10:60,15:60:120`` = "at most 8 requests per 10s, and 15 per 60s;
    breaching either earns a ban of the third number in seconds".

    Rather than trusting the server's running count (which lags, and is
    shared with any other tool the user is running), this keeps its own
    timestamps and refuses to send a request that would exceed a rule. One
    request is held back from every cap as headroom, because a price check
    that waits half a second is strictly better than one that gets the IP
    banned for two minutes.
    """

    _RULE_RE = re.compile(r"(\d+):(\d+):(\d+)")

    def __init__(self) -> None:
        self._rules: list[tuple[int, int]] = [(6, 10)]  # cautious until told
        self._hits: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def update_from(self, headers) -> None:
        for name in ("X-Rate-Limit-Ip", "X-Rate-Limit-Account"):
            raw = headers.get(name)
            if not raw:
                continue
            rules = [
                (int(hits), int(period))
                for hits, period, _ban in self._RULE_RE.findall(raw)
            ]
            if rules:
                self._rules = rules
                return

    def penalise(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                if now < self._blocked_until:
                    delay = self._blocked_until - now
                else:
                    delay = self._delay_for(now)
                    if delay <= 0:
                        self._hits.append(now)
                        return
            time.sleep(min(delay, 5.0))

    def _delay_for(self, now: float) -> float:
        """How long before one more request would still be inside every rule.

        The arithmetic here was wrong in a way that cost whole seconds. For a
        rule of "N per P seconds", the request that has to expire before
        another may be sent is the *N-th most recent*, and the wait is
        whatever is left of P since then. Indexing from the wrong end
        measured from the oldest request in the window instead, which under
        a 300-second rule produced waits of half a minute after a dozen
        requests -- against a policy that permits twelve every four seconds.
        """
        longest = max((period for _hits, period in self._rules), default=60)
        while self._hits and now - self._hits[0] > longest:
            self._hits.popleft()
        wait = 0.0
        for allowed, period in self._rules:
            # One request of headroom, so a burst never lands exactly on the
            # cap -- other tools on the same IP share this budget.
            cap = max(1, allowed - 1)
            recent = [t for t in self._hits if now - t < period]
            if len(recent) < cap:
                continue
            # The oldest request that still counts against the cap.
            oldest_counted = recent[len(recent) - cap]
            wait = max(wait, period - (now - oldest_counted))
        return max(0.0, wait)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------
class TradeAPI:
    def __init__(self, config):
        self.config = config
        self._limiter = _RateLimiter()
        self._stats_index: dict[str, list[dict]] | None = None
        self._bases: list[str] | None = None
        self._names: dict[str, str] | None = None
        self._types: dict[str, str] | None = None
        self._lock = threading.Lock()

    # ---- settings ---------------------------------------------------------
    def _settings(self) -> dict:
        return self.config.data.get("trade", {})

    @property
    def realm(self) -> str:
        return self._settings().get("realm", DEFAULT_REALM)

    @property
    def base_url(self) -> str:
        return REALMS.get(self.realm, REALMS[DEFAULT_REALM])

    def league(self) -> str:
        return self._settings().get("league", "") or "Standard"

    # ---- transport --------------------------------------------------------
    def _request(self, path: str, payload: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method="POST" if data else "GET",
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        self._limiter.wait()
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                self._limiter.update_from(response.headers)
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._limiter.update_from(exc.headers)
            if exc.code == 429:
                retry = float(exc.headers.get("Retry-After", 60) or 60)
                self._limiter.penalise(retry)
                raise RateLimited(retry) from exc
            raise TradeError(self._describe(exc)) from exc
        except urllib.error.URLError as exc:
            raise TradeError(f"거래소에 연결할 수 없습니다: {exc.reason}") from exc

    @staticmethod
    def _describe(exc: urllib.error.HTTPError) -> str:
        """The site explains a rejected query in the body; a bare status code
        ("400") tells the user nothing they can act on."""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            message = body.get("error", {}).get("message")
            if message:
                return f"거래소 오류: {message}"
        except Exception:
            pass
        return f"거래소 오류 (HTTP {exc.code})"

    # ---- cached static data ----------------------------------------------
    def _cache_path(self, name: str) -> Path:
        directory = paths.app_dir() / "cache"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"trade_{self.realm}_{name}.json"

    def _cached(self, name: str, path: str) -> Any:
        target = self._cache_path(name)
        if target.exists():
            age_days = (time.time() - target.stat().st_mtime) / 86400
            if age_days < _CACHE_DAYS:
                try:
                    return json.loads(target.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    logger.debug("discarding unreadable cache %s", target)
        fetched = self._request(path)
        try:
            target.write_text(json.dumps(fetched, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.debug("could not write %s", target, exc_info=True)
        return fetched

    def leagues(self) -> list[dict]:
        """``[{"id": "Allflame", "text": "올플레임"}, ...]`` -- id is what the
        search endpoint takes, text is what to show the user."""
        return self._cached("leagues", "/api/trade/data/leagues").get("result", [])

    def stats(self) -> list[dict]:
        return self._cached("stats", "/api/trade/data/stats").get("result", [])

    def items(self) -> list[dict]:
        return self._cached("items", "/api/trade/data/items").get("result", [])

    def base_types(self) -> list[str]:
        """Every base type the site knows, longest first.

        Longest first because resolution is "which base is inside this
        string", and 신성한 생명력 플라스크 must win over 생명력 플라스크
        when both are contained in the same name.
        """
        with self._lock:
            if self._bases is None:
                names = {
                    entry["type"]
                    for group in self.items()
                    for entry in group.get("entries", [])
                    if entry.get("type")
                }
                self._bases = sorted(names, key=len, reverse=True)
                logger.info("trade base types: %d", len(self._bases))
            return self._bases

    def _name_index(self) -> tuple[dict[str, str], dict[str, str]]:
        """Unique names and base types, keyed by their whitespace-flattened form.

        The value is the site's *own* spelling, because that is what the
        search endpoint demands -- see :meth:`resolve_name`.

        A failed download is not cached: the item list is only needed to tidy
        up spelling, and a price check that cannot reach it should fall back
        to the printed text rather than refuse to search at all.
        """
        with self._lock:
            if self._names is not None and self._types is not None:
                return self._names, self._types
            try:
                groups = self.items()
            except TradeError:
                logger.debug("item list unavailable; using printed names", exc_info=True)
                return {}, {}
            names: dict[str, str] = {}
            types: dict[str, str] = {}
            for group in groups:
                for entry in group.get("entries", []):
                    for attribute, target in (("name", names), ("type", types)):
                        value = entry.get(attribute)
                        if value:
                            target.setdefault(_flatten(value), value)
            self._names, self._types = names, types
            logger.info("trade item index: %d names, %d types", len(names), len(types))
            return names, types

    def resolve_name(self, printed: str) -> str:
        """The site's exact spelling of a unique's name, or "".

        The search endpoint matches ``name`` exactly and answers anything else
        with HTTP 400 "Unknown item name" -- and the site's own data is not
        always tidy. 들불 체관 is published as ``"들불 체관 "``, trailing space
        and all, so the perfectly correct name read off the item was rejected
        outright. Comparing with whitespace flattened and then sending back
        whatever the site itself calls the item sidesteps every such typo,
        present and future, without a hand-maintained fix-up table.
        """
        names, _types = self._name_index()
        return names.get(_flatten(printed), "")

    def resolve_type(self, printed: str) -> str:
        """The site's exact spelling of a base type, or ""."""
        _names, types = self._name_index()
        return types.get(_flatten(printed), "")

    def resolve_base(self, printed: str) -> str:
        """The base type inside a magic item's decorated name.

        A magic item prints as "<prefix> <base> - <suffix>" with no way to
        tell the three apart, and the site only accepts the base. Rather than
        trying to strip affixes -- there are thousands, in two languages --
        this looks for the longest base type the site itself publishes that
        appears in the string. Returns "" when nothing matches, which leaves
        the search to fall back on mods alone.
        """
        printed = (printed or "").strip()
        if not printed:
            return ""
        for base in self.base_types():
            if base in printed:
                return base
        return ""

    # ---- stat lookup ------------------------------------------------------
    def stat_index(self) -> dict[str, list[dict]]:
        """Normalised mod text -> the stat entries that render as it.

        One text can map to several ids (the same wording exists as an
        implicit, an explicit and a fractured mod), so the value is a list and
        picking from it is :mod:`query`'s job -- it is the only part that
        knows which kind the item actually had.
        """
        with self._lock:
            if self._stats_index is None:
                index: dict[str, list[dict]] = {}
                for group in self.stats():
                    for entry in group.get("entries", []):
                        key = normalise_stat(entry.get("text", ""))
                        if key:
                            index.setdefault(key, []).append(
                                {
                                    "id": entry["id"],
                                    "text": entry["text"],
                                    "group": group.get("label", ""),
                                }
                            )
                self._stats_index = index
                logger.info("trade stat index: %d distinct mod texts", len(index))
            return self._stats_index

    def find_stat(self, text: str) -> list[dict]:
        """Look *text* (one mod, possibly multi-line) up in the index.

        Several spellings are tried because the game and the site disagree on
        the sign in a few predictable ways -- see normalise_stat. The site
        renders a signed stat with a leading "+" whichever way it rolled, so
        "받는 화염 피해 -1" on the item has to be looked up as "+#": one stat
        id covers both directions, and the sign lives in the filter value.
        """
        index = self.stat_index()
        key = normalise_stat(text)
        for candidate in (
            key,
            key.replace("+#", "#"),
            key.replace("#", "+#", 1),
            key.replace("-#", "+#"),
        ):
            found = index.get(candidate)
            if found:
                return found
        return []

    # ---- searching --------------------------------------------------------
    def search(self, query: dict, league: str | None = None) -> dict:
        league = league or self.league()
        result = self._request(f"/api/trade/search/{_quote(league)}", query)
        return {
            "id": result.get("id", ""),
            "total": result.get("total", 0),
            "ids": result.get("result", []),
        }

    def fetch(self, ids: list[str], search_id: str) -> list[dict]:
        """Listings for up to 10 result ids -- the site's own page size."""
        if not ids:
            return []
        joined = ",".join(ids[:10])
        result = self._request(f"/api/trade/fetch/{joined}?query={search_id}")
        return [entry for entry in result.get("result", []) if entry]

    def search_url(self, search_id: str, league: str | None = None) -> str:
        """The human page for a search, for the '거래소에서 열기' button."""
        league = league or self.league()
        return f"{self.base_url}/trade/search/{_quote(league)}/{search_id}"


def _quote(value: str) -> str:
    return urllib.request.quote(value, safe="")


def _flatten(text: str) -> str:
    """A name reduced to what it *says*, so stray whitespace cannot hide it."""
    return re.sub(r"\s+", " ", text or "").strip()


# ---------------------------------------------------------------------------
# Normalising a mod line
# ---------------------------------------------------------------------------
# The game writes the rolled value, and (with advanced mod descriptions on)
# the possible range after it: "소환수가 주는 피해 29(26-30)% 증가". The trade
# site writes the same mod as "소환수가 주는 피해 #% 증가". Reducing both to
# the same shape is the whole trick, and it takes exactly four rules --
# verified against a real rare and a real unique, all 13 mods matched.
_RANGE_RE = re.compile(r"\((?:[-+]?[\d.]+)(?:-[-+]?[\d.]+)?\)")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# "— 변경이 불가능한 값" is the game's note that a unique's value cannot be
# changed; "(특정)" is the site's note that a mod is local to the item. Each
# appears on only one side, so both come off before comparing.
_UNSCALABLE = "— 변경이 불가능한 값"
_LOCAL = "(특정)"


def normalise_stat(text: str) -> str:
    text = text.replace(_UNSCALABLE, "").replace(_LOCAL, "")
    text = _RANGE_RE.sub("", text)
    # The sign is deliberately kept: the site writes "+#" for mods that only
    # add, and dropping the + turns them into a different (missing) key.
    text = _NUMBER_RE.sub("#", text)
    return "\n".join(re.sub(r"\s+", " ", line).strip() for line in text.split("\n")).strip()

"""Client for the official trade site's JSON API.

The Korean (Kakao) realm serves the same API as pathofexile.com at its own
host, and takes the same stat ids -- which is what lets :mod:`gamedata`,
built against the international game data, drive a search here at all.

    GET  /api/trade/data/leagues     -> current leagues
    GET  /api/trade/data/filters     -> what this realm will accept in a query
    GET  /api/trade/data/items       -> the site's own spelling of every name
    GET  /api/trade/data/static      -> currency ids and their localised names
    GET  /api/trade/data/stats       -> every searchable modifier, localised
    POST /api/trade/search/<league>  -> a search id + up to 100 result ids
    GET  /api/trade/fetch/<ids>      -> the actual listings for those ids
    POST /api/trade/exchange/<league> -> bulk rates, for things sold by the pile

Understanding an item is :mod:`gamedata`'s job now, not this module's. What
is left here is everything only the *realm* can answer:

*What it will accept.* Kakao is not the same build as the international
realm and the two disagree about filters in both directions -- it has no
``foulborn_item`` and still calls that ``mutated``, it has no
``sentinel_filters``, and it does have ``sanctum_filters``. Sending a filter
the realm has never heard of rejects the whole search, so :meth:`filter_allowed`
asks first.

*How it spells a name.* The search endpoint matches names exactly and the
site's own data is not tidy -- 들불 체관 is published with a trailing space.

*Rate limits.* GGG publishes the policy in response headers and enforces it
with temporary IP bans. :class:`_RateLimiter` reads that policy and blocks
*before* sending a request that would breach it, rather than reacting to a
429 after the damage is done.

*Not re-downloading the world.* These payloads are several megabytes and
change only when the game does, so they are cached next to the executable and
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

# The longest a request will be held back before the caller is told to try
# again instead. Long enough to absorb the site's short-window pacing rules,
# short enough that a price check never looks like it has hung.
_MAX_WAIT = 15.0


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
        """Block until one more request is inside the policy, or give up.

        Pacing and a ban are two different things and only one of them is
        worth waiting through. Holding a request back for a second or two so
        a burst stays inside "8 per 10s" is invisible; sitting on it for the
        half hour a breach of "60 per 300s" costs is not -- the window would
        show "거래소에서 찾는 중…" for thirty minutes with no way to tell it
        from a hang. So a wait longer than :data:`_MAX_WAIT` is reported
        instead of served.
        """
        while True:
            with self._lock:
                now = time.monotonic()
                if now < self._blocked_until:
                    raise RateLimited(self._blocked_until - now)
                delay = self._delay_for(now)
                if delay <= 0:
                    self._hits.append(now)
                    return
                if delay > _MAX_WAIT:
                    raise RateLimited(delay)
            time.sleep(min(delay, 1.0))

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
        self._filter_ids: dict[str, set[str]] | None = None
        self._categories: set[str] | None = None
        self._currency: dict[str, str] | None = None
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

    def filters(self) -> list[dict]:
        return self._cached("filters", "/api/trade/data/filters").get("result", [])

    def static(self) -> list[dict]:
        return self._cached("static", "/api/trade/data/static").get("result", [])

    # ---- what this realm actually accepts ---------------------------------
    def _filter_index(self) -> tuple[dict[str, set[str]], set[str]]:
        """The filter ids and category options this realm publishes.

        Worth asking rather than assuming. The Korean realm is not the same
        build as the international one and the two disagree about filters in
        both directions: Kakao has no ``foulborn_item`` (it still calls that
        ``mutated``) and no ``sentinel_filters``, while it does have
        ``sanctum_filters`` and ``chart_shape``. Sending a filter the realm
        does not know earns HTTP 400 for the whole search, so a filter this
        app knows about is only sent once the realm has agreed it exists.

        A failed download leaves both empty, which is read as "allow it and
        let the site decide" -- refusing to search at all because a *list of
        filters* could not be fetched would be the worse failure.
        """
        with self._lock:
            if self._filter_ids is not None and self._categories is not None:
                return self._filter_ids, self._categories
            ids: dict[str, set[str]] = {}
            categories: set[str] = set()
            try:
                groups = self.filters()
            except TradeError:
                logger.debug("filter list unavailable", exc_info=True)
                return {}, set()
            for group in groups:
                names = ids.setdefault(group.get("id", ""), set())
                for entry in group.get("filters", ()):
                    names.add(entry.get("id", ""))
                    if entry.get("id") == "category":
                        categories.update(
                            option.get("id")
                            for option in (entry.get("option") or {}).get("options", ())
                            if option.get("id")
                        )
            self._filter_ids, self._categories = ids, categories
            logger.info(
                "trade filters: %d groups, %d categories", len(ids), len(categories)
            )
            return ids, categories

    def filter_allowed(self, group: str, name: str) -> bool:
        ids, _categories = self._filter_index()
        if not ids:
            return True
        return name in ids.get(group, set())

    def category_allowed(self, category: str) -> bool:
        _ids, categories = self._filter_index()
        return not categories or category in categories

    def currency_names(self) -> dict[str, str]:
        """Currency ids mapped to the names a Korean player reads.

        The site publishes these itself, localised, which beats the short
        hand-written table this used to render prices with -- that one had
        eighteen entries and the game has hundreds.
        """
        with self._lock:
            if self._currency is not None:
                return self._currency
            names: dict[str, str] = {}
            try:
                for group in self.static():
                    for entry in group.get("entries", ()):
                        if entry.get("id") and entry.get("text"):
                            names.setdefault(entry["id"], entry["text"])
            except TradeError:
                logger.debug("static data unavailable", exc_info=True)
            self._currency = names
            return names

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

    def find_stat_ids(self, text: str, kind: str) -> list[str]:
        """The site's own ids for a modifier line, as a second opinion.

        The bundled game data is what normally resolves a mod, and it is
        better at it -- it knows that a line printed as 감소 is a negative
        증가, which no amount of comparing text can work out. But it is a
        snapshot, and the realm gets new modifiers before a new snapshot is
        downloaded. So when the data has never heard of a line, the site is
        asked whether *it* has.

        Costs a multi-megabyte download the first time, which is why nothing
        calls this on the path that opens the window.
        """
        entries = self.find_stat(text)
        if not entries:
            return []
        wanted = [e for e in entries if e["id"].startswith(f"{kind}.")]
        return [e["id"] for e in (wanted or entries)][:1]

    # ---- searching --------------------------------------------------------
    def search(self, query: dict, league: str | None = None) -> dict:
        league = league or self.league()
        result = self._request(f"/api/trade/search/{_quote(league)}", query)
        return {
            "id": result.get("id", ""),
            "total": result.get("total", 0),
            "ids": result.get("result", []),
        }

    def exchange(
        self, want: str, have: str = "chaos", league: str | None = None, minimum: int = 1
    ) -> dict:
        """What a pile of *want* trades for, in *have*.

        Currency, fragments, essences, fossils and scarabs are not sold as
        listings of one item -- they are exchanged in bulk, and a normal
        search finds only the handful of people who happened to list a single
        one. Pricing a stack of chaos as if it were a rare wand is why
        currency never returned a believable number.

        The response has the same shape as a search, so :meth:`fetch` reads
        it, but each listing carries an ``exchange`` block with the rate
        rather than a ``price``.
        """
        league = league or self.league()
        payload = {
            "query": {
                "status": {"option": "online"},
                "have": [have],
                "want": [want],
                "minimum": minimum,
            },
            "sort": {"have": "asc"},
            "engine": "new",
        }
        result = self._request(f"/api/trade/exchange/{_quote(league)}", payload)
        return {
            "id": result.get("id", ""),
            "total": result.get("total", 0),
            # The exchange endpoint answers with the listings inline, keyed
            # by result id, rather than with ids to fetch separately.
            "listings": list((result.get("result") or {}).values())
            if isinstance(result.get("result"), dict)
            else (result.get("result") or []),
        }

    def exchange_url(self, search_id: str, league: str | None = None) -> str:
        league = league or self.league()
        return f"{self.base_url}/trade/exchange/{_quote(league)}/{search_id}"

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

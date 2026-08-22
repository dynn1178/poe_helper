"""The game's own data: every modifier, every base type, every client label.

This is what replaced asking the trade site to explain a Korean item. The
site publishes each searchable stat with its localised wording, and matching
the game's text against that wording works -- until it doesn't:

* the game prints ``공격 속도 12% 감소`` where the site indexes
  ``공격 속도 #% 증가`` and expects a *negative* value;
* with advanced mod descriptions on, the game writes
  ``모든 면죄(화염구-마나 주입 지팡이) 젬 레벨 +1`` where the site says
  ``모든 면죄 젬 레벨 +#``;
* the game drops the number entirely when a roll is at its cap
  (``명중 시 항상 중독 유발`` is the 100% roll of a chance mod);
* one wording is published under two ids, one *local* to the item and one
  global, and only the kind of item it sits on can say which was meant.

Measured against the Kakao realm's own stat list, plain text comparison
matched 7,053 of 10,353 distinct wordings; the four cases above account for
the other 3,300. The data files here carry each of them explicitly, so the
answer is looked up rather than guessed at.

    data/trade/ko/stats.ndjson         modifiers, with the trade ids they map to
    data/trade/ko/items.ndjson         bases, uniques, gems, cards
    data/trade/ko/client_strings.json  the labels the client prints

They come from Awakened PoE Trade (MIT licensed), which generates them from
the game's data files; ``tools/update_trade_data.py`` refreshes them. Every
stat id in them was checked against poe.kakaogames.com and all 12,585 exist
there, so the Korean realm takes them as they are.

Nothing in this module touches the network. That is the point: a price check
now understands the item before it says a word to the trade site, and can
therefore ask a precise question instead of a hopeful one.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "ko"


# ---------------------------------------------------------------------------
# Item categories
# ---------------------------------------------------------------------------
# The category the data files use (the game's own grouping) mapped onto the
# id the trade site's type_filters.category takes. Sending it turns "a rare
# with these mods" into "a *wand* with these mods", which is most of what
# makes a rare item's price check meaningful -- the same mods on a ring and
# on a two-handed axe are not the same item.
#
# Entries the realm does not offer are dropped at query time rather than
# here: Kakao publishes its own filter list and it is not the same as the
# international realm's. See TradeAPI.category_allowed.
TRADE_CATEGORY = {
    "Abyss Jewel": "jewel.abyss",
    "Amulet": "accessory.amulet",
    "Belt": "accessory.belt",
    "Body Armour": "armour.chest",
    "Boots": "armour.boots",
    "Bow": "weapon.bow",
    "Chart": "chart",
    "Claw": "weapon.claw",
    "Cluster Jewel": "jewel.cluster",
    "Dagger": "weapon.dagger",
    "Expedition Logbook": "logbook",
    "Fishing Rod": "weapon.rod",
    "Flask": "flask",
    "Gloves": "armour.gloves",
    "Graft": "graft",
    "Heist Blueprint": "heistmission.blueprint",
    "Heist Brooch": "heistequipment.heistreward",
    "Heist Cloak": "heistequipment.heistutility",
    "Heist Contract": "heistmission.contract",
    "Heist Gear": "heistequipment.heistweapon",
    "Heist Tool": "heistequipment.heisttool",
    "Helmet": "armour.helmet",
    "Idol": "idol",
    "Invitation": "map.invitation",
    "Jewel": "jewel",
    "Map": "map",
    "One-Handed Axe": "weapon.oneaxe",
    "One-Handed Mace": "weapon.onemace",
    "One-Handed Sword": "weapon.onesword",
    "Quiver": "armour.quiver",
    "Ring": "accessory.ring",
    "Rune Dagger": "weapon.runedagger",
    "Sanctum Relic": "sanctum.relic",
    "Sceptre": "weapon.sceptre",
    "Shield": "armour.shield",
    "Staff": "weapon.staff",
    "Tincture": "tincture",
    "Trinket": "accessory.trinket",
    "Two-Handed Axe": "weapon.twoaxe",
    "Two-Handed Mace": "weapon.twomace",
    "Two-Handed Sword": "weapon.twosword",
    "Wand": "weapon.wand",
    "Warstaff": "weapon.warstaff",
}

# The three groups a stat's ``resolve.test`` can name. Everything else in a
# test is a category spelled out in full.
WEAPON = frozenset(
    c for c, t in TRADE_CATEGORY.items() if t.startswith("weapon.")
)
# Quivers are armour.* on the trade site but carry no armour value of their
# own, so a "방어도 #% 증가" on one is the global mod, never the local one.
ARMOUR = frozenset({"Body Armour", "Boots", "Gloves", "Helmet", "Shield"})
HEIST_EQUIPMENT = frozenset(
    {"Heist Brooch", "Heist Cloak", "Heist Gear", "Heist Tool"}
)


def in_category(category: str, expected: str) -> bool:
    if not category:
        return False
    if expected == "WEAPON":
        return category in WEAPON
    if expected == "ARMOUR":
        return category in ARMOUR
    if expected == "HEIST_EQUIPMENT":
        return category in HEIST_EQUIPMENT
    return category == expected


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Stat:
    """One modifier as the game defines it, with the ids the search takes."""

    ref: str                        # the English wording, used only for logs
    better: int                     # 1 higher is better, -1 lower is, 0 neither
    dp: bool                        # does this stat roll fractional values?
    ids: dict[str, list[str]]       # explicit/implicit/... -> trade stat ids
    group: int = -1                 # index into GameData._groups, -1 if alone
    slot: int = 0                   # position of this stat inside that group


@dataclass(frozen=True)
class Matcher:
    """One wording that resolves to a :class:`Stat`."""

    text: str
    stat: Stat
    negate: bool = False            # printed the opposite way round
    value: float | None = None      # the roll the wording implies


@dataclass
class Group:
    """Several stats that share a wording, and the rule for telling them apart."""

    strat: str
    test: list[str | None] = field(default_factory=list)
    kind: list[str] = field(default_factory=list)
    stats: list[Stat] = field(default_factory=list)


@dataclass
class StatMatch:
    """A modifier line understood: which stat, how it rolled, what to search."""

    stat: Stat
    matcher: Matcher
    values: list[float] = field(default_factory=list)
    ranges: list[tuple[float, float]] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)

    @property
    def value(self) -> float | None:
        """The one number the site filters on.

        A two-number mod ("화염 피해 47~80 추가") is one filter over the
        average; anything else is read off the first number, which is what
        the site itself does.
        """
        return _roll_of(self.values)

    @property
    def low(self) -> float | None:
        return _roll_of([r[0] for r in self.ranges]) if self.ranges else None

    @property
    def high(self) -> float | None:
        return _roll_of([r[1] for r in self.ranges]) if self.ranges else None


def _roll_of(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 2:
        return (values[0] + values[1]) / 2
    return values[0]


@dataclass(frozen=True)
class ItemInfo:
    """A base type, unique, gem or card, as the game and the site name it."""

    name: str               # the Korean name printed on the item
    ref_name: str           # the English name, which is also the site's id
    namespace: str          # ITEM / UNIQUE / GEM / DIVINATION_CARD / ...
    category: str = ""      # the game's category, e.g. "Body Armour"
    trade_tag: str = ""     # the bulk-exchange id, currency and fragments
    unique_base: str = ""   # UNIQUE only: the English base it sits on
    max_gem_level: int = 0
    transfigured: bool = False
    armour: dict = field(default_factory=dict)

    @property
    def trade_category(self) -> str:
        """The id for type_filters.category, or "" when there is none."""
        if self.namespace == "GEM":
            if not self.ref_name.endswith("Support"):
                return "gem.activegem"
            return (
                "gem.supportgemplus"
                if self.ref_name.startswith("Awakened ")
                else "gem.supportgem"
            )
        if self.namespace == "DIVINATION_CARD":
            return "card"
        if self.namespace == "CAPTURED_BEAST":
            return "monster.beast"
        return TRADE_CATEGORY.get(self.category, "")


# ---------------------------------------------------------------------------
# Reading a printed line
# ---------------------------------------------------------------------------
# Every number on a modifier line, with the range the game prints after it
# when advanced mod descriptions are on: "29(26-30)" is a 29 rolled out of
# 26..30. The lookbehind keeps the second half of "26-30" from being read as
# a number in its own right.
_ROLL_RE = re.compile(
    r"(?P<value>(?<![\d)])[+-]?\d+(?:\.\d+)?)"
    r"(?:\((?P<min>[^)-]*)(?:-(?P<max>[^)]+))?\))?"
)
_EMPTY_PARENS_RE = re.compile(r"\(\)")

# Which of the numbers found on a line to put back as literal text, in the
# order to try them. A line reads "최근 4초 이내 적을 처치한 경우 공격 속도
# 12% 증가" and the site's wording is "최근 4초 이내 … 공격 속도 #% 증가":
# the 4 is part of the sentence and the 12 is the roll, and nothing about
# either number says which is which. So every division is tried, most
# literal first, and the first one the game's own data recognises wins.
_PLACEHOLDER_MAP: tuple[tuple[tuple[int, ...], ...], ...] = (
    ((),),
    ((0,), ()),
    ((0, 1), (0,), (1,), ()),
    ((0, 1, 2), (1, 2), (0, 2), (0, 1), (2,), (1,), (0,)),
    (
        (0, 1, 2, 3), (1, 2, 3), (0, 2, 3), (0, 1, 3), (0, 1, 2),
        (2, 3), (1, 3), (1, 2), (0, 3), (0, 2), (0, 1),
    ),
)


@dataclass
class _Number:
    roll: float
    text: str
    bounds: tuple[float, float] | None


def _combinations(line: str):
    """Yield ``(lookup text, rolls)`` for a printed modifier line.

    See _PLACEHOLDER_MAP: the numbers on the line are found once, and then
    handed out as either "part of the wording" or "a roll" in every
    combination there is, so the caller can try each against the game's data.
    """
    numbers: list[_Number] = []

    def take(match: re.Match) -> str:
        low, high = match.group("min"), match.group("max")
        if low is not None and high is None:
            high = low  # "# uses remaining" and other one-sided legacy rolls
        bounds: tuple[float, float] | None = None
        try:
            if low is not None:
                bounds = (float(low), float(high))
        except (TypeError, ValueError):
            bounds = None
        numbers.append(_Number(float(match.group("value")), match.group("value"), bounds))
        if bounds is None and low is not None:
            return f"#({low}-{high})"
        return "#"

    blanked = _ROLL_RE.sub(take, _EMPTY_PARENS_RE.sub("", line))

    if len(numbers) < len(_PLACEHOLDER_MAP):
        for literal in _PLACEHOLDER_MAP[len(numbers)]:
            index = -1

            def restore(_match: re.Match, literal=literal) -> str:
                nonlocal index
                index += 1
                return numbers[index].text if index in literal else "#"

            yield (
                re.sub("#", restore, blanked),
                [n for i, n in enumerate(numbers) if i not in literal],
            )
    # Lines with five or more numbers are searched as they are printed.
    yield line, []


# ---------------------------------------------------------------------------
# The data itself
# ---------------------------------------------------------------------------
class ClientStrings:
    """The labels the client prints, as literals and compiled patterns.

    Attribute access rather than a dict so a typo is an AttributeError at the
    point of use instead of a silently missing label. A key the data file
    does not carry answers as an empty string, which every caller already
    treats as "this client does not print that".
    """

    def __init__(self, raw: dict):
        self._raw = raw
        self._patterns: dict[str, re.Pattern] = {}

    def __getattr__(self, name: str):
        try:
            value = self._raw[name]
        except KeyError:
            return ""
        if isinstance(value, dict):
            pattern = self._patterns.get(name)
            if pattern is None:
                pattern = re.compile(
                    value["re"], re.IGNORECASE if value.get("flags") else 0
                )
                self._patterns[name] = pattern
            return pattern
        return value

    def __contains__(self, name: str) -> bool:
        return name in self._raw


class GameData:
    def __init__(self, directory: Path):
        self.directory = directory
        self.strings = ClientStrings(
            json.loads((directory / "client_strings.json").read_text(encoding="utf-8"))
        )
        self._groups: list[Group] = []
        self._by_text: dict[str, list[Matcher]] = {}
        # Keyed by id() because Stat is frozen and therefore cannot carry a
        # mutable list of its own without giving up the hashability that
        # makes it usable as a dict key elsewhere.
        self._stat_matchers: dict[int, list[Matcher]] = {}
        self._by_ref: dict[str, Stat] = {}
        self._items: dict[str, list[ItemInfo]] = {}
        self._bases: list[ItemInfo] = []
        self._load_stats()
        self._load_items()

    # ---- loading ----------------------------------------------------------
    def _load_stats(self) -> None:
        path = self.directory / "stats.ndjson"
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if "stats" in record:
                resolve = record.get("resolve", {})
                group = Group(
                    strat=resolve.get("strat", ""),
                    test=list(resolve.get("test", [])),
                    kind=list(resolve.get("kind", [])),
                )
                index = len(self._groups)
                self._groups.append(group)
                for slot, raw in enumerate(record["stats"]):
                    group.stats.append(self._add_stat(raw, index, slot))
            else:
                self._add_stat(record, -1, 0)
        logger.info(
            "game data: %d mod wordings, %d ambiguous groups",
            len(self._by_text),
            len(self._groups),
        )

    def find_by_ref(self, ref: str) -> Stat | None:
        """The stat with this English wording.

        Used for the handful of things a search needs that are never printed
        as a modifier line -- an item's influences, which the game shows as a
        section of their own but the site indexes as stats.
        """
        return self._by_ref.get(ref) if ref else None

    def _add_stat(self, raw: dict, group: int, slot: int) -> Stat:
        stat = Stat(
            ref=raw.get("ref", ""),
            better=raw.get("better", 1),
            dp=bool(raw.get("dp")),
            # Copied rather than shared: trivial-merge folds one stat's ids
            # into another's, and doing that to the parsed document would
            # change what the *next* item resolves to.
            ids={kind: list(ids) for kind, ids in raw["trade"]["ids"].items()},
            group=group,
            slot=slot,
        )
        self._by_ref.setdefault(stat.ref, stat)
        mine = self._stat_matchers.setdefault(id(stat), [])
        for entry in raw.get("matchers", ()):
            for text in (entry.get("string"), entry.get("advanced")):
                if not text:
                    continue
                matcher = Matcher(
                    text=text,
                    stat=stat,
                    negate=bool(entry.get("negate")),
                    value=entry.get("value"),
                )
                self._by_text.setdefault(text, []).append(matcher)
                mine.append(matcher)
        return stat

    def _load_items(self) -> None:
        path = self.directory / "items.ndjson"
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            namespace = record.get("namespace", "")
            if namespace in ("AREA", "MERCENARY_BUILD"):
                continue
            info = ItemInfo(
                name=record["name"],
                ref_name=record.get("refName", ""),
                namespace=namespace,
                category=(record.get("craftable") or {}).get("category", ""),
                trade_tag=record.get("tradeTag", ""),
                unique_base=(record.get("unique") or {}).get("base", ""),
                max_gem_level=(record.get("gem") or {}).get("maxLevel", 0),
                transfigured=bool((record.get("gem") or {}).get("transfigured")),
                armour=record.get("armour") or {},
            )
            self._items.setdefault(flatten(info.name), []).append(info)
            if namespace == "ITEM" and info.category:
                self._bases.append(info)
        # Longest first: resolving a magic item's decorated name asks which
        # published base is *inside* it, and 신성한 생명력 플라스크 has to win
        # over 생명력 플라스크 when both are.
        self._bases.sort(key=lambda i: len(i.name), reverse=True)
        logger.info("game data: %d item names, %d bases", len(self._items), len(self._bases))

    # ---- items ------------------------------------------------------------
    def find_item(
        self, name: str, namespace: str = "", base: str = ""
    ) -> ItemInfo | None:
        """The entry for a printed name, narrowed by what kind of thing it is.

        *base* picks between uniques that exist on several bases -- 선도자의
        상징 is published five times, once per base it can drop on, and only
        the base printed under the name says which copy is in hand.
        """
        found = self._items.get(flatten(name), ())
        if not found:
            return None
        if namespace:
            found = [i for i in found if i.namespace == namespace] or list(found)
        if base and len(found) > 1:
            wanted = self.find_item(base, "ITEM")
            if wanted is not None:
                exact = [i for i in found if i.unique_base == wanted.ref_name]
                if exact:
                    return exact[0]
        return found[0]

    def base_inside(self, printed: str) -> ItemInfo | None:
        """The base type hiding in a magic item's decorated name.

        A magic item prints "<접두어> <기본> - <접미어>" with nothing to mark
        the three apart, and the trade site accepts only the base. Rather
        than trying to strip thousands of affixes, this asks which published
        base appears in the string.
        """
        printed = (printed or "").strip()
        if not printed:
            return None
        for info in self._bases:
            if info.name in printed:
                return info
        return None

    # ---- stats ------------------------------------------------------------
    def match_stat(
        self, printed: str, kind: str, category: str = ""
    ) -> StatMatch | None:
        """Understand one printed modifier, as *kind*, on a *category* item.

        Returns None when the game's data has no such wording, or has it but
        not as this kind of modifier -- a crafted-only stat read off an
        implicit line is not a match, and pretending otherwise is what sends
        a search after an id no listing carries.
        """
        for text, numbers in _combinations(printed):
            candidates = self._by_text.get(text)
            if not candidates:
                continue
            roll = numbers[0].roll if len(numbers) == 1 else None
            resolved = self._resolve(candidates, text, kind, category, roll)
            if resolved is None:
                continue
            stat, matcher, extra = resolved
            ids = stat.ids.get(kind)
            if not ids:
                continue
            ids = list(ids) + [i for i in extra if i not in ids]

            values = [n.roll for n in numbers]
            bounds = [n.bounds for n in numbers]
            if matcher.negate:
                values = [-v for v in values]
                bounds = [(-b[1], -b[0]) if b else None for b in bounds]
            if not values and matcher.value is not None:
                # "명중 시 항상 중독 유발" is the 100% roll of a chance mod:
                # the game stops printing the number once it caps, and the
                # site still wants one.
                values = [float(matcher.value)]
                bounds = [(float(matcher.value), float(matcher.value))]

            ranges = []
            for value, bound in zip(values, bounds):
                if bound is None:
                    continue
                low, high = bound
                if low > high:
                    low, high = high, low
                # A roll outside its own range is a legacy one the game will
                # not print a range for any more. Widening beats discarding:
                # the window uses the range to say how good the roll is, and
                # "110 out of 20-100" would read as an impossible 150%.
                ranges.append((min(low, value), max(high, value)))
            if len(ranges) != len(values):
                ranges = []
            return StatMatch(stat=stat, matcher=matcher, values=values,
                             ranges=ranges, ids=list(ids))
        return None

    def _resolve(
        self,
        candidates: list[Matcher],
        text: str,
        kind: str,
        category: str,
        roll: float | None,
    ) -> tuple[Stat, Matcher, list[str]] | None:
        """Pick one stat when a wording resolves to several.

        Three quite different reasons a wording is ambiguous, and the data
        says which applies:

        ``select``
            The same words are a *local* stat on one kind of item and a
            global one everywhere else -- "공격 속도 12% 증가" makes this
            weapon swing faster, or makes the character attack faster, and
            searching the wrong id returns nothing at all while listings
            exist. The item's category settles it.
        ``trivial-merge``
            Two genuinely different mods that read identically ("화염 저항
            42%" is both "+42% to Fire Resistance" and "Fire Resistance is
            42%"). Either will do, so both are searched.
        ``percent-merge`` / ``flag-merge``
            A chance mod and its capped form are indexed separately; a roll
            at the cap is both, so both are searched.

        The third element of the answer is any *additional* trade id the
        merge strategies turned up. It is returned rather than written back
        into the stat because whether a merge applies depends on the roll in
        hand, and the same stat is resolved again for the next item.
        """
        groups: dict[int, list[Matcher]] = {}
        for matcher in candidates:
            groups.setdefault(matcher.stat.group, []).append(matcher)

        fallback: tuple[Stat, Matcher, list[str]] | None = None
        for index, matchers in sorted(groups.items()):
            if index == -1:
                stat = next(
                    (m.stat for m in matchers if kind in m.stat.ids), matchers[0].stat
                )
                extra: list[str] = []
            else:
                resolved = self._resolve_group(
                    self._groups[index], text, kind, category, roll
                )
                if resolved is None:
                    continue
                stat, extra = resolved
            matcher = next((m for m in matchers if m.stat is stat), None)
            if matcher is None:
                continue
            if kind in stat.ids:
                return stat, matcher, extra
            # Remembered only in case nothing better turns up. match_stat
            # rejects a stat that cannot be searched as this kind, which
            # lets it move on to the next way of reading the line.
            fallback = fallback or (stat, matcher, extra)
        return fallback

    def _resolve_group(
        self, group: Group, text: str, kind: str, category: str, roll: float | None
    ) -> tuple[Stat, list[str]] | None:
        if group.strat == "select":
            slot = next(
                (
                    i
                    for i, expected in enumerate(group.test)
                    if expected is not None and in_category(category, expected)
                ),
                -1,
            )
            if slot == -1 and None in group.test:
                slot = group.test.index(None)
            if not 0 <= slot < len(group.stats):
                return None
            return group.stats[slot], []

        usable = [s for s in group.stats if kind in s.ids]
        if len(usable) == 1:
            return usable[0], []
        owns = [s for s in group.stats if any(m.text == text for m in self.matchers_of(s))]

        if group.strat == "trivial-merge":
            merging = [s for s in owns if kind in s.ids] or usable
            if not merging:
                return None
            return merging[0], _other_ids(merging[1:], kind)

        if group.strat == "percent-merge":
            # The chance form ("#%의 확률로 …") and the certainty form ("항상
            # …") are separate ids. A roll of 100 is both, so both are
            # searched -- the flag id with no value, since it indexes no
            # number to compare against.
            percent, value = _slot(group, "percent"), _slot(group, "value")
            if percent is None:
                return (owns[0], []) if owns else None
            capped = any(
                m.text == text and m.value == 100 for m in self.matchers_of(percent)
            )
            if capped and value is not None:
                return percent, _other_ids([value], kind, "{empty}")
            return (owns[0] if owns else percent), []

        if group.strat == "flag-merge":
            # The same split, but with a cap the data names rather than a
            # flat 100 ("감전의 최대 효과 +40%" is the flag form).
            value, flag = _slot(group, "value"), _slot(group, "flag")
            if value is None or flag is None:
                return (usable[0], []) if usable else None
            flag_roll = next(
                (m.value for m in self.matchers_of(flag) if m.value is not None), None
            )
            if roll is not None and flag_roll is not None and roll == flag_roll:
                return value, _other_ids([flag], kind, "{empty}")
            return value, []

        return (usable[0], []) if usable else None

    def matchers_of(self, stat: Stat) -> list[Matcher]:
        return self._stat_matchers.get(id(stat), [])


def _slot(group: Group, kind: str) -> Stat | None:
    try:
        return group.stats[group.kind.index(kind)]
    except (ValueError, IndexError):
        return None


def _other_ids(stats: list[Stat], kind: str, prefix: str = "") -> list[str]:
    """The first trade id each of *stats* offers for *kind*.

    ``{empty}`` marks an id whose filter must carry no value: it is the
    capped form of the mod, indexed as a flag rather than as a number, and
    asking it for "at least 100" matches nothing.
    """
    out = []
    for stat in stats:
        ids = stat.ids.get(kind)
        if ids:
            out.append(f"{prefix}{ids[0]}")
    return out


def flatten(text: str) -> str:
    """A name reduced to what it says, so stray whitespace cannot hide it.

    The site's own data is not tidy -- 들불 체관 is published with a trailing
    space -- and the game's is not always spaced the way the data files are.
    """
    return re.sub(r"\s+", " ", text or "").strip()


# ---------------------------------------------------------------------------
_lock = threading.Lock()
_loaded: dict[str, GameData] = {}


def data_dir(language: str) -> Path:
    """Where the files live, preferring a copy the user can update by hand."""
    local = paths.app_dir() / "data" / "trade" / language
    if (local / "stats.ndjson").exists():
        return local
    return paths.bundle_dir() / "data" / "trade" / language


# Where the files are generated and kept up to date, and how old a copy has
# to be before it is worth fetching again. A league changes the game roughly
# every three months and fixes trickle in for a week or two after; seven days
# keeps up with that without asking GitHub anything most weeks.
_SOURCE = (
    "https://raw.githubusercontent.com/SnosMe/awakened-poe-trade/master/"
    "renderer/public/data"
)
_REFRESH_DAYS = 7
_FILES = ("stats.ndjson", "items.ndjson")


def refresh(language: str = DEFAULT_LANGUAGE, *, force: bool = False) -> bool:
    """Fetch newer game data if the copy on disk has gone stale.

    Returns whether anything was written. Called on a background thread at
    startup, because the alternative -- shipping a snapshot and leaving it --
    means every league adds modifiers this app cannot read until the next
    release of the app itself.

    Only the two data files, not client_strings: that one needs converting
    from JavaScript, which is ``tools/update_trade_data.py``'s job and not
    something to do at runtime. The labels it holds change far less often
    than the modifier list anyway.
    """
    import time
    import urllib.error
    import urllib.request

    target = paths.app_dir() / "data" / "trade" / language
    marker = target / "stats.ndjson"
    if not force and marker.exists():
        age = (time.time() - marker.stat().st_mtime) / 86400
        if age < _REFRESH_DAYS:
            return False

    written = False
    try:
        target.mkdir(parents=True, exist_ok=True)
        # data_dir picks one directory and reads all three files from it, so
        # a refresh that leaves client_strings.json behind in the bundle
        # would make the fresh data unreadable. Seed it first.
        labels = target / "client_strings.json"
        if not labels.exists():
            bundled = paths.bundle_dir() / "data" / "trade" / language / labels.name
            if bundled.exists():
                labels.write_bytes(bundled.read_bytes())
            else:
                return False
        for name in _FILES:
            request = urllib.request.Request(
                f"{_SOURCE}/{language}/{name}",
                headers={"User-Agent": "KuanPoeHelper-gamedata/1.0"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
            # Written whole, then moved into place: a half-downloaded
            # stats.ndjson would be read next start-up as a data file with a
            # few thousand modifiers missing, which is worse than a stale one.
            temporary = target / f"{name}.part"
            temporary.write_bytes(payload)
            temporary.replace(target / name)
            written = True
    except (urllib.error.URLError, OSError, TimeoutError):
        logger.debug("게임 데이터 갱신 실패", exc_info=True)
        return False

    if written:
        with _lock:
            _loaded.pop(language, None)
        logger.info("게임 데이터를 갱신했습니다: %s", target)
    return written


def load(language: str = DEFAULT_LANGUAGE) -> GameData | None:
    """The game data for *language*, read once and kept.

    Answers None when the files are missing rather than raising: they are a
    few megabytes shipped alongside the program, and a build that lost them
    should fall back to asking the trade site rather than refuse to price
    anything at all.
    """
    with _lock:
        cached = _loaded.get(language)
        if cached is not None:
            return cached
        directory = data_dir(language)
        try:
            game_data = GameData(directory)
        except (OSError, ValueError, KeyError):
            logger.warning("게임 데이터를 읽지 못했습니다: %s", directory, exc_info=True)
            return None
        _loaded[language] = game_data
        return game_data

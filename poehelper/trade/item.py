"""Parse the text the game puts on the clipboard for a hovered item.

Ctrl+C over an item is a *game* feature: the client writes the item's full
description to the clipboard as plain text. Nothing here reads game memory
or the screen -- this module only takes that text apart.

The Korean client's format, confirmed against real items::

    아이템 종류: 마법봉
    아이템 희귀도: 희귀
    혼백 첨탑                       <- rolled name (rare/unique only)
    소집의 마법봉                    <- base type
    --------
    마법봉
    퀄리티: +20% (augmented)
    물리 피해: 34-62 (augmented)
    초당 공격 횟수: 1.50
    --------
    요구사항:
    레벨: 72
    --------
    홈: B-W-W
    --------
    아이템 레벨: 85
    --------
    { 고정 속성 부여 — 피해, 소환수 }
    소환수가 주는 피해 29(26-30)% 증가
    --------
    { 접두어 속성 부여 "엄벌가의" (등급: 1) — 소환수, 젬 }
    모든 소환수 스킬 젬 레벨 +1
    --------
    분열된 아이템

Every label in that -- "아이템 희귀도: ", "타락", the shape of a ``{ ... }``
header -- comes from :mod:`gamedata`'s client-strings file rather than from a
list written out here. That file is generated from the game's own data, so a
label the client changes is one download away from being right again, and the
same parser reads an English client by loading a different one.

The ``{ ... }`` headers appear only when *advanced mod descriptions* are
switched on in the game's UI options, and they are worth a great deal: they
say where each mod block starts (so a two-line mod can be told from two
one-line mods), whether it is a prefix, a suffix, an implicit or crafted, and
at which tier it rolled. Without them the item is still parsed -- every line
becomes its own explicit mod, which a price check can work with -- but
multi-line mods stop resolving. :attr:`ParsedItem.advanced` says which of the
two happened so the UI can suggest turning the option on.

Each modifier is resolved against the game's data *here*, while the item is
being read, rather than later against the trade site's wording. That is what
lets a line printed as 감소 be understood as a negative 증가, an implied
100% roll be filled in, and a mod that is local to a weapon be told apart
from the global one that reads identically. See :mod:`gamedata`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import gamedata
from .gamedata import GameData, StatMatch

logger = logging.getLogger(__name__)

SEPARATOR = "--------"
EM_DASH = "—"

# The kinds of modifier the trade site indexes separately, and the client
# strings whose presence in a { ... } header names each one. Order matters:
# a crafted or fractured header contains 접두어/접미어 too, so the specific
# wordings are tested before the generic ones.
_HEADER_KINDS = (
    ("crafted", ("CRAFTED_PREFIX", "CRAFTED_SUFFIX")),
    ("fractured", ("FRACTURED_PREFIX", "FRACTURED_SUFFIX")),
    ("veiled", ("VEILED_PREFIX", "VEILED_SUFFIX")),
    ("implicit", ("CORRUPTED_IMPLICIT", "IMPLICIT_MODIFIER")),
    ("explicit", ("PREFIX_MODIFIER", "SUFFIX_MODIFIER")),
)
_PREFIX_KEYS = ("CRAFTED_PREFIX", "FRACTURED_PREFIX", "VEILED_PREFIX", "PREFIX_MODIFIER")
_SUFFIX_KEYS = ("CRAFTED_SUFFIX", "FRACTURED_SUFFIX", "VEILED_SUFFIX", "SUFFIX_MODIFIER")

# Rarities the site prices by identity rather than by rolls.
_BY_NAME = frozenset({"unique", "currency", "card", "gem"})

_BRACED_RE = re.compile(r"^\{\s*(?P<body>.*?)\s*\}$")
# Reminder text: the game's own gloss on a keyword, printed under the mod it
# belongs to and wrapped in parentheses from end to end --
#     ("점화"는 지속 화염 피해를 주며 …)
# No stat the game publishes has a line of that shape, so the rule can be
# this blunt. Leaving them in glued the gloss onto the mod above and made the
# mod unfindable -- which is what a tincture, whose every line carries one,
# ran into.
_REMINDER_OPEN_RE = re.compile(r"^\s*[(（]")
_REMINDER_CLOSE_RE = re.compile(r"[)）]\s*$")

# "34-62", or several at once for elemental damage: "5-10, 12-30".
_DAMAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _after(line: str, label) -> str | None:
    """The value part of a ``라벨: 값`` line, or None if the label is absent."""
    if not label or not isinstance(label, str):
        return None
    if line.startswith(label):
        return line[len(label):].strip()
    # The data file writes these labels with the trailing space included;
    # some clients print the colon without one.
    stripped = label.rstrip()
    if stripped.endswith(":") and line.startswith(stripped):
        return line[len(stripped):].strip()
    return None


def _first_int(text: str) -> int | None:
    match = re.search(r"-?\d+", text or "")
    return int(match.group()) if match else None


@dataclass
class ItemMod:
    """One modifier block: its text, where it came from, and how it rolled."""

    text: str                       # joined with \n if the mod spans lines
    kind: str = "explicit"          # explicit / implicit / crafted / fractured / enchant
    affix: str = ""                 # prefix / suffix, when the game says so
    tier: int | None = None
    rank: int | None = None         # eldritch implicits are ranked, not tiered
    values: list[float] = field(default_factory=list)
    ranges: list[tuple[float, float]] = field(default_factory=list)
    # What the game's data says this line is. None means the line was read
    # but not recognised -- it is still shown, it simply cannot be searched.
    match: StatMatch | None = None
    unscalable: bool = False

    @property
    def ids(self) -> list[str]:
        """The trade stat ids to search this mod under."""
        return list(self.match.ids) if self.match else []

    @property
    def better(self) -> int:
        """1 when a higher roll is better, -1 when a lower one is, 0 neither.

        This is what decides whether the search should bound the mod from
        below or from above. "받는 피해 8% 감소" bounded from below asks for
        items strictly *worse* than the one in hand.
        """
        return self.match.stat.better if self.match else 1

    @property
    def decimals(self) -> bool:
        return bool(self.match and self.match.stat.dp)

    @property
    def value(self) -> float | None:
        """One number to filter on.

        A two-number mod ("화염 피해 47~80 추가") is one filter over the
        average -- searching on 47 alone would happily return items far worse
        than the one in hand. Three or more numbers filter on the first,
        which is what the site itself does.
        """
        if self.match is not None:
            return self.match.value
        if not self.values:
            return None
        return sum(self.values) / len(self.values)

    @property
    def quality(self) -> float | None:
        """Where in its possible range this roll landed, 0..1.

        Only available with advanced mod descriptions on; it is what lets the
        window offer "the three best mods" instead of the first three.
        """
        if not self.ranges or len(self.ranges) != len(self.values):
            return None
        scores = []
        for value, (low, high) in zip(self.values, self.ranges):
            if high == low:
                scores.append(1.0)
            else:
                scores.append(max(0.0, min(1.0, (value - low) / (high - low))))
        return sum(scores) / len(scores)

    @property
    def best(self) -> float | None:
        """The best this mod can roll, for deciding how far to widen a search."""
        if not self.ranges:
            return None
        highs = [high for _low, high in self.ranges]
        return sum(highs) / len(highs)


@dataclass
class ParsedItem:
    rarity: str = "normal"
    item_class: str = ""
    name: str = ""          # rolled name, rare/unique only
    base: str = ""          # base type, what the site calls "type"
    category: str = ""      # the game's own category, e.g. "Body Armour"
    info: gamedata.ItemInfo | None = None
    item_level: int | None = None
    quality: int | None = None
    gem_level: int | None = None
    stack_size: int | None = None
    physical_dps: float = 0.0
    elemental_dps: float = 0.0
    attacks_per_second: float = 0.0
    crit_chance: float = 0.0
    armour: int = 0
    evasion: int = 0
    energy_shield: int = 0
    ward: int = 0
    block: int = 0
    area_level: int | None = None
    map_tier: int | None = None
    memory_strands: int | None = None
    sockets: str = ""
    mods: list[ItemMod] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    influences: set[str] = field(default_factory=set)
    advanced: bool = False  # were { ... } headers present?
    raw: str = ""
    unknown: list[str] = field(default_factory=list)  # lines nothing claimed

    # ---- derived ----------------------------------------------------------
    @property
    def total_dps(self) -> float:
        """Physical plus elemental, which is what the site filters on.

        Chaos damage is deliberately absent: the client prints it, but
        weapon_filters offers dps, pdps and edps and nothing else, so a chaos
        figure would be a number with nowhere to send it.
        """
        return self.physical_dps + self.elemental_dps

    @property
    def is_weapon(self) -> bool:
        return self.category in gamedata.WEAPON or self.total_dps > 0

    @property
    def has_defence(self) -> bool:
        return bool(self.armour or self.evasion or self.energy_shield or self.ward)

    @property
    def corrupted(self) -> bool:
        return "corrupted" in self.flags

    @property
    def trade_category(self) -> str:
        """The id for the site's type_filters.category, or "".

        A unique's own entry carries no category -- it is a name attached to
        a base -- so the answer comes from the base it sits on, which
        :func:`_identify` has already looked up.
        """
        if self.info is not None:
            derived = self.info.trade_category
            if derived:
                return derived
        return gamedata.TRADE_CATEGORY.get(self.category, "")

    @property
    def links(self) -> int:
        """Largest linked group, e.g. "B-W-W R" -> 3."""
        if not self.sockets:
            return 0
        return max((len(group.split("-")) for group in self.sockets.split()), default=0)

    @property
    def socket_count(self) -> int:
        return len([c for c in self.sockets if c.isalpha()])

    @property
    def display(self) -> str:
        if self.name and self.base:
            return f"{self.name} · {self.base}"
        return self.name or self.base or "(이름 없음)"

    @property
    def searchable_mods(self) -> list[ItemMod]:
        """Mods worth offering as search filters.

        Uniques are included: two copies of the same unique can differ by a
        wide roll (a Doryani's Prototype rolls 150-200% on one line), and the
        name alone prices them as if they were interchangeable. They are
        offered unticked, because the name is usually enough.
        """
        return [m for m in self.mods if m.kind != "enchant"]

    @property
    def priced_by_name(self) -> bool:
        """Whether identity, rather than rolls, is what this is worth."""
        return self.rarity in _BY_NAME

    @property
    def exchange_tag(self) -> str:
        """The bulk-exchange id, for the things priced by the pile.

        Currency, fragments, essences and fossils are not sold as listings of
        one item; they are exchanged in bulk, and the site has a separate
        endpoint for it. An empty string means this is not such an item.
        """
        if self.info is None or self.rarity not in ("currency", "normal"):
            return ""
        return self.info.trade_tag


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse(text: str, data: GameData | None = None) -> ParsedItem | None:
    """Clipboard text -> :class:`ParsedItem`, or ``None`` if it is not an item.

    Returning None rather than raising is deliberate: the hotkey fires
    whatever is under the cursor, and "the clipboard held something else" is
    an ordinary outcome, not an error to report.
    """
    if not text or SEPARATOR not in text:
        return None
    if data is None:
        data = gamedata.load()
    if data is None:
        return None
    strings = data.strings

    sections = [
        [line.rstrip() for line in block.strip("\n").split("\n") if line.strip()]
        for block in text.replace("\r\n", "\n").split(SEPARATOR)
    ]
    sections = [s for s in sections if s]
    if not sections:
        return None

    item = ParsedItem(raw=text)
    if not _parse_header(sections[0], item, data):
        return None
    _identify(item, data)

    # Whether the client is marking mods up has to be known *before* the
    # sections are walked, because it changes what counts as a modifier at
    # all: when headers are present, a section without one is never a mod.
    # That single rule is what keeps a flask's "마시려면 우클릭하십시오…", a
    # gem's skill description, a contract's briefing and a unique's flavour
    # text out of the mod list -- each of which used to be searched as if it
    # were a rolled property, and each of which made the item unfindable.
    item.advanced = any(
        _BRACED_RE.match(line) for section in sections[1:] for line in section
    )

    for section in sections[1:]:
        for parser in _SECTION_PARSERS:
            if parser(section, item, data, strings):
                break
        else:
            item.unknown.extend(section)
    _derive_flags(item)
    return item


# Item states the client announces only through the modifiers it prints. The
# game's string table has no label for "분열된 아이템" as a *section*, so
# testing for one would mean writing the Korean out here and losing it again
# the moment the client is patched -- while the mod headers that imply the
# same thing are in the data and stay right.
_IMPLIED_FLAGS = (
    ("fractured", "fractured_item"),
    ("veiled", "veiled"),
)


def _derive_flags(item: ParsedItem) -> None:
    kinds = {mod.kind for mod in item.mods}
    for kind, flag in _IMPLIED_FLAGS:
        if kind in kinds:
            item.flags.add(flag)


def _parse_header(lines: list[str], item: ParsedItem, data: GameData) -> bool:
    """The first section carries class, rarity and the name(s)."""
    strings = data.strings
    rarities = {
        strings.RARITY_NORMAL: "normal",
        strings.RARITY_MAGIC: "magic",
        strings.RARITY_RARE: "rare",
        strings.RARITY_UNIQUE: "unique",
        strings.RARITY_GEM: "gem",
        strings.RARITY_CURRENCY: "currency",
        strings.RARITY_DIVCARD: "card",
        strings.RARITY_QUEST: "quest",
    }
    names: list[str] = []
    for line in lines:
        value = _after(line, strings.ITEM_CLASS)
        if value is not None:
            item.item_class = value
            continue
        value = _after(line, strings.RARITY)
        if value is not None:
            item.rarity = rarities.get(value, "normal")
            continue
        names.append(line)

    if item.rarity in ("rare", "unique") and len(names) >= 2:
        item.name, item.base = names[0], names[1]
    elif names:
        # Normal and magic items have no rolled name; a magic item's line is
        # "칠흑의 사슬 장갑 - 회피의" and the base has to be dug back out of
        # it, which _identify does.
        item.base = names[-1]

    # Decorations the client writes *into* the name, each of which the trade
    # site rejects as part of a base type. They are facts about the item, so
    # they are recorded before being stripped.
    for attribute, flag in (
        ("ITEM_SYNTHESISED", "synthesised_item"),
        ("FOULBORN_NAME", "mutated"),
        ("VESTIGIAL_NAME", "vestigial"),
        ("MAP_BLIGHT_RAVAGED", "map_uberblighted"),
        ("MAP_BLIGHTED", "map_blighted"),
        ("ITEM_SUPERIOR", ""),
        ("QUALITY_ANOMALOUS", "alt_quality"),
        ("QUALITY_DIVERGENT", "alt_quality"),
        ("QUALITY_PHANTASMAL", "alt_quality"),
    ):
        pattern = getattr(strings, attribute)
        if not pattern:
            continue
        stripped = pattern.match(item.base)
        if stripped:
            item.base = stripped.group(1)
            if flag:
                item.flags.add(flag)
    tier = strings.MAP_TIER.search(item.base) if strings.MAP_TIER else None
    if tier:
        item.map_tier = int(tier.group(1))
        item.base = item.base[: tier.start()].strip()

    return bool(names or item.item_class)


def _identify(item: ParsedItem, data: GameData) -> None:
    """Find this item in the game's data, and take its category from there.

    The category is what turns "a rare with these mods" into "a *wand* with
    these mods". It also settles which of two identically worded mods was
    meant -- see :meth:`GameData.match_stat` -- so it has to be known before
    a single modifier is read.
    """
    namespace = {
        "unique": "UNIQUE",
        "gem": "GEM",
        "card": "DIVINATION_CARD",
    }.get(item.rarity, "")
    lookup = item.name if item.rarity == "unique" else item.base
    info = data.find_item(lookup, namespace, base=item.base)
    if info is None and item.rarity != "unique":
        # A magic item prints "<접두어> <기본> - <접미어>" with nothing to
        # mark the three apart, and the site accepts only the base.
        info = data.base_inside(item.base)
        if info is not None:
            item.base = info.name
    if info is None and item.name:
        info = data.find_item(item.name)
    if info is None:
        return
    item.info = info
    if info.namespace == "UNIQUE":
        # A unique's own category comes from the base it sits on.
        base = data.find_item(item.base, "ITEM") if item.base else None
        item.category = base.category if base else ""
    else:
        item.category = info.category


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------
# Each is tried in turn against a section and answers whether it claimed it.
# Anything nothing claims lands in ParsedItem.unknown, which is how a section
# this app has not learned to read yet stays visible rather than being
# silently searched as if it were a list of modifiers.
def _parse_flags(section, item: ParsedItem, data: GameData, s) -> bool:
    """One-line sections that are a statement about the item."""
    if len(section) != 1:
        return False
    line = section[0]
    for key, flag in (
        ("CORRUPTED", "corrupted"),
        ("UNIDENTIFIED", "unidentified"),
        ("MIRRORED", "mirrored"),
        ("SPLIT", "split"),
        ("SECTION_SYNTHESISED", "synthesised_item"),
        ("FOIL_UNIQUE", "foil"),
        ("UNMODIFIABLE", "unmodifiable"),
        ("CANNOT_USE_ITEM", ""),
    ):
        if line == getattr(s, key):
            if flag:
                item.flags.add(flag)
            return True
    for key, influence in (
        ("INFLUENCE_SHAPER", "shaper"),
        ("INFLUENCE_ELDER", "elder"),
        ("INFLUENCE_CRUSADER", "crusader"),
        ("INFLUENCE_HUNTER", "hunter"),
        ("INFLUENCE_REDEEMER", "redeemer"),
        ("INFLUENCE_WARLORD", "warlord"),
    ):
        if line == getattr(s, key):
            item.influences.add(influence)
            return True
    return False


def _parse_labelled(section, item: ParsedItem, data: GameData, s) -> bool:
    """Sections that are one ``라벨: 값`` line and nothing else."""
    if len(section) != 1:
        return False
    line = section[0]
    for label, attribute in (
        (s.ITEM_LEVEL, "item_level"),
        (s.STACK_SIZE, "stack_size"),
        (s.TALISMAN_TIER, None),
        (s.CORPSE_LEVEL, None),
        (s.MEMORY_STRANDS, "memory_strands"),
        (s.SCRYING_MAP_AREA, None),
        (s.MERCENARY_LEVEL, None),
    ):
        if label and line.startswith(label):
            if attribute:
                setattr(item, attribute, _first_int(line[len(label):]))
            return True
    value = _after(line, s.SOCKETS)
    if value is not None:
        item.sockets = value.strip()
        return True
    return False


def _parse_ignored(section, item: ParsedItem, data: GameData, s) -> bool:
    """Sections a price check has no use for.

    Requirements are a consequence of the item rather than a property of it,
    and the note is the price the player wrote on their own stash tab.
    """
    head = section[0]
    # The stash-tab note is the only label here the game's string table does
    # not carry, because it is written by the player rather than the client.
    if head.startswith("메모: ") or head.startswith("Note: "):
        return True
    if head.rstrip(":") in ("요구사항", "Requirements"):
        return True
    if s.FLASK_CHARGES and s.FLASK_CHARGES.match(head):
        return True
    for key in ("METAMORPH_HELP", "BEAST_HELP", "VOIDSTONE_HELP"):
        if head == getattr(s, key):
            return True
    return False


def _count(text: str) -> int:
    return _first_int(text) or 0


def _decimal(text: str) -> float:
    found = _NUMBER_RE.search(text)
    return float(found.group()) if found else 0.0


# Which client label feeds which attribute of the item, and how to read the
# number out of it. Order matters only in that the first label a line starts
# with claims the line; no two of these share a prefix.
_PROPERTIES = (
    ("QUALITY", "quality", _first_int),
    ("AREA_LEVEL", "area_level", _first_int),
    ("ARMOUR", "armour", _count),
    ("EVASION", "evasion", _count),
    ("ENERGY_SHIELD", "energy_shield", _count),
    ("TAG_WARD", "ward", _count),
    ("BLOCK_CHANCE", "block", _count),
    ("ATTACK_SPEED", "attacks_per_second", _decimal),
    ("CRIT_CHANCE", "crit_chance", _decimal),
)

# Labels that mark a line as part of a properties block but carry nothing a
# price check uses. Listed so the block is *claimed* -- a section nothing
# claims is reported as unread, and a map's quantity line is read, not
# unread.
_PROPERTY_NOISE = (
    "MAP_ITEM_QUANTITY", "MAP_ITEM_RARITY", "MAP_MONSTER_PACK_SIZE",
    "MAP_MORE_MAPS", "MAP_MORE_SCARABS", "MAP_MORE_CURRENCY",
    "MAP_MORE_DIVINATION_CARDS", "SENTINEL_CHARGE",
)

# The two kinds of damage the site can filter on. Chaos damage is printed by
# the client too, but the site publishes no filter for it -- weapon_filters
# offers dps, pdps and edps and nothing else -- so reading it would produce a
# number with nowhere to go.
_DAMAGE_LABELS = (("PHYSICAL_DAMAGE", "physical"), ("ELEMENTAL_DAMAGE", "elemental"))


def _parse_properties(section, item: ParsedItem, data: GameData, s) -> bool:
    """The block of numbers the client prints under the item's name.

    Damage and attack speed become DPS, because that is the number anyone
    actually price-checks a weapon on and the printed pair is not comparable
    between weapons. The printed numbers already include quality and local
    mods -- the client marks them "(augmented)" -- so averaging each range
    and multiplying by attacks per second is the whole calculation.
    """
    claimed = False
    damage: dict[str, float] = {}

    for line in section:
        if _read_property(line, item, s):
            claimed = True
            continue
        if _read_damage(line, damage, s):
            claimed = True
            continue
        # A gem's block leads with "레벨: 19", which is the gem level. On
        # anything else 레벨 is a requirement, and requirements are a
        # consequence of the item rather than a property of it.
        if item.rarity == "gem":
            found = _after(line, s.GEM_LEVEL)
            if found is not None:
                item.gem_level = _first_int(found)
                claimed = True
                continue
        if any(_after(line, getattr(s, key)) is not None for key in _PROPERTY_NOISE):
            claimed = True

    if item.attacks_per_second and damage:
        item.physical_dps = round(damage.get("physical", 0.0) * item.attacks_per_second, 1)
        item.elemental_dps = round(damage.get("elemental", 0.0) * item.attacks_per_second, 1)
    return claimed


def _read_property(line: str, item: ParsedItem, s) -> bool:
    for key, attribute, read in _PROPERTIES:
        value = _after(line, getattr(s, key))
        if value is not None:
            setattr(item, attribute, read(value))
            return True
    return False


def _read_damage(line: str, damage: dict[str, float], s) -> bool:
    for key, name in _DAMAGE_LABELS:
        value = _after(line, getattr(s, key))
        if value is None:
            continue
        # Elemental damage carries one range per element -- "5-10, 12-30" --
        # and every one of them contributes.
        damage[name] = damage.get(name, 0.0) + sum(
            (float(low) + float(high)) / 2 for low, high in _DAMAGE_RE.findall(value)
        )
        return True
    return False


def _parse_modifiers(section, item: ParsedItem, data: GameData, s) -> bool:
    """A block of rolled properties, split on its ``{ ... }`` headers."""
    if item.rarity in ("currency", "card", "gem"):
        # None of these has rolls. A currency orb's paragraph explaining what
        # it does, a card's reward line and a gem's description of the skill
        # all look exactly like a mod list to any structural test, so the
        # rarity is what has to rule them out. A gem is priced on its level,
        # quality and corruption -- what the skill does is the same on every
        # copy.
        return False
    braced = any(_BRACED_RE.match(line) for line in section)
    if item.advanced and not braced:
        # With headers available, an unheaded section is prose by definition.
        return False
    if not braced and not _looks_like_mods(section):
        return False

    blocks = _split_blocks(section, s) if braced else [
        ({}, [line]) for line in _without_reminders(section)
    ]
    for header, lines in blocks:
        if not lines:
            continue
        item.mods.extend(_resolve_block(header, lines, item, data))
    return True


def _split_blocks(section, s) -> list[tuple[dict, list[str]]]:
    """Every line after a ``{ ... }`` header belongs to that mod.

    This is the only reliable way to know that "스킬 사용 시 장착된 주문
    발동…" and "이렇게 발동된 주문의 비용 150% 증폭" are one modifier rather
    than two.
    """
    blocks: list[tuple[dict, list[str]]] = []
    header: dict = {}
    body: list[str] = []
    for line in _without_reminders(section):
        braced = _BRACED_RE.match(line)
        if braced:
            if body:
                blocks.append((header, body))
            header, body = _parse_header_line(braced.group("body"), s), []
        else:
            body.append(line)
    if body:
        blocks.append((header, body))
    return blocks


def _parse_header_line(body: str, s) -> dict:
    """``접두어 속성 부여 "엄벌가의" (등급: 1) — 소환수, 젬`` taken apart.

    The em-dashed tail is the mod's tags and, on an eldritch implicit, the
    bonus its rank grants. Neither says anything a search can use, and both
    get in the way of reading the part that does, so the line is cut at the
    first em dash before anything else looks at it.
    """
    info = body.split(EM_DASH)[0].strip()
    out: dict = {"kind": "explicit", "affix": "", "tier": None, "rank": None}

    for attribute, kind in (("EATER_IMPLICIT", "implicit"), ("EXARCH_IMPLICIT", "implicit")):
        pattern = getattr(s, attribute)
        if pattern and pattern.match(info):
            out["kind"] = kind
            return out
    for key, flag in (("FOULBORN_MODIFIER", "explicit"), ("VESTIGIAL_IMPLICIT", "implicit")):
        if getattr(s, key) and info.startswith(getattr(s, key)):
            out["kind"] = flag
            return out

    match = s.MODIFIER_LINE.match(info) if s.MODIFIER_LINE else None
    kind_text = (match.group("type") if match else info).strip()
    for kind, keys in _HEADER_KINDS:
        if any(getattr(s, key) and getattr(s, key) in kind_text for key in keys):
            out["kind"] = kind
            break
    else:
        # No client string in this build names enchantments, and they are the
        # one header type that does. Falling back to the word itself is
        # narrow enough to be safe: nothing else in a header says 인챈트.
        if "인챈트" in kind_text or "Enchant" in kind_text:
            out["kind"] = "enchant"
    if any(getattr(s, key) and getattr(s, key) in kind_text for key in _PREFIX_KEYS):
        out["affix"] = "prefix"
    elif any(getattr(s, key) and getattr(s, key) in kind_text for key in _SUFFIX_KEYS):
        out["affix"] = "suffix"
    if match:
        out["tier"] = int(match.group("tier")) if match.group("tier") else None
        out["rank"] = int(match.group("rank")) if match.group("rank") else None
    return out


def _resolve_block(header: dict, lines: list[str], item: ParsedItem, data: GameData):
    """Turn one ``{ ... }`` block into the modifiers the site indexes.

    Which way a two-line block goes is the game's call, not ours: the crafted
    trigger mod is published as a single two-line stat, while a tincture's
    implicit -- equally one ``{ 고정 속성 부여 }`` block -- is two separate
    stats. So the combined text is looked up first and kept when it exists;
    only when it does not, and the pieces do, is the block taken apart.
    """
    kind = header.get("kind", "explicit")
    unscalable = data.strings.UNSCALABLE_VALUE
    common = {
        "kind": kind,
        "affix": header.get("affix", ""),
        "tier": header.get("tier"),
        "rank": header.get("rank"),
        # A unique's roll the game marks as fixed. Worth knowing: there is
        # no point offering to widen a search around a number that is the
        # same on every copy of the item.
        "unscalable": bool(unscalable) and any(l.endswith(unscalable) for l in lines),
    }
    joined = "\n".join(lines)
    whole = _match(joined, kind, item, data)
    if whole is not None or len(lines) == 1:
        return [_mod(joined, whole, **common)]

    pieces = [(line, _match(line, kind, item, data)) for line in lines]
    if any(found is not None for _line, found in pieces):
        return [_mod(line, found, **common) for line, found in pieces]
    # Nothing recognised either way: kept whole so the window still shows the
    # item as the game printed it. It simply contributes no filter.
    return [_mod(joined, None, **common)]


def _match(text: str, kind: str, item: ParsedItem, data: GameData) -> StatMatch | None:
    unscalable = data.strings.UNSCALABLE_VALUE
    if unscalable and text.endswith(unscalable):
        text = text[: -len(unscalable)]
    found = data.match_stat(text, kind, item.category)
    if found is None and kind not in ("explicit", "enchant"):
        # A wording the game publishes only as an explicit ("번개 피해만 줄
        # 수 있음" on a unique) still has to be searchable when it turns up
        # under some other header.
        found = data.match_stat(text, "explicit", item.category)
    return found


def _mod(text: str, found: StatMatch | None, **common) -> ItemMod:
    mod = ItemMod(text=text.strip(), match=found, **common)
    if found is not None:
        mod.values = list(found.values)
        mod.ranges = list(found.ranges)
    else:
        # Unrecognised, so every number on the line is a guess at a roll.
        # Shown, never searched -- ItemMod.ids is empty without a match.
        mod.values = [float(n) for n in _NUMBER_RE.findall(text)]
    return mod


def _without_reminders(section: list[str]) -> list[str]:
    """Drop the game's parenthesised gloss on a keyword.

    A gloss can run to several lines, so this tracks whether one is open
    rather than testing each line on its own.
    """
    out: list[str] = []
    inside = False
    for line in section:
        if not inside and _REMINDER_OPEN_RE.match(line):
            inside = True
        if inside:
            if _REMINDER_CLOSE_RE.search(line):
                inside = False
            continue
        out.append(line)
    return out


def _looks_like_mods(lines: list[str]) -> bool:
    """Reject flavour text without needing to know what flavour text says.

    A unique's quote block is prose: it has no numbers and it is wrapped in
    quotation marks or attributed with a leading dash. Real mods without any
    number exist ("번개 피해만 줄 수 있음"), so the test has to be about the
    shape of the block rather than the presence of digits alone.
    """
    text = " ".join(lines)
    if text.startswith('"') or text.startswith("'"):
        return False
    if lines and lines[-1].startswith("- "):
        return False
    return True


_SECTION_PARSERS = (
    _parse_flags,
    _parse_labelled,
    _parse_ignored,
    _parse_properties,
    _parse_modifiers,
)

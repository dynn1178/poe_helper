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
    { 대가의 제작 접미어 속성 부여 "- 제작" — 시전, 젬 }
    스킬 사용 시 장착된 주문 발동, 재사용 대기시간 8초
    이렇게 발동된 주문의 비용 150% 증폭
    --------
    분열된 아이템

The ``{ ... }`` headers appear only when *advanced mod descriptions* are
switched on in the game's UI options, and they are worth a great deal here:
they say where each mod block starts (so a two-line mod can be told from two
one-line mods), whether it is a prefix, a suffix, an implicit or crafted, and
at which tier it rolled. Without them the item is still parsed -- every line
simply becomes its own explicit mod, which is what a price check can work
with -- but multi-line mods stop resolving. :attr:`ParsedItem.advanced` says
which of the two happened so the UI can suggest turning the option on.

English is accepted throughout as a fallback so the international client is
not locked out.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SEPARATOR = "--------"

# Rarity, both clients. Everything not listed reads as 일반/Normal.
RARITY = {
    "일반": "normal", "마법": "magic", "희귀": "rare", "고유": "unique",
    "화폐": "currency", "디바인 카드": "card", "젬": "gem",
    "Normal": "normal", "Magic": "magic", "Rare": "rare", "Unique": "unique",
    "Currency": "currency", "Divination Card": "card", "Gem": "gem",
}

_LABELS = {
    "class": ("아이템 종류", "Item Class"),
    "rarity": ("아이템 희귀도", "희귀도", "Rarity"),
    "ilvl": ("아이템 레벨", "Item Level"),
    "quality": ("퀄리티", "Quality"),
    "sockets": ("홈", "Sockets"),
    "requirements": ("요구사항", "Requirements"),
    # Blocks that carry no search terms. "메모" is the price tag the player
    # wrote on their own stash tab, and "중첩 개수" is how many are in the
    # pile -- neither says anything about what the item is.
    "note": ("메모", "Note"),
    "stack": ("중첩 개수", "Stack Size"),
    "gem_level": ("레벨", "Level"),
    "physical": ("물리 피해", "Physical Damage"),
    # The client normally prints one combined "원소 피해: 5-10, 12-30" line,
    # but the per-element labels are accepted too so a build that splits
    # them still contributes to elemental DPS rather than silently reading
    # as zero.
    "elemental": (
        "원소 피해", "Elemental Damage",
        "화염 피해", "냉기 피해", "번개 피해",
        "Fire Damage", "Cold Damage", "Lightning Damage",
    ),
    "chaos": ("카오스 피해", "Chaos Damage"),
    "aps": ("초당 공격 횟수", "Attacks per Second"),
    "area_level": ("지역 레벨", "Area Level"),
}

_FLAGS = {
    "corrupted": ("타락", "Corrupted"),
    "fractured_item": ("분열된 아이템", "Fractured Item"),
    "synthesised": ("합성된 아이템", "Synthesised Item"),
    "mirrored": ("거울 복제됨", "Mirrored"),
    "unidentified": ("미확인", "Unidentified"),
    # Names taken from the trade site's own filter labels rather than
    # guessed at -- see misc_filters in /api/trade/data/filters.
    "mutated": ("삿된 아이템", "Mutated Item"),
    "searing": ("작열의 총주교 아이템", "Searing Exarch Item"),
    "tangled": ("세계 포식자 아이템", "Eater of Worlds Item"),
}

# The kind of affix a { ... } header describes. Order matters: a fractured or
# crafted header also contains 접두어/접미어, so the specific words are tested
# before the generic ones.
_AFFIX_KINDS = (
    ("fractured", ("분열된", "Fractured")),
    ("crafted", ("제작", "Crafted", "Master Crafted")),
    ("enchant", ("인챈트", "Enchant")),
    ("implicit", ("고정", "Implicit")),
    ("unique", ("고유", "Unique")),
    ("prefix", ("접두어", "Prefix")),
    ("suffix", ("접미어", "Suffix")),
)

_MOD_HEADER_RE = re.compile(r"^\{\s*(?P<body>.+?)\s*\}$")
# Reminder text: the game's own gloss on a keyword, printed under the mod it
# belongs to and wrapped in parentheses from end to end --
#     ("점화"는 지속 화염 피해를 주며 …)
# No stat the trade site publishes has a line of that shape (checked against
# all 18,180 of them), so the rule can be this blunt. Leaving them in glued
# the gloss onto the mod above and made the mod unfindable -- which is what a
# tincture, whose every line carries one, ran into.
_REMINDER_RE = re.compile(r"^\(.*\)$")
_TIER_RE = re.compile(r"(?:등급|Tier):\s*(\d+)")
# "29(26-30)" -> rolled 29 out of 26..30. Bare numbers have no known range.
_ROLL_RE = re.compile(r"(?P<value>[-+]?\d+(?:\.\d+)?)\((?P<lo>[-+]?[\d.]+)-(?P<hi>[-+]?[\d.]+)\)")
_BARE_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


@dataclass
class ItemMod:
    """One modifier block: its text, where it came from, and how it rolled."""

    text: str                       # joined with \n if the mod spans lines
    kind: str = "explicit"          # explicit / implicit / crafted / fractured / enchant
    affix: str = ""                 # prefix / suffix, when the game says so
    tier: int | None = None
    values: list[float] = field(default_factory=list)
    ranges: list[tuple[float, float]] = field(default_factory=list)

    @property
    def value(self) -> float | None:
        """One number to filter on.

        Averaged rather than "the first": a mod like "화염 피해 47~80 추가" is
        two numbers, and searching on 47 alone would happily return items far
        worse than the one in hand.
        """
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

    def split(self) -> list["ItemMod"]:
        """This modifier as one :class:`ItemMod` per line.

        One ``{ ... }`` header can cover several stats -- a tincture's implicit
        is "점화 유발" *and* "점화의 피해 증가" under a single header -- and the
        trade site indexes those as two separate ids. Splitting is not always
        right, though: the crafted trigger mod is published as one two-line
        stat. Only the site can say which, so this hands back the pieces and
        :func:`query.expand_mods` decides.
        """
        lines = self.text.split("\n")
        if len(lines) < 2:
            return [self]
        return [
            ItemMod(text=line, kind=self.kind, affix=self.affix, tier=self.tier, **_rolls(line))
            for line in lines
        ]


@dataclass
class ParsedItem:
    rarity: str = "normal"
    item_class: str = ""
    name: str = ""          # rolled name, rare/unique only
    base: str = ""          # base type, what the site calls "type"
    item_level: int | None = None
    quality: int | None = None
    gem_level: int | None = None
    physical_dps: float = 0.0
    elemental_dps: float = 0.0
    chaos_dps: float = 0.0
    area_level: int | None = None
    sockets: str = ""
    mods: list[ItemMod] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    advanced: bool = False  # were { ... } headers present?
    raw: str = ""

    @property
    def total_dps(self) -> float:
        return self.physical_dps + self.elemental_dps + self.chaos_dps

    @property
    def is_weapon(self) -> bool:
        return self.total_dps > 0

    @property
    def corrupted(self) -> bool:
        return "corrupted" in self.flags

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
        offered unticked, because the name is usually enough -- see
        default_checked.
        """
        return [m for m in self.mods if m.kind != "enchant"]

    @property
    def priced_by_name(self) -> bool:
        """Whether identity, rather than rolls, is what this is worth."""
        return self.rarity in ("unique", "currency", "card", "gem")


def _strip_label(line: str, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        prefix = f"{key}:"
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _first_int(text: str) -> int | None:
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else None


def parse(text: str) -> ParsedItem | None:
    """Clipboard text -> :class:`ParsedItem`, or ``None`` if it is not an item.

    Returning None rather than raising is deliberate: the hotkey fires
    whatever is under the cursor, and "the clipboard held something else"
    is an ordinary outcome, not an error to report.
    """
    if not text or SEPARATOR not in text:
        return None
    blocks = [
        [line.rstrip() for line in block.strip("\n").split("\n") if line.strip()]
        for block in text.replace("\r\n", "\n").split(SEPARATOR)
    ]
    blocks = [b for b in blocks if b]
    if not blocks:
        return None

    item = ParsedItem(raw=text)
    if not _parse_header(blocks[0], item):
        return None
    # Whether the client is printing { ... } headers has to be known *before*
    # the blocks are walked, because it changes what counts as a modifier at
    # all: when they are present, a block without one is never a mod. That
    # single rule is what keeps a flask's "마시려면 우클릭하십시오…", a gem's
    # skill description, a contract's briefing and a unique's flavour text
    # out of the mod list -- each of which used to be searched as if it were
    # a rolled property, and each of which made the item unfindable.
    item.advanced = any(
        _MOD_HEADER_RE.match(line) for block in blocks[1:] for line in block
    )
    for block in blocks[1:]:
        _parse_block(block, item)
    return item


def _parse_header(lines: list[str], item: ParsedItem) -> bool:
    """The first block carries class, rarity and the name(s)."""
    names: list[str] = []
    for line in lines:
        value = _strip_label(line, _LABELS["class"])
        if value is not None:
            item.item_class = value
            continue
        value = _strip_label(line, _LABELS["rarity"])
        if value is not None:
            item.rarity = RARITY.get(value, "normal")
            continue
        names.append(line)

    if item.rarity in ("rare", "unique") and len(names) >= 2:
        item.name, item.base = names[0], names[1]
    elif names:
        # Normal and magic items have no rolled name; a magic item's line is
        # "칠흑의 사슬 장갑 - 회피의" and the base cannot be recovered from it
        # without the full base list, so it is left whole and the search falls
        # back to mods.
        item.base = names[-1]
    return bool(names or item.item_class)


# Rarities whose blocks after the header are flavour and usage instructions,
# never modifiers. A currency orb's paragraph explaining what it does looks
# exactly like a mod list to any structural test, so the rarity is what has
# to rule it out.
_NO_MODS = {"currency", "card"}

# Gems are priced on level, quality and corruption; the block listing what
# the skill does is a description of the skill, not rolls on this copy.
_NO_MOD_CLASSES = ("스킬 젬", "보조 젬", "Skill Gem", "Support Gem")


def _parse_block(lines: list[str], item: ParsedItem) -> None:
    joined = lines[0]

    for flag, words in _FLAGS.items():
        if joined in words:
            item.flags.add(flag)
            return

    value = _strip_label(joined, _LABELS["ilvl"])
    if value is not None:
        item.item_level = _first_int(value)
        return
    value = _strip_label(joined, _LABELS["sockets"])
    if value is not None:
        item.sockets = value.strip()
        return
    if any(joined.startswith(k) for k in _LABELS["requirements"]):
        return  # requirements never take part in a price check
    for key in ("note", "stack"):
        if _strip_label(joined, _LABELS[key]) is not None:
            return

    # A properties block (quality, damage, attack speed). Quality is the only
    # part the search uses; the rest is shown by the site itself.
    _parse_weapon(lines, item)

    quality = gem_level = area_level = None
    for line in lines:
        found = _strip_label(line, _LABELS["quality"])
        if found is not None:
            quality = _first_int(found)
        found = _strip_label(line, _LABELS["area_level"])
        if found is not None:
            area_level = _first_int(found)
        elif item.rarity == "gem":
            # A gem's properties block leads with "레벨: 19", which is the gem
            # level. On anything else "레벨" is a requirement, not a property.
            found = _strip_label(line, _LABELS["gem_level"])
            if found is not None:
                gem_level = _first_int(found)
    if quality is not None or gem_level is not None or area_level is not None:
        item.quality = quality if quality is not None else item.quality
        item.gem_level = gem_level if gem_level is not None else item.gem_level
        item.area_level = area_level if area_level is not None else item.area_level
        return

    if item.rarity in _NO_MODS or item.item_class in _NO_MOD_CLASSES:
        return

    if any(_MOD_HEADER_RE.match(line) for line in lines):
        item.mods.extend(_parse_advanced(lines))
    elif not item.advanced and _looks_like_mods(lines):
        # Only trusted when the client is not marking mods up at all. With
        # headers available, an unheaded block is prose by definition.
        item.mods.extend(
            ItemMod(text=line, **_rolls(line))
            for line in lines
            if not _REMINDER_RE.match(line)
        )


# "34-62" or, for elemental damage, several of them: "5-10, 12-30".
_DAMAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)")


def _parse_weapon(lines: list[str], item: ParsedItem) -> None:
    """Damage per second, split the way a weapon is actually judged.

    The game prints damage as a range and attack speed separately, which is
    not comparable between weapons; DPS is what everyone actually comes to a
    price check with. The printed numbers already include quality and local
    mods (the client marks them "(augmented)"), so this needs no adjustment
    -- averaging each range and multiplying by attacks per second is the
    whole calculation.
    """
    speed = 0.0
    damage: dict[str, float] = {}
    for line in lines:
        found = _strip_label(line, _LABELS["aps"])
        if found is not None:
            try:
                speed = float(_DAMAGE_RE.sub("", found).strip() or 0)
            except ValueError:
                speed = 0.0
            if not speed:
                numbers = re.findall(r"\d+(?:\.\d+)?", found)
                speed = float(numbers[0]) if numbers else 0.0
            continue
        for key in ("physical", "elemental", "chaos"):
            found = _strip_label(line, _LABELS[key])
            if found is None:
                continue
            # Elemental damage can carry several ranges at once, one per
            # element; they all contribute.
            total = sum(
                (float(low) + float(high)) / 2
                for low, high in _DAMAGE_RE.findall(found)
            )
            damage[key] = damage.get(key, 0.0) + total
    if not speed or not damage:
        return
    item.physical_dps = round(damage.get("physical", 0.0) * speed, 1)
    item.elemental_dps = round(damage.get("elemental", 0.0) * speed, 1)
    item.chaos_dps = round(damage.get("chaos", 0.0) * speed, 1)


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
    if len(lines) and lines[-1].startswith("- "):
        return False
    return True


def _parse_advanced(lines: list[str]) -> list[ItemMod]:
    """Split a block on its ``{ ... }`` headers.

    Every line after a header, up to the next one, belongs to that mod. This
    is the only reliable way to know that "스킬 사용 시 장착된 주문 발동…" and
    "이렇게 발동된 주문의 비용 150% 증폭" are one modifier rather than two.
    """
    mods: list[ItemMod] = []
    kind, affix, tier = "explicit", "", None
    body: list[str] = []

    def flush() -> None:
        if not body:
            return
        text = "\n".join(body)
        mods.append(ItemMod(text=text, kind=kind, affix=affix, tier=tier, **_rolls(text)))

    for line in lines:
        header = _MOD_HEADER_RE.match(line)
        if header:
            flush()
            body = []
            kind, affix, tier = _classify(header.group("body"))
        elif not _REMINDER_RE.match(line):
            body.append(line)
    flush()
    return mods


def _classify(header: str) -> tuple[str, str, int | None]:
    kind = "explicit"
    for name, words in _AFFIX_KINDS:
        if any(word in header for word in words):
            kind = name
            break
    affix = ""
    if any(w in header for w in ("접두어", "Prefix")):
        affix = "prefix"
    elif any(w in header for w in ("접미어", "Suffix")):
        affix = "suffix"
    # unique/implicit headers are a source, not a slot -- they map onto the
    # site's "explicit"/"implicit" buckets, which is what query.py needs.
    if kind in ("prefix", "suffix", "unique"):
        kind = "explicit"
    tier_match = _TIER_RE.search(header)
    return kind, affix, int(tier_match.group(1)) if tier_match else None


def _rolls(text: str) -> dict:
    """Pull the rolled numbers, and their ranges when the game gives them."""
    values: list[float] = []
    ranges: list[tuple[float, float]] = []
    remainder = text
    for match in _ROLL_RE.finditer(text):
        values.append(float(match.group("value")))
        ranges.append((float(match.group("lo")), float(match.group("hi"))))
        remainder = remainder.replace(match.group(0), " ", 1)
    if not values:
        values = [float(n) for n in _BARE_RE.findall(remainder)]
    return {"values": values, "ranges": ranges}

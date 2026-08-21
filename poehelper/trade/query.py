"""Turn a parsed item plus the user's choices into a trade-site search.

Two quite different searches come out of here, because two quite different
questions are being asked:

*Currency, cards, gems* -- "what does this thing sell for?". The item's
identity is its name, and it has no rolls, so the query is the name.

*A unique* -- the name says which unique it is, but two copies are not
interchangeable when one rolled 150% and the other 200%. So the name
identifies it and any ticked mods narrow it to comparable copies. Links and
corruption go in too: both move a unique's price more than its rolls do.

*Everything else* -- "what does an item with these properties sell for?". The
name is random and worthless; the value is in the base type and the mods, so
the query is the base plus whichever mods were ticked.

Bounds come from the window, not from here. They start at the value actually
on the item and are walked outwards from there, which is why every filter
takes an explicit min/max rather than deriving one.
"""
from __future__ import annotations

from dataclasses import dataclass

from .item import ItemMod, ParsedItem


@dataclass
class Selection:
    """One ticked mod and the range the user wants it searched over.

    ``None`` means "unbounded on that side", which is how a search widens: a
    mod with neither bound asks only that the item *has* it.
    """

    mod: ItemMod
    minimum: float | None = None
    maximum: float | None = None

# Which stat-id prefix to prefer for a mod that came from a given place on the
# item. The site publishes the same wording under several prefixes, so this is
# how a fractured mod is searched as fractured rather than as an explicit that
# happens to read the same.
_KIND_PREFIX = {
    "explicit": "explicit",
    "implicit": "implicit",
    "crafted": "crafted",
    "fractured": "fractured",
    "enchant": "enchant",
}

# Rarities priced by identity rather than by rolls.
_BY_NAME = {"unique", "currency", "card", "gem"}

# How much slack to leave under the rolled value. See the module docstring.
_SLACK = 0.9

# At or below this, a roll is a count rather than a magnitude (gem levels,
# "+1 to Maximum Frenzy Charges") and is searched exactly.
_SMALL_INTEGER = 6

# How many mods to tick by default. Two is the number that keeps finding
# comparable items: each additional AND-ed filter roughly halves the result
# set, and a price check with no results tells you nothing at all.
DEFAULT_MOD_COUNT = 2


# The site marks a *local* stat -- one that changes the item's own damage or
# defences rather than the character's -- by appending this to its wording.
# The game never prints it, so both variants of a mod collapse onto the same
# text and only the item can say which was meant. See _local_fits.
_LOCAL = "(특정)"

# Which of the two a local variant belongs to. Everything that is not about
# the piece's own defences is about a weapon's own damage: that split covers
# all 15 wordings the site publishes both ways.
_DEFENCE_WORDS = ("방어도", "회피", "에너지 보호막", "Armour", "Evasion", "Energy Shield")


def is_priced_by_name(item: ParsedItem) -> bool:
    return item.rarity in _BY_NAME


def _local_fits(item: ParsedItem | None, text: str) -> bool:
    """Whether *item* is the kind of thing this local stat can sit on.

    "공격 속도 12% 증가" is published twice: once local, meaning this weapon
    swings faster, and once global. On a pair of gloves only the global one
    is ever indexed, so searching the local id -- which is what came out of
    the list first -- returned nothing at all while listings existed. The
    same trap catches "번개 피해 1~52 추가" on a ring and "방어도 +#" on a
    weapon.

    The item's own properties settle it: a weapon prints damage and attack
    speed, a piece of armour prints armour/evasion/energy shield, and a ring
    prints neither.
    """
    if item is None:
        return False
    if any(word in text for word in _DEFENCE_WORDS):
        return item.has_defence
    return item.is_weapon


def resolve(api, mod: ItemMod, item: ParsedItem | None = None) -> tuple[str, int] | None:
    """The stat id to search *mod* under, and how many numbers it takes.

    The second half matters more than it looks. A mod's text can contain
    numbers that are not rolls -- the crafted trigger mod reads "…재사용
    대기시간 8초 / 이렇게 발동된 주문의 비용 150% 증폭", where 150 is part of
    the sentence. The site's own wording says which numbers are variable by
    writing them as ``#``, so counting those is what tells 8 (a roll) apart
    from 150 (prose). Averaging both gave 79, a number the item does not
    have anywhere on it.
    """
    candidates = api.find_stat(mod.text)
    if not candidates:
        return None
    wanted = _KIND_PREFIX.get(mod.kind, "explicit")
    # Some mods exist only as one kind (a unique's "번개 피해만 줄 수 있음" is
    # explicit-only), so an exact-kind miss falls back to the whole list
    # rather than dropping the mod out of the search entirely.
    same_kind = [e for e in candidates if e["id"].startswith(f"{wanted}.")] or candidates
    want_local = _local_fits(item, same_kind[0]["text"])
    entry = next(
        (e for e in same_kind if (_LOCAL in e["text"]) == want_local),
        same_kind[0],
    )
    return entry["id"], entry["text"].count("#")


def expand_mods(api, mods: list[ItemMod]) -> list[ItemMod]:
    """Split multi-line modifiers the site indexes one line at a time.

    Which way a two-line modifier goes is the site's call, not ours: the
    crafted trigger mod is published as a single two-line stat, while a
    tincture's implicit -- equally one ``{ 고정 속성 부여 }`` block -- is two
    separate ids. So the combined text is looked up first and kept when it
    exists; only when it does not, and the pieces do, is the mod taken apart.
    A piece the site does not know is kept anyway, so the window still shows
    the whole item; it simply contributes no filter.
    """
    expanded: list[ItemMod] = []
    for mod in mods:
        parts = mod.split()
        if len(parts) > 1 and not api.find_stat(mod.text) and any(
            api.find_stat(part.text) for part in parts
        ):
            expanded.extend(parts)
        else:
            expanded.append(mod)
    return expanded


def rolled_values(mod: ItemMod, placeholders: int) -> list[float]:
    return mod.values[:placeholders] if placeholders else []


def current_value(mod: ItemMod, placeholders: int) -> float | None:
    """The number actually on the item, as the site would filter on it.

    "Adds 47 to 80 Fire Damage" is one filter taking the average, which is
    why this reduces the roll to a single number rather than keeping both.
    """
    values = rolled_values(mod, placeholders)
    if not values:
        return None
    value = sum(values) / len(values)
    if value < 0:
        return None
    return round(value, 1) if value < 10 else float(int(value))


def bound_for(mod: ItemMod, placeholders: int) -> float | None:
    """A slightly loosened version of the roll, for callers that want slack."""
    values = rolled_values(mod, placeholders)
    if not values:
        return None
    value = sum(values) / len(values)
    if value < 0:
        # A negative roll is worse as it grows; bounding it from below would
        # ask for items strictly worse than this one.
        return None
    if value <= _SMALL_INTEGER and float(value).is_integer():
        # "+1 to all Minion Skill Gems" has no meaningful 10%-worse version:
        # slack turned it into ">= 0.9", which is not a thing an item can
        # have and quietly excluded every +1 item from the search.
        return float(value)
    bounded = value * _SLACK
    return round(bounded, 1) if bounded < 10 else float(int(bounded))


def _type_for(api, item: ParsedItem) -> str:
    """The base type as the site itself spells it.

    Two things stop the printed line from being usable as it stands, and both
    are answered by asking which published base is *inside* it:

    * a magic item prints its affixes into the same line ("전문의의 신성한
      생명력 플라스크 - 누그러뜨림");
    * a synthesised item prints 결합된 in front of the base ("결합된 토파즈
      반지"). The site publishes the plain base and rejects the decorated one
      with "Unknown item base type", so a synthesised item -- unique or not
      -- never found anything at all.

    Exact spelling is still tried first: containment is a fallback, not the
    rule, and 사슬 장갑 is inside more base names than it should decide.
    """
    if not item.base:
        return ""
    if item.rarity == "magic":
        return api.resolve_base(item.base) or item.base
    return api.resolve_type(item.base) or api.resolve_base(item.base) or item.base


def build(
    api,
    item: ParsedItem,
    selected: list[Selection],
    *,
    online_only: bool = True,
    extra_filters: dict | None = None,
    sort: str = "indexed",
) -> dict:
    """Assemble the JSON body for ``POST /api/trade/search/<league>``.

    *selected* is what the user ticked, each with the bounds they set. Which
    parts of the item identify it depends on what kind of thing it is:

    * currency, cards, gems -- the name is the whole item, and it has no
      rolls to filter on.
    * uniques -- the name says which unique, and any ticked mods narrow it to
      copies that rolled as well as this one.
    * everything else -- the name is random noise, so the base type and the
      ticked mods are all there is to go on.
    """
    query: dict = {
        "status": {"option": "online" if online_only else "any"},
        "stats": [{"type": "and", "filters": []}],
    }
    filters: dict = {}

    if item.rarity == "unique" and item.name:
        # A unique has both: the name says which one, the base pins the
        # variant (some uniques exist on several bases).
        #
        # Both go through the site's own spelling first. The endpoint matches
        # these exactly and rejects a near miss with "Unknown item name", and
        # the site's data contains stray whitespace here and there -- 들불 체관
        # is published with a trailing space -- so the name read correctly off
        # the item is not always the string the search will accept.
        query["name"] = api.resolve_name(item.name) or item.name
        if item.base:
            query["type"] = _type_for(api, item)
    elif item.base:
        # Currency, cards and gems have no "name" on the trade site at all --
        # what the game prints as the item's name is its *type* there. Sending
        # it as "name" matched nothing, which is why currency never searched.
        #
        # A magic item prints its affixes into that same line ("전문의의 신성한
        # 생명력 플라스크 - 누그러뜨림") and the site rejects the whole string
        # with "Unknown item base type", so the real base has to be dug back
        # out of it -- see TradeAPI.resolve_base.
        query["type"] = _type_for(api, item)

    if not item.priced_by_name:
        # A rare's own rarity has to be pinned, or the search happily matches
        # the unique that shares its base type and costs fifty times more.
        filters.setdefault("type_filters", {"filters": {}})["filters"]["rarity"] = {
            "option": "nonunique"
        }

    # Mods apply to uniques as well now: two copies of the same unique are not
    # the same item when one rolled 150% and the other 200%.
    for selection in selected:
        resolved = resolve(api, selection.mod, item)
        if resolved is None:
            continue
        stat_id, _placeholders = resolved
        entry: dict = {"id": stat_id, "disabled": False}
        bounds = {}
        if selection.minimum is not None:
            bounds["min"] = selection.minimum
        if selection.maximum is not None:
            bounds["max"] = selection.maximum
        if bounds:
            entry["value"] = bounds
        query["stats"][0]["filters"].append(entry)

    # Links, quality, corruption and the rest are chosen in the window rather
    # than inferred here: what counts as part of "the same item" is a
    # judgement about what is being priced, and only the person holding it
    # can make it.
    for group, entries in (extra_filters or {}).items():
        target = filters.setdefault(group, {"filters": {}})["filters"]
        target.update(entries)

    if filters:
        query["filters"] = filters

    # Newest first by default: a price check is asking what the item is
    # going for *now*, and the cheapest listings on a busy item are often the
    # oldest ones -- long-dead sellers whose price nobody has met.
    order = {"price": "asc"} if sort == "price" else {"indexed": "desc"}
    return {"query": query, "sort": order}


def summarise_price(listing: dict) -> str:
    price = (listing.get("listing") or {}).get("price") or {}
    amount, currency = price.get("amount"), price.get("currency")
    if amount is None or not currency:
        return "가격 미표기"
    pretty = int(amount) if float(amount).is_integer() else amount
    return f"{pretty} {CURRENCY_NAMES.get(currency, currency)}"


def seller_of(listing: dict) -> str:
    account = ((listing.get("listing") or {}).get("account") or {})
    # The character name is what you whisper; the account name is what the
    # site shows. Prefer the character, since that is what goes in a @.
    character = account.get("lastCharacterName") or ""
    return character or account.get("name", "").split("#")[0]


# The site returns currency ids; these are the names a Korean player reads.
# Anything unlisted falls through as its id, which is still readable.
CURRENCY_NAMES = {
    "chaos": "카오스",
    "divine": "디바인",
    "exalted": "엑잘티드",
    "alch": "연금",
    "alt": "변화",
    "fusing": "고리",
    "chrome": "색채",
    "jew": "보석",
    "chisel": "정",
    "vaal": "바알",
    "regal": "리갈",
    "gcp": "보석상",
    "mirror": "미러",
    "ancient": "고대",
    "annul": "무효",
    "blessed": "축복",
    "scour": "정제",
    "chance": "기회",
}

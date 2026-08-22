"""Turn a parsed item plus the user's choices into a trade-site search.

Three quite different searches come out of here, because three quite
different questions are being asked:

*Currency, fragments, essences* -- "what does a pile of these go for?". They
are not sold as listings of one item, they are exchanged in bulk, and the
site has its own endpoint for that. See :meth:`TradeAPI.exchange`.

*Cards, gems, uniques* -- "what does this thing sell for?". Its identity is
its name. A unique still takes mods, because two copies are not
interchangeable when one rolled 150% and the other 200%.

*Everything else* -- "what does an item with these properties sell for?". The
name is random and worthless; the value is in what kind of item it is, what
it rolled, and how much of it there is. So the query is the category, the
base type, the ticked mods, and -- for a weapon or a piece of armour -- the
numbers the item's own properties add up to.

Two things worth knowing about what is sent:

*The category is sent.* "A rare with these mods" and "a *wand* with these
mods" are different questions, and only the second one has a useful answer.
It comes from the game's own data (see :mod:`gamedata`) rather than from the
base type, so it is right even when the printed name is decorated beyond
recognition.

*Totals are offered.* Nobody prices a rare ring on "+45 생명력 최대치"; they
price it on how much life it adds altogether. The site indexes those totals
as ``pseudo`` stats, and :func:`pseudo_filters` adds up the mods that feed
each one.

Bounds come from the window, not from here. They start at the value actually
on the item and are walked outwards from there, which is why every filter
takes an explicit min/max rather than deriving one.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .item import ItemMod, ParsedItem

logger = logging.getLogger(__name__)


@dataclass
class Selection:
    """One ticked filter and the range the user wants it searched over.

    ``None`` means "unbounded on that side", which is how a search widens: a
    filter with neither bound asks only that the item *has* the thing.
    """

    mod: ItemMod | None = None
    minimum: float | None = None
    maximum: float | None = None
    # Set for the filters that are not a line on the item -- the totals and
    # the influences. When empty the ids come from the mod.
    ids: list[str] = field(default_factory=list)

    @property
    def stat_ids(self) -> list[str]:
        return self.ids or (self.mod.ids if self.mod else [])


# How much slack to leave under the rolled value when the window seeds a
# bound. Ten percent: enough that a comparable item is not excluded by a
# point or two, tight enough that the results are still the same item.
_SLACK = 0.9

# At or below this a roll is a count rather than a magnitude (gem levels,
# "+1 to Maximum Frenzy Charges") and is searched exactly. "+1 to all Minion
# Skill Gems" has no meaningful ten-percent-worse version: slack turned it
# into ">= 0.9", which is not a thing an item can have, and quietly excluded
# every +1 item from the search.
_SMALL_INTEGER = 6

# How many mods to tick by default. Two is the number that keeps finding
# comparable items: each additional AND-ed filter roughly halves the result
# set, and a price check with no results tells you nothing at all.
DEFAULT_MOD_COUNT = 2


def is_priced_by_name(item: ParsedItem) -> bool:
    return item.priced_by_name


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------
def current_value(mod: ItemMod) -> float | None:
    """The number actually on the item, as the site would filter on it."""
    value = mod.value
    if value is None:
        return None
    return round(value, 1) if mod.decimals or abs(value) < 10 else float(int(value))


def bound_for(mod: ItemMod) -> tuple[float | None, float | None]:
    """A slightly loosened (min, max) for this mod, as the window seeds it.

    Which side is loosened depends on which direction is *better*, and the
    game's data says so rather than this having to guess from the sign.
    "받는 원소 피해 8% 감소" is a good mod that reads as a negative number;
    bounding it from below asks for items strictly worse than the one in
    hand, which is how such mods used to disappear from a search.
    """
    value = current_value(mod)
    if value is None:
        return None, None
    if mod.better < 0:
        return None, _loosen(value, up=True)
    return _loosen(value, up=False), None


def _loosen(value: float, *, up: bool) -> float:
    if abs(value) <= _SMALL_INTEGER and float(value).is_integer():
        return float(value)
    loosened = value * (1 / _SLACK if up else _SLACK)
    return round(loosened, 1) if abs(loosened) < 10 else float(int(loosened))


# ---------------------------------------------------------------------------
# Totals (the site's "pseudo" stats)
# ---------------------------------------------------------------------------
@dataclass
class PseudoFilter:
    """One total the site indexes, and what this item adds up to."""

    id: str
    label: str
    value: float
    # The lines that added up to it. The window needs these to avoid asking
    # for the same thing twice: a ticked "생명력 합계 >= 89" already covers
    # the "생명력 최대치 +99" line that produced it, and searching both only
    # halves the results.
    sources: list[ItemMod] = field(default_factory=list)


# The English wordings the game's data uses are regular enough to be read
# rather than enumerated: "+#% to Fire and Cold Resistances" names its two
# elements in the text itself. Matching on that keeps every combination the
# game adds later working without a new entry here.
_RESISTANCE_RE = re.compile(
    r"^\+?#% to (?P<what>.+?) Resistances?$"
)
_ATTRIBUTE_RE = re.compile(r"^\+?# to (?P<what>.+?)$")

_ELEMENTS = ("Fire", "Cold", "Lightning")
_ATTRIBUTES = ("Strength", "Dexterity", "Intelligence")

# The site publishes two pseudo stats per resistance and they are easy to
# confuse: ``pseudo_count_elemental_resistances`` is *how many* elemental
# resistances the item carries (one to three) and
# ``pseudo_total_elemental_resistance`` is how much they add up to. Asking
# the count for "at least 78" is a question no item can answer, and every
# such search returned nothing at all.
_TOTALS = {
    "life": ("pseudo.pseudo_total_life", "생명력 합계"),
    "energy_shield": ("pseudo.pseudo_total_energy_shield", "에너지 보호막 합계"),
    "mana": ("pseudo.pseudo_total_mana", "마나 합계"),
    "elemental": ("pseudo.pseudo_total_elemental_resistance", "원소 저항 합계"),
    "resistances": ("pseudo.pseudo_total_resistance", "전체 저항 합계"),
    "Strength": ("pseudo.pseudo_total_strength", "힘 합계"),
    "Dexterity": ("pseudo.pseudo_total_dexterity", "민첩 합계"),
    "Intelligence": ("pseudo.pseudo_total_intelligence", "지능 합계"),
    "attributes": ("pseudo.pseudo_total_all_attributes", "모든 능력치 합계"),
}


def pseudo_filters(item: ParsedItem) -> list[PseudoFilter]:
    """The totals worth offering for this item.

    Nobody prices a rare ring on any one of its lines; they price it on how
    much life and resistance it adds altogether, which is what the site's
    pseudo stats index. Two mods of +30 life are a +60 life ring to every
    buyer and were two separate filters that found nothing to this app.

    Only totals with something in them are returned, and only for items that
    are priced on their rolls -- a unique's totals are a property of the
    unique, not of this copy.
    """
    if item.priced_by_name and item.rarity != "unique":
        return []

    parts: dict[str, float] = {}
    sources: dict[str, list[ItemMod]] = {}
    for mod in item.mods:
        if mod.match is None or mod.kind == "enchant":
            continue
        value = mod.value
        if value is None:
            continue
        for key, amount in _contributions(mod.match.stat.ref, value):
            parts[key] = parts.get(key, 0.0) + amount
            sources.setdefault(key, []).append(mod)

    # Strength is life and intelligence is mana, at the rate the game itself
    # grants them. A ring with +40 강인함 is worth 20 life to a buyer, and
    # leaving that out understates the item.
    def rolled_up(target: str, *keys: str) -> None:
        """Fold the contributions of *keys* into *target*, sources included."""
        for key in keys:
            sources.setdefault(target, []).extend(
                m for m in sources.get(key, ()) if m not in sources[target]
            )

    strength = parts.get("Strength", 0.0)
    intelligence = parts.get("Intelligence", 0.0)
    if strength:
        parts["life"] = parts.get("life", 0.0) + strength // 2
        rolled_up("life", "Strength")
    if intelligence:
        parts["mana"] = parts.get("mana", 0.0) + intelligence // 2
        rolled_up("mana", "Intelligence")
    elemental = sum(parts.get(e, 0.0) for e in _ELEMENTS)
    if elemental:
        parts["elemental"] = elemental
        parts["resistances"] = elemental + parts.get("Chaos", 0.0)
        sources.setdefault("elemental", [])
        rolled_up("elemental", *_ELEMENTS)
        sources.setdefault("resistances", [])
        rolled_up("resistances", *_ELEMENTS, "Chaos")
    attributes = min((parts.get(a, 0.0) for a in _ATTRIBUTES), default=0.0)
    if attributes:
        parts["attributes"] = attributes
        sources.setdefault("attributes", [])
        rolled_up("attributes", *_ATTRIBUTES)

    out = []
    for key, (stat_id, label) in _TOTALS.items():
        value = parts.get(key, 0.0)
        if value:
            out.append(
                PseudoFilter(
                    id=stat_id, label=label, value=float(value),
                    sources=list(sources.get(key, ())),
                )
            )
    return out


def _contributions(ref: str, value: float):
    """Which totals one modifier feeds, and by how much.

    Read off the English wording rather than a list of stat ids: the game
    publishes "+#% to Fire and Lightning Resistances" and a dozen more like
    it, and every one of them contributes to both of the elements it names.
    """
    resistance = _RESISTANCE_RE.match(ref)
    if resistance:
        what = resistance.group("what")
        if what == "all Elemental":
            for element in _ELEMENTS:
                yield element, value
            return
        for element in (*_ELEMENTS, "Chaos"):
            if element in what:
                yield element, value
        return

    for wording, key in (
        ("+# to maximum Life", "life"),
        ("+# to maximum Energy Shield", "energy_shield"),
        ("+# to maximum Mana", "mana"),
    ):
        if ref == wording:
            yield key, value
            return

    attribute = _ATTRIBUTE_RE.match(ref)
    if attribute:
        what = attribute.group("what")
        if what == "all Attributes":
            for name in _ATTRIBUTES:
                yield name, value
            return
        # "+# to Strength and Dexterity" grants both in full. The test is
        # that every word of the phrase is an attribute, so "+# to Level of
        # all Absolution Gems" -- which fits the same wording -- is not read
        # as one.
        named = [part.strip() for part in what.split(" and ")]
        if all(part in _ATTRIBUTES for part in named):
            for name in named:
                yield name, value


# ---------------------------------------------------------------------------
# Item properties the site filters on directly
# ---------------------------------------------------------------------------
# What a weapon or a piece of armour is worth is mostly the size of the
# numbers under its name, and the site filters on those directly rather than
# through a stat id. Seeded at ten percent under the item's own, which is the
# same slack a mod gets.
def property_filters(item: ParsedItem) -> dict[str, dict[str, float]]:
    """Suggested weapon/armour bounds, as ``{group: {key: value}}``."""
    out: dict[str, dict[str, float]] = {}
    weapon = {}
    if item.total_dps:
        weapon["dps"] = _floor(item.total_dps)
        if item.physical_dps:
            weapon["pdps"] = _floor(item.physical_dps)
        if item.elemental_dps:
            weapon["edps"] = _floor(item.elemental_dps)
    if weapon:
        out["weapon_filters"] = weapon
    armour = {}
    for key, value in (
        ("ar", item.armour), ("ev", item.evasion),
        ("es", item.energy_shield), ("ward", item.ward),
    ):
        if value:
            armour[key] = _floor(value)
    if armour:
        out["armour_filters"] = armour
    return out


def _floor(value: float) -> float:
    return float(int(value * _SLACK))


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------
# The influences an item carries are searched as stats rather than as a
# filter of their own, so each is looked up by the wording the game's data
# gives it.
_INFLUENCE_REFS = {
    "shaper": "Has Shaper Influence",
    "elder": "Has Elder Influence",
    "crusader": "Has Crusader Influence",
    "hunter": "Has Hunter Influence",
    "redeemer": "Has Redeemer Influence",
    "warlord": "Has Warlord Influence",
}


_INFLUENCE_LABELS = {
    "shaper": "쉐이퍼", "elder": "엘더", "crusader": "십자군",
    "hunter": "사냥꾼", "redeemer": "대속자", "warlord": "전쟁군주",
}


def influence_filters(item: ParsedItem, data) -> list[tuple[str, list[str]]]:
    """``(label, ids)`` for each influence the item carries.

    An influenced base is worth a multiple of the same base without it, and
    the game announces it in a section of its own rather than as a modifier
    line -- so without this a Shaper helmet was priced as an ordinary one.
    The site indexes them as pseudo stats, which is why they come back as
    something to tick rather than as a filter of their own.
    """
    out = []
    for influence in sorted(item.influences):
        stat = data.find_by_ref(_INFLUENCE_REFS.get(influence, "")) if data else None
        ids = (stat.ids.get("pseudo") or stat.ids.get("explicit")) if stat else None
        if ids:
            out.append((f"{_INFLUENCE_LABELS.get(influence, influence)} 아이템", list(ids)))
    return out


def _stat_entry(selection: Selection) -> dict | None:
    """One entry for the query's stat list.

    A mod that resolves to several ids -- the same wording published as two
    different stats -- becomes a nested "count" group asking for at least one
    of them, which is the site's own way of saying "either of these".
    """
    ids = selection.stat_ids
    if not ids:
        return None
    bounds: dict[str, float] = {}
    if selection.minimum is not None:
        bounds["min"] = selection.minimum
    if selection.maximum is not None:
        bounds["max"] = selection.maximum

    if len(ids) == 1:
        entry: dict = {"id": ids[0], "disabled": False}
        if bounds:
            entry["value"] = bounds
        return entry

    filters = []
    for stat_id in ids:
        # An id the game's data marked "{empty}" is the capped form of the
        # mod, indexed as a flag rather than as a number; asking it for "at
        # least 100" matches nothing.
        flag = stat_id.startswith("{empty}")
        entry = {"id": stat_id.removeprefix("{empty}"), "disabled": False}
        if bounds and not flag:
            entry["value"] = dict(bounds)
        filters.append(entry)
    return {
        "type": "count",
        "value": {"min": 1},
        "filters": filters,
        "disabled": False,
    }


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

    *selected* is everything the user ticked -- lines on the item, totals and
    influences alike -- each with the bounds they set.
    """
    query: dict = {
        "status": {"option": "online" if online_only else "any"},
        "stats": [{"type": "and", "filters": []}],
    }
    filters: dict = {}

    if item.rarity == "unique" and item.name:
        # A unique has both: the name says which one, the base pins the
        # variant (some uniques exist on several bases).
        query["name"] = _site_name(api, item, item.name)
        if item.base:
            query["type"] = _site_type(api, item)
    elif item.base:
        # Currency, cards and gems have no "name" on the trade site at all --
        # what the game prints as the item's name is its *type* there.
        query["type"] = _site_type(api, item)

    category = item.trade_category
    if category and api.category_allowed(category):
        # What kind of item this is, rather than only what it is called. On a
        # rare this is most of the question: the base type alone matches the
        # handful of listings that happen to share it, while the category
        # matches every wand a buyer would consider instead of this one.
        filters.setdefault("type_filters", {"filters": {}})["filters"]["category"] = {
            "option": category
        }
    if not item.priced_by_name:
        # A rare's own rarity has to be pinned, or the search happily matches
        # the unique that shares its base type and costs fifty times more.
        filters.setdefault("type_filters", {"filters": {}})["filters"]["rarity"] = {
            "option": "nonunique"
        }

    for selection in selected:
        entry = _stat_entry(selection)
        if entry is not None:
            query["stats"][0]["filters"].append(entry)

    # Links, quality, corruption, DPS and the rest are chosen in the window
    # rather than inferred here: what counts as part of "the same item" is a
    # judgement about what is being priced, and only the person holding it
    # can make it.
    for group, entries in (extra_filters or {}).items():
        target = filters.setdefault(group, {"filters": {}})["filters"]
        target.update(entries)

    if filters:
        query["filters"] = filters

    order = {"price": "asc"} if sort == "price" else {"indexed": "desc"}
    return {"query": query, "sort": order}


def _site_name(api, item: ParsedItem, printed: str) -> str:
    """A unique's name as the search endpoint will accept it.

    The endpoint matches this exactly and answers anything else with HTTP 400
    "Unknown item name" -- and the site's own data is not always tidy. 들불
    체관 is published as ``"들불 체관 "``, trailing space and all. So the
    site's own spelling wins where it has one, the game data's is next, and
    what was printed on the item is the last resort.
    """
    candidates = [printed]
    if item.info is not None and item.info.namespace == "UNIQUE":
        candidates.insert(0, item.info.name)
    for candidate in candidates:
        exact = api.resolve_name(candidate)
        if exact:
            return exact
    return candidates[0]


def _site_type(api, item: ParsedItem) -> str:
    """The base type as the site itself spells it.

    The printed line is not usable as it stands -- a magic item prints its
    affixes into it, a synthesised one prints 결합된 in front of the base,
    and a map prints its tier after it. All of that is undone while the item
    is parsed, so what arrives here is the bare base; this only has to agree
    with the site on how to spell it.
    """
    printed = item.base
    if item.info is not None and item.info.namespace != "UNIQUE":
        printed = item.info.name
    return api.resolve_type(printed) or printed


# ---------------------------------------------------------------------------
# Reading the results
# ---------------------------------------------------------------------------
def summarise_price(listing: dict, names: dict | None = None) -> str:
    price = (listing.get("listing") or {}).get("price") or {}
    amount, currency = price.get("amount"), price.get("currency")
    if amount is None or not currency:
        return "가격 미표기"
    pretty = int(amount) if float(amount).is_integer() else amount
    return f"{pretty} {currency_name(currency, names)}"


def seller_of(listing: dict) -> str:
    account = (listing.get("listing") or {}).get("account") or {}
    # The character name is what you whisper; the account name is what the
    # site shows. Prefer the character, since that is what goes in a @.
    character = account.get("lastCharacterName") or ""
    return character or account.get("name", "").split("#")[0]


def currency_name(currency: str, names: dict | None = None) -> str:
    if names and currency in names:
        return names[currency]
    return CURRENCY_NAMES.get(currency, currency)


# A fallback only: TradeAPI.currency_names asks the site for the whole list,
# localised, and that is what a listing is normally rendered with. These are
# the ones common enough to be worth reading correctly if that call fails.
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

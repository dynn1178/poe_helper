"""The map modifiers the 정규식 tab can build a search out of, and the
fragment of text that identifies each one.

What a "pattern" is here
------------------------
Not the whole mod line -- a short piece of it that no other mod anyone cares
about contains. The game's own item search takes a regular expression, and
the box it is typed into holds **50 characters**. A dozen full mod names do
not come close to fitting, so every regex tool for this game works in short
distinctive fragments and this one is no different: 반사 rather than
"몬스터가 물리 피해의 40%를 반사".

Shorter also means *more* correct here, not less. A fragment matches every
wording the mod appears in -- 물리 피해 반사, 원소 피해 반사 and 사술 반사 are
three separate mods that one 반사 covers -- and it keeps matching when the
localisation changes a word somewhere else in the line.

Where the wording came from
---------------------------
The trade site's own stat list for the Korean realm (``cache/trade_*_stats
.json``, the same download that makes price checking possible -- see
``trade/api.py``). That is GGG's Korean text for every modifier in the game,
so the fragments below are checked against what the client actually prints
rather than transcribed by hand from a wiki.

Fragments deliberately avoid spaces where a natural one exists: the game
treats a space as "and also match this separately" unless the whole search
is quoted, and a fragment that only works quoted is one that breaks the
moment someone unquotes it.

What a fragment is matched against
---------------------------------
Not just the mod lines. A map's text also carries, and a short fragment will
happily match any of it:

* **속성 부여 headers** -- ``{ 접두어 속성 부여 "뚫리지 않는" — 물리, 카오스,
  공격, 상태 이상 }``. Those trailing words are the affix's damage-type tags,
  and they contain 카오스, 물리, 공격 and friends on a map with no such mod.
* **Reminder text** -- ``(시간의 사슬은 동작 속도를 15% 감소시키는 사술입니다...)``.
  The rules explanation under a curse, full of 속도, 사술, 피해, 저항.
* **Property lines** -- ``몬스터 레벨: 83``, ``아이템 희귀도: 희귀``. Every map
  has them, so a fragment matching one matches everything.

This is where the first version of this list went wrong: 카오스 matched a
header tag and a 중독 explanation on a map whose monsters gain no chaos
damage at all.

The rule every fragment now follows
-----------------------------------
**Match the option, not the word.** A mod line is a statement about the area
-- it ends in 증폭, 증가, 걸림, 불가, 등장, 존재 -- while a reminder is prose
that opens with the mechanic's *name* and explains it. So a fragment is
anchored past the bare keyword, onto the part only the statement has:

===================  ==========================================  ============
keyword (was)        the mod line                                 anchored to
===================  ==========================================  ============
취약                 플레이어가 취약성 **저주에 걸림**            취약성 저주
사슬                 플레이어가 시간의 사슬 **저주에 걸림**       사슬 저주
약화                 플레이어가 원소 약화 **저주에 걸림**         약화 저주
카오스               ...추가 카오스 **피해로 획득**               피해로 획득
사술                 몬스터가 **사술 방지** 보유                  사술 방지
속도                 몬스터의 이동 **속도 25% 증가**              속도 [1-9]
지대                 지역에 용암 **지대 존재**                    지대 존재
몬스터 레벨          **지역 몬스터** 레벨 +1                      지역 몬스터
===================  ==========================================  ============

``check_fragments.py`` in the project root enforces this against real copied
maps in ``data/map_samples/``: any fragment landing on a 설명 or 구분 line is
a failure, and the fix is to anchor it further rather than to accept it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The 50-character ceiling on the game's search box. Not enforced -- a longer
# pattern is still a perfectly good thing to keep in a preset and paste
# somewhere else -- but shown, because a pattern that silently gets truncated
# in game is a filter that quietly stops filtering.
SEARCH_LIMIT = 50


@dataclass(frozen=True)
class MapMod:
    id: str
    category: str
    label: str
    pattern: str
    # Set only where a *number* is the point of the mod. It is the literal
    # text the value follows on the item -- "수량: \+" for "아이템 수량: +107%"
    # -- so a threshold can be pinned to the right line instead of matching
    # any 107 anywhere on the map.
    #
    # Only the property lines get one. Their prefix is fixed and printed the
    # same way on every map; a rolled mod writes its value mid-sentence with
    # its own range after it ("몬스터의 생명력 92(90-100)% 증폭"), where a
    # threshold would be guesswork.
    numeric: str = ""


DANGER = "위험 (회피)"
MONSTER = "몬스터 강화"
CURSE = "저주 / 상태 이상"
REWARD = "보상"
CONTENT = "콘텐츠"

CATEGORIES = [DANGER, MONSTER, CURSE, REWARD, CONTENT]

_CURATED_MODS: list[MapMod] = [
    # ---- 위험: the mods that kill builds rather than slow them down --------
    # Three separate mods, not one. They were behind a single 반사 tick, which
    # is wrong twice over: the player-facing reflect and the two Thorns mods
    # are avoided by completely different builds -- a physical attacker cares
    # about the first and the third, a spellcaster about neither -- and one
    # tick meant throwing away maps carrying any of them.
    #
    # The old label also said 사술, which no map mod does: the only 사술 mod
    # is 몬스터가 사술 방지 보유 (Hexproof), a different thing entirely. All
    # three wordings below are the game's own; see tools/check_map_mods.py.
    MapMod("reflect", DANGER, "물리 피해 반사 (공격자에게)", "공격자에게 반사"),
    MapMod("thorns_ele", DANGER, "희귀 몬스터 원소 가시", "원소 가시"),
    MapMod("thorns_phys", DANGER, "희귀 몬스터 물리 가시", "물리 가시"),
    MapMod("penetration", DANGER, "몬스터 피해가 원소 저항 관통", "관통"),
    MapMod("no_regen", DANGER, "생명력·마나·에너지 보호막 재생 불가", "재생"),
    MapMod("no_leech", DANGER, "몬스터 생명력·마나 흡수 불가", "흡수"),
    # 최대치 alone also matched 몬스터의 격분 충전 최대치 +1, which is a
    # different mod entirely. 저항 최대치 covers both the one that lowers
    # yours and the one that raises the monsters', and nothing else.
    MapMod("max_res", DANGER, "저항 최대치 (플레이어 감소 / 몬스터 증가)", "저항 최대치"),
    MapMod("less_recovery", DANGER, "생명력·에너지 보호막 회복 속도 감폭", "회복"),
    MapMod("flask_charges", DANGER, "플라스크 충전량 감소", "충전량"),
    MapMod("less_armour", DANGER, "플레이어 방어도 감폭", "방어도"),
    MapMod("less_accuracy", DANGER, "플레이어 정확도 감폭", "정확도"),
    MapMod("no_block", DANGER, "막기 확률 감소 / 막기 불가", "막기"),
    MapMod("no_suppress", DANGER, "주문 피해 억제 감소 / 억제 불가", "억제"),
    MapMod("crit_taken", DANGER, "플레이어가 치명타로 피격될 확률 증가", "치명타"),
    # "플레이어에 대한 공격에 화염 피해 24~35 추가" -- 대한 is the only piece
    # of that line the reward mods do not also use.
    MapMod("added_damage", DANGER, "플레이어에 대한 공격에 원소 피해 추가", "대한"),
    MapMod("aoe", DANGER, "효과 범위 (몬스터 증가 / 플레이어 감폭)", "효과 범위"),
    MapMod("projectiles", DANGER, "투사체 (플레이어 관통 / 추가 발사)", "투사체"),
    MapMod("no_exp", DANGER, "경험치 획득 불가", "경험치"),
    MapMod("portals", DANGER, "포탈 제한 / 서서히 닫힘", "포탈"),
    MapMod("void", DANGER, "사망 시 공허로 보내짐", "공허"),

    # ---- 몬스터 강화 -------------------------------------------------------
    # 속도 alone matched the 시간의 사슬 reminder ("동작 속도를 15% 감소"),
    # which is on a great many maps that do not speed monsters up at all. The
    # real mods all read "... 속도 25% 증가", so a digit after the space is
    # what separates them from the explanation.
    MapMod("monster_speed", MONSTER, "몬스터 이동·공격·시전 속도 증가", "속도 [1-9]"),
    MapMod("monster_life", MONSTER, "몬스터 생명력 증폭", "생명력"),
    MapMod("monster_damage", MONSTER, "몬스터·보스가 주는 피해 증폭", "주는 피해"),
    MapMod("monster_extra_ele", MONSTER, "몬스터가 추가 원소 피해로 가함", "속성으로"),
    # 카오스 was the first version of this and it was wrong: the word turns up
    # in affix header tags and in the 중독 reminder text on maps with no such
    # mod. "피해로 획득" is the tail of the real lines -- 몬스터가 물리 피해의
    # #%를 추가 카오스 피해로 획득, and the one where hits against *you* gain
    # extra fire damage -- and appears nowhere else on a map.
    MapMod("monster_chaos", MONSTER, "추가 피해 획득 (몬스터 카오스 / 피격 원소)", "피해로 획득"),
    MapMod("monster_crit", MONSTER, "몬스터 치명타 확률 / 피해 배율 증가", "배율"),
    # "몬스터가 20초마다 격분 충전 3개 획득" -- the timer is what makes this
    # line unmistakable; 충전 alone also catches the flask mods.
    # 20초마다 was the wording years ago; the game now grants charges on hit
    # and on being hit. Checked against the game's own area-mod list, which
    # has four such mods and none mentioning a timer.
    MapMod("monster_charges", MONSTER, "몬스터가 명중/피격 시 충전 획득", "충전 획득"),
    # 가 명중 시, not 명중 시: the 취약성 reminder contains "명중 시 대상에게
    # 출혈을 유발할 확률", and 취약성 is one of the most common map curses.
    MapMod("monster_ailments", MONSTER, "몬스터가 명중 시 상태 이상·충전 획득", "가 명중 시"),
    # Covers both 긴급회피 (원소 상태 이상) and the plain 중독·꿰뚫기·출혈 회피.
    MapMod("monster_avoid", MONSTER, "몬스터가 상태 이상·중독·출혈 회피", "회피"),
    # The game prints "몬스터의 원소 저항 40%" with no plus sign, so the old
    # "저항 \+" matched nothing at all. Anchored on 몬스터의 so it cannot
    # catch the penetration mod ("몬스터 피해가 …의 원소 저항 관통") or the
    # player's own 최대치 mod, both of which are separate options here.
    MapMod("monster_res", MONSTER, "몬스터 원소·카오스 저항 증가", "몬스터의.{0,5}저항"),
    MapMod("monster_pdr", MONSTER, "몬스터 물리 피해 감소", "피해 감소"),
    # 사술 on its own matched "...부여하는 사술입니다" in every curse's
    # reminder text. 사술 반사 is already covered by the 반사 entry.
    MapMod("monster_hexproof", MONSTER, "몬스터가 사술 방지 보유", "사술 방지"),
    MapMod("monster_immune", MONSTER, "몬스터 기절·도발 면역", "면역"),
    MapMod("monster_chain", MONSTER, "몬스터의 스킬 추가 연쇄", "연쇄"),
    MapMod("monster_es", MONSTER, "몬스터가 추가 에너지 보호막 획득", "보호막 최대치"),
    # Every map prints "몬스터 레벨: 83" as a property, so that fragment
    # matched all of them. The mod itself reads "지역 몬스터 레벨 +1".
    MapMod("monster_level", MONSTER, "지역 몬스터 레벨 증가", "지역 몬스터"),
    MapMod("magic_monsters", MONSTER, "마법 몬스터 증가", "마법 몬스터"),
    MapMod("rare_monsters", MONSTER, "희귀 몬스터 증가 / 속성 추가", "희귀 몬스터"),
    MapMod("boss", MONSTER, "지도 보스 강화", "보스"),

    # ---- 저주 / 상태 이상 --------------------------------------------------
    # Every one of these is anchored past the curse's *name*, because the name
    # on its own is also how the game opens the explanation printed under it:
    # the mod says "플레이어가 취약성 저주에 걸림" and the reminder underneath
    # says "(취약성은 받는 물리 피해를...)". 취약 matches both; 취약성 저주
    # matches only the mod. Curses are the mechanic that carries reminder text
    # on maps, so this is where anchoring earns its keep.
    MapMod("curse_any", CURSE, "저주 계열 전체 (추가 저주 포함)", "저주에 걸림|추가 저주"),
    MapMod("curse_weakness", CURSE, "원소 약화 / 쇠약화", "약화 저주"),
    MapMod("curse_vulnerability", CURSE, "취약성", "취약성 저주"),
    MapMod("curse_temporal", CURSE, "시간의 사슬", "사슬 저주"),
    MapMod("ground", CURSE, "지대 (용암·얼음·감전·훼손)", "지대 존재"),

    # ---- 보상 --------------------------------------------------------------
    # The ": +" is load-bearing on 희귀도. A rare map prints its own rarity as
    # "아이템 희귀도: 희귀" and its item-rarity bonus as "아이템 희귀도: +110%"
    # -- the same words -- so the bare fragment matched every rare map whether
    # or not it rolled any bonus at all. 수량 is written the same way for
    # symmetry and to keep it off mod lines that merely mention a quantity.
    #
    # These six are also the ones worth a number: the game prints each on its
    # own property line with a fixed prefix ("아이템 수량: +107%"), so a
    # threshold can be attached to the right value with no ambiguity.
    MapMod("quantity", REWARD, "아이템 수량 증가", r"수량: \+", r"수량: \+"),
    MapMod("rarity", REWARD, "아이템 희귀도 증가", r"희귀도: \+", r"희귀도: \+"),
    MapMod("pack_size", REWARD, "무리 규모 증가", "무리 규모", r"무리 규모: \+"),
    MapMod("more_maps", REWARD, "지도 더 많음", "지도 더 많음", r"지도 더 많음: \+"),
    MapMod("scarab", REWARD, "갑충석", "갑충석", r"갑충석 더 많음: \+"),
    MapMod("currency", REWARD, "화폐", "화폐", r"화폐 더 많음: \+"),
    # The client prints this as its own property line ("점술 카드 증폭: +30%"),
    # exactly like the four above it -- so it gets the same treatment. A bare
    # 점술 also matched the reminder text under an unrelated mod.
    MapMod("divination", REWARD, "점술 카드 증폭", "점술 카드 증폭", r"점술 카드 증폭: \+"),

    # ---- 콘텐츠 ------------------------------------------------------------
    MapMod("beyond", CONTENT, "이계", "이계"),
    MapMod("breach", CONTENT, "균열", "균열"),
    MapMod("legion", CONTENT, "군단", "군단"),
    MapMod("abyss", CONTENT, "심연", "심연"),
    # An essence is a 갇힌 몬스터 on the map; the word 에센스 never appears.
    MapMod("essence", CONTENT, "에센스 (갇힌 몬스터)", "갇힌 몬스터"),
    MapMod("harbinger", CONTENT, "선구자", "선구자"),
    MapMod("delirium", CONTENT, "환영", "환영"),
    MapMod("ritual", CONTENT, "의식", "의식"),
    MapMod("expedition", CONTENT, "탐험", "탐험"),
    MapMod("blight", CONTENT, "역병", "역병"),
    MapMod("harvest", CONTENT, "수확 (신성한 숲)", "신성한 숲"),
    MapMod("ultimatum", CONTENT, "결전", "결전"),
    MapMod("shrine", CONTENT, "성소", "성소"),
    MapMod("strongbox", CONTENT, "금고", "금고"),
    MapMod("vaal", CONTENT, "바알 부가 지역", "바알"),
    MapMod("torment", CONTENT, "고통받는 혼백", "혼백"),
    MapMod("beast", CONTENT, "야수", "야수"),
]

INCLUDE, INCLUDE_ALL, EXCLUDE = "include", "include_all", "exclude"

UNSORTED = "기타"


# ---------------------------------------------------------------------------
# The option list, built from the game's own map modifiers
# ---------------------------------------------------------------------------
# One option per modifier a map can actually roll, rather than the handful of
# grouped fragments this used to offer. Grouping was convenient to write and
# wrong to use: a single 반사 tick threw away maps carrying *any* of three
# unrelated mods, and a physical attacker and a spellcaster do not avoid the
# same ones. The game's list is the honest one, and it is 207 entries long --
# which the tab's search box handles far better than a taxonomy would.
#
# Three things have to be produced for each, and only the first is free:
#
# *The label* is the wording the client prints, taken as-is. Nothing to
# decide, and it is what the player will read on the map.
#
# *The fragment* is the shortest run of words from that wording which no
# other map modifier contains. Shortest because the game's search box holds
# 50 characters; unique because a fragment that also matches a neighbour is
# how a filter quietly throws away maps it was never asked about. In Korean
# this comes out at a median of three characters, so a dozen still fit.
#
# *The category* is inherited from whichever curated fragment above used to
# match it -- a mapping that was checked by hand once and can be re-derived
# rather than re-guessed. Anything no curated fragment covered stays in 기타
# instead of being sorted by keyword, because guessing from the text puts
# "이 지역에서 발견하는 아이템 수량 증가" under 콘텐츠 and the player looking
# for it under 보상 never finds it.
_MIN_FRAGMENT = 2


def _slug(ref: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", ref.lower()).strip("_")[:56] or "mod"


def _word_runs(text: str) -> list[str]:
    """Every run of consecutive words, shortest first, later ones preferred.

    Later because the end of a mod line is the part that states the effect
    ("... 25% 증가"), while the opening is a subject many mods share.

    Runs never cross the rolled value. On the item that value is a number
    this has no way to predict, so a fragment spanning it could not match --
    and one *ending* at it comes out as the useless "몬스터 %". Splitting the
    line at the placeholder leaves only the runs that are literal text on
    every copy of the mod.
    """
    runs: set[str] = set()
    for segment in re.split(r"\S*#\S*", text):
        words = [w for w in segment.split() if w]
        runs.update(
            " ".join(words[start:start + length])
            for length in range(1, len(words) + 1)
            for start in range(len(words) - length + 1)
        )
    return sorted(runs, key=lambda run: (len(run), -text.find(run)))


def _fragment_for(
    wording: str, index: int, others: list[tuple[int, str]], avoid: list[str]
) -> str:
    """The shortest run of *wording* that matches this modifier and nothing else.

    "Nothing else" is two separate requirements and the second is the one
    that bites. A run must not appear in another modifier -- obvious -- and
    it must not appear in the parts of a map that are *not* modifiers: the
    ``{ 접두어 속성 부여 … — 물리, 카오스 }`` headers, whose trailing words are
    damage-type tags, and the parenthesised reminder paragraphs that explain
    a curse. Both are full of the same words the mods use, so the shortest
    unique-among-mods run is regularly a word out of a rules explanation.
    See _avoid_corpus.
    """
    for run in _word_runs(wording):
        if len(run) < _MIN_FRAGMENT:
            continue
        if any(run in other for j, other in others if j != index):
            continue
        if any(run in line for line in avoid):
            continue
        return run
    # No unique run exists: this modifier's whole wording is contained in a
    # longer one ("효과 범위 #% 증가" inside "몬스터의 효과 범위 #% 증가"), so
    # nothing short of the whole line distinguishes it -- and even that will
    # also match the longer mod, which coverage() reports honestly.
    #
    # The wording has to become a *pattern* on the way out. It carries the
    # game's "#" where the rolled number goes, and the item has a digit
    # there; left as-is the fragment matched nothing at all.
    return "".join(
        r"\d+" if part == "#" else re.escape(part)
        for part in re.split(r"(#)", wording)
    )


def _avoid_corpus() -> list[str]:
    """Lines a fragment must never match, read off real copied maps.

    ``data/map_samples/`` holds maps someone actually pasted in, and the
    lines in them that are not modifiers are the trap this exists for: an
    affix header carries the mod's damage-type tags, and a reminder
    paragraph restates the mechanic in the same words the mod uses. A
    fragment landing on either matches maps that do not have the mod at all.

    Best-effort. With no samples on disk the fragments are merely unique
    among modifiers, which is where this started; ``check_fragments.py``
    reports whatever slips through.
    """
    from . import paths

    lines: list[str] = []
    directory = paths.data_path("map_samples")
    try:
        files = sorted(directory.glob("*.txt")) if directory.is_dir() else []
    except OSError:
        return lines
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines += [
            line.strip()
            for line in text.splitlines()
            if line.strip() and classify_line(line) != MOD_LINE
        ]
    return lines


def _generate(data) -> list[MapMod]:
    entries: list[tuple[str, list[str]]] = []
    for ref, wordings in data.area_mods():
        # Split on newlines as well as between wordings. A two-line mod is
        # two lines on the item too, and a fragment spanning the join ("…
        # 않음 몬스터의 …") is text that appears nowhere.
        cleaned = [
            re.sub(r"[ \t]+", " ", line).strip()
            for wording in wordings
            for line in wording.split("\n")
        ]
        cleaned = [w for w in cleaned if w]
        if cleaned:
            entries.append((ref, cleaned))
    if not entries:
        return []

    # Every wording in the game, so a fragment can be tested for uniqueness
    # against all of them and not merely against the first of each mod.
    pool = [(i, w) for i, (_ref, ws) in enumerate(entries) for w in ws]
    avoid = _avoid_corpus()
    category = _inherited_categories(entries)

    out: list[MapMod] = []
    # Seeded with the property-line ids so a generated slug can never collide
    # with one of them -- all_mods() puts both in the same list, and BY_ID
    # would silently drop whichever came second.
    used: set[str] = {m.id for m in _CURATED_MODS if m.numeric}
    for index, (ref, wordings) in enumerate(entries):
        slug = _slug(ref)
        while slug in used:
            slug += "_2"
        used.add(slug)
        out.append(
            MapMod(
                id=slug,
                category=category.get(ref, UNSORTED),
                label=wordings[0],
                pattern=_fragment_for(wordings[0], index, pool, avoid),
            )
        )
    out.sort(key=lambda m: (CATEGORIES.index(m.category) if m.category in CATEGORIES else 99, m.label))
    return out


def _inherited_categories(entries: list[tuple[str, list[str]]]) -> dict[str, str]:
    """Category per modifier, taken from the curated fragment that matched it."""
    found: dict[str, str] = {}
    for curated in _CURATED_MODS:
        if curated.numeric:
            continue
        try:
            pattern = re.compile(curated.pattern)
        except re.error:
            continue
        for ref, wordings in entries:
            if ref not in found and any(pattern.search(w) for w in wordings):
                found[ref] = curated.category
    return found


_mods_cache: list[MapMod] | None = None


def all_mods() -> list[MapMod]:
    """Every option the tab offers: the map's property lines, then its mods.

    Falls back to the curated list when the game data is unavailable -- an
    offer of fifteen grouped options is a great deal better than an empty
    tab, and the data is only missing on a build that lost its data folder.
    """
    global _mods_cache
    if _mods_cache is not None:
        return _mods_cache
    try:
        from .trade import gamedata

        data = gamedata.load()
    except Exception:  # noqa: BLE001 - the tab must open with or without it
        logger.debug("map mod data unavailable", exc_info=True)
        data = None
    generated = _generate(data) if data is not None else []
    if not generated:
        logger.warning("게임 데이터가 없어 기존 지도 옵션 목록을 사용합니다")
        _mods_cache = list(_CURATED_MODS)
        return _mods_cache
    # The property lines first: they are the numeric ones, they are on every
    # map, and they are what a "good map" filter is mostly made of.
    _mods_cache = [m for m in _CURATED_MODS if m.numeric] + generated
    return _mods_cache


def categories() -> list[str]:
    """The categories actually present, in the order the list uses."""
    present = {mod.category for mod in all_mods()}
    return [c for c in CATEGORIES if c in present] + (
        [UNSORTED] if UNSORTED in present else []
    )


_legacy_cache: dict[str, list[str]] | None = None


def legacy_ids() -> dict[str, list[str]]:
    """Old grouped option ids mapped onto the individual ones that replaced them.

    A saved preset stores option ids, and the grouped list they were saved
    against is gone. Rather than silently dropping them -- which would turn
    someone's "위험 옵션 제외" filter into a shorter one that quietly lets the
    dangerous maps through -- each old id resolves to every modifier its
    fragment used to catch. That is the same set of maps it filtered before,
    now as separate ticks the user can thin out.
    """
    global _legacy_cache
    if _legacy_cache is not None:
        return _legacy_cache
    generated = [m for m in all_mods() if not m.numeric]
    found: dict[str, list[str]] = {}
    for curated in _CURATED_MODS:
        if curated.numeric:
            continue  # property lines kept their id
        try:
            pattern = re.compile(curated.pattern)
        except re.error:
            continue
        found[curated.id] = [m.id for m in generated if pattern.search(m.label)]
    _legacy_cache = found
    return _legacy_cache


def by_id() -> dict[str, MapMod]:
    """Options keyed by id. A function, not a module global, because code
    *inside* this module cannot reach the lazy ``BY_ID`` attribute -- module
    __getattr__ only answers lookups from outside."""
    return {mod.id: mod for mod in all_mods()}


def __getattr__(name: str):
    """``MAP_MODS`` and ``BY_ID`` on demand.

    Module-level rather than eager because building them reads several
    megabytes of game data, and importing this module must stay cheap -- it
    is imported on the hotkey path, where the price of a 200ms load would be
    paid by whichever keypress happened to be first.
    """
    if name == "MAP_MODS":
        return all_mods()
    if name == "BY_ID":
        return by_id()
    raise AttributeError(name)


# ---------------------------------------------------------------------------
# Checking a fragment against what a map can actually roll
# ---------------------------------------------------------------------------
# The fragments above are short by necessity and therefore wrong in two ways
# that reading them cannot catch: one that matches no real mod filters out
# every map, and one that matches too much throws away the maps worth
# running. Both look identical from the outside -- a list that came back
# shorter than expected.
#
# The game's own data settles it. Every modifier a map can carry is marked
# in it (see :meth:`GameData.area_mods`), with the text the client prints, so
# a fragment can simply be run against all of them. That is what the 맵모드
# tab shows next to each option, and what ``tools/check_map_mods.py`` reports
# in bulk.
#
# Rechecked whenever the game data is refreshed rather than recorded here,
# because a wording the game changes is exactly the case a hand-kept note
# would get wrong.
_coverage: dict[str, list[str]] | None = None


def coverage() -> dict[str, list[str]]:
    """Which real map modifiers each fragment catches, keyed by mod id.

    An empty list means the fragment matches nothing the game can put on a
    map -- either the wording moved or the mod no longer exists. A fragment
    for a *property* line (one with ``numeric``) is not a modifier at all and
    is reported as covered by definition; the client prints those on every
    map and they are checked by the threshold, not by the fragment.

    Answers ``{}`` when the game data is unavailable, which the UI reads as
    "nothing to say" rather than as "every option is broken".
    """
    global _coverage
    if _coverage is not None:
        return _coverage
    try:
        from .trade import gamedata

        data = gamedata.load()
    except Exception:  # noqa: BLE001 - a missing data file must not break the tab
        logger.debug("map-mod coverage unavailable", exc_info=True)
        data = None
    if data is None:
        return {}

    # "#" is where the data writes a rolled number; on the item it is a
    # digit, and a fragment may legitimately anchor just past one.
    corpus = [
        (ref, [w.replace("#", "7") for w in wordings])
        for ref, wordings in data.area_mods()
    ]
    found: dict[str, list[str]] = {}
    for mod in all_mods():
        if mod.numeric:
            found[mod.id] = ["(지도 속성 줄)"]
            continue
        try:
            pattern = re.compile(mod.pattern)
        except re.error:
            found[mod.id] = []
            continue
        found[mod.id] = [
            ref for ref, wordings in corpus if any(pattern.search(w) for w in wordings)
        ]
    _coverage = found
    return _coverage

# Percentages on a map do not reach four figures, and assuming they do not
# keeps the generated threshold short enough to be worth having.
_MAX_DIGITS = 3


def at_least(value: int) -> str:
    """A regex matching any integer from *value* up to three digits.

    Needed because the search has no idea what a number is -- it matches
    text -- so "수량 100% 이상" has to be spelled out as the shapes a
    qualifying number can take: 100-999 is any three digits, 80 or more is
    "8 or 9 then any digit, or any three digits".

    Built rather than written out because the useful thresholds are whatever
    the user types, and because getting the boundary wrong by one is both
    easy and invisible -- a filter that quietly passes 79% looks exactly like
    one that works.
    """
    value = int(value)
    if value <= 0:
        return ""  # "0 이상" is every map -- no condition at all
    low = str(value)
    if len(low) > _MAX_DIGITS:
        return low  # beyond what a map prints; match it literally
    parts: list[str] = []

    # Numbers with more digits than the threshold are all larger than it.
    for length in range(len(low) + 1, _MAX_DIGITS + 1):
        parts.append(r"\d" * length)

    # len > 1 matters: 10 is the smallest two-digit number, but the smallest
    # *one*-digit number is 0, not 1 -- so this shortcut would let 0 through
    # a threshold of 1.
    if len(low) > 1 and low == "1" + "0" * (len(low) - 1):
        # A round power of ten is the smallest number of its length, so every
        # number of that length qualifies. Worth its own case because 100 is
        # the threshold people actually type, and the general construction
        # below spells it out as 10\d|1[1-9]\d|[2-9]\d\d -- 23 characters
        # against a 50-character budget, for what is just \d\d\d.
        parts.append(r"\d" * len(low))
    else:
        # Same digit count: fix a prefix, let one position climb, free the
        # rest. For 80 that gives "8[0-9]" (80-89) and "[9-9]\d" (90-99).
        for index in range(len(low) - 1, -1, -1):
            digit = int(low[index])
            head = low[:index]
            if index == len(low) - 1:
                parts.append(f"{head}[{digit}-9]")
            elif digit < 9:
                parts.append(f"{head}[{digit + 1}-9]" + r"\d" * (len(low) - index - 1))

    return "|".join(_tidy(part) for part in parts)


def _tidy(part: str) -> str:
    """Shorten the classes the construction above leaves behind.

    ``[0-9]`` is ``\\d`` and ``[9-9]`` is ``9``; both turn up on every
    threshold ending in 0 or starting a run at 9, and the difference is four
    characters out of a fifty-character budget.
    """
    return part.replace("[0-9]", r"\d").replace("[9-9]", "9")


def fragment_for(mod_id: str, threshold: str | int = "") -> str:
    """One mod's search text, sharpened by a threshold where it has one.

    Without a threshold this is the plain fragment -- "does the map have this
    at all". With one it becomes the mod's own prefix followed by the shapes
    a qualifying number can take, so "아이템 수량: +107%" is tested as a
    number rather than as the words around it.
    """
    mod = by_id().get(mod_id)
    if mod is None:
        return ""
    try:
        wanted = int(str(threshold).strip() or 0)
    except ValueError:
        wanted = 0  # mid-edit ("1", "1x"); fall back to plain presence
    if not mod.numeric or wanted <= 0:
        return mod.pattern
    return f"{mod.numeric}({at_least(wanted)})"


def build_pattern(
    mod_ids: list[str],
    mode: str = EXCLUDE,
    extra: str = "",
    quote: bool = True,
    thresholds: dict[str, str] | None = None,
) -> str:
    """The search string for a set of ticked mods.

    Three shapes, because "정교한 조건" splits three ways in practice:

    * 제외 -- ``"!반사|관통"``. Alternation under the game's own ``!``: throw
      the map away if it has *any* of these. One bad mod is enough.
    * 포함 (하나라도) -- ``"수량: \\+(\\d\\d\\d)|무리 규모"``. Same alternation
      without the negation: keep the map if it has any of them.
    * 포함 (모두) -- ``"수량: \\+(\\d\\d\\d)" "무리 규모"``. Separate quoted
      terms. The game ANDs terms separated by spaces, which is the only way
      to ask for a map that is *both* high quantity and high pack size --
      and the reason quoting each term is not optional here: unquoted, every
      space inside a fragment would start a term of its own.

    ``thresholds`` maps a mod id to a minimum, applied only to the mods that
    carry a number (see ``MapMod.numeric``).
    """
    thresholds = thresholds or {}
    fragments = [
        fragment_for(mod_id, thresholds.get(mod_id, ""))
        for mod_id in mod_ids
        if mod_id in by_id()
    ]
    fragments += [part.strip() for part in extra.split(",") if part.strip()]
    # Deduplicated in place: two ticked mods can share a fragment (they are
    # picked for being distinctive, not for being unique), and a repeated
    # alternative is pure waste against a 50-character budget.
    seen: list[str] = []
    for fragment in fragments:
        if fragment and fragment not in seen:
            seen.append(fragment)
    if not seen:
        return ""

    if mode == INCLUDE_ALL:
        # Always quoted, whatever `quote` says: an unquoted AND is a
        # contradiction in terms, since the spaces joining the terms are the
        # very thing that would split the fragments up.
        return " ".join(f'"{fragment}"' for fragment in seen)

    body = "|".join(seen)
    if mode == EXCLUDE:
        body = "!" + body
    return f'"{body}"' if quote else body


# ---------------------------------------------------------------------------
# Checking a pattern against a real item
# ---------------------------------------------------------------------------
# The four kinds of line in a copied map, because *which* kind a fragment
# landed on is the whole answer to "why did this match?". Only MOD is the
# item actually having something; the other three are decoration a short
# fragment can hit by accident.
MOD_LINE, HEADER_LINE, REMINDER_LINE, PROPERTY_LINE = "속성", "구분", "설명", "정보"

_PROPERTY_KEYS = (
    "아이템 종류", "아이템 희귀도", "아이템 수량", "아이템 레벨", "몬스터 레벨",
    "몬스터 무리 규모", "지도 더 많음", "갑충석 더 많음", "화폐 더 많음", "퀄리티",
)


def classify_line(line: str) -> str:
    """Which kind of line this is, by the shape the game prints it in."""
    text = line.strip()
    if text.startswith("{"):
        return HEADER_LINE       # { 접두어 속성 부여 "이글거리는" — 물리, 카오스 }
    if text.startswith("("):
        return REMINDER_LINE     # (취약성은 받는 물리 피해를 15% 증가시키고...)
    if any(text.startswith(f"{key}:") for key in _PROPERTY_KEYS):
        return PROPERTY_LINE     # 몬스터 레벨: 83
    return MOD_LINE


def _top_level_split(body: str) -> list[str]:
    """Split on ``|``, but only where it actually separates alternatives.

    A threshold expands to ``수량: \\+(\\d\\d\\d|5\\d|[6-9]\\d)`` -- three
    alternatives *inside* a group that together mean one condition. Splitting
    that on every ``|`` produces ``수량: \\+(\\d\\d\\d``, ``5\\d`` and
    ``[6-9]\\d)``: fragments that are not valid regexes, do not correspond to
    anything the user ticked, and match wildly more than the real condition
    does -- a bare ``5\\d`` hits any two-digit number on the map.
    """
    parts: list[str] = []
    current = ""
    depth = 0
    in_class = False
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            current += body[index:index + 2]  # an escape swallows its partner
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "|" and depth == 0:
            parts.append(current)
            current = ""
            index += 1
            continue
        current += char
        index += 1
    parts.append(current)
    return [part for part in parts if part]


def split_pattern(pattern: str) -> tuple[str, list[str]]:
    """``(mode, conditions)`` back out of a finished pattern string.

    Works on a hand-edited pattern as well as a generated one, which is the
    point: the box is editable, and a check that only understood what this
    module produced would be useless on exactly the patterns worth checking.
    """
    import re as _re

    text = pattern.strip()
    quoted = _re.findall(r'"([^"]*)"', text)
    if len(quoted) > 1:
        # Several quoted terms side by side is the game's AND.
        return INCLUDE_ALL, [term for term in quoted if term]

    body = quoted[0] if quoted else text
    mode = INCLUDE
    if body.startswith("!"):
        mode, body = EXCLUDE, body[1:]
    return mode, _top_level_split(body)


def describe_matches(pattern: str, item_text: str) -> tuple[bool, list[tuple[str, list[tuple[str, str]]]]]:
    """``(matched, [(fragment, [(kind, line), ...]), ...])`` for one item.

    *matched* is whether the game's search would consider this item a hit.
    For 포함(하나라도) and 제외 that is any condition landing -- one bad mod
    is enough to throw a map away. For 포함(모두) it is every condition
    landing, since the game ANDs terms separated by spaces. Each condition is
    reported with every line it hit and what kind of line that was, so a
    surprising result explains itself.
    """
    import re as _re

    mode, conditions = split_pattern(pattern)
    lines = [line for line in item_text.splitlines() if line.strip()]
    report: list[tuple[str, list[tuple[str, str]]]] = []
    results: list[bool] = []
    for condition in conditions:
        try:
            hits = [
                (classify_line(line), line.strip())
                for line in lines
                if _re.search(condition, line)
            ]
        except _re.error:
            # An unfinished hand-edit ("반사|" mid-typing, a stray bracket).
            # Reported as its own kind of answer rather than raised: the user
            # is looking at the box it came from.
            report.append((condition, [("오류", "정규식으로 읽을 수 없는 조건입니다")]))
            results.append(False)
            continue
        results.append(bool(hits))
        report.append((condition, hits))

    if not results:
        return False, report
    matched = all(results) if mode == INCLUDE_ALL else any(results)
    return matched, report

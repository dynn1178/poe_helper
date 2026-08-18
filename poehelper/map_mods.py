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
"""
from __future__ import annotations

from dataclasses import dataclass

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


DANGER = "위험 (회피)"
MONSTER = "몬스터 강화"
CURSE = "저주 / 상태 이상"
REWARD = "보상"
CONTENT = "콘텐츠"

CATEGORIES = [DANGER, MONSTER, CURSE, REWARD, CONTENT]

MAP_MODS: list[MapMod] = [
    # ---- 위험: the mods that kill builds rather than slow them down --------
    MapMod("reflect", DANGER, "피해 반사 (물리 / 원소 / 사술)", "반사"),
    MapMod("penetration", DANGER, "몬스터 피해가 원소 저항 관통", "관통"),
    MapMod("no_regen", DANGER, "생명력·마나·에너지 보호막 재생 불가", "재생"),
    MapMod("no_leech", DANGER, "몬스터 생명력·마나 흡수 불가", "흡수"),
    MapMod("max_res", DANGER, "플레이어 저항 최대치 감소", "최대치"),
    MapMod("less_recovery", DANGER, "생명력·에너지 보호막 회복 속도 감폭", "회복"),
    MapMod("flask_charges", DANGER, "플라스크 충전량 감소", "충전량"),
    MapMod("less_armour", DANGER, "플레이어 방어도 감폭", "방어도"),
    MapMod("less_evasion", DANGER, "플레이어 회피 감폭 / 회피 불가", "회피"),
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
    MapMod("monster_speed", MONSTER, "몬스터 이동·공격·시전 속도 증가", "속도"),
    MapMod("monster_life", MONSTER, "몬스터 생명력 증폭", "생명력"),
    MapMod("monster_damage", MONSTER, "몬스터·보스가 주는 피해 증폭", "주는 피해"),
    MapMod("monster_extra_ele", MONSTER, "몬스터가 추가 원소 피해로 가함", "속성으로"),
    MapMod("monster_chaos", MONSTER, "몬스터가 추가 카오스 피해 획득", "카오스"),
    MapMod("monster_crit", MONSTER, "몬스터 치명타 확률 / 피해 배율 증가", "배율"),
    # "몬스터가 20초마다 격분 충전 3개 획득" -- the timer is what makes this
    # line unmistakable; 충전 alone also catches the flask mods.
    MapMod("monster_charges", MONSTER, "몬스터가 주기적으로 충전 획득", "20초마다"),
    MapMod("monster_ailments", MONSTER, "몬스터가 명중 시 상태 이상 유발", "명중 시"),
    MapMod("monster_avoid", MONSTER, "몬스터가 상태 이상 긴급회피", "긴급회피"),
    MapMod("monster_res", MONSTER, "몬스터 저항 증가", "저항 +"),
    MapMod("monster_pdr", MONSTER, "몬스터 물리 피해 감소", "피해 감소"),
    MapMod("monster_hexproof", MONSTER, "몬스터가 사술 방지 / 사술 반사", "사술"),
    MapMod("monster_immune", MONSTER, "몬스터 기절·도발 면역", "면역"),
    MapMod("monster_chain", MONSTER, "몬스터의 스킬 추가 연쇄", "연쇄"),
    MapMod("monster_es", MONSTER, "몬스터가 추가 에너지 보호막 획득", "보호막 최대치"),
    MapMod("monster_level", MONSTER, "지역 몬스터 레벨 증가", "몬스터 레벨"),
    MapMod("magic_monsters", MONSTER, "마법 몬스터 증가", "마법 몬스터"),
    MapMod("rare_monsters", MONSTER, "희귀 몬스터 증가 / 속성 추가", "희귀 몬스터"),
    MapMod("boss", MONSTER, "지도 보스 강화", "보스"),

    # ---- 저주 / 상태 이상 --------------------------------------------------
    MapMod("curse_any", CURSE, "저주 계열 전체 (추가 저주 포함)", "저주"),
    MapMod("curse_weakness", CURSE, "원소 약화 / 쇠약화", "약화"),
    MapMod("curse_vulnerability", CURSE, "취약성", "취약"),
    MapMod("curse_temporal", CURSE, "시간의 사슬", "사슬"),
    MapMod("ground", CURSE, "지대 (용암·얼음·감전·훼손)", "지대"),

    # ---- 보상 --------------------------------------------------------------
    MapMod("quantity", REWARD, "아이템 수량 증가", "수량"),
    MapMod("rarity", REWARD, "아이템 희귀도 증가", "희귀도"),
    MapMod("pack_size", REWARD, "무리 규모 증가", "규모"),
    MapMod("scarab", REWARD, "갑충석", "갑충석"),
    MapMod("currency", REWARD, "화폐", "화폐"),
    MapMod("divination", REWARD, "점술 카드", "점술"),

    # ---- 콘텐츠 ------------------------------------------------------------
    MapMod("beyond", CONTENT, "이계", "이계"),
    MapMod("breach", CONTENT, "균열", "균열"),
    MapMod("legion", CONTENT, "군단", "군단"),
    MapMod("abyss", CONTENT, "심연", "심연"),
    MapMod("essence", CONTENT, "에센스", "에센스"),
    MapMod("harbinger", CONTENT, "선구자", "선구자"),
    MapMod("delirium", CONTENT, "환영", "환영"),
    MapMod("ritual", CONTENT, "의식", "의식"),
    MapMod("expedition", CONTENT, "탐험", "탐험"),
    MapMod("blight", CONTENT, "역병", "역병"),
    MapMod("harvest", CONTENT, "수확", "수확"),
    MapMod("ultimatum", CONTENT, "결전", "결전"),
    MapMod("shrine", CONTENT, "성소", "성소"),
    MapMod("strongbox", CONTENT, "금고", "금고"),
    MapMod("vaal", CONTENT, "바알 부가 지역", "바알"),
    MapMod("torment", CONTENT, "고통받는 혼백", "혼백"),
    MapMod("beast", CONTENT, "야수", "야수"),
]

BY_ID = {mod.id: mod for mod in MAP_MODS}

INCLUDE, EXCLUDE = "include", "exclude"


def build_pattern(
    mod_ids: list[str],
    mode: str = EXCLUDE,
    extra: str = "",
    quote: bool = True,
) -> str:
    """The search string for a set of ticked mods.

    Alternation, because "any one of these" is what both modes want: a map
    is worth throwing away if it has *any* of the mods you cannot run, and
    worth keeping if it has *any* of the ones you are farming.

    ``!`` in front is the game's own negation, so 제외 is one character of
    difference rather than a different construction.

    Quoting keeps the whole thing as a single term. Without it the game
    splits on spaces and silently turns one filter into several -- which
    matters for the handful of fragments that contain a space, and costs
    nothing for the ones that don't.
    """
    fragments = [BY_ID[mod_id].pattern for mod_id in mod_ids if mod_id in BY_ID]
    fragments += [part.strip() for part in extra.split(",") if part.strip()]
    # Deduplicated in place: two ticked mods can share a fragment (they are
    # picked for being distinctive, not for being unique), and a repeated
    # alternative is pure waste against a 50-character budget.
    seen: list[str] = []
    for fragment in fragments:
        if fragment not in seen:
            seen.append(fragment)
    if not seen:
        return ""

    body = "|".join(seen)
    if mode == EXCLUDE:
        body = "!" + body
    return f'"{body}"' if quote else body

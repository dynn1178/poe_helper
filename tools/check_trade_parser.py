"""Put a set of representative items through the parser and report what it made of them.

Run after refreshing the game data, or after touching the parser::

    python tools/check_trade_parser.py

Each sample below is written in the shape the Korean client uses, with base
types, unique names and modifier wordings taken from the game's own data
rather than invented -- the rolls on them are made up, the words are not. One
covers advanced mod descriptions on, one covers them off, and the rest cover
the item kinds whose searches are built differently: a unique, a card, bulk
currency, a gem, a synthesised base, a magic item whose name hides its base.

A sample is a failure when a mod it carries resolves to no trade id: the
window will still show that line, and the search will quietly ignore it.

``check_trade_search.py`` takes the same samples further and sends them to
the real site, which is the only way to find out that a query the site
accepts is not the query you meant.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from poehelper.trade import item as parser  # noqa: E402
from poehelper.trade import query  # noqa: E402

SAMPLES: dict[str, str] = {}

SAMPLES["희귀 마법봉 (고급 설명 켬)"] = """아이템 종류: 마법봉
아이템 희귀도: 희귀
혼백 첨탑
소집의 마법봉
--------
마법봉
퀄리티: +20% (augmented)
물리 피해: 34-62 (augmented)
치명타 확률: 7.00%
초당 공격 횟수: 1.50
--------
요구사항:
레벨: 72
지능: 188
--------
홈: B-B-B
--------
아이템 레벨: 85
--------
{ 고정 속성 부여 — 피해, 소환수 }
소환수가 주는 피해 29(26-30)% 증가
--------
{ 접두어 속성 부여 "엄벌가의" (등급: 1) — 소환수, 젬 }
모든 소환수 스킬 젬 레벨 +1
{ 접미어 속성 부여 "칼날의" (등급: 3) — 공격 }
공격 속도 12(10-13)% 증가
{ 접두어 속성 부여 "타오르는" (등급: 2) — 원소, 화염, 피해, 공격 }
화염 피해 47(40-50)~80(70-90) 추가
{ 접미어 속성 부여 "저항의" (등급: 4) — 저항, 원소, 화염 }
화염 저항 +35(30-38)%
"""

SAMPLES["희귀 갑옷 (고급 설명 끔)"] = """아이템 종류: 갑옷
아이템 희귀도: 희귀
공포의 보루
바알 예복
--------
에너지 보호막: 442
--------
요구사항:
레벨: 68
지능: 194
--------
홈: B-B-B-B-B-B
--------
아이템 레벨: 84
--------
생명력 최대치 +99
화염 저항 +45%
냉기 저항 +42%
공격 속도 5% 감소
에너지 보호막 최대치 12% 증가
--------
타락
"""

SAMPLES["고유 아이템"] = """아이템 종류: 목걸이
아이템 희귀도: 고유
카루이의 수호
비취 목걸이
--------
요구사항:
레벨: 60
--------
아이템 레벨: 82
--------
{ 고정 속성 부여 — 저항, 카오스 }
카오스 저항 +23(20-30)%
--------
{ 고유 속성 부여 }
힘 +100(80-100)
생명력 최대치 +100(80-100)
--------
"우리 부족의 심장은 결코 멈추지 않는다."
"""

SAMPLES["점술 카드"] = """아이템 종류: 점술 카드
아이템 희귀도: 점술 카드
의사
--------
중첩 개수: 3/8
--------
헤드헌터
--------
"그는 언제나 정확한 처방을 내린다."
"""

SAMPLES["화폐"] = """아이템 종류: 스택 가능한 화폐
아이템 희귀도: 화폐
카오스 오브
--------
중첩 개수: 137/10
--------
무작위로 희귀 아이템의 속성을 다시 부여합니다
--------
우클릭한 다음 좌클릭으로 희귀 아이템에 사용하십시오.
"""

SAMPLES["스킬 젬"] = """아이템 종류: 스킬 젬
아이템 희귀도: 젬
지옥불 맹타
--------
바알, 주문, 화염, 지속 피해, 집중
레벨: 20
마나 소모: 20
시전 시간: 0.50초
치명타 확률: 6.00%
효과 범위: 22
--------
요구사항:
레벨: 70
지능: 155
--------
전방으로 화염 광선을 발사합니다.
--------
경험치: 1000000/1000000
--------
타락
"""

SAMPLES["결합된 반지"] = """아이템 종류: 반지
아이템 희귀도: 희귀
파멸의 소용돌이
결합된 토파즈 반지
--------
요구사항:
레벨: 66
--------
아이템 레벨: 84
--------
{ 고정 속성 부여 — 저항, 원소, 번개 }
번개 저항 +30(25-30)%
--------
{ 접두어 속성 부여 "정력의" (등급: 2) — 생명력 }
생명력 최대치 +75(70-79)
--------
결합된 아이템
"""

SAMPLES["마법 플라스크"] = """아이템 종류: 유틸리티 플라스크
아이템 희귀도: 마법
곰의 화강암 플라스크 - 누그러뜨림
--------
퀄리티: +20% (augmented)
--------
요구사항:
레벨: 27
--------
아이템 레벨: 83
--------
{ 접미어 속성 부여 "누그러뜨림의" (등급: 1) }
효과를 받는 동안 방어도 60(56-60)% 증가
--------
마시려면 우클릭하십시오. 충전량이 충분해야 사용할 수 있습니다.
"""

SAMPLES["쉐이퍼 투구"] = """아이템 종류: 투구
아이템 희귀도: 희귀
공포의 관
자만의 관
--------
에너지 보호막: 132
--------
요구사항:
레벨: 69
지능: 154
--------
홈: B-B-B-B
--------
아이템 레벨: 86
--------
{ 접두어 속성 부여 "정력의" (등급: 2) — 생명력 }
생명력 최대치 +95(90-99)
{ 접미어 속성 부여 "저항의" (등급: 3) — 저항, 원소, 화염 }
화염 저항 +40(36-41)%
{ 접미어 속성 부여 "제련의" (등급: 1) — 방어 }
에너지 보호막 최대치 32(30-34)% 증가
--------
쉐이퍼 아이템
"""


def report() -> int:
    problems = 0
    for label, text in SAMPLES.items():
        item = parser.parse(text)
        print(f"\n=== {label}")
        if item is None:
            print("   파싱 실패")
            problems += 1
            continue
        named = f" / {item.name}" if item.name else ""
        print(
            f"   {item.rarity} / {item.base}{named}"
            f"  분류={item.category or '-'} -> {item.trade_category or '-'}"
        )
        bits = []
        if item.item_level:
            bits.append(f"ilvl {item.item_level}")
        if item.quality:
            bits.append(f"퀄 {item.quality}%")
        if item.total_dps:
            bits.append(f"DPS {item.total_dps}")
        if item.energy_shield:
            bits.append(f"ES {item.energy_shield}")
        if item.links:
            bits.append(f"{item.links}링크")
        if item.stack_size:
            bits.append(f"{item.stack_size}개")
        if item.gem_level:
            bits.append(f"젬 레벨 {item.gem_level}")
        if item.exchange_tag:
            bits.append(f"교환태그 {item.exchange_tag}")
        if item.influences:
            bits.append("영향: " + " ".join(sorted(item.influences)))
        if item.flags:
            bits.append(" ".join(sorted(item.flags)))
        print("   " + " · ".join(bits))

        for mod in item.mods:
            mark = " " if mod.ids else "✗"
            first = mod.text.splitlines()[0]
            print(
                f"   {mark} [{mod.kind:9}{mod.affix:7}"
                f"{('T' + str(mod.tier)) if mod.tier else '  ':4}]"
                f" {first[:44]:46} {mod.ids}"
            )
            if not mod.ids:
                problems += 1
        for line in item.unknown:
            print(f"   ? {line[:60]}")
        pseudo = query.pseudo_filters(item)
        if pseudo:
            print("   유사: " + ", ".join(f"{p.label}={p.value:g}" for p in pseudo))
    print(f"\n검색 불가 속성: {problems}건")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(report())

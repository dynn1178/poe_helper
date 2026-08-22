# 서드파티 고지

## Awakened PoE Trade

`data/trade/<언어>/` 아래의 게임 데이터 파일과, 그 파일을 읽는
`poehelper/trade/gamedata.py`의 매칭 알고리즘은
[Awakened PoE Trade](https://github.com/SnosMe/awakened-poe-trade)에서
가져왔습니다. MIT 라이선스이며 전문은 아래와 같습니다.

가져온 것:

| 항목 | 위치 |
|---|---|
| `stats.ndjson` — 게임의 모든 수식어와 거래소 stat id | `data/trade/ko/` |
| `items.ndjson` — 기본 아이템·고유·젬·카드 | `data/trade/ko/` |
| `client_strings.js` → `client_strings.json` — 클라이언트 출력 문자열 | `data/trade/ko/` |
| 자리표시자 조합 매칭(`_combinations`)과 `select`/`*-merge` 해석 규칙 | `poehelper/trade/gamedata.py` |

원본 데이터는 GGG의 게임 파일에서 생성된 것입니다. `tools/update_trade_data.py`가
위 저장소의 `renderer/public/data/`에서 최신본을 내려받고,
`poehelper/trade/gamedata.py`의 `refresh()`가 실행 중에 주 단위로 갱신합니다.

```
MIT License

Copyright (c) 2020 Alexander Drozdov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Path of Exile

Path of Exile은 Grinding Gear Games의 상표입니다. 이 프로그램은 GGG가 만들거나
보증한 것이 아니며, 게임이 클립보드에 직접 기록하는 텍스트와 GGG가 공개한
거래소 API만을 사용합니다.

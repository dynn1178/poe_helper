"""Send the queries the parser builds to the real trade site and report back.

    python tools/check_trade_search.py [리그이름]

The parser check (``check_trade_parser.py``) proves a mod resolved to a stat
id. This proves the *whole* query is one the site accepts and answers -- a
distinction that matters, because the failure this replaced was a search that
looked perfectly well formed and returned zero results forever.

It sends one search per sample plus a bulk exchange, so it is a handful of
requests: enough to be worth running after a change, few enough not to be
rude. The rate limiter in trade/api.py paces them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from poehelper.trade import item as parser  # noqa: E402
from poehelper.trade import query  # noqa: E402
from poehelper.trade.api import TradeAPI, TradeError  # noqa: E402
from tools.check_trade_parser import SAMPLES  # noqa: E402


class _Settings:
    """The slice of Config that TradeAPI reads, without loading the app."""

    def __init__(self, league: str):
        self.data = {"trade": {"realm": "kakao", "league": league}}


def _default_league(api: TradeAPI) -> str:
    permanent = {"Standard", "Hardcore", "Ruthless", "Hardcore Ruthless"}
    for entry in api.leagues():
        name = entry.get("id", "")
        if name and name not in permanent and "SSF" not in name:
            return name
    return "Standard"


def run(league: str = "") -> int:
    api = TradeAPI(_Settings(league))
    if not league:
        league = _default_league(api)
        api.config.data["trade"]["league"] = league
    print(f"리그: {league}\n")

    failures = 0
    for label, text in SAMPLES.items():
        item = parser.parse(text)
        if item is None:
            print(f"{label}: 파싱 실패")
            failures += 1
            continue

        if item.exchange_tag:
            try:
                result = api.exchange(item.exchange_tag)
            except TradeError as exc:
                print(f"{label}: 대량 거래 실패 -- {exc}")
                failures += 1
                continue
            print(f"{label}: 대량 거래 {result['total']}건")
            continue

        # The same two things the window ticks by default: the totals that
        # carry a value, and nothing else. Fewer filters than the window
        # would send, which is the point -- a query that fails here fails for
        # a reason in the query itself, not in what happened to be ticked.
        selected = [
            query.Selection(ids=[total.id], minimum=int(total.value * 0.9))
            for total in query.pseudo_filters(item)[:2]
        ]
        if not selected:
            selected = [
                query.Selection(mod=mod, minimum=query.bound_for(mod)[0])
                for mod in item.searchable_mods[:2]
                if mod.ids
            ]
        payload = query.build(api, item, selected, sort="price")
        try:
            result = api.search(payload)
        except TradeError as exc:
            print(f"{label}: 검색 거부 -- {exc}")
            print(json.dumps(payload, ensure_ascii=False, indent=1))
            failures += 1
            continue
        print(f"{label}: {result['total']}건")
        if not result["total"]:
            # Not a failure: a well-formed query for an item nobody is
            # selling is a correct answer. Worth showing, because a query
            # that is subtly wrong looks exactly like this one.
            print("   " + json.dumps(payload["query"], ensure_ascii=False)[:300])

    print(f"\n거부된 검색: {failures}건")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else ""))

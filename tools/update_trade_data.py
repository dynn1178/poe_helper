"""Refresh the bundled Path of Exile game data under ``data/trade/``.

The three files this downloads are what let the price check understand a
Korean item without asking the trade site to explain it:

``stats.ndjson``
    Every modifier in the game, as the *client* prints it, paired with the
    stat ids the trade search takes. Crucially it also carries the shapes the
    printed line can take that a naive text comparison cannot undo -- a mod
    printed as 감소 that the site indexes as 증가 with a negative value, a
    line whose roll is implied rather than printed ("항상 중독 유발" is 100%),
    and the longer wording the client uses when advanced mod descriptions are
    switched on.

``items.ndjson``
    Base types, uniques, gems and cards, each with the trade site's own
    category ("weapon.wand"), so an item can be pinned by *what it is* rather
    than only by the name printed on it.

``client_strings.js``
    The labels the client prints -- "아이템 희귀도: ", "타락", the shape of a
    ``{ ... }`` modifier header. Converted here to JSON so the app does not
    need a JavaScript parser at runtime.

All three come from Awakened PoE Trade (MIT), which generates them from the
game's own data files. Taking them from the repository rather than from an
installed copy of that program is deliberate: they are version-controlled
there, so this can be re-run whenever a league changes the game.

    python tools/update_trade_data.py            # ko, the Kakao client
    python tools/update_trade_data.py --lang en

The stat ids in these files were checked against the Kakao realm's own
``/api/trade/data/stats``: all 12,585 of them exist there, so the data is
usable as-is on the Korean realm. What is *not* portable is the set of
filter ids -- Kakao lags the international realm and has filters it does not
(and vice versa) -- which is why nothing here touches filters, and
trade/api.py asks the realm itself what it accepts.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "SnosMe/awakened-poe-trade"
BRANCH = "master"
BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/renderer/public/data"

FILES = ("stats.ndjson", "items.ndjson", "client_strings.js")
ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "trade"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "KuanPoeHelper-data-update/1.0"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


# ---------------------------------------------------------------------------
# client_strings.js -> JSON
# ---------------------------------------------------------------------------
# The file is an ES module exporting one object literal. Its values are only
# ever a quoted string, a regular expression literal, or an array of quoted
# strings, which is a small enough grammar to read directly -- and a great
# deal less trouble than shelling out to node just to print JSON.
_KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*")


class _Reader:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def skip_blank(self) -> None:
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char in " \t\r\n,":
                self.pos += 1
            elif self.text.startswith("//", self.pos):
                end = self.text.find("\n", self.pos)
                self.pos = len(self.text) if end < 0 else end
            elif self.text.startswith("/*", self.pos):
                end = self.text.find("*/", self.pos)
                self.pos = len(self.text) if end < 0 else end + 2
            else:
                return

    def string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        out = []
        while self.text[self.pos] != quote:
            char = self.text[self.pos]
            if char == "\\":
                self.pos += 1
                escaped = self.text[self.pos]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(escaped, escaped))
            else:
                out.append(char)
            self.pos += 1
        self.pos += 1
        return "".join(out)

    def regex(self) -> dict:
        """A ``/.../flags`` literal, translated to Python's dialect.

        Only two things actually differ for the patterns in this file: named
        groups spell their name differently, and JavaScript's flags are
        letters after the closing slash rather than inline. A character class
        can contain an unescaped ``/``, so the scan has to track them.
        """
        self.pos += 1
        out = []
        in_class = False
        while True:
            char = self.text[self.pos]
            if char == "\\":
                out.append(self.text[self.pos:self.pos + 2])
                self.pos += 2
                continue
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                self.pos += 1
                break
            out.append(char)
            self.pos += 1
        flags = ""
        while self.pos < len(self.text) and self.text[self.pos] in "gimsuy":
            flags += self.text[self.pos]
            self.pos += 1
        pattern = "".join(out).replace("(?<", "(?P<").replace("(?P<=", "(?<=").replace(
            "(?P<!", "(?<!"
        )
        return {"re": pattern, "flags": "i" if "i" in flags else ""}

    def array(self) -> list:
        self.pos += 1
        out = []
        while True:
            self.skip_blank()
            if self.text[self.pos] == "]":
                self.pos += 1
                return out
            out.append(self.value())

    def value(self):
        self.skip_blank()
        char = self.text[self.pos]
        if char in "'\"`":
            return self.string()
        if char == "/":
            return self.regex()
        if char == "[":
            return self.array()
        raise ValueError(f"unexpected {char!r} at {self.pos}")


def convert_client_strings(source: str) -> dict:
    start = source.index("{", source.index("export default"))
    reader = _Reader(source)
    reader.pos = start + 1
    out: dict = {}
    while True:
        reader.skip_blank()
        if reader.text[reader.pos] == "}":
            return out
        match = _KEY_RE.match(reader.text, reader.pos)
        if not match:
            raise ValueError(f"expected a key at {reader.pos}")
        reader.pos = match.end()
        out[match.group(1)] = reader.value()


# ---------------------------------------------------------------------------
def update(language: str) -> None:
    directory = TARGET / language
    directory.mkdir(parents=True, exist_ok=True)
    written = {}
    for name in FILES:
        raw = fetch(f"{BASE}/{language}/{name}")
        if name == "client_strings.js":
            strings = convert_client_strings(raw.decode("utf-8"))
            path = directory / "client_strings.json"
            path.write_text(
                json.dumps(strings, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            written[path.name] = f"{len(strings)} keys"
        else:
            path = directory / name
            path.write_bytes(raw)
            written[name] = f"{len(raw) / 1e6:.1f} MB"
        print(f"  {path.name}: {written[path.name]}")

    (TARGET / "SOURCE.json").write_text(
        json.dumps(
            {
                "source": f"https://github.com/{REPO}",
                "license": "MIT",
                "branch": BRANCH,
                "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "languages": sorted(
                    p.name for p in TARGET.iterdir() if p.is_dir()
                ),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", default="ko", help="ko, en, ru or cmn-Hant")
    args = parser.parse_args()
    print(f"{args.lang} 게임 데이터를 내려받는 중…")
    update(args.lang)
    print("완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

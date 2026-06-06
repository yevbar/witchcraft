"""build_oracle_corpus.py — distill MTGJSON AllPrintings.json into a compact, deduplicated oracle
corpus (mtgjson/oracle_corpus.json), one entry per unique card name.

AllPrintings.json (~600MB) is bulk data, gitignored. Fetch it first:
    mkdir -p mtgjson && curl -L -o mtgjson/AllPrintings.json.gz \
        https://mtgjson.com/api/v5/AllPrintings.json.gz && gunzip -kf mtgjson/AllPrintings.json.gz
then run this to (re)generate the corpus the card pipeline reads.
"""

from __future__ import annotations

import json
from pathlib import Path

_MTG = Path(__file__).parent / "mtgjson"
_KEEP = ("name", "manaCost", "type", "types", "subtypes", "supertypes",
         "keywords", "power", "toughness", "text", "layout", "colorIdentity")
_SKIP_LAYOUT = {"token", "emblem", "art_series", "double_faced_token"}


def main() -> None:
    data = json.load(open(_MTG / "AllPrintings.json", encoding="utf-8"))["data"]
    seen: dict[str, dict] = {}
    for s in data.values():
        for c in s.get("cards", []):
            n = c.get("name")
            if not n or n in seen or c.get("layout") in _SKIP_LAYOUT:
                continue
            seen[n] = {k: c.get(k) for k in _KEEP}
    cards = list(seen.values())
    json.dump(cards, open(_MTG / "oracle_corpus.json", "w", encoding="utf-8"), ensure_ascii=False)
    with_text = sum(1 for c in cards if c.get("text"))
    print(f"wrote mtgjson/oracle_corpus.json ({len(cards)} unique cards, {with_text} with oracle text)")


if __name__ == "__main__":
    main()

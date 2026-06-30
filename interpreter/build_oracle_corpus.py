"""build_oracle_corpus.py — distill MTGJSON AllPrintings.json into a compact, deduplicated oracle
corpus (mtgjson/oracle_corpus.json), one entry per unique card name.

AllPrintings.json (~600MB) is bulk data, gitignored. Fetch it first:
    mkdir -p mtgjson && curl -L -o mtgjson/AllPrintings.json.gz \
        https://mtgjson.com/api/v5/AllPrintings.json.gz && gunzip -kf mtgjson/AllPrintings.json.gz
then run this to (re)generate the corpus the card pipeline reads.

AllPrintings.json is ~640MB; a plain json.load of it peaks multiple GB and OOMs a memory-thin box. We
ijson-STREAM the top-level "data" set map instead (one set object resident at a time), which holds the
build to ~130MB RSS. Card iteration order matches a json.load (file order of the set map, cards in order),
so the first-seen-wins dedup is unchanged.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
from pathlib import Path

import ijson

_MTG = Path(__file__).parent.parent / "mtgjson"  # repo root (module lives in interpreter/)
_KEEP = ("name", "manaCost", "type", "types", "subtypes", "supertypes",
         "keywords", "power", "toughness", "loyalty", "text", "layout", "colorIdentity")
_SKIP_LAYOUT = {"token", "emblem", "art_series", "double_faced_token"}
# §712 TRANSFORM cards store TWO card objects under one combined "Front // Back" name (side a/b). The front
# (side a) is the cast-able card; the BACK (side b) is the transformed permanent (e.g. Ral, Leyline Prodigy).
# We keep the front keyed by the combined name (with a `back` pointer) AND emit the back as its OWN entry
# keyed by its faceName, so the pipeline interprets the back-face permanent (its loyalty abilities, ETB, …).
_TRANSFORM = {"transform"}


def main() -> None:
    seen: dict[str, dict] = {}
    src = open(_MTG / "AllPrintings.json", "rb")
    for _code, s in ijson.kvitems(src, "data"):     # stream set objects: ~130MB RSS, not multi-GB
        for c in s.get("cards", []):
            n = c.get("name")
            if not n or c.get("layout") in _SKIP_LAYOUT:
                continue
            if c.get("layout") in _TRANSFORM and c.get("side") and c.get("side") != "a":
                # a transform BACK face -> its own entry keyed by faceName (its own slug). `front` links back.
                fn = c.get("faceName") or n
                if fn in seen:
                    continue
                entry = {k: c.get(k) for k in _KEEP}
                entry["name"] = fn                            # the back face's OWN name -> its own slug
                entry["face"] = "back"
                if " // " in n:
                    entry["front"] = n.split(" // ", 1)[0]
                seen[fn] = entry
                continue
            if n in seen:
                continue
            entry = {k: c.get(k) for k in _KEEP}
            if c.get("layout") in _TRANSFORM and " // " in n:    # the front records its back face's name
                entry["back"] = n.split(" // ", 1)[1]
            seen[n] = entry
    cards = list(seen.values())
    json.dump(cards, open(_MTG / "oracle_corpus.json", "w", encoding="utf-8"), ensure_ascii=False)
    with_text = sum(1 for c in cards if c.get("text"))
    backs = sum(1 for c in cards if c.get("face") == "back")
    print(f"wrote mtgjson/oracle_corpus.json ({len(cards)} unique cards, {with_text} with oracle text, "
          f"{backs} transform back faces)")


if __name__ == "__main__":
    main()

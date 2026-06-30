"""bridge_cards.py — measure how far the interpreted keyword map reaches into real cards.

The §701/§702 keyword DEFINITIONS (datalog/keyword_definitions.dl, interpreted from rules.txt)
form a keyword -> meaning map. A real card that bears a keyword expands through that map. This
script streams AllPrintings.json (MTGJSON), reads each unique card's normalized `keywords`, and
reports what fraction of keyword-bearing cards have EVERY keyword defined by our interpreted map
— i.e. cards whose keyword mechanics are already expandable from rules.txt alone. This is the
bridge from the rules phase to the cards phase; it holds no game logic, it just measures reach.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
import sys
from pathlib import Path

import ijson


def _defined_keywords() -> set[str]:
    """Keywords with a full substitution DEFINITION (keyword_means / keyword_represents) — strong:
    the keyword's mechanics expand to concrete text (e.g. investigate -> 'Create a Clue token.')."""
    dl = Path("datalog/keyword_definitions.dl").read_text()
    return (set(re.findall(r'keyword_means\("([^"]+)"', dl))
            | set(re.findall(r'keyword_represents\("([^"]+)"', dl)))


def _classified_keywords() -> set[str]:
    """Keywords at least CLASSIFIED by the §702 taxonomy (keyword_class) — covers the evergreen
    keywords (flying, trample, ...) the rules define functionally, which the engine handles by class."""
    dl = Path("datalog/keyword_taxonomy.dl").read_text()
    return set(re.findall(r'keyword_class\("([^"]+)"', dl))


def survey(limit_cards: int | None = None) -> dict:
    defined = _defined_keywords()
    classified = _classified_keywords() | defined
    seen: set[str] = set()
    total = with_kw = fully_def = fully_cls = 0
    kw_hits, examples = {}, []
    with open("AllPrintings.json", "rb") as f:
        for _set, setobj in ijson.kvitems(f, "data"):
            for c in setobj.get("cards", []):
                name = c.get("name")
                if not name or name in seen:
                    continue
                seen.add(name)
                total += 1
                kws = [k.lower() for k in c.get("keywords", []) or []]
                if not kws:
                    continue
                with_kw += 1
                if all(k in classified for k in kws):
                    fully_cls += 1
                if all(k in defined for k in kws):
                    fully_def += 1
                    for k in kws:
                        kw_hits[k] = kw_hits.get(k, 0) + 1
                    if len(examples) < 8:
                        examples.append((name, kws))
                if limit_cards and total >= limit_cards:
                    break
            else:
                continue
            break
    return {"defined_keywords": len(defined), "classified_keywords": len(classified),
            "unique_cards": total, "with_keywords": with_kw, "fully_defined": fully_def,
            "fully_classified": fully_cls, "top_keywords": sorted(kw_hits.items(), key=lambda x: -x[1])[:12],
            "examples": examples}


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    r = survey(limit)
    wk = r["with_keywords"] or 1
    print(f"interpreted keyword DEFINITIONS (keyword_means/represents): {r['defined_keywords']}")
    print(f"interpreted keyword CLASSIFICATIONS (taxonomy, incl. above): {r['classified_keywords']}")
    print(f"unique cards surveyed:           {r['unique_cards']}")
    print(f"  with >=1 keyword:              {r['with_keywords']}")
    print(f"  ALL keywords classified:       {r['fully_classified']}  ({100*r['fully_classified']//wk}% of keyword-bearing cards)")
    print(f"  ALL keywords fully defined:    {r['fully_defined']}  ({100*r['fully_defined']//wk}% — expand to concrete text)")
    print("\ntop fully-defined keywords (card count):")
    for k, n in r["top_keywords"]:
        print(f"  {n:6}  {k}")
    print("\nworked examples (card -> keywords, each expandable via keyword_means):")
    for name, kws in r["examples"]:
        print(f"  {name}: {', '.join(kws)}")


if __name__ == "__main__":
    main()

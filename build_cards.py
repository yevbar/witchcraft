"""build_cards.py — emit datalog/cards.dl: grounded facts for every interpretable oracle ability unit.

The card-side analogue of the build_*.py rules emitters. Each fact uses only rules-grounded relations
(card_keyword grounds in §702, card_mana_ability in §605/§107). Output is gitignored: unlike the rules
datalog (derived from the committed rules.txt), cards.dl derives from MTGJSON bulk data that isn't in
the repo, so it's regenerated locally, not committed.

Run `python3 build_oracle_corpus.py` first (needs mtgjson/AllPrintings.json).
"""

from __future__ import annotations

import collections
from pathlib import Path

import card_corpus
import ground
from dlgen import Program
from transpile_card import transpile_unit


def build() -> tuple[str, dict]:
    names: dict[str, str] = {}
    facts: list[str] = []
    seen = set()
    by_pattern = collections.Counter()
    for c in card_corpus.load_cards():
        cid = ground.slug(c["name"])
        emitted = False
        for seq, u in enumerate(card_corpus.units_of(c)):
            o = transpile_unit(u, {"id": cid, "card": c, "seq": seq})
            if not o:
                continue
            by_pattern[o.pattern] += 1
            emitted = True
            for f in o.facts:
                if f not in seen:
                    seen.add(f)
                    facts.append(f)
        if emitted:
            names[cid] = c["name"].replace('"', "'")

    p = Program()
    p.comment("cards.dl — grounded card-oracle facts, interpreted from MTGJSON oracle text. GENERATED.")
    p.comment("Every relation grounds in rules.txt: card_keyword -> §702; card_mana_ability -> §605/§107.")
    p.blank()
    p.decl("card_name", [("id", "symbol"), ("name", "symbol")])
    p.decl("card_keyword", [("card", "symbol"), ("keyword", "symbol")])
    p.decl("card_keyword_param", [("card", "symbol"), ("keyword", "symbol"), ("arg", "symbol")])
    p.decl("card_mana_ability", [("card", "symbol"), ("cost", "symbol")])
    p.decl("card_adds_mana", [("card", "symbol"), ("cost", "symbol"), ("produces", "symbol")])
    p.decl("ability", [("card", "symbol"), ("aid", "symbol"), ("kind", "symbol")])
    p.decl("ability_cost", [("card", "symbol"), ("aid", "symbol"), ("cost", "symbol")])
    p.decl("ability_trigger", [("card", "symbol"), ("aid", "symbol"), ("event", "symbol")])
    p.decl("ability_modifier", [("card", "symbol"), ("aid", "symbol"), ("modifier", "symbol")])
    p.decl("effect", [("card", "symbol"), ("aid", "symbol"), ("seq", "number"),
                      ("verb", "symbol"), ("amount", "symbol"), ("target", "symbol"),
                      ("extra", "symbol"), ("cond", "symbol")])
    p.decl("mode_option", [("card", "symbol"), ("aid", "symbol")])
    p.decl("card_modal", [("card", "symbol"), ("mode", "symbol")])
    p.decl("card_cant", [("card", "symbol"), ("action", "symbol")])
    p.decl("card_doesnt_untap", [("card", "symbol"), ("who", "symbol")])
    p.decl("card_attacks_each_combat", [("card", "symbol")])
    p.decl("card_enters_with_counters", [("card", "symbol"), ("kind", "symbol"), ("n", "symbol")])
    p.decl("card_enters_tapped", [("card", "symbol")])
    p.decl("card_etb_choose", [("card", "symbol"), ("what", "symbol")])
    p.decl("card_static_player", [("card", "symbol"), ("rule", "symbol")])
    p.blank()
    for cid, nm in sorted(names.items()):
        p.fact(f'card_name("{cid}", "{nm}")')
    p.blank()
    for f in facts:
        p.fact(f)
    p.blank()
    p.output("card_keyword", "card_keyword_param", "card_mana_ability", "card_adds_mana",
             "ability", "ability_cost", "ability_trigger", "ability_modifier", "effect", "mode_option",
             "card_modal",
             "card_cant", "card_doesnt_untap", "card_attacks_each_combat", "card_enters_with_counters",
             "card_enters_tapped", "card_etb_choose", "card_static_player")
    p.blank()
    p.comment("conformance — a grounded keyword the rules define (§702.9) on a known card")
    p.conformance(
        [("expect_kw", [("card", "symbol"), ("keyword", "symbol")])],
        [("kw", "expect_kw(C, K)", "miss", "card_keyword(C, K)")])
    p.fact('expect_kw("serra_angel", "flying")')
    return p.text(), {"cards_with_facts": len(names), "facts": len(facts), "by_pattern": dict(by_pattern)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    src, report = build()
    Path("datalog/cards.dl").write_text(src, encoding="utf-8")
    print(f"wrote datalog/cards.dl ({report['cards_with_facts']} cards, {report['facts']} facts, "
          f"by_pattern={report['by_pattern']})")


if __name__ == "__main__":
    main()

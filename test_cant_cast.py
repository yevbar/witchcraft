"""test_cant_cast.py — §601.3e static CASTING restriction ('<player-set> can't cast <spell-set>').

The largest non-combat sub-family of the restriction statics (~60 lines). The combat frames (_ns_cant)
key on combat verbs, so they decline 'cast'; the clause parses as `nscant` and previously ABSTAINED,
abstaining the whole card. `_ns_cast` (symmetric with `_ns_cant`, in the same nscant transformer) grounds
it as cant_cast(-, _target(player-set), slug(spell-set + qualifier)) — the qualifier AND any 'more than N
… each turn' LIMIT preserved in the slug (dropping a limit would invert it into a blanket prohibition).
Faithful-or-abstain: a compound non-cast action ('cast spells or play lands') or a non-player subject abstains.

Run: MTG_NO_SPACY=1 python3 test_cant_cast.py
"""
from __future__ import annotations

import card_corpus
import ground
from card_effects import parse_clause
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _clauses() -> None:
    for src, who, extra in [
        ("your opponents can't cast spells of the chosen color", "your_opponents", "spells_of_the_chosen_color"),
        ("your opponents can't cast spells with even mana values", "your_opponents", "spells_with_even_mana_values"),
        ("your opponents can't cast spells with the chosen name", "your_opponents", "spells_with_the_chosen_name"),
        ("players can't cast noncreature spells this turn", "players", "noncreature_spells_this_turn"),
        ("you can't cast spells this turn", "you", "spells_this_turn"),
    ]:
        e = parse_clause(src)
        check(f"{src[:42]!r} -> cant_cast({who}, {extra})",
              e is not None and e.verb == "cant_cast" and e.target == who and e.extra == extra)

    # a 'more than N … each turn' LIMIT is preserved whole (faithful — never collapsed to a blanket ban)
    e = parse_clause("each player can't cast more than one noncreature spell each turn")
    check("limit preserved: 'more than one noncreature spell each turn' rides the slug",
          e is not None and e.verb == "cant_cast" and e.target == "each_player"
          and e.extra == "more_than_one_noncreature_spell_each_turn")


def _abstains() -> None:
    # a compound NON-cast action is a conflation the slug can't represent -> abstain (not a lossy cant_cast)
    e = parse_clause("players can't cast spells or play lands with a name originally printed in the arabian nights")
    check("conflation 'cast spells or play lands' abstains", e is None or e.verb != "cant_cast")
    # a non-cast restriction (play lands) is NOT claimed by the cast frame (a later slice)
    e = parse_clause("players can't play lands")
    check("'can't play lands' not mis-grounded as cant_cast", e is None or e.verb != "cant_cast")
    # the combat path is untouched: '~ can't attack' still grounds via the combat template, not cant_cast
    e = parse_clause("~ can't attack")
    check("combat '~ can't attack' unchanged (cant_attack, not cant_cast)",
          e is not None and e.verb == "cant_attack")
    # a non-player subject doesn't cast -> the player-set guard abstains
    e = parse_clause("enchanted creature can't cast spells")
    check("non-player subject 'enchanted creature' abstains (only players cast)",
          e is None or e.verb != "cant_cast")


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    # real played casting-restriction cards that previously abstained; the qualifier is recorded faithfully
    for n, want_extra in [("Iona, Shield of Emeria", "spells_of_the_chosen_color"),
                          ("Gideon's Intervention", None),
                          ("Deafening Silence", "more_than_one_noncreature_spell_each_turn"),
                          ("Drannith Magistrate", "spells_from_anywhere_other_than_their_hands")]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        outs = [transpile_unit(u, {"id": cid, "card": c, "seq": i}) for i, u in enumerate(card_corpus.units_of(c))]
        check(f"{n} fully ingests (casting restriction grounds)", all(outs))
        if want_extra is not None:
            facts = [f for o in outs if o for f in o.facts]
            check(f"{n} records the qualifier faithfully ({want_extra})",
                  any("cant_cast" in f and want_extra in f for f in facts))


def run() -> None:
    _clauses()
    _abstains()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

"""test_cant_passive.py — passive-voice restriction statics (slice 3 of can't/restriction).

'<X> can't be <countered|prevented|activated>' — the subject is the thing restricted, not a player:
a spell-set (§701.5f counter lock), a damage descriptor (§615 prevention lock), or an ability set (§602.5
activation lock). `_ns_passive_restrict` (card_lark, same nscant transformer) grounds each as cant_be_countered
/ cant_prevent_damage / cant_be_activated with the whole subject slugged faithfully ('~' -> 'self' so it's
not lossy; a type-list 'and' is the SUBJECT, kept). _combat_restriction still owns the _TGT/creatures 'be
countered' subjects (runs first); this frame catches the spell-set/damage/ability subjects it can't match.

Run: MTG_NO_SPACY=1 python3 test_cant_passive.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter import card_corpus
from interpreter import ground
from interpreter.card_effects import parse_clause
from interpreter.transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _clauses() -> None:
    for src, verb, who, cond in [
        ("instant and sorcery spells you control can't be countered", "cant_be_countered", "instant_and_sorcery_spells_you_control", "-"),
        ("spells can't be countered", "cant_be_countered", "spells", "-"),
        ("~ can't be countered", "cant_be_countered", "self", "-"),
        ("combat damage can't be prevented", "cant_prevent_damage", "combat_damage", "-"),
        ("damage that would be dealt by ~ can't be prevented", "cant_prevent_damage", "damage_that_would_be_dealt_by_self", "-"),
        ("damage can't be prevented this turn", "cant_prevent_damage", "damage", "this_turn"),
        ("enchanted creature's activated abilities can't be activated", "cant_be_activated", "enchanted_creature_s_activated_abilities", "-"),
    ]:
        e = parse_clause(src)
        check(f"{src[:44]!r} -> {verb}({who}{', '+cond if cond!='-' else ''})",
              e is not None and e.verb == verb and e.target == who and e.cond == cond)


def _guards() -> None:
    # each passive verb only claims its own kind of subject (no over-claim)
    e = parse_clause("enchanted creature can't be sacrificed")
    check("'be sacrificed' not claimed as a passive cant_be_* (wrong kind)",
          e is None or e.verb not in ("cant_be_countered", "cant_prevent_damage", "cant_be_activated"))
    # combat path untouched
    e = parse_clause("~ can't be blocked")
    check("combat '~ can't be blocked' unchanged", e is not None and e.verb == "cant_be_blocked")


def _cards() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Sphinx of the Final Word", "Taigam, Ojutai Master", "Destiny Spinner", "Frenzied Baloth",
              "Stupefying Touch", "Overmaster", "Vexing Shusher"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        outs = [(u.raw, transpile_unit(u, {"id": cid, "card": c, "seq": i}))
                for i, u in enumerate(card_corpus.units_of(c))]
        restr = [o for raw, o in outs if "can't be countered" in raw.lower()
                 or "can't be prevented" in raw.lower() or "can't be activated" in raw.lower()]
        check(f"{n}: its passive restriction line grounds", restr and all(restr))

    # the conditional+compound 'If X is 5 or more, ~ can't be countered and the damage can't be prevented'
    # SPLITS into two faithful facts, the 'If …' riding cond (Banefire)
    c = cards.get("Banefire")
    if c:
        facts = [f for i, u in enumerate(card_corpus.units_of(c))
                 for f in (transpile_unit(u, {"id": "banefire", "card": c, "seq": i}) or type("X", (), {"facts": []})).facts]
        check("Banefire: cant_be_countered(self, x_is_5_or_more) emitted",
              any("cant_be_countered" in f and "self" in f and "x_is_5_or_more" in f for f in facts))
        check("Banefire: cant_prevent_damage(the_damage) emitted",
              any("cant_prevent_damage" in f and "the_damage" in f for f in facts))


def run() -> None:
    _clauses()
    _guards()
    _cards()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

"""test_mono_red_statics.py — six PARSER groundings from the mono-red collection worklist, each a real
comprehensive-rules mechanic added to the grounded vocabulary (ground._CORE_ACTIONS) + a spaCy+Lark
production (no new interpretation regex — fixed-phrase terminals / reused spans / the QUOTED terminal):

  * set_max_hand_size — The Ten Rings 'your maximum hand size is ten' / Reliquary Tower 'no maximum hand size'
  * intensify          — Drix Interlacer '~ intensifies by 1' (§701.61 Duskmourn keyword action)
  * get_boon           — Swiftspear's Teachings 'you get a one-time boon with "<ability>"'
  * add_type           — Energybending 'lands you control gain all basic land types until end of turn'
  * add_mana (dyn)     — Tarnation Vista 'for each color among <X>, add one mana of that color'
  * move-counter       — Nesting Grounds 'move a counter from <src> onto <dst>' (any kind; put_counter+moved_from)

Run: MTG_NO_SPACY=1 python3 test_mono_red_statics.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import card_corpus
import transpile_card
import ground
from card_effects import parse_clause

PASS = FAIL = 0


def check(msg, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {msg}")
    else:
        FAIL += 1
        print(f"  FAIL {msg}")


def _full(nm, corpus):
    c = corpus[nm]
    cid = ground.slug(nm)
    return all(transpile_card.transpile_unit(u, {"id": cid, "card": c, "seq": i}) is not None
               for i, u in enumerate(card_corpus.units_of(c)))


def main():
    # each verb is in the grounded vocabulary (so emitting it is faithful, not minted)
    for v in ("set_max_hand_size", "intensify", "get_boon", "add_type"):
        check(f"'{v}' is a grounded effect verb", v in ground.effect_verbs())

    # clause-level grounding
    e = parse_clause("your maximum hand size is ten")
    check("max hand size N", e and e.verb == "set_max_hand_size" and e.amount == 10)
    e = parse_clause("you have no maximum hand size")
    check("no maximum hand size -> unlimited", e and e.verb == "set_max_hand_size" and e.amount == "unlimited")
    e = parse_clause("~ intensifies by 1")
    check("intensify by N", e and e.verb == "intensify" and e.amount == 1 and e.target == "self")
    e = parse_clause('you get a one-time boon with "When you cast a creature spell, it gains haste"')
    check("get_boon carries the quoted ability", e and e.verb == "get_boon" and "haste" in e.extra)
    e = parse_clause("lands you control gain all basic land types until end of turn")
    check("add_type all basic land types", e and e.verb == "add_type" and e.extra == "all_basic_land_types"
          and e.target == "lands_you_control")
    e = parse_clause("for each color among monocolored permanents you control, add one mana of that color")
    check("dynamic multicolor mana", e and e.verb == "add_mana"
          and e.amount == "for_each_color_among_monocolored_permanents_you_control" and e.extra == "that_color")
    e = parse_clause("move a counter from target permanent you control onto a second target permanent")
    check("move a counter (any kind) -> put_counter + moved_from",
          e and e.verb == "put_counter" and e.extra == "any" and e.cond == "moved_from_target_permanent_you_control")

    # regressions: the productions these extend/sit beside still work
    check("normal grant unaffected (GLTYPES doesn't steal 'gain')",
          (parse_clause("creatures you control gain flying") or {}).verb == "grant_keyword"
          if parse_clause("creatures you control gain flying") else False)
    check("kinded move-counter still works",
          (parse_clause("move a +1/+1 counter from target creature onto another target creature") or {}).extra == "+1/+1"
          if parse_clause("move a +1/+1 counter from target creature onto another target creature") else False)
    check("normal 'for each' mana unaffected",
          (parse_clause("add {R} for each creature you control") or {}).verb == "add_mana"
          if parse_clause("add {R} for each creature you control") else False)

    # cards full-ingest
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    for nm in ["The Ten Rings", "Drix Interlacer", "Swiftspear's Teachings", "Energybending",
               "Tarnation Vista", "Nesting Grounds"]:
        check(f"{nm} full-ingest", _full(nm, corpus))

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)

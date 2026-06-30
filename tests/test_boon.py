"""test_boon.py — §113 a one-time BOON the controller gets (Swiftspear's Teachings: 'You get a one-time boon
with "When you cast a creature spell, it gains your choice of prowess or haste."').

The boon is a delayed one-shot triggered ability with no permanent to hang on. boon_v DECOMPOSES the inner
ability (reusing the clause parser) into '<trigger>|<recipient>|<grant>'; the bridge emits a register_boon
spell_effect; the driver carries it in `_boons` and fires on the controller's NEXT creature-spell cast,
granting the chosen keyword (haste is engine-modelled; prowess is faithfully inert), then consumes it.

Run: MTG_NO_SPACY=1 python3 test_boon.py
"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re

from interpreter import card_corpus
from interpreter import transpile_card
from interpreter import ground
import bridge_to_engine as bridge
import driver
import effect_handlers

effect_handlers.load()

PASS = FAIL = 0


def check(msg, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {msg}")
    else:
        FAIL += 1
        print(f"  FAIL {msg}")


_CORPUS = {c["name"]: c for c in card_corpus.load_cards()}


def _fresh_db_entry(name):
    c = _CORPUS[name]
    cid = ground.slug(name)
    db = {}
    for seq, u in enumerate(card_corpus.units_of(c)):
        o = transpile_card.transpile_unit(u, {"id": cid, "card": c, "seq": seq})
        for f in (o.facts if o else []):
            m = re.match(r'(\w+)\((.*)\)\.?\s*$', f.strip())
            if not m:
                continue
            rel = m.group(1)
            a = [x.strip().strip('"') for x in re.findall(r'"[^"]*"|[^,]+', m.group(2))]
            ab = db.setdefault(cid, {}).setdefault("abilities", {})
            if rel == "card_ability":
                ab.setdefault(a[1], {"kind": a[2], "effects": []})["kind"] = a[2]
            elif rel == "card_effect":
                e = a[6] if len(a) > 6 else "-"
                cd = a[7] if len(a) > 7 else "-"
                ab.setdefault(a[1], {"kind": "spell", "effects": []})["effects"].append(
                    (int(a[2]), a[3], a[4], a[5], e, cd))
            elif rel == "name":
                db.setdefault(cid, {})["name"] = a[1]
    return db


def _fire(grant_choice=None, creature=True):
    """Register a boon for alice, cast a (creature or noncreature) spell, return the keyword granted to it
    (or None). `grant_choice` overrides the _choose seam."""
    st = {"is_player": {("alice",)}, "spell_type": {("sp", "creature")} if creature else {("sp", "instant")},
          "_boons": {("alice", "you_cast_a_creature_spell", "it", "choice_prowess_or_haste")}}
    if grant_choice is not None:
        st["_forced"] = {"boon_keyword": grant_choice}
    driver._fire_boons(st, "alice", "sp")
    granted = {kw for (_e, o, kw) in st.get("eff_grant_keyword", set()) if o == "sp"}
    return granted, st.get("_boons", set())


def main():
    # --- boon_v decomposes the inner ability (parse level) ---
    o = None
    for seq, u in enumerate(card_corpus.units_of(_CORPUS["Swiftspear's Teachings"])):
        oo = transpile_card.transpile_unit(u, {"id": "swiftspear_s_teachings", "card": _CORPUS["Swiftspear's Teachings"], "seq": seq})
        if oo and any("get_boon" in f for f in oo.facts):
            o = oo
    boon_fact = next(f for f in o.facts if "get_boon" in f)
    check("boon_v decomposes the inner ability -> '<trigger>|<recipient>|<grant>'",
          "you_cast_a_creature_spell|it|choice_prowess_or_haste" in boon_fact)

    # --- bridge emits register_boon (fresh facts; cards.dl may be stale) ---
    bf, dr = bridge.card_facts("Swiftspear's Teachings", "alice", "st", _fresh_db_entry("Swiftspear's Teachings"), _CORPUS)
    check("bridge emits register_boon spell_effect, no get_boon drop",
          ("st", "register_boon", 0, "you_cast_a_creature_spell|it|choice_prowess_or_haste") in bf.get("spell_effect", set())
          and not any("get_boon" in str(d) for d in dr))

    # --- applier registers the boon ---
    st = {"is_player": {("alice",)}}
    effect_handlers.APPLY["register_boon"](driver, st, "st", 0,
                                           "you_cast_a_creature_spell|it|choice_prowess_or_haste", "st", "alice")
    check("register_boon applier adds the boon to _boons",
          ("alice", "you_cast_a_creature_spell", "it", "choice_prowess_or_haste") in st.get("_boons", set()))

    # --- _fire_boons: creature cast grants the chosen keyword + consumes the boon ---
    granted, remaining = _fire()
    check("casting a creature spell grants haste (default choice) to it", granted == {"haste"})
    check("the one-time boon is consumed after firing", not remaining)

    granted, _ = _fire(grant_choice="prowess")
    check("choosing prowess grants prowess (faithful; inert until prowess is modelled)", granted == {"prowess"})

    # --- a NONcreature cast does not fire the (creature-gated) boon ---
    granted, remaining = _fire(creature=False)
    check("casting a noncreature spell does NOT fire the boon", not granted)
    check("the boon survives an unmatched cast", remaining)

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)

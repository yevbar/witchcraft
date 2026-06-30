"""test_conditional_damage_engine.py — ENGINE resolution of the §616.1 conditional damage UPGRADE
('<spell> deals N to <tgt>. If <cond>, it deals M instead' — Burst Lightning, Brimstone Volley, Invasive
Maneuvers). The parser grounds a base deal_damage + an upgrade deal_damage(extra='instead', cond, target=
'that_target'); the bridge emits a driver-only spell_damage_upgrade(spell, M, cond); the driver deals M
INSTEAD of N iff it can CONFIRM the condition — never an over-deal on an unconfirmable cond.

  * you_control_a_<type/subtype> — a live board read (Invasive Maneuvers' Spacecraft): MODELLED.
  * morbid (a_creature_died_this_turn) — from the turn-scoped death flag, when present: MODELLED.
  * was_kicked / was_bargained — optional ADDITIONAL costs the cast model doesn't pay -> treated as NOT met,
    so the BASE amount resolves (faithful: the spell wasn't kicked/bargained in this engine).

Run: MTG_NO_SPACY=1 python3 test_conditional_damage_engine.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import re

import card_corpus
import transpile_card
import ground
import bridge_to_engine as bridge
import driver

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
    """A single-card db entry from FRESH transpile facts — robust to a stale/mid-rebuild cards.dl."""
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
            elif rel == "ability_trigger":
                ab.setdefault(a[1], {"kind": "triggered", "effects": []})["trigger"] = a[2]
            elif rel == "card_effect":
                e = a[6] if len(a) > 6 else "-"
                cd = a[7] if len(a) > 7 else "-"
                ab.setdefault(a[1], {"kind": "spell", "effects": []})["effects"].append(
                    (int(a[2]), a[3], a[4], a[5], e, cd))
            elif rel == "name":
                db.setdefault(cid, {})["name"] = a[1]
            elif rel == "printed_keyword":
                db.setdefault(cid, {}).setdefault("keywords", set()).add(a[1])
    return db


def _killed_3tough(cond, *, spacecraft=False, died=False):
    """Resolve a base-2 / upgrade-4 conditional damage spell at a 3-toughness creature; return whether it died
    (4 kills it, 2 doesn't) — i.e. whether the upgrade fired."""
    st = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("c",)}, "printed_control": {("bob", "c")},
          "printed_type": {("c", "creature")}, "printed_power": {("c", 3)}, "printed_toughness": {("c", 3)},
          "instance_of": {("sp", "x")}, "on_stack": {("sp", 0)}, "life": {("alice", 20), ("bob", 20)},
          "spell_damage": {("sp", "2", "creature_any")}, "spell_damage_upgrade": {("sp", 4, cond)}}
    if spacecraft:
        st["on_battlefield"].add(("ss",)); st["printed_control"].add(("alice", "ss")); st["printed_type"].add(("ss", "spacecraft"))
    if died:
        st["_died_this_turn"] = True
    driver._run_spell_damage(st, "sp", "alice")
    return ("c",) not in st.get("on_battlefield", set())


def main():
    # bridge emission (fresh facts)
    cases = [("Burst Lightning", 4, "was_kicked"),
             ("Invasive Maneuvers", 5, "you_control_a_spacecraft"),
             ("Brimstone Volley", 5, "a_creature_died_this_turn")]
    for nm, up, cond in cases:
        bf, _ = bridge.card_facts(nm, "alice", "s", _fresh_db_entry(nm), _CORPUS)
        check(f"{nm}: bridge emits spell_damage_upgrade (s, {up}, {cond})",
              ("s", up, cond) in bf.get("spell_damage_upgrade", set()))

    # driver resolution — the upgrade fires ONLY on a confirmed condition; never over-deals otherwise
    check("you_control_a_spacecraft + control a Spacecraft -> upgrade (4 kills the 3/3)",
          _killed_3tough("you_control_a_spacecraft", spacecraft=True))
    check("you_control_a_spacecraft, NO Spacecraft -> base (2 leaves the 3/3)",
          not _killed_3tough("you_control_a_spacecraft", spacecraft=False))
    check("morbid + a creature died this turn -> upgrade (4 kills)",
          _killed_3tough("a_creature_died_this_turn", died=True))
    check("morbid, nothing died -> base (2 leaves it)",
          not _killed_3tough("a_creature_died_this_turn", died=False))
    check("was_kicked (unmodelled optional cost) -> base, never an over-deal",
          not _killed_3tough("was_kicked"))
    check("was_bargained (unmodelled optional cost) -> base",
          not _killed_3tough("was_bargained"))

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)

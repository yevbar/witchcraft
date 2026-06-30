"""test_translate_spell2.py — ONE WORLD (spell slice 2): an instant/sorcery's CREATURE-scoped effects are
now DERIVED IN DATALOG (translate.dl) from the card PARSE facts (instance_of / card_ability / card_effect),
keyed by the SPELL instance id — mirroring test_translate_spell.py (the player-scoped slice) but for the
creature-target / board-scope / damage / reanimate relations:

  * spell_target    — a single 'target creature' verb (destroy/exile/tap/untap/return_to_hand/grant/counter)
  * spell_scope     — the same verbs over a board scope (creatures_you_control / all_creatures)
  * spell_damage    — §120 direct damage (n + the damage kind)
  * spell_reanimate — §701 put a graveyard/hand creature card onto the battlefield (the source-zone mode)

This proves: (1) the engine DERIVES each relation end-to-end from the parse facts; (2) the derivation EQUALS
the OLD python bridge emission ACROSS THE WHOLE CORPUS (0 mismatches per relation). modify_pt's P/T payload
(single-target spell_target + board-scope spell_scope) and switch_pt are now DERIVED too — the payload is
lexed by the pt_value foundation table (souffle can't parse '+1/+1'), so they're included in the corpus
equivalence below (the OLD bridge oracle replicates the pre-migration python f'{dp}/{dt}' emission).
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import driver
import bridge_to_engine as bridge


PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    PASS += bool(cond)
    FAIL += not cond


def _spell_state(card: str, aid: str, effects: list) -> dict:
    """A minimal state where instance 's' of `card` is a spell on the stack with the given parse-fact
    effects — so the engine derives spell_target/spell_scope/spell_damage/spell_reanimate(s, …) itself."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("main1",)},
        "on_stack": {("s", 0)}, "instance_of": {("s", card)},
        "card_ability": {(card, aid, "spell")},
        "card_effect": {(card, aid, i, v, a, t, x, "-") for (i, v, a, t, x) in effects},
        "counter": set(), "tapped": set(),
    }


def _derived(card, aid, effects, rel):
    st = _spell_state(card, aid, effects)
    return sorted((v, p, c) for (s, v, p, c) in driver.run(st, [rel])[rel]) if rel in ("spell_target", "spell_scope") \
        else sorted((n, k) for (s, n, k) in driver.run(st, [rel])[rel]) if rel == "spell_damage" \
        else sorted(m for (s, m) in driver.run(st, [rel])[rel])


def _derivation_checks():
    # spell_target — a single 'target creature' verb -> the engine surfaces (verb, payload, class).
    check("datalog derives spell destroy target_creature -> spell_target(destroy, -, any)",
          ("destroy", "-", "any") in _derived("c", "a0", [(0, "destroy", "-", "target_creature", "-")], "spell_target"))
    check("datalog derives spell return_to_hand target_creature -> spell_target(return_to_hand, -, any)",
          ("return_to_hand", "-", "any") in _derived("c", "a0", [(0, "return_to_hand", "-", "target_creature", "-")], "spell_target"))
    check("datalog derives spell grant_keyword flying (target) -> spell_target(grant, flying, any)",
          ("grant", "flying", "any") in _derived("c", "a0", [(0, "grant_keyword", "-", "target_creature", "flying")], "spell_target"))
    check("datalog derives spell put_counter +1/+1 x2 (target) -> spell_target(counter, p1p1:2, any)",
          ("counter", "p1p1:2", "any") in _derived("c", "a0", [(0, "put_counter", "2", "target_creature", "+1/+1")], "spell_target"))
    # modify_pt single-target P/T pump/shrink (Giant Growth): payload 'dp/dt' lexed via pt_value.
    check("datalog derives spell modify_pt +3/+3 (target) -> spell_target(modify_pt, 3/3, any)",
          ("modify_pt", "3/3", "any") in _derived("c", "a0", [(0, "modify_pt", "+3/+3", "target_creature", "-")], "spell_target"))
    check("datalog derives spell modify_pt -2/-0 (target) -> spell_target(modify_pt, -2/0, any) (negatives)",
          ("modify_pt", "-2/0", "any") in _derived("c", "a0", [(0, "modify_pt", "-2/-0", "target_creature", "-")], "spell_target"))
    # switch_pt single-target §613 layer-7d switch -> spell_target(switchpt, -, class), no P/T payload.
    check("datalog derives spell switch_pt (target) -> spell_target(switchpt, -, any)",
          ("switchpt", "-", "any") in _derived("c", "a0", [(0, "switch_pt", "-", "target_creature", "-")], "spell_target"))
    # an unrecognized keyword (grant) and a restricted target ABSTAIN, like the bridge did.
    check("grant of a non-engine keyword abstains (no spell_target)",
          not _derived("c", "a0", [(0, "grant_keyword", "-", "target_creature", "fakeword")], "spell_target"))
    check("a restricted target abstains (no spell_target)",
          not _derived("c", "a0", [(0, "destroy", "-", "target_creature_with_flying", "-")], "spell_target"))

    # spell_scope — the same verbs over a board scope.
    check("datalog derives spell destroy all_creatures -> spell_scope(destroy, -, all_creatures)",
          ("destroy", "-", "all_creatures") in _derived("c", "a0", [(0, "destroy", "-", "all_creatures", "-")], "spell_scope"))
    check("datalog derives spell grant trample (creatures_you_control) -> spell_scope(grant, trample, creatures_you_control)",
          ("grant", "trample", "creatures_you_control") in
          _derived("c", "a0", [(0, "grant_keyword", "-", "creatures_you_control", "trample")], "spell_scope"))
    check("datalog derives spell put_counter -1/-1 (all_creatures) -> spell_scope(counter, m1m1:1, all_creatures)",
          ("counter", "m1m1:1", "all_creatures") in
          _derived("c", "a0", [(0, "put_counter", "1", "all_creatures", "-1/-1")], "spell_scope"))
    # modify_pt board-scope P/T anthem-on-resolution (Overrun): payload 'dp/dt' lexed via pt_value.
    check("datalog derives spell modify_pt +3/+3 (creatures_you_control) -> spell_scope(modify_pt, 3/3, creatures_you_control)",
          ("modify_pt", "3/3", "creatures_you_control") in
          _derived("c", "a0", [(0, "modify_pt", "+3/+3", "creatures_you_control", "-")], "spell_scope"))

    # spell_damage — §120 direct damage.
    check("datalog derives spell deal_damage 3 (any_target) -> spell_damage(3, any_target)",
          (3, "any_target") in [(int(n), k) for (n, k) in _derived("c", "a0", [(0, "deal_damage", "3", "any_target", "-")], "spell_damage")])
    check("datalog derives spell deal_damage 2 (each_creature) -> spell_damage(2, all_creatures)",
          (2, "all_creatures") in [(int(n), k) for (n, k) in _derived("c", "a0", [(0, "deal_damage", "2", "each_creature", "-")], "spell_damage")])
    check("a variable damage amount (X) abstains (no spell_damage)",
          not _derived("c", "a0", [(0, "deal_damage", "X", "any_target", "-")], "spell_damage"))

    # spell_reanimate — §701 the source zone / tappedness mode.
    check("datalog derives reanimate from graveyard -> spell_reanimate(graveyard)",
          "graveyard" in _derived("c", "a0", [(0, "return_to_battlefield", "-", "target_creature_card", "from_graveyard")], "spell_reanimate"))
    check("datalog derives reanimate from graveyard tapped -> spell_reanimate(graveyard_tapped)",
          "graveyard_tapped" in _derived("c", "a0", [(0, "return_to_battlefield", "-", "creature_card", "from_graveyard_tapped")], "spell_reanimate"))
    check("datalog derives put-into-play from hand -> spell_reanimate(hand)",
          "hand" in _derived("c", "a0", [(0, "return_to_battlefield", "-", "a_creature_card", "from_hand")], "spell_reanimate"))
    check("a non-reanimate target abstains (no spell_reanimate)",
          not _derived("c", "a0", [(0, "return_to_battlefield", "-", "it", "from_graveyard")], "spell_reanimate"))


def _old_bridge_rows(verb, amt, tgt, extra):
    """The OLD (pre-migration) bridge spell-branch emission for one effect clause -> {rel: row(without tid)}.
    Replicates the python control flow exactly — INCLUDING the modify_pt single-target/board-scope and
    switch_pt paths (the f'{dp}/{dt}' payload + 'switchpt' class) the python bridge emitted before migration."""
    out = {}
    if verb in bridge._CREATURE_VERBS:
        scope = bridge._scope(tgt)
        if scope in ("creatures_you_control", "all_creatures"):
            r = bridge._creature_verb_payload(verb, amt, extra)
            if r[0] is not None:
                out["spell_scope"] = (r[0], r[1], scope)
            return out
        if scope is None:
            ev, payload, cls = bridge._single_target_payload(verb, amt, tgt, extra)
            if ev is not None:
                out["spell_target"] = (ev, payload, cls)
            return out
        return out                                           # scope == self -> falls through (no creature row)
    if verb == "deal_damage":
        n, dk = bridge._int(amt), bridge._damage_target(tgt)
        if n is not None and dk is not None:
            out["spell_damage"] = (n, dk)
        return out
    if verb == "put_counter":
        cp = bridge._counter_payload(amt, extra)
        if cp is not None:
            cls = bridge._target_class(tgt)
            sc = bridge._scope(tgt)
            if cls is not None:
                out["spell_target"] = ("counter", cp, cls); return out
            if sc in ("creatures_you_control", "all_creatures"):
                out["spell_scope"] = ("counter", cp, sc); return out
        return out
    if verb == "return_to_battlefield" and bridge._reanimates(tgt, extra):
        out["spell_reanimate"] = (bridge._reanimate_mode(extra),)
        return out
    if verb == "switch_pt" and bridge._target_class(tgt) is not None:   # §613 'switch target creature's P/T'
        out["spell_target"] = ("switchpt", "-", bridge._target_class(tgt))
    return out


def _engine_rows(verb, amt, tgt, extra):
    """The engine-DERIVED rows for one effect clause -> {rel: row(without tid)}, normalized to match the
    old bridge's python value types (spell_damage's n back to int)."""
    st = _spell_state("c", "a0", [(0, verb, str(amt), str(tgt), str(extra))])
    out = {}
    for (s, v, p, c) in driver.run(st, ["spell_target"])["spell_target"]:
        out["spell_target"] = (v, p, c)
    for (s, v, p, c) in driver.run(st, ["spell_scope"])["spell_scope"]:
        out["spell_scope"] = (v, p, c)
    for (s, n, k) in driver.run(st, ["spell_damage"])["spell_damage"]:
        out["spell_damage"] = (int(n), k)
    for (s, m) in driver.run(st, ["spell_reanimate"])["spell_reanimate"]:
        out["spell_reanimate"] = (m,)
    return out


def _equivalence_checks():
    import sim
    db = sim.load_db()
    rels = ("spell_target", "spell_scope", "spell_damage", "spell_reanimate")
    checked = {r: 0 for r in rels}
    derived = {r: 0 for r in rels}
    mism = {r: 0 for r in rels}
    printed = 0
    for cid, e in db.items():
        for aid, ab in (e.get("abilities") or {}).items():
            if ab.get("kind") != "spell":
                continue
            for (seq, verb, amt, tgt, extra, cond) in ab.get("effects", []):
                if cond != "-":
                    continue                                 # datalog derives only the unconditional slice
                old = _old_bridge_rows(verb, amt, tgt, extra)
                new = _engine_rows(verb, amt, tgt, extra)
                for r in rels:
                    o, n = old.get(r), new.get(r)
                    if o is None and n is None:
                        continue
                    checked[r] += 1
                    derived[r] += n is not None
                    if o != n:
                        mism[r] += 1
                        if printed < 8:
                            printed += 1
                            print(f"      MISMATCH {cid}/{verb} {r}: bridge={o} datalog={n} "
                                  f"(amt={amt!r} tgt={tgt!r} extra={extra!r})")
    for r in rels:
        check(f"datalog == old bridge for {r}: {checked[r]} cards, {derived[r]} derived, {mism[r]} mismatches",
              mism[r] == 0)


def _no_python_translation():
    import sim
    from interpreter import card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    # find a real destroy-target removal spell (Murder-like) and a real burn spell; the bridge must feed the
    # parse facts but emit NO python spell_target / spell_damage for the migrated verbs (datalog owns them).
    def emits_migrated(name):
        f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        return f.get("spell_target", set()), f.get("spell_scope", set()), f.get("spell_damage", set()), f.get("spell_reanimate", set())

    sample = None
    for cid, e in db.items():
        for aid, ab in (e.get("abilities") or {}).items():
            if ab.get("kind") != "spell":
                continue
            for (seq, verb, amt, tgt, extra, cond) in ab.get("effects", []):
                if cond == "-" and verb in ("destroy", "deal_damage") and (
                        bridge._target_class(tgt) or bridge._damage_target(tgt) or bridge._scope(tgt)):
                    sample = e.get("name") or cid
                    break
            if sample:
                break
        if sample:
            break
    if sample:
        t, sc, dm, ra = emits_migrated(sample)
        check(f"the bridge emits NO python spell_target/scope/damage/reanimate for a migrated spell ({sample})",
              not (t or sc or dm or ra))
    else:
        check("found a migrated spell sample", False)


def main():
    _derivation_checks()
    _equivalence_checks()
    _no_python_translation()
    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)

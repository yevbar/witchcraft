"""test_translate_spell.py — ONE WORLD (spell slice): an instant/sorcery's player-scoped effect (draw /
gain N life / lose N life / mill / discard) is now DERIVED IN DATALOG (translate.dl) from the card PARSE
facts (instance_of / card_ability / card_effect), keyed by the SPELL instance id — exactly mirroring the
triggered slice (test_translate.py) but for kind=="spell" and the relation `spell_effect`. This test proves:
(1) the engine derives spell_effect end-to-end from parse facts; (2) the derivation EQUALS the old python
bridge logic across the whole corpus (0 mismatches); (3) the bridge no longer translates the UNCONDITIONAL
player-scoped slice — it only feeds the parse facts (a CONDITIONAL pscope effect still goes the old route).
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtg import driver
from mtg import bridge_to_engine as bridge


PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    PASS += bool(cond)
    FAIL += not cond


def _spell_state(card: str, aid: str, effects: list) -> dict:
    """A minimal state where instance 's' of `card` is a spell on the stack with the given parse-fact
    effects — so the engine derives spell_effect(s, …) itself (read back via the spell_effect output)."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("main1",)},
        "on_stack": {("s", 0)}, "instance_of": {("s", card)},
        "card_ability": {(card, aid, "spell")},
        "card_effect": {(card, aid, i, v, a, t, "-", "-") for (i, v, a, t) in effects},
        "counter": set(), "tapped": set(),
    }


def _derivation_checks():
    # the engine DERIVES each player-scoped spell effect from the parse facts, keyed by the spell id 's'.
    cases = [
        ("draw", "2", "you", "draw", "2", "controller"),
        ("gain_life", "3", "you", "gain_life", "3", "controller"),
        ("lose_life", "2", "target_player", "lose_life", "2", "each_opponent"),
        ("mill", "2", "target_opponent", "mill", "2", "each_opponent"),
        ("discard", "2", "each_player", "discard", "2", "each_opponent"),
    ]
    for verb, amt, tgt, eff, n, scope in cases:
        st = _spell_state("c", "a0", [(0, verb, amt, tgt)])
        se = driver.run(st, ["spell_effect"])["spell_effect"]
        ok = ("s", eff, n, scope) in se
        check(f"datalog derives spell {verb} {amt} ({tgt}) -> spell_effect({eff}, {n}, {scope})", ok)

    # a variable amount (X) and an unrecognized verb ABSTAIN (no derivation), like the bridge did.
    st = _spell_state("c", "a0", [(0, "draw", "X", "you")])
    check("a variable amount (X) abstains — no spell_effect derived",
          not driver.run(st, ["spell_effect"])["spell_effect"])

    # multiple effects on one spell each derive (Sign in Blood: draw 2 + lose 2, both to the target player).
    st = _spell_state("c", "a0", [(0, "draw", "2", "target_player"), (1, "lose_life", "2", "target_player")])
    se = driver.run(st, ["spell_effect"])["spell_effect"]
    check("both effects of a multi-effect spell derive",
          ("s", "draw", "2", "each_opponent") in se and ("s", "lose_life", "2", "each_opponent") in se)


def _equivalence_checks():
    # ACROSS THE CORPUS: the datalog derivation == the OLD python bridge logic for the migrated spell slice.
    from mtg import sim
    db = sim.load_db()

    def old_bridge_effect(verb, amt, tgt):
        """The bridge's pre-migration _resolved_effect for a player-scoped spell verb (int amount + _target)."""
        r = bridge._resolved_effect(verb, amt, tgt, "-")
        return None if r is None else (r[0], int(r[1]), r[2])

    mism = checked = derived = 0
    for cid, e in db.items():
        for aid, ab in (e.get("abilities") or {}).items():
            if ab.get("kind") != "spell":
                continue
            for (seq, verb, amt, tgt, extra, cond) in ab.get("effects", []):
                if verb not in bridge._PSCOPE_DATALOG or cond != "-":
                    continue
                checked += 1
                old = old_bridge_effect(verb, amt, tgt)
                st = _spell_state("c", "a0", [(int(seq), verb, str(amt), str(tgt))])
                se = driver.run(st, ["spell_effect"])["spell_effect"]
                new = next(((eff, int(n), sc) for (sp, eff, n, sc) in se), None)
                derived += new is not None
                if old is None and new is None:                # both abstain — agreement
                    continue
                if old != new:
                    mism += 1
                    if mism <= 5:
                        print(f"      MISMATCH {cid}/{verb}: bridge={old} datalog={new} (amt={amt!r} tgt={tgt!r})")
    check(f"datalog == old bridge for all {checked} unconditional player-scoped spell effects "
          f"({derived} derived, 0 mismatches)", mism == 0)


def _no_python_translation():
    from mtg import sim
    from interpreter import card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    # Divination ('draw two') — a player-scoped sorcery: the bridge feeds the PARSE facts but emits NO
    # python spell_effect for the unconditional draw (datalog owns it).
    f, _ = bridge.card_facts("Divination", "alice", "x", db, corpus)
    check("the bridge feeds card_effect parse facts for a spell (one world)", bool(f.get("card_effect")))
    check("the bridge emits NO python spell_effect for the migrated draw (datalog owns it)",
          not any(verb == "draw" for (_s, verb, _n, _t) in f.get("spell_effect", set())))


def main():
    _derivation_checks()
    _equivalence_checks()
    _no_python_translation()
    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)

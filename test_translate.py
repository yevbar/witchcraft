"""test_translate.py — ONE WORLD: the parse->operational translation now lives in DATALOG (translate.dl),
not the python bridge. A triggered ability's player-scoped effect (draw / gain N life / lose N life / mill /
discard) is DERIVED by souffle from the card PARSE facts (card_ability / ability_trigger / card_effect), with
the same ability id the bridge used. This test proves: (1) the engine derives the effect end-to-end from
parse facts; (2) the derivation EQUALS the old python bridge logic across the corpus; (3) the bridge no
longer translates these — it only feeds the parse facts.
"""

from __future__ import annotations

import driver
import bridge_to_engine as bridge


PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    PASS += bool(cond)
    FAIL += not cond


def _fires_state(card: str, aid: str, phrase: str, effect: tuple) -> dict:
    """A minimal state where an instance 'i' of `card` is in play with a triggered ability on the upkeep
    event, plus the card's PARSE facts — so the engine derives the effect itself."""
    seq, verb, amt, tgt = effect
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("upkeep",)},
        "on_battlefield": {("i",)}, "printed_type": {("i", "creature")}, "printed_control": {("alice", "i")},
        # ONE WORLD: has_trigger is no longer fed — the engine DERIVES it from the parse facts below.
        "instance_of": {("i", card)},
        "card_ability": {(card, aid, "triggered")},
        "ability_trigger": {(card, aid, phrase)},
        "card_effect": {(card, aid, seq, verb, amt, tgt, "-", "-")},
        "counter": set(), "tapped": set(),
    }


def _derivation_checks():
    # the engine DERIVES each player-scoped effect from the parse facts (read back via `pending`).
    cases = [
        ("draw", "1", "you", "draw", 1, "controller"),
        ("gain_life", "3", "you", "gain_life", 3, "controller"),
        ("lose_life", "2", "each_opponent", "lose_life", 2, "each_opponent"),
        ("mill", "2", "target_opponent", "mill", 2, "each_opponent"),     # _target: opponent-ish -> each_opponent
        ("discard", "1", "each_player", "discard", 1, "each_opponent"),
    ]
    for verb, amt, tgt, eff, n, scope in cases:
        st = _fires_state("c", "a0", "the_beginning_of_your_upkeep", (0, verb, amt, tgt))
        out = driver.run(st, ["pending", "fires"])
        ok = ("i_a0", eff, str(n), scope, "i", "alice") in out["pending"]
        check(f"datalog derives {verb} {amt} ({tgt}) -> pending({eff}, {n}, {scope})", ok)
        # ONE WORLD: has_trigger is now DERIVED IN DATALOG (no manual feed) — proven via `fires`, which
        # derives only from has_trigger. (has_trigger isn't an .output, so `fires` is the readable proxy.)
        check(f"datalog DERIVES has_trigger for {verb} (fires(i_a0, i))", ("i_a0", "i") in out["fires"])

    # variable amount and unrecognized constructs ABSTAIN (no derivation), like the bridge did.
    st = _fires_state("c", "a0", "the_beginning_of_your_upkeep", (0, "draw", "X", "you"))
    check("a variable amount (X) abstains — no effect derived", not driver.run(st, ["pending"])["pending"])


def _equivalence_checks():
    # ACROSS THE CORPUS: the datalog derivation == the OLD python bridge logic for the migrated slice.
    import sim, card_corpus, ground
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def old_bridge_effect(amt, tgt):
        """The bridge's pre-migration _resolved_effect for a player-scoped verb: int amount + _target."""
        n = bridge._int(amt)
        return None if n is None else (n, bridge._target(str(tgt)))

    mism = 0
    checked = 0
    for name in list(corpus):
        e = db.get(ground.slug(name)) or {}
        for aid, ab in (e.get("abilities") or {}).items():
            if ab.get("kind") != "triggered" or ab.get("trigger") not in bridge._EVENT:
                continue
            for (seq, verb, amt, tgt, extra, cond) in ab.get("effects", []):
                if verb not in bridge._PSCOPE_DATALOG or cond not in ("-", None):
                    continue
                checked += 1
                old = old_bridge_effect(amt, tgt)
                st = _fires_state("c", "a0", "the_beginning_of_your_upkeep", (int(seq), verb, str(amt), str(tgt)))
                pend = driver.run(st, ["pending"])["pending"]
                new = next(((int(n), sc) for (_a, e2, n, sc, _s, _p) in pend if e2 == verb), None)
                # the bridge would emit iff old is not None; datalog derives iff new is not None — and they agree
                if old is None and new is None:
                    continue
                if old != new:
                    mism += 1
                    if mism <= 5:
                        print(f"      MISMATCH {name}/{verb}: bridge={old} datalog={new} (amt={amt!r} tgt={tgt!r})")
    check(f"datalog == old bridge for all {checked} player-scoped triggered effects (0 mismatches)", mism == 0)


def _no_python_translation():
    import sim
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus_load()}
    # a card with a player-scoped trigger emits the PARSE facts but NO python trigger_effect for it.
    f, _ = bridge.card_facts("Phyrexian Rager", "alice", "x", db, corpus)
    check("the bridge feeds card_effect parse facts (one world)", bool(f.get("card_effect")))
    check("the bridge emits NO python trigger_effect (datalog owns it)", not f.get("trigger_effect"))


def card_corpus_load():
    import card_corpus
    return card_corpus.load_cards()


def main():
    _derivation_checks()
    _equivalence_checks()
    _no_python_translation()
    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)

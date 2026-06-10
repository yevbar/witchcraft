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


def _fires_state_extra(card, aid, phrase, effect):
    """Like _fires_state but the effect carries an `extra` column (5-tuple seq/verb/amt/tgt/extra)."""
    seq, verb, amt, tgt, extra = effect
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("upkeep",)},
        "on_battlefield": {("i",)}, "printed_type": {("i", "creature")}, "printed_control": {("alice", "i")},
        "instance_of": {("i", card)},
        "card_ability": {(card, aid, "triggered")},
        "ability_trigger": {(card, aid, phrase)},
        "card_effect": {(card, aid, seq, verb, amt, tgt, extra, "-")},
        "counter": set(), "tapped": set(),
    }


def _target_derivation_checks():
    # ONE WORLD: the §115/§120/§122/§701 TRIGGERED single-target / damage / counter / reanimate / switchpt
    # relations are DERIVED IN DATALOG (translate.dl) — read back via pending_target/pending_damage/
    # pending_reanimate/pending on a forced-firing upkeep trigger.
    P = "the_beginning_of_your_upkeep"
    # single-target creature verbs -> trigger_target(verb, "-", class).
    for verb, ev in (("destroy", "destroy"), ("exile", "exile"), ("tap", "tap"),
                     ("untap", "untap"), ("return_to_hand", "return_to_hand")):
        st = _fires_state_extra("c", "a0", P, (0, verb, "-", "target_creature", "-"))
        pt = driver.run(st, ["pending_target"])["pending_target"]
        check(f"datalog derives single-target {verb} -> pending_target({ev}, -, any)",
              ("i_a0", "i", ev, "-", "any", "alice") in pt)
    # a non-battlefield-zone bounce ABSTAINS (from_graveyard).
    st = _fires_state_extra("c", "a0", P, (0, "return_to_hand", "-", "target_creature", "from_graveyard"))
    check("single-target bounce from a non-battlefield zone abstains",
          not driver.run(st, ["pending_target"])["pending_target"])
    # single-target grant_keyword -> trigger_target("grant", keyword, class); non-engine keyword abstains.
    st = _fires_state_extra("c", "a0", P, (0, "grant_keyword", "-", "target_creature_you_control", "flying"))
    check("datalog derives single-target grant_keyword -> pending_target(grant, flying, you_control)",
          ("i_a0", "i", "grant", "flying", "you_control", "alice") in driver.run(st, ["pending_target"])["pending_target"])
    st = _fires_state_extra("c", "a0", P, (0, "grant_keyword", "-", "target_creature", "banding"))
    check("single-target grant of a non-engine keyword abstains",
          not driver.run(st, ["pending_target"])["pending_target"])
    # direct damage -> trigger_damage(N, kind); a creature target and a face target.
    st = _fires_state_extra("c", "a0", P, (0, "deal_damage", "3", "target_creature", "-"))
    check("datalog derives deal_damage -> pending_damage(3, creature_any)",
          ("i_a0", "i", "3", "creature_any", "alice") in driver.run(st, ["pending_damage"])["pending_damage"])
    st = _fires_state_extra("c", "a0", P, (0, "deal_damage", "X", "target_creature", "-"))
    check("a variable damage amount (X) abstains", not driver.run(st, ["pending_damage"])["pending_damage"])
    # put_counter -> trigger_target("counter", "p1p1:N"/"m1m1:N", class); a 0 count and non-P/T abstain.
    st = _fires_state_extra("c", "a0", P, (0, "put_counter", "2", "target_creature", "+1/+1"))
    check("datalog derives put_counter -> pending_target(counter, p1p1:2, any)",
          ("i_a0", "i", "counter", "p1p1:2", "any", "alice") in driver.run(st, ["pending_target"])["pending_target"])
    st = _fires_state_extra("c", "a0", P, (0, "put_counter", "0", "target_creature", "+1/+1"))
    check("a zero-count counter abstains", not driver.run(st, ["pending_target"])["pending_target"])
    # reanimation -> trigger_reanimate(mode); graveyard, hand, and tapped variants.
    for extra, mode in (("from_graveyard", "graveyard"), ("from_hand", "hand"),
                        ("from_graveyard_tapped", "graveyard_tapped")):
        st = _fires_state_extra("c", "a0", P, (0, "return_to_battlefield", "-", "target_creature_card", extra))
        check(f"datalog derives reanimate ({extra}) -> pending_reanimate({mode})",
              ("i_a0", "i", mode, "alice") in driver.run(st, ["pending_reanimate"])["pending_reanimate"])
    # switch P/T: a SELF switch -> trigger_effect(switchpt) -> pending; a TARGET switch -> trigger_target.
    st = _fires_state_extra("c", "a0", P, (0, "switch_pt", "-", "self", "-"))
    check("datalog derives self switch_pt -> pending(switchpt, 0, -)",
          ("i_a0", "switchpt", "0", "-", "i", "alice") in driver.run(st, ["pending"])["pending"])
    st = _fires_state_extra("c", "a0", P, (0, "switch_pt", "-", "target_creature", "-"))
    check("datalog derives target switch_pt -> pending_target(switchpt, -, any)",
          ("i_a0", "i", "switchpt", "-", "any", "alice") in driver.run(st, ["pending_target"])["pending_target"])


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


def _old_bridge_triggered_payloads(ab, a):
    """The (trigger_target / trigger_damage / trigger_reanimate / trigger_effect-switchpt) rows the OLD
    python bridge would have emitted for ONE triggered ability `ab` with id `a`, replicating the pre-
    migration card_facts triggered-branch logic EXACTLY (the migrated single-target / damage / counter /
    reanimate / switch_pt blocks only — NOT modify_pt, which stays in the bridge). Returns a dict of sets."""
    out = {"trigger_target": set(), "trigger_damage": set(), "trigger_reanimate": set(), "switchpt_self": set()}
    for (_seq, verb, amt, tgt, extra, cond) in ab.get("effects", []):
        if cond not in ("-", None):
            continue
        if verb in ("modify_pt", "grant_keyword", "destroy", "exile", "tap", "untap", "return_to_hand"):
            if verb in ("return_to_hand", "exile") and extra in ("from_graveyard", "from_exile", "from_library", "from_hand"):
                continue
            scope = bridge._scope(tgt)
            if scope is None:
                if verb == "modify_pt":          # NOT migrated — skip from the datalog-equivalence set
                    continue
                ev, payload, cls = bridge._single_target_payload(verb, amt, tgt, extra)
                if ev is not None:
                    out["trigger_target"].add((a, ev, payload, cls))
            continue                              # board-scope (trigger_effect_*) is NOT our slice
        if verb == "deal_damage":
            n, dk = bridge._int(amt), bridge._damage_target(tgt)
            if n is not None and dk is not None:
                out["trigger_damage"].add((a, n, dk))
            continue
        if verb == "put_counter":
            cp, cls = bridge._counter_payload(amt, extra), bridge._target_class(tgt)
            if cp is not None and cls is not None:
                out["trigger_target"].add((a, "counter", cp, cls))
            continue
        if verb == "return_to_battlefield" and bridge._reanimates(tgt, extra):
            out["trigger_reanimate"].add((a, bridge._reanimate_mode(extra)))
            continue
        if verb == "switch_pt":
            if str(tgt) in ("self", "it"):
                out["switchpt_self"].add((a,))
            elif bridge._target_class(tgt) is not None:
                out["trigger_target"].add((a, "switchpt", "-", bridge._target_class(tgt)))
            continue
    return out


def _triggered_equivalence_checks():
    """ACROSS THE CORPUS: the datalog-derived trigger_target / trigger_damage / trigger_reanimate (read
    back via pending_target / pending_damage / pending_reanimate on a forced-firing upkeep trigger) EXACTLY
    equals the OLD python bridge's output for the migrated single-target / damage / counter / reanimate /
    switchpt slice. Per relation: X cards, 0 mismatches."""
    import sim, card_corpus, ground
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    stats = {"target": [0, 0], "damage": [0, 0], "reanimate": [0, 0], "switchpt_self": [0, 0]}
    for name in corpus:
        e = db.get(ground.slug(name)) or {}
        card = ground.slug(name)
        for aid, ab in (e.get("abilities") or {}).items():
            if ab.get("kind") != "triggered" or ab.get("trigger") not in bridge._EVENT:
                continue
            a = f"x_{aid}"
            old = _old_bridge_triggered_payloads(ab, a)
            if not any(old.values()):
                continue
            # feed the ability's parse facts on a forced-firing upkeep trigger, derive pending_*.
            ce = {(card, aid, int(s), v, str(am), str(tg), str(ex), str(co))
                  for (s, v, am, tg, ex, co) in ab.get("effects", [])}
            st = {
                "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("upkeep",)},
                "on_battlefield": {("x",)}, "printed_type": {("x", "creature")}, "printed_control": {("alice", "x")},
                "instance_of": {("x", card)},
                "card_ability": {(card, aid, "triggered")},
                "ability_trigger": {(card, aid, "the_beginning_of_your_upkeep")},
                "card_effect": ce, "counter": set(), "tapped": set(),
            }
            out = driver.run(st, ["pending_target", "pending_damage", "pending_reanimate", "pending"])
            # trigger_target: pending_target(A, S, V, Pay, Cl, P) -> (A, V, Pay, Cl). switchpt-self is a
            # trigger_effect -> pending(A, switchpt, 0, -, S, P); split it out from the trigger_target set.
            got_tgt = {(A, V, Pay, Cl) for (A, _S, V, Pay, Cl, _P) in out["pending_target"]}
            got_dmg = {(A, int(N), K) for (A, _S, N, K, _P) in out["pending_damage"]}
            got_rea = {(A, M) for (A, _S, M, _P) in out["pending_reanimate"]}
            got_swself = {(A,) for (A, Eff, _N, _T, _S, _P) in out["pending"] if Eff == "switchpt"}
            for key, old_set, got_set in (("target", old["trigger_target"], got_tgt),
                                          ("damage", old["trigger_damage"], got_dmg),
                                          ("reanimate", old["trigger_reanimate"], got_rea),
                                          ("switchpt_self", old["switchpt_self"], got_swself)):
                if not old_set:
                    continue
                stats[key][0] += 1
                if old_set != got_set:
                    stats[key][1] += 1
                    if stats[key][1] <= 5:
                        print(f"      MISMATCH {name}/{key}: bridge={old_set} datalog={got_set}")
    for key, label in (("target", "trigger_target"), ("damage", "trigger_damage"),
                       ("reanimate", "trigger_reanimate"), ("switchpt_self", "trigger_effect(switchpt,self)")):
        cnt, mism = stats[key]
        check(f"datalog == old bridge for all {cnt} {label} abilities (0 mismatches)", mism == 0)


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
    _target_derivation_checks()
    _equivalence_checks()
    _triggered_equivalence_checks()
    _no_python_translation()
    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)

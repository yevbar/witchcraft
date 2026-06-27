"""test_impulse.py — §608 impulse 'exile top N, may play this turn' (effect_handlers/impulse.py + the engine's
may_play / playable_source / cast-from-exile). Run: python3 effect_handlers/test_impulse.py"""
from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver
import effect_handlers

effect_handlers.load()
CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _run(state, outs):
    with contextlib.redirect_stdout(io.StringIO()):
        return driver.run(state, outs)


def _fold_checks():
    # the bridge _fold_impulse recognizes 'play/cast <the exiled cards> (without paying …)' across the spell,
    # triggered, and modal paths — the storm-payoff card engine.
    import bridge_to_engine as B
    E = lambda effs: B._fold_impulse(effs, lambda *a: None)

    def fires(exile_tgt, play_verb, play_tgt, n="1"):
        effs = [(0, "exile", n, exile_tgt, "-", "-"), (1, play_verb, "-", play_tgt, "-", "may")]
        return bool(E(effs))

    check("impulse folds 'play that card without paying its mana cost' (Mind's Desire)",
          fires("top_of_library", "play", "that_card_without_paying_its_mana_cost"))
    check("impulse folds 'cast it' (cast verb, not just play)", fires("top_of_library", "cast", "it"))
    check("impulse folds 'play those cards' (Opera Love Song)", fires("top_of_library", "play", "those_cards", n="2"))
    check("impulse folds 'you may play that card' (Stella Lee trigger)", fires("top_of_library", "play", "that_card"))
    check("impulse does NOT fold an exile with no play/cast rider",
          not fires("top_of_library", "draw", "you"))
    check("impulse does NOT fold a play of an UNRELATED object (not the exiled cards)",
          not fires("top_of_library", "play", "target_land"))

    # §118.9 the impulse_play payload carries the FREE flag when the play clause says 'without paying its mana
    # cost' (Mind's Desire); the applier then sets free_grant (cast cost 0). A normal impulse carries '-'.
    payloads = {}
    B._fold_impulse([(0, "exile", "1", "top_of_library", "-", "-"),
                     (1, "play", "-", "that_card_without_paying_its_mana_cost", "-", "may")],
                    lambda e, n, t: payloads.update({e: t}))
    check("impulse_play carries 'free' for a 'without paying' play clause", payloads.get("impulse_play") == "free")
    payloads.clear()
    B._fold_impulse([(0, "exile", "2", "top_of_library", "-", "-"),
                     (1, "play", "-", "those_cards", "-", "may")],
                    lambda e, n, t: payloads.update({e: t}))
    check("impulse_play carries '-' for a normal-cost play clause", payloads.get("impulse_play") == "-")

    # §608 ACTIVATED impulse fold — only when the [exile top N] + [play them] pair is the WHOLE ability.
    A = lambda effs: B._fold_impulse_activated(effs, lambda *a: None)
    check("activated impulse folds the bare exile+play pair (Dark-Dweller Oracle)",
          bool(A([(0, "exile", "1", "top_of_library", "-", "-"), (1, "play", "-", "that_card", "-", "may")])))
    check("activated impulse ABSTAINS with a sibling clause (Oracle's Vault's brick counter)",
          not A([(0, "exile", "1", "top_of_library", "-", "-"), (1, "play", "-", "that_card", "-", "may"),
                 (2, "put_counter", "1", "self", "brick", "-")]))
    check("activated impulse ABSTAINS on a restricted cond (Nivix: 'if it's an instant or sorcery')",
          not A([(0, "exile", "1", "top_of_library", "-", "-"),
                 (1, "cast", "-", "it", "-", "may__it_s_an_instant_or_sorcery_spell")]))

    # end to end: an ACTIVATED impulse_play exiles top N and makes them castable FROM EXILE (the can_cast gate).
    ast = {"is_player": {("me",)}, "has_priority": {("me",)}, "active_player": {("me",)},
           "current_step": {("precombat_main",)}, "in_library": {("me", "z1"), ("me", "z2")},
           "_lib_order": {"me": ["z1", "z2"]}, "exile": set(), "may_play": set(), "free_grant": set(),
           "spell_type": {("z1", "instant")}, "mana_cost": {("z1", 1)}, "mana_available": {("me", 5)}}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(ast, {("dwo_a0", "impulse_play", 1, "-", "dwo", "me")})
    check("activated impulse exiles the top card", ("z1",) in ast["exile"])
    check("activated impulse makes it castable FROM EXILE (can_cast)",
          ("me", "z1") in _run(ast, ["can_cast"])["can_cast"])
    check("a NORMAL-cost impulse does not free-grant the card", ("me", "z1") not in ast["free_grant"])

    # the FREE impulse (Oracle's Vault's brick-fed mode): free_grant -> free_cast -> castable with ZERO mana.
    fst = {"is_player": {("me",)}, "has_priority": {("me",)}, "active_player": {("me",)},
           "current_step": {("precombat_main",)}, "in_library": {("me", "w1")}, "_lib_order": {"me": ["w1"]},
           "exile": set(), "may_play": set(), "free_grant": set(), "spell_type": {("w1", "sorcery")},
           "mana_generic": {("w1", 4)}, "mana_pip": set(), "mana_available": {("me", 0)}}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(fst, {("ov_a1", "impulse_play", 1, "free", "ov", "me")})
    check("free impulse sets free_grant on the exiled card", ("me", "w1") in fst["free_grant"])
    check("free impulse makes it castable with ZERO mana (free_cast)",
          ("me", "w1") in _run(fst, ["can_cast"])["can_cast"])

    # the real corpus cards this recovers resolve CLEAN (no dropped play/cast clause).
    import card_corpus
    import sim
    _db = sim.load_db()
    _corpus = {c["name"]: c for c in card_corpus.load_cards()}
    for nm in ("Dark-Dweller Oracle", "Professional Face-Breaker"):
        _f, _dr = B.card_facts(nm, "me", "x", _db, _corpus)
        check(f"{nm}: no dropped play/cast clause",
              not [d for d in _dr if d[0] == "effect" and d[1] in ("play", "cast")])
    # Oracle's Vault: its FREE brick-fed mode (a1) folds with the 'free' payload (castable for 0 from exile);
    # its other mode (a0) keeps a sibling brick-counter clause, so that one stays abstained (single-slot path).
    _ovf, _ = B.card_facts("Oracle's Vault", "me", "x", _db, _corpus)
    check("Oracle's Vault free mode folds with the 'free' payload",
          any(r[4] == "impulse_play" and r[6] == "free" for r in _ovf.get("activated_ability", set())))
    # Nivix's restricted impulse is correctly ABSTAINED (its play/cast clause stays dropped — never mis-cast).
    _f, _dr = B.card_facts("Nivix, Aerie of the Firemind", "me", "x", _db, _corpus)
    check("Nivix (restricted impulse) is faithfully abstained (cast clause dropped)",
          any(d[1] == "cast" for d in _dr if d[0] == "effect"))

    # end to end: a TRIGGERED impulse_play exiles top N and flags may_play (the Stella Lee path).
    st = {"is_player": {("me",)}, "in_library": {("me", "z1"), ("me", "z2")}, "_lib_order": {"me": ["z1", "z2"]},
          "exile": set(), "may_play": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, {("stella", "impulse_play", 1, "-", "stella", "me")})
    check("a triggered impulse exiles the top card", ("z1",) in st["exile"])
    check("a triggered impulse flags it may_play", ("me", "z1") in st["may_play"])


def _theft_and_freecast_checks():
    import bridge_to_engine as B
    import card_corpus
    import ground
    import sim
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # §608 THEFT IMPULSE (Ragavan): exile the top of an OPPONENT's library, the caster may cast it.
    st = {"is_player": {("me",), ("op",)}, "in_library": {("op", "o1"), ("op", "o2")},
          "_lib_order": {"op": ["o1", "o2"]}, "exile": set(), "may_play": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["impulse_opp"](driver, st, "rag", 1, "-", "rag", "me")
    check("theft impulse exiles the opponent's top card", ("o1",) in st["exile"])
    check("theft impulse flags it may_play for the CASTER (not the owner)",
          ("me", "o1") in st["may_play"] and ("op", "o1") not in st["may_play"])

    # the corpus cards that motivated these resolve CLEAN.
    for nm in ("Ragavan, Nimble Pilferer", "Kari Zev's Expertise", "Storm of Memories",
               "Rite of Flame", "Jeska's Will"):
        f, dropped = B.card_facts(nm, "me", "x", db, corpus)
        check(f"{nm} is CLEAN", dropped == [])

    # §118.9 cast_free (Kari Zev): free-cast a MV-2 spell from hand through the real cast path.
    full = B.make_deck_state({"me": ["Shock", "Mountain", "Mountain", "Mountain"], "op": ["Island"] * 4}, seed=1, hand=0, life=40)
    shock = next(t for (t, n) in sorted(full["instance_of"]) if n == ground.slug("Shock") and ("me", t) in full["in_library"])
    full["in_library"].discard(("me", shock)); full["in_hand"].add(("me", shock))
    if shock in full["_lib_order"]["me"]:
        full["_lib_order"]["me"].remove(shock)
    full["active_player"] = {("me",)}; full["has_priority"] = {("me",)}; full["current_step"] = {("precombat_main",)}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(full, {("kz", "cast_free", 0, "hand|any|2", "kz", "me")})
    check("cast_free casts the spell from hand (it leaves the hand)", ("me", shock) not in full["in_hand"])
    check("cast_free resolved the spell (Shock -> graveyard after dealing damage)", (shock,) in full.get("graveyard", set()))


def run():
    # engine: may_play makes an EXILED card a castable source (playable_source = in_hand ∪ may_play)
    base = {"is_player": {("me",)}, "has_priority": {("me",)}, "active_player": {("me",)},
            "current_step": {("precombat_main",)}, "spell_type": {("bolt", "instant")},
            "mana_cost": {("bolt", 1)}, "mana_available": {("me", 1)}, "exile": {("bolt",)}}
    check("an exiled card is NOT castable without may_play", ("me", "bolt") not in _run(base, ["can_cast"])["can_cast"])
    flagged = dict(base); flagged["may_play"] = {("me", "bolt")}
    check("an exiled card WITH may_play IS castable (impulse)", ("me", "bolt") in _run(flagged, ["can_cast"])["can_cast"])

    # applier: exile top N of library + flag may_play; library shrinks
    st = {"is_player": {("me",)}, "_lib_order": {"me": ["a", "b", "c"]},
          "in_library": {("me", "a"), ("me", "b"), ("me", "c")}}
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["impulse_play"](driver, st, "x", 2, "-", "src", "me")
    check("impulse exiles the top N cards", {("a",), ("b",)} <= st.get("exile", set()))
    check("impulse flags them may_play for the controller", {("me", "a"), ("me", "b")} <= st.get("may_play", set()))
    check("impulse pulls them out of the library", st["_lib_order"]["me"] == ["c"] and ("me", "a") not in st["in_library"])

    # cast-from-exile lifecycle: casting a may_play card leaves exile + loses the flag
    cs = {"is_player": {("me",)}, "active_player": {("me",)}, "exile": {("bolt",)}, "may_play": {("me", "bolt")},
          "spell_type": {("bolt", "instant")}, "mana_cost": {("bolt", 0)}, "mana_available": {("me", 0)},
          "mana_pool": set(), "on_stack": set(), "_stack_info": {}, "in_hand": set(), "all_passed": set(),
          "current_step": {("precombat_main",)}, "has_priority": set(), "instance_of": set(), "graveyard": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._cast_spell(cs, "me", "bolt", ["me"])
    check("casting an impulse card removes it from exile", ("bolt",) not in cs.get("exile", set()))
    check("casting an impulse card clears its may_play flag", ("me", "bolt") not in cs.get("may_play", set()))

    _fold_checks()
    _theft_and_freecast_checks()

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

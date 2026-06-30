"""test_keyword_actions2.py — §701 keyword-action REDUCTIONS (effect_handlers/keyword_actions2.py): discover,
manifest_dread, learn, cant_be_regenerated. Verified in BOTH perfect and imperfect information (observe.observe).
Run: python3 effect_handlers/test_keyword_actions2.py"""
from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtg import driver
import observe
import effect_handlers

effect_handlers.load()
CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _forced(state, **choices):
    state["_forced"] = dict(choices)


# ─────────────────────────────────────────────────────────────────────────────
# discover N — exile until a nonland (mv ≤ N), cast it free OR to hand; rest to bottom.
# ─────────────────────────────────────────────────────────────────────────────
def _discover_checks():
    E = effect_handlers.ENCODE["discover"]
    check("ENCODE discover 3 -> ('discover', 3, 'controller')", E("discover", "3", "you", "-") == ("discover", 3, "controller"))
    check("ENCODE discover (no amount) abstains", E("discover", "-", "you", "-") is None)
    check("ENCODE discover (another player's lib) abstains", E("discover", "3", "target_opponent", "-") is None)

    def _lib_state():
        # me's library top->bottom: a LAND (l0), a too-expensive nonland (s0 mv4), the discovered spell (s1 mv2),
        # then a spare (s2). discover 3 should skip l0+s0 to exile order, hit s1.
        return {
            "is_player": {("me",), ("op",)},
            "in_library": {("me", "l0"), ("me", "s0"), ("me", "s1"), ("me", "s2")},
            "_lib_order": {"me": ["l0", "s0", "s1", "s2"]},
            "printed_type": {("l0", "land")},
            "spell_type": {("s0", "sorcery"), ("s1", "sorcery"), ("s2", "instant")},
            "mana_generic": {("s0", 4), ("s1", 2), ("s2", 0)},
            "mana_pip": set(),
            "exile": set(), "in_hand": set(), "graveyard": set(),
        }

    # MODE = hand (forced): the discovered card s1 goes to hand; l0 + s0 (skipped) go to the bottom.
    s = _lib_state(); _forced(s, discover_mode="hand")
    _quiet(effect_handlers.APPLY["discover"], driver, s, "x", 3, "controller", "src", "me")
    check("discover: hits s1 (nonland mv 2 <= 3), puts it in hand", ("me", "s1") in s["in_hand"])
    check("discover: the skipped LAND l0 went to the bottom (still in library)", ("me", "l0") in s["in_library"])
    check("discover: the skipped too-expensive s0 (mv 4) went to the bottom", ("me", "s0") in s["in_library"])
    check("discover: skipped cards are at the BOTTOM (after the untouched s2)", s["_lib_order"]["me"] == ["s2", "l0", "s0"])
    check("discover: s1 is no longer in the library", ("me", "s1") not in s["in_library"])

    # whiff: cap 0, only nonland is mv >= 1 -> nothing discovered; all looked-at cards go to the bottom.
    sw = {"is_player": {("me",)}, "in_library": {("me", "n0")}, "_lib_order": {"me": ["n0"]},
          "printed_type": set(), "spell_type": {("n0", "sorcery")}, "mana_generic": {("n0", 1)}, "mana_pip": set(),
          "exile": set(), "in_hand": set()}
    _quiet(effect_handlers.APPLY["discover"], driver, sw, "x", 0, "controller", "src", "me")
    check("discover 0: no nonland mv<=0 -> nothing to hand, the card returns to the bottom",
          not sw["in_hand"] and ("me", "n0") in sw["in_library"])

    # MODE = cast (free): end-to-end through the real cast path with a deck state (mirrors test_impulse).
    from mtg import bridge_to_engine as B
    from interpreter import ground
    full = B.make_deck_state({"me": ["Shock", "Mountain", "Mountain", "Shock"], "op": ["Island"] * 4},
                             seed=1, hand=0, life=40)
    shock = next(t for (t, n) in sorted(full["instance_of"]) if n == ground.slug("Shock"))
    # force Shock to the very top of me's library so discover hits it first (Shock is a nonland mv 1).
    full["_lib_order"]["me"].remove(shock); full["_lib_order"]["me"].insert(0, shock)
    full["active_player"] = {("me",)}; full["has_priority"] = {("me",)}; full["current_step"] = {("precombat_main",)}
    _forced(full, discover_mode="cast")
    _quiet(effect_handlers.APPLY["discover"], driver, full, "x", 3, "controller", "src", "me")
    check("discover (cast mode): the discovered Shock was cast (resolved -> graveyard)", (shock,) in full.get("graveyard", set()))
    check("discover (cast mode): Shock is no longer in the library", ("me", shock) not in full["in_library"])

    # IMPERFECT info: the cast result (a public graveyard) and a to-hand result. The bottomed library cards
    # are the controller's OWN library (hidden order from opponents). The opponent does NOT see me's hand.
    s = _lib_state(); _forced(s, discover_mode="hand")
    _quiet(effect_handlers.APPLY["discover"], driver, s, "x", 3, "controller", "src", "me")
    vme = observe.observe(s, "me"); vop = observe.observe(s, "op")
    check("imperfect info: me sees the discovered card in its own hand", ("me", "s1") in vme.get("in_hand", set()))
    check("imperfect info: op does NOT see me's hand (sees only a count)", not any(p == "op" for (p, _c) in vop.get("in_hand", set())) and not any(p == "me" for (p, _c) in vop.get("in_hand", set())))
    check("imperfect info: op does NOT learn me's library order (top hidden)", "_lib_order" not in vop)


# ─────────────────────────────────────────────────────────────────────────────
# manifest_dread — look at top 2, manifest one (face-down 2/2), bin the other.
# ─────────────────────────────────────────────────────────────────────────────
def _manifest_dread_checks():
    E = effect_handlers.ENCODE["manifest_dread"]
    check("ENCODE manifest_dread -> ('manifest_dread', 0, 'controller')", E("manifest_dread", "-", "you", "-") == ("manifest_dread", 0, "controller"))

    def _state():
        # me's top two: 'drg' (really a creature) and 'sol' (a spell). Real identities live in instance_of.
        return {
            "is_player": {("me",), ("op",)},
            "in_library": {("me", "drg"), ("me", "sol"), ("me", "x3")},
            "_lib_order": {"me": ["drg", "sol", "x3"]},
            "instance_of": {("drg", "dragonslug"), ("sol", "solring"), ("x3", "x3slug")},
            "printed_type": {("drg", "creature")},
            "on_battlefield": set(), "printed_control": set(), "face_down": set(),
            "known": set(), "graveyard": set(), "_sick": set(),
        }

    s = _state(); _forced(s, manifest_dread_pick="drg")
    _quiet(effect_handlers.APPLY["manifest_dread"], driver, s, "x", 0, "controller", "src", "me")
    check("manifest_dread: drg is on the battlefield", ("drg",) in s["on_battlefield"])
    check("manifest_dread: drg is face down", ("drg",) in s["face_down"])
    check("manifest_dread: drg is summoning sick", ("drg",) in s["_sick"])
    check("manifest_dread: controller KNOWS the manifested card", ("me", "drg") in s["known"])
    check("manifest_dread: the OTHER top card (sol) went to the graveyard", ("sol",) in s["graveyard"])
    check("manifest_dread: x3 (3rd card) was untouched in the library", s["_lib_order"]["me"] == ["x3"])
    check("manifest_dread: neither looked-at card remains in the library", ("me", "drg") not in s["in_library"] and ("me", "sol") not in s["in_library"])

    # engine: the manifested card is a 2/2 colorless creature (face-down body) regardless of its real type.
    o = _quiet(driver.run, s, ["power", "eff_toughness", "creature"])
    pw = next((int(v) for (c, v) in o["power"] if c == "drg"), None)
    tg = next((int(v) for (c, v) in o["eff_toughness"] if c == "drg"), None)
    check("engine: the manifested permanent is a 2/2 (face-down body)", (pw, tg) == (2, 2))
    check("engine: the manifested permanent is a creature", ("drg",) in o["creature"])

    # IMPERFECT info: opponent sees the face-down 2/2 EXISTS but not its identity; controller knows it.
    vop = observe.observe(s, "op"); vme = observe.observe(s, "me")
    check("observe(op): sees the face-down permanent exists", ("drg",) in vop.get("on_battlefield", set()))
    check("observe(op): identity hidden (no instance_of for the face-down card)",
          not any(c == "drg" for (c, _x) in vop.get("instance_of", set())))
    check("observe(op): face_down survives -> engine on op's view still derives a 2/2",
          (lambda oo: (next((int(v) for (c, v) in oo["power"] if c == "drg"), None),
                       next((int(v) for (c, v) in oo["eff_toughness"] if c == "drg"), None)) == (2, 2))(_quiet(driver.run, vop, ["power", "eff_toughness"])))
    check("observe(me): controller sees the real manifested card identity",
          ("drg", "dragonslug") in vme.get("instance_of", set()))
    check("observe(op): the binned card IS public in the graveyard", ("sol",) in vop.get("graveyard", set()))


# ─────────────────────────────────────────────────────────────────────────────
# learn — the rummage half: you may draw a card, then discard a card (your choice from your hand).
# ─────────────────────────────────────────────────────────────────────────────
def _learn_checks():
    E = effect_handlers.ENCODE["learn"]
    check("ENCODE learn -> ('learn', 0, 'controller')", E("learn", "-", "you", "-") == ("learn", 0, "controller"))

    def _state(hand, lib):
        s = {"is_player": {("me",), ("op",)},
             "in_hand": {("me", c) for c in hand}, "in_library": {("me", c) for c in lib},
             "_lib_order": {"me": list(lib)}, "graveyard": set(), "printed_control": set(),
             "on_battlefield": set(), "_known_top": {}}
        return s

    # rummage: draw d0, then discard h0 (forced). Net hand size unchanged (drew 1, discarded 1).
    s = _state(["h0"], ["d0", "d1"]); _forced(s, learn_rummage=True, learn_discard="h0")
    _quiet(effect_handlers.APPLY["learn"], driver, s, "x", 0, "controller", "src", "me")
    check("learn: drew d0 (left the library)", ("me", "d0") not in s["in_library"])
    check("learn: discarded h0 to the graveyard", ("h0",) in s["graveyard"])
    check("learn: hand is now {d0} (drew d0, discarded h0)", {c for (p, c) in s["in_hand"] if p == "me"} == {"d0"})

    # 'you may' declined: nothing happens.
    s = _state(["h0"], ["d0"]); _forced(s, learn_rummage=False)
    _quiet(effect_handlers.APPLY["learn"], driver, s, "x", 0, "controller", "src", "me")
    check("learn declined: no draw, no discard", {c for (p, c) in s["in_hand"] if p == "me"} == {"h0"} and not s["graveyard"])

    # IMPERFECT info: the discard CHOICE is over me's own hand; the discarded card is public in the graveyard,
    # but op never sees me's remaining hand.
    s = _state(["h0", "h1"], ["d0"]); _forced(s, learn_rummage=True, learn_discard="h1")
    _quiet(effect_handlers.APPLY["learn"], driver, s, "x", 0, "controller", "src", "me")
    vme = observe.observe(s, "me"); vop = observe.observe(s, "op")
    check("imperfect info: me sees its own remaining hand (h0, d0)", {c for (p, c) in vme.get("in_hand", set()) if p == "me"} == {"h0", "d0"})
    check("imperfect info: op sees the discarded h1 in the (public) graveyard", ("h1",) in vop.get("graveyard", set()))
    check("imperfect info: op does NOT see me's hand", not any(p == "me" for (p, _c) in vop.get("in_hand", set())))


# ─────────────────────────────────────────────────────────────────────────────
# cant_be_regenerated — flag the destroy chokepoint reads so a regen shield can't save the permanent.
# ─────────────────────────────────────────────────────────────────────────────
def _cant_regen_checks():
    E = effect_handlers.ENCODE["cant_be_regenerated"]
    check("ENCODE cant_be_regenerated 'it' -> ('cant_be_regenerated', 0, 'self')", E("cant_be_regenerated", "-", "it", "-") == ("cant_be_regenerated", 0, "self"))
    check("ENCODE cant_be_regenerated 'self' -> self", E("cant_be_regenerated", "-", "self", "-") == ("cant_be_regenerated", 0, "self"))
    check("ENCODE cant_be_regenerated 'target_creature' -> target_creature", E("cant_be_regenerated", "-", "target_creature", "-") == ("cant_be_regenerated", 0, "target_creature"))
    check("ENCODE cant_be_regenerated (subtyped) abstains", E("cant_be_regenerated", "-", "target_zombie", "-") is None)

    # self: marks the source.
    s = {"is_player": {("me",)}, "on_battlefield": {("c0",)}, "cant_be_regenerated": set()}
    _quiet(effect_handlers.APPLY["cant_be_regenerated"], driver, s, "x", 0, "self", "c0", "me")
    check("cant_be_regenerated (self): the source is flagged", ("c0",) in s["cant_be_regenerated"])

    # the flag DEFEATS a regen shield at the destroy chokepoint (the whole point — completing the regen story).
    s = {"is_player": {("me",)}, "on_battlefield": {("c0",)}, "printed_type": {("c0", "creature")},
         "_regen_shield": {("c0",)}, "cant_be_regenerated": {("c0",)}, "tapped": set(), "attacks": set(), "blocks": set()}
    saved = _quiet(driver._consume_regen_shield, s, "c0")
    check("cant_be_regenerated DEFEATS the regen shield (chokepoint returns False -> it dies)", saved is False)
    # without the flag, the same shield WOULD save it (control).
    s2 = {"is_player": {("me",)}, "on_battlefield": {("c0",)}, "printed_type": {("c0", "creature")},
          "_regen_shield": {("c0",)}, "cant_be_regenerated": set(), "tapped": set(), "attacks": set(), "blocks": set()}
    saved2 = _quiet(driver._consume_regen_shield, s2, "c0")
    check("control: WITHOUT the flag the shield saves it (chokepoint returns True)", saved2 is True)

    # target_creature: driver picks an opponent's creature (the disruptive default), via _choose.
    s = {"is_player": {("me",), ("op",)},
         "on_battlefield": {("mine",), ("theirs",)},
         "printed_type": {("mine", "creature"), ("theirs", "creature")},
         "printed_power": {("mine", 2), ("theirs", 3)}, "printed_toughness": {("mine", 2), ("theirs", 3)},
         "printed_control": {("me", "mine"), ("op", "theirs")}, "cant_be_regenerated": set()}
    _quiet(effect_handlers.APPLY["cant_be_regenerated"], driver, s, "x", 0, "target_creature", "src", "me")
    check("cant_be_regenerated (target): picks the opponent's creature", ("theirs",) in s["cant_be_regenerated"])

    # cant_be_regenerated is PUBLIC state -> survives observe for both seats.
    s = {"is_player": {("me",), ("op",)}, "on_battlefield": {("c0",)}, "cant_be_regenerated": set(),
         "printed_control": {("me", "c0")}}
    _quiet(effect_handlers.APPLY["cant_be_regenerated"], driver, s, "x", 0, "self", "c0", "me")
    check("imperfect info: cant_be_regenerated is public (op sees the flag)", ("c0",) in observe.observe(s, "op").get("cant_be_regenerated", set()))


def run():
    _discover_checks()
    _manifest_dread_checks()
    _learn_checks()
    _cant_regen_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

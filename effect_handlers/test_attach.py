"""test_attach.py — §701.3 ATTACH as an EFFECT (effect_handlers/attach.py): a resolving ability moves the
SOURCE Equipment/Aura onto a creature you control, re-pointing attached_to so the 'equipped/enchanted
creature' static buff re-derives onto the new host, and LEAVING any prior host (the §701.3 move).
Run: python3 effect_handlers/test_attach.py"""
from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtg import driver
import effect_handlers
from effect_handlers.attach import encode_attach

effect_handlers.load()
CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _board():
    """me controls a 2/2 'a', a 5/5 'big', and an Equipment 'equip' carrying a +1/+0 'attached' static buff."""
    return {
        "is_player": {("me",), ("op",)}, "active_player": {("me",)}, "current_step": {("precombat_main",)},
        "on_battlefield": {("a",), ("big",), ("equip",)},
        "printed_control": {("me", "a"), ("me", "big"), ("me", "equip")},
        "printed_type": {("a", "creature"), ("big", "creature"), ("equip", "artifact")},
        "printed_subtype": {("equip", "equipment")},
        "printed_power": {("a", 2), ("big", 5)}, "printed_toughness": {("a", 2), ("big", 5)},
        "static_pt": {("equip", 1, 0, "attached")}, "attached_to": set(),
    }


def _attach(st, ctrl="me", src="equip"):
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, {(f"{src}_a0", "attach", 0, "you_control", src, ctrl)})


def _run() -> None:
    # --- the encoder (faithful-or-abstain) -------------------------------------------------------------
    check("encode self -> you_control creature resolves",
          encode_attach("attach", "-", "target_creature_you_control", "it") == ("attach", 0, "you_control"))
    check("encode self -> 'a creature you control' resolves",
          encode_attach("attach", "-", "a_creature_you_control", "self") == ("attach", 0, "you_control"))
    check("encode self -> up-to-one creature you control resolves",
          encode_attach("attach", "-", "up_to_one_target_creature_you_control", "it") == ("attach", 0, "you_control"))
    check("ABSTAIN: target equipment moved object (double choice)",
          encode_attach("attach", "-", "target_creature_you_control", "target_equipment_you_control") is None)
    check("ABSTAIN: bare 'target creature' host (enemy/control-aura unmodeled)",
          encode_attach("attach", "-", "target_creature", "it") is None)
    check("ABSTAIN: anaphoric 'that creature' host",
          encode_attach("attach", "-", "that_creature", "self") is None)
    check("ABSTAIN: restricted subtype host",
          encode_attach("attach", "-", "target_legendary_creature_you_control", "it") is None)
    check("ABSTAIN: up-to-N moved object",
          encode_attach("attach", "-", "target_creature_you_control", "up_to_one_target_equipment_you_control") is None)

    # --- the applier (§701.3 move + buff re-derive) ----------------------------------------------------
    st = _board()
    _attach(st)
    check("fresh attach -> strongest friendly host (big)", st["attached_to"] == {("equip", "big")})
    out = driver.run(st, ["power"])
    pw = {c: int(n) for (c, n) in out["power"]}
    check("buff follows attached_to onto host (big 5 -> 6)", pw.get("big") == 6 and pw.get("a") == 2)

    st = _board(); st["attached_to"] = {("equip", "a")}            # already on the 2/2
    _attach(st)
    check("re-attach LEAVES the old host 'a'", ("equip", "a") not in st["attached_to"])
    check("re-attach moves to the new host 'big' only", st["attached_to"] == {("equip", "big")})

    st = {
        "is_player": {("me",)}, "active_player": {("me",)}, "current_step": {("precombat_main",)},
        "on_battlefield": {("equip",)}, "printed_control": {("me", "equip")},
        "printed_type": {("equip", "artifact")}, "printed_subtype": {("equip", "equipment")},
        "static_pt": set(), "attached_to": set(),
    }
    _attach(st)
    check("no legal host -> stays unattached (no-op)", st["attached_to"] == set())

    st = _board()
    _attach(st, ctrl="op")                                        # an opponent has no creatures of their own here
    check("attach respects controller (op has no host) -> no-op", st["attached_to"] == set())


def main() -> int:
    _run()
    ok = sum(1 for _n, c in CHECKS if c)
    for n, c in CHECKS:
        print(f"  [{'ok' if c else 'XX'}] {n}")
    print(f"\n{ok}/{len(CHECKS)} checks pass")
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

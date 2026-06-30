"""test_copy.py — §707.10 'copy target spell' (effect_handlers/copy.py). Run: python3 effect_handlers/test_copy.py"""
from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtg import driver
import effect_handlers

effect_handlers.load()
CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _enc(verb, amt, tgt, extra):
    h = effect_handlers.ENCODE.get(verb)
    return h(verb, amt, tgt, extra) if h else None


def run():
    # encode: 'copy target instant or sorcery spell' -> copy_spell; a non-spell target abstains
    check("encode copy spell -> copy_spell x1", _enc("copy", "-", "target_instant_or_sorcery_spell", "-") == ("copy_spell", 1, "target_spell"))
    check("encode copy with count 2", _enc("copy", "2", "target_spell", "-") == ("copy_spell", 2, "target_spell"))
    check("encode copy of a PERMANENT abstains", _enc("copy", "-", "target_artifact", "-") is None)
    check("choose_new_targets encodes a covered no-op", _enc("choose_new_targets", "-", "copy", "-") == ("copy_noop", 0, "-"))

    # apply: the copy targets the topmost OTHER spell on the stack and lands as a fresh copy of its card
    state = {"on_stack": {("bolt", 2), ("tc", 3)}, "_stack_info": {"bolt": "me", "tc": "me"},
             "spell_type": {("bolt", "instant"), ("tc", "instant")}, "instance_of": {("bolt", "lightning_bolt")},
             "printed_control": {("me", "bolt"), ("me", "tc")}}
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["copy_spell"](driver, state, "a", 1, "target_spell", "tc", "me")
    copies = [o for (o,) in state.get("_is_copy", set())]
    check("apply: a copy was created", len(copies) == 1)
    check("apply: the copy points at the targeted spell's card", any(s == "lightning_bolt" for (o, s) in state["instance_of"] if o in copies))
    check("apply: the copy is on the stack above the original", any(o in copies and p > 3 for (o, p) in state["on_stack"]))
    check("apply: it copied the OTHER spell (bolt), not its own source (tc)", "bolt__copy1" in copies and not any("tc__copy" in o for o in copies))

    # apply: nothing legal to copy -> no-op (abstain), no phantom copy
    s2 = {"on_stack": {("tc", 3)}, "spell_type": {("tc", "instant")}, "_stack_info": {"tc": "me"}}
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["copy_spell"](driver, s2, "a", 1, "target_spell", "tc", "me")
    check("apply: no other spell on the stack -> no copy made", not s2.get("_is_copy"))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

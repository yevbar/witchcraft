"""Unit checks for effect_handlers/counters.py — the §122 self/source put_counter encoder.

These exercise the ENCODE half directly (verb, amt, tgt, extra) and the driver's add_counter APPLY arm
(which puts the counter on `src`). The bridge integration is verified separately via card_facts; note the
bridge currently SHADOWS this verb through its _EFFECT map (see the report), so these unit checks pin the
handler's correctness independent of that integration gap.
"""

from __future__ import annotations

import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_root, os.path.join(_root, "packages")):  # repo root + packages/ (for the mtg package)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import effect_handlers
import effect_handlers.counters as C
from mtg import driver as D

CHECKS: list = []


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, bool(ok)))


def _encode_checks() -> None:
    enc = C._encode_put_counter

    # SELF target + fixed count + a named non-P/T kind -> ('add_counter', N, kind) on the source.
    check("burden on self -> add_counter/burden",
          enc("put_counter", "1", "self", "burden") == ("add_counter", 1, "burden"))
    check("loyalty on 'it' -> add_counter/loyalty",
          enc("put_counter", "1", "it", "loyalty") == ("add_counter", 1, "loyalty"))
    check("knowledge on 'him' -> add_counter/knowledge",
          enc("put_counter", "1", "him", "knowledge") == ("add_counter", 1, "knowledge"))
    # a P/T counter kind normalizes to the engine's p1p1/m1m1 slug.
    check("+1/+1 on self -> add_counter/p1p1",
          enc("put_counter", "2", "self", "+1/+1") == ("add_counter", 2, "p1p1"))
    check("-1/-1 on self -> add_counter/m1m1",
          enc("put_counter", "1", "self", "-1/-1") == ("add_counter", 1, "m1m1"))

    # ABSTAIN: a chosen board target / scope is NOT self-scoped -> engine path.
    check("target creature abstains", enc("put_counter", "1", "target_creature", "+1/+1") is None)
    check("each creature you control abstains",
          enc("put_counter", "1", "each_creature_you_control", "+1/+1") is None)
    # ABSTAIN: a variable/dynamic count -> no value to feed.
    check("X count abstains", enc("put_counter", "X", "self", "+1/+1") is None)
    check("per-counter count abstains",
          enc("put_counter", "1_per_burden_counter_on", "self", "+1/+1") is None)
    check("zero count abstains", enc("put_counter", "0", "self", "burden") is None)
    # ABSTAIN: no nameable kind.
    check("missing kind abstains", enc("put_counter", "1", "self", "-") is None)


def _apply_checks() -> None:
    # The driver's add_counter arm puts the counter on `src` for an arbitrary kind. Drive _apply_effects
    # with a single pending row and confirm the source gains the counter.
    state: dict = {"counter": set()}
    # pending tuple shape: (a, eff, amt, tgt, src, ctrl) — tgt carries the counter KIND for add_counter.
    pending = {("ring_upkeep", "add_counter", 1, "burden", "the_one_ring", "alice")}
    D._apply_effects(state, pending)
    check("driver applies a burden counter to the source",
          ("the_one_ring", "burden", 1) in state["counter"])

    # a second firing accumulates on the same (obj, kind).
    D._apply_effects(state, {("ring_upkeep2", "add_counter", 1, "burden", "the_one_ring", "alice")})
    check("a second burden counter accumulates to 2",
          ("the_one_ring", "burden", 2) in state["counter"])


def run() -> None:
    effect_handlers.load()
    _encode_checks()
    _apply_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

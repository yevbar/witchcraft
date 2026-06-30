"""test_prowess.py — §702.108 PROWESS, derived from the keyword and riding the existing cast-trigger +
until-EOT-pump machinery (the other half of the cast-counter / spell-copy work — see test_storm.py).

A creature with prowess gets +1/+1 until end of turn whenever its controller casts a NONCREATURE spell.
Verified: a single trigger, STACKING across multiple casts (the per-cast salt on the effect id), no trigger
off a creature spell, no trigger off an OPPONENT's spell, and the pump wearing off at cleanup. Power is read
off the engine's `power` relation; the toughness half is checked via the eff_mod_toughness layer rows.

Run: python3 test_prowess.py
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib
import io

from mtg import bridge_to_engine as B
from mtg import driver

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _power(st, c):
    return next((int(n) for (x, n) in driver.run(st, ["power"])["power"] if x == c), None)


def _tough_mods(st, c):
    return [r for r in st.get("eff_mod_toughness", set()) if r[1] == c]


def _cast(st, who, tid):
    with contextlib.redirect_stdout(io.StringIO()):
        driver._cast_spell(st, who, tid, ["alice", "bob"])


def _board(alice_deck, bob_deck=("Mountain",) * 30):
    st = B.make_deck_state({"alice": list(alice_deck), "bob": list(bob_deck)}, seed=1, hand=0, life=20)
    st["_sick"] = set()
    return st


def _fids(st, slug, who="alice"):
    return [t for (t, n) in sorted(st["instance_of"]) if n == slug and (who, t) in st["in_library"]]


def _to_battlefield(st, tid, who="alice"):
    st["in_library"].discard((who, tid)); st["on_battlefield"].add((tid,)); st["printed_control"].add((who, tid))


def _to_hand(st, tid, who="alice"):
    st["in_library"].discard((who, tid)); st["in_hand"].add((who, tid))


def _own_turn():
    # alice's main phase, Swiftspear out, two Bolts + a Bears in hand. Bob has NO castable instant, so no
    # response window perturbs the scenario (a held bob instant would resolve in response and muddy it).
    st = _board(["Monastery Swiftspear", "Lightning Bolt", "Lightning Bolt", "Grizzly Bears"])
    sw = _fids(st, "monastery_swiftspear")[0]
    bolts = _fids(st, "lightning_bolt")[:2]
    bear = _fids(st, "grizzly_bears")[0]
    _to_battlefield(st, sw)
    for t in bolts + [bear]:
        _to_hand(st, t)
    st["active_player"] = {("alice",)}; st["current_step"] = {("precombat_main",)}
    st["mana_pool"] = {("alice", "red", 9), ("alice", "green", 9)}; st["mana_available"] = {("alice", 9)}
    return st, sw, bolts, bear


def run():
    st, sw, bolts, bear = _own_turn()
    check("prowess: base power is 1", _power(st, sw) == 1)

    _cast(st, "alice", bolts[0])
    check("prowess: +1/+1 after one noncreature cast (power 2)", _power(st, sw) == 2)
    check("prowess: the toughness half is applied too (one +1 mod)",
          len(_tough_mods(st, sw)) == 1 and all(r[2] == 1 for r in _tough_mods(st, sw)))

    _cast(st, "alice", bolts[1])
    check("prowess: STACKS across casts (power 3 after two noncreature spells)", _power(st, sw) == 3)
    check("prowess: a second +1 toughness mod stacks (two distinct per-cast ids)",
          len(_tough_mods(st, sw)) == 2)

    _cast(st, "alice", bear)
    check("prowess: a CREATURE spell does not trigger prowess (still power 3)", _power(st, sw) == 3)

    driver._end_of_turn(st)
    check("prowess: the pump wears off at cleanup (back to power 1)", _power(st, sw) == 1)
    check("prowess: toughness mods cleared at cleanup", not _tough_mods(st, sw))

    # OPPONENT case: bob (active) casts a noncreature spell; alice holds a prowess Swiftspear but no way to
    # respond (no instants/mana), so the only cast is bob's — and it must NOT pump alice's creature.
    st = _board(["Monastery Swiftspear"], bob_deck=["Lightning Bolt"] + ["Mountain"] * 30)
    sw = _fids(st, "monastery_swiftspear")[0]
    bob_bolt = _fids(st, "lightning_bolt", "bob")[0]
    _to_battlefield(st, sw)
    _to_hand(st, bob_bolt, "bob")
    st["active_player"] = {("bob",)}; st["current_step"] = {("precombat_main",)}
    st["mana_pool"] = {("bob", "red", 5)}; st["mana_available"] = {("bob", 5)}
    _cast(st, "bob", bob_bolt)
    check("prowess: an opponent's noncreature cast does not pump my creature", _power(st, sw) == 1)
    check("prowess: no eff-mod applied from an opponent's cast", not _tough_mods(st, sw))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

"""test_protection_engine.py — §702.16 'protection from <colour>' is now WIRED into the engine through the
bridge, so the engine's targeting rule enforces it.

The parser records protection as `printed_keyword(protection) + keyword_param(protection, from_<colour>)`.
sim.load_db already parsed both, but the bridge never translated the quality into the engine's
`protection_from(instance, colour)` input — so the engine's
  illegal_target(S, T) :- targets(S, T), spell_color(S, Col), protection_from(T, Col).
never fired for real cards. This asserts (a) the bridge feeds protection_from for a single/compound colour
quality and ABSTAINS on a non-colour one, and (b) end-to-end: a spell of the protected colour targeting the
creature FIZZLES (the engine output that depends on illegal_target), while an off-colour spell does not.

Run: python3 test_protection_engine.py
"""
from __future__ import annotations

import card_corpus
import sim
import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def run() -> None:
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}

    # (a) BRIDGE: colour protection -> protection_from; non-colour -> abstain.
    bf, dropped = bridge.card_facts("White Knight", "alice", "knight", db, corpus)
    pf = bf.get("protection_from") or set()
    check("White Knight: bridge feeds protection_from(knight, black)", ("knight", "black") in pf)

    bf2, _ = bridge.card_facts("Animar, Soul of Elements", "alice", "animar", db, corpus)
    pf2 = bf2.get("protection_from") or set()
    check("Animar: compound quality -> protection_from white AND black",
          ("animar", "white") in pf2 and ("animar", "black") in pf2)

    bf3, dr3 = bridge.card_facts("Cybernetica Datasmith", "alice", "ds", db, corpus)
    check("Cybernetica Datasmith (from robots): no protection_from", not bf3.get("protection_from"))
    check("Cybernetica Datasmith: protection abstains (non-colour)",
          any(k == "protection" for k, _ in dr3))

    # (b) END-TO-END through the engine: a same-colour spell targeting the protected creature FIZZLES.
    base = {k: set(v) for k, v in bf.items()}            # White Knight's bridge facts, instance 'knight'
    base.update({
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": {("knight",)},
        "printed_control": {("bob", "knight")},          # bob's creature; alice is casting at it
        "on_stack": {("bolt", 0)},
        "in_hand": {("alice", "bolt")},
        "targets": {("bolt", "knight")},
    })

    blk = dict(base); blk["spell_color"] = {("bolt", "black")}
    check("a BLACK spell targeting protection-from-black -> fizzles",
          ("bolt",) in driver.run(blk, ["fizzles"])["fizzles"])

    wht = dict(base); wht["spell_color"] = {("bolt", "white")}
    check("a WHITE spell targeting the same creature -> does NOT fizzle (control)",
          ("bolt",) not in driver.run(wht, ["fizzles"])["fizzles"])

    fails = [n for n, ok in CHECKS if not ok]
    for n, ok in CHECKS:
        print(("ok  " if ok else "FAIL") + "  " + n)
    print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} passed")
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    run()

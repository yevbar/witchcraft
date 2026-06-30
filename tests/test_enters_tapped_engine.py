"""test_enters_tapped_engine.py — the §614 'enters the battlefield tapped' fact is now WIRED into the engine
through the bridge.

The parser produces `card_enters_tapped(card, cond)` (718 cards), but sim.load_db didn't parse it and the
bridge didn't translate it — so the engine's EXISTING replacement machinery (repl_enters_tapped ->
enters_tapped -> the driver taps the entering permanent) never received it. This asserts the bridge now emits
`repl_enters_tapped(tid, tid)` for the UNCONDITIONAL form (cond '-') and faithfully ABSTAINS on a conditional
form ('unless you control …' / 'if …'), which the engine can't gate on a slug condition. (The downstream
chain is the same one driver.py already reads — driver applies `enters_tapped` when a permanent enters.)

Run: python3 test_enters_tapped_engine.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter import card_corpus
from mtg import sim
from mtg import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def run() -> None:
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}

    # sim.load_db parses the relation into the per-card dict.
    check("sim.load_db parses card_enters_tapped (Temple of Mystery)",
          db.get("temple_of_mystery", {}).get("enters_tapped") == "-")

    # UNCONDITIONAL -> repl_enters_tapped(tid, tid), no abstain.
    for name in ("Temple of Mystery", "Tranquil Cove"):
        bf, dropped = bridge.card_facts(name, "alice", "tX", db, corpus)
        check(f"{name}: bridge feeds repl_enters_tapped", ("tX", "tX") in (bf.get("repl_enters_tapped") or set()))
        check(f"{name}: no enters_tapped abstain", not any(k == "enters_tapped" for k, _ in dropped))

    # CONDITIONAL ('unless …' checkland) -> abstain (the engine can't evaluate the slug condition at ETB).
    bf, dropped = bridge.card_facts("Glacial Fortress", "alice", "tX", db, corpus)
    check("Glacial Fortress (conditional): no repl fed", not bf.get("repl_enters_tapped"))
    check("Glacial Fortress (conditional): abstains as enters_tapped",
          any(k == "enters_tapped" for k, _ in dropped))

    # A land that enters UNTAPPED (no card_enters_tapped fact) -> nothing fed, nothing abstained.
    bf, dropped = bridge.card_facts("Brushland", "alice", "tX", db, corpus)
    check("Brushland (untapped land): no repl, no enters_tapped abstain",
          not bf.get("repl_enters_tapped") and not any(k == "enters_tapped" for k, _ in dropped))

    fails = [n for n, ok in CHECKS if not ok]
    for n, ok in CHECKS:
        print(("ok  " if ok else "FAIL") + "  " + n)
    print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} passed")
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    run()

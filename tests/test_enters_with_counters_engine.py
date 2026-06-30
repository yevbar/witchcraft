"""test_enters_with_counters_engine.py — the §122 'enters with N +1/+1 counters' fact is now WIRED into the
engine through the bridge.

The parser produces `enters_with_counters(card, kind, amount)`; previously `sim.load_db` didn't parse it and
the bridge didn't translate it, so the engine's existing replacement machinery (`repl_enters_with_counter` ->
`enters_with_counter` -> the driver adds the counter, which the P/T rule sums) never received it. This asserts
the bridge now emits `repl_enters_with_counter(tid, tid, "p1p1", N)` for a static +1/+1 count and faithfully
ABSTAINS on a dynamic count ('X' / 'equal to …') or a non-+1/+1 kind. (The downstream engine chain is proven
by driver.py's self-playing demo — a creature with repl_enters_with_counter enters as a 3/3.)

Run: python3 test_enters_with_counters_engine.py
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


def _facts(name: str, db, corpus):
    return bridge.card_facts(name, "alice", "tX", db, corpus)


def run() -> None:
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}

    # sim.load_db now parses the relation into the per-card dict.
    for cid in ("triskelion", "ghave_guru_of_spores"):
        check(f"sim.load_db parses enters_with_counters for {cid}",
              isinstance(db.get(cid, {}).get("enters_with_counters"), tuple))

    # STATIC +1/+1 count -> repl_enters_with_counter(tid, tid, "p1p1", N), no abstain.
    for name, n in [("Triskelion", 3), ("Scrounging Bandar", 2), ("Ghave, Guru of Spores", 5)]:
        bf, dropped = _facts(name, db, corpus)
        rwc = bf.get("repl_enters_with_counter") or set()
        check(f"{name}: bridge feeds repl_enters_with_counter p1p1 x{n}",
              ("tX", "tX", "p1p1", n) in rwc)
        check(f"{name}: no enters_with_counters abstain", not any(k == "enters_with_counters" for k, _ in dropped))

    # DYNAMIC count ('X') -> faithful abstain (the engine can't resolve X at replacement time).
    bf, dropped = _facts("Rock Hydra", db, corpus)   # enters_with_counters(..., "1_1", "x")
    check("Rock Hydra (X counters): no repl fed", not bf.get("repl_enters_with_counter"))
    check("Rock Hydra (X counters): abstains as enters_with_counters",
          any(k == "enters_with_counters" for k, _ in dropped))

    # NON-+1/+1 kind ('1_0' on Clockwork Beast) -> abstain (the engine's counter math is +1/+1 / -1/-1 only).
    bf, dropped = _facts("Clockwork Beast", db, corpus)
    check("Clockwork Beast (+1/+0 counters): not fed as p1p1", not bf.get("repl_enters_with_counter"))
    check("Clockwork Beast (+1/+0 counters): abstains",
          any(k == "enters_with_counters" for k, _ in dropped))

    fails = [n for n, ok in CHECKS if not ok]
    for n, ok in CHECKS:
        print(("ok  " if ok else "FAIL") + "  " + n)
    print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} passed")
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    run()

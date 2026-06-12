"""test_engine_native.py — the compiled native engine must equal the souffle interpreter.

engine_native compiles datalog/engine_rules.dl to a native binary and drives it; this asserts its
output (every derived relation, as sets) is identical to the interpreter's over a spread of game
states, and that driver.py's full demos play byte-identically through either backend. Skips cleanly
when the local toolchain can't build a binary. Run: python3 test_engine_native.py
"""

from __future__ import annotations

import csv
import subprocess
import tempfile
from pathlib import Path

import engine_native
from driver import RULES, _facts_key, _lit


def _interp(fkey) -> dict:
    """The souffle-interpreter evaluation of a fact set (driver.py's fallback path), for comparison."""
    facts = "\n".join(f"{rel}({', '.join(map(_lit, row))})." for rel, rows in fkey for row in rows)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "e.dl").write_text(RULES + "\n" + facts)
        subprocess.run(["souffle", f"{d}/e.dl", "-D", d], check=True, capture_output=True)
        return {f.stem: {tuple(r) for r in csv.reader(f.open(), delimiter="\t")}
                for f in Path(d).glob("*.csv")}


# a spread of base states the engine derives consequences from.
STATES = [
    {  # combat: two creatures, simultaneous deaths
        "on_battlefield": {("bear",), ("ogre",)},
        "printed_control": {("alice", "bear"), ("bob", "ogre")},
        "printed_type": {("bear", "creature"), ("ogre", "creature")},
        "printed_power": {("bear", 2), ("ogre", 3)},
        "printed_toughness": {("bear", 2), ("ogre", 3)},
        "is_player": {("alice",), ("bob",)},
    },
    {  # empty board, just players
        "is_player": {("alice",), ("bob",)},
        "life": {("alice", 20), ("bob", 1)},
    },
    {  # a tapped attacker and an untapped blocker
        "on_battlefield": {("knight",), ("wall",)},
        "printed_control": {("alice", "knight"), ("bob", "wall")},
        "printed_type": {("knight", "creature"), ("wall", "creature")},
        "printed_power": {("knight", 4), ("wall", 0)},
        "printed_toughness": {("knight", 4), ("wall", 5)},
        "tapped": {("knight",)},
        "is_player": {("alice",), ("bob",)},
    },
]


def run() -> None:
    if not engine_native.available():
        print("native engine UNAVAILABLE on this toolchain — skipping (interpreter fallback covers it)")
        return

    checks = []
    for i, state in enumerate(STATES):
        fkey = _facts_key(state)
        # compare DERIVED FACTS: a relation that derived no rows carries no information and reads back as the
        # empty set in driver.run, so normalize both backends by dropping empty-valued relations (native now
        # omits them to save IO + cache memory; the interpreter helper still lists them).
        nat = {k: v for k, v in engine_native.evaluate(fkey).items() if v}
        intp = {k: v for k, v in _interp(fkey).items() if v}
        shared = set(nat) & set(intp)
        ok = nat.keys() == intp.keys() and all(nat[k] == intp[k] for k in shared)
        checks.append((f"state {i}: native relations == interpreter", ok))
        if not ok:
            only_n, only_i = set(nat) - set(intp), set(intp) - set(nat)
            diffs = [k for k in shared if nat[k] != intp[k]]
            print(f"  state {i} MISMATCH: only_native={sorted(only_n)[:4]} "
                  f"only_interp={sorted(only_i)[:4]} value_diffs={diffs[:4]}")

    # driver demos must play byte-identically through either backend
    import os
    for demo in ("demo", "demo_sacrifice"):
        out = {}
        for env, label in ((None, "native"), ("1", "interp")):
            os.environ.pop("MTG_NO_NATIVE", None)
            if env:
                os.environ["MTG_NO_NATIVE"] = env
            r = subprocess.run(["python3", "-c", f"import driver; driver.{demo}()"],
                               capture_output=True, text=True)
            out[label] = r.stdout
        os.environ.pop("MTG_NO_NATIVE", None)
        checks.append((f"driver.{demo}() byte-identical across backends", out["native"] == out["interp"]))

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(checks)} checks passed")
    if passed != len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

"""test_engine_incremental.py — the incremental `update` driver must equal the full-recompute engine.

engine_incremental drives the engine through the fork's `update` subroutine (stage the input diff, re-evaluate
only the affected strata) instead of a from-scratch fixpoint. This asserts the Phase-3 oracle at the driver
level: bootstrapping a state then incrementally UPDATING through a sequence of further states yields, at every
step, the SAME output relations as engine_inproc's full recompute of that state. Also reports the wall-clock
speedup at realistic scale (a ~500-fact board, one-creature diff per move).

Skips cleanly when the fork toolchain can't build the --incremental .so. Run: python3 test_engine_incremental.py
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "incremental" / "harness"))

import engine_inproc
import engine_incremental
from driver import _facts_key
from test_engine_native import STATES


def _norm(d: dict) -> dict:
    """Normalize an output dict to {rel: set(tuple-of-str)} so the two backends compare regardless of int/str."""
    return {k: {tuple(map(str, row)) for row in v} for k, v in d.items() if v}


def _diff(a: dict, b: dict):
    return [k for k in set(a) | set(b) if a.get(k, set()) != b.get(k, set())]


def _seq_check(states, label, results):
    """Drive `states` as a sequence through engine_incremental (bootstrap then update) and compare each step to
    engine_inproc's independent full recompute."""
    engine_incremental.reset()
    ok = True
    for i, st in enumerate(states):
        fkey = _facts_key(st)
        got = _norm(engine_incremental.evaluate(fkey))
        want = _norm(engine_inproc.evaluate(fkey))
        d = _diff(got, want)
        if d:
            print(f"  {label} step {i}: DIFFERS in {len(d)} relations: {d[:8]}")
            ok = False
    if ok:
        print(f"  {label}: {len(states)} steps, update == recompute ✓")
    results.append(ok)


def _bench(results):
    """Realistic-scale timing: a ~500-fact board, tap one creature per move. Reports incremental vs recompute."""
    try:
        from bench import make_state
    except Exception as e:
        print(f"  bench scale skipped ({e})")
        return
    base = make_state(100)
    tap = {k: set(v) for k, v in base.items()}
    tap["tapped"].add(("cr0",))
    seq = [base, tap] * 6  # alternate tap/untap as a move sequence

    # correctness across the sequence
    engine_incremental.reset()
    ok = True
    for st in seq:
        got = _norm(engine_incremental.evaluate(_facts_key(st)))
        want = _norm(engine_inproc.evaluate(_facts_key(st)))
        if _diff(got, want):
            ok = False
    results.append(ok)
    print(f"  scale (≈506 facts) correctness over {len(seq)} moves: {'✓' if ok else 'FAIL'}")

    # timing: incremental update vs engine_inproc full recompute, per move
    engine_incremental.reset()
    engine_incremental.evaluate(_facts_key(base))  # bootstrap (not timed)
    fk = [_facts_key(s) for s in seq]
    ut, rt = [], []
    for k in fk:
        t = time.perf_counter(); engine_incremental.evaluate(k); ut.append(time.perf_counter() - t)
    for k in fk:
        t = time.perf_counter(); engine_inproc.evaluate(k); rt.append(time.perf_counter() - t)
    um, rm = statistics.median(ut) * 1000, statistics.median(rt) * 1000
    print(f"  scale timing: incremental update={um:.3f}ms  engine_inproc recompute={rm:.3f}ms  "
          f"speedup={rm/um:.2f}x")


def run() -> int:
    if not engine_incremental.available() or not engine_inproc.available():
        print("engine_incremental/engine_inproc UNAVAILABLE on this toolchain — skipping")
        return 0
    results = []
    # the 3 real game states, driven as a sequence (arbitrary state->state transitions, all == recompute)
    _seq_check(STATES, "real states", results)
    # the reverse order too, to exercise different diffs
    _seq_check(list(reversed(STATES)), "real states (rev)", results)
    _bench(results)
    ok = all(results)
    print("ENGINE INCREMENTAL DRIVER (update == recompute):", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())

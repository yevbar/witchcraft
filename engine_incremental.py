"""engine_incremental.py — the compiled souffle engine driven by the INCREMENTAL `update` subroutine.

engine_inproc.py removed the subprocess + filesystem overhead but still recomputes the FULL fixpoint every
move (p->run()). This module goes further: it compiles the engine with the fork's `--incremental` strategy and,
per move, stages only the ACTUAL input diff into the `diff_plus_<R>` / `diff_minus_<R>` relations and calls the
`update` subroutine, which re-evaluates only the strata downstream of the change (selective-stratum + the
three-term delta/DRed update). At realistic scale (~485-fact states differing by ~1 move) this is ~2.3x faster
than a from-scratch recompute, and the resident relations are byte-identical to a full recompute (the Phase-3
oracle, verified in test_engine.py and test_engine_incremental.py).

It reuses the proven in-process harness (incremental/harness/harness.py) as the ctypes bridge — that Harness is
the incremental driver; this module wraps it with a stateful `evaluate(fkey)` matching engine_inproc.evaluate
(bootstrap once, then incremental updates), and an actual-diff staging convention (every relation, input+head
included — all SHIM_INPUTS relations are delta-eligible, so the update applies their diff in place).

Graceful degradation (agent-first): available() is False if the fork toolchain can't build the .so, and the
caller falls back to engine_inproc (full recompute) and then the interpreter. Nothing here is required for
correctness — only speed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import engine_native  # reuse _SRC / _edb / _wrapper so the wrapped program matches engine_inproc/engine_native

_HARNESS_DIR = Path(__file__).resolve().parent / "incremental" / "harness"
sys.path.insert(0, str(_HARNESS_DIR))
import harness  # noqa: E402  (the in-process incremental ctypes bridge — the fork's --incremental .so)

_H = None                     # the live Harness (one bootstrapped SouffleProgram, reused across moves)
_LOADED: dict | None = None   # {rel: frozenset(rows)} currently resident in the instance
_OUTPUTS: list | None = None  # the .output relation names to read back
_DECLS: list | None = None    # all declared relations (for staging-relation purge)
_PREV_OUT: dict | None = None  # last result, carried forward and patched per move (dump only CHANGED outputs)
_FAILED = False


def _prepare():
    """Compile + bootstrap-free setup: returns the (src, outputs, decls) for the wrapped engine, or None."""
    global _OUTPUTS, _DECLS
    rules = engine_native._SRC.read_text()
    _OUTPUTS = re.findall(r"^\.output\s+(\w+)", rules, re.M)
    _DECLS = re.findall(r"^\.decl\s+(\w+)", rules, re.M)
    return engine_native._wrapper(rules, engine_native._edb(rules))


def available() -> bool:
    return harness.available()


def reset() -> None:
    """Drop the live instance so the next evaluate() bootstraps fresh (e.g. starting a new game / test run)."""
    global _H, _LOADED, _PREV_OUT
    if _H is not None:
        _H.close()
    _H = None
    _LOADED = None
    _PREV_OUT = None


def _stage(old: dict, new: dict) -> dict:
    """Actual-diff staging for every relation: diff_plus_<R> = added rows, diff_minus_<R> = removed rows."""
    stage = {}
    for rel in set(old) | set(new):
        added = set(new.get(rel, set())) - set(old.get(rel, set()))
        removed = set(old.get(rel, set())) - set(new.get(rel, set()))
        if added:
            stage[f"diff_plus_{rel}"] = added
        if removed:
            stage[f"diff_minus_{rel}"] = removed
    return stage


def evaluate(fkey) -> dict:
    """Run the engine on a fact set via the incremental update; return {relation: set(tuples)} for every
    non-empty OUTPUT relation — same signature/return as engine_inproc.evaluate. The first call bootstraps a
    from-scratch fixpoint; each subsequent call stages the input diff from the previously-loaded state and runs
    the `update` subroutine. Raises if the library is unavailable (guard with available())."""
    global _H, _LOADED, _PREV_OUT
    if not available():
        raise RuntimeError("incremental engine library unavailable")
    new = {rel: set(rows) for rel, rows in fkey if rows}
    if _H is None:
        _H = harness.Harness(_prepare(), incremental=True)
        _H.bootstrap(new)
        _PREV_OUT = {rel: rows for rel, rows in _H.dump(_OUTPUTS).items() if rows}
    else:
        _H.insert(_stage(_LOADED, new))
        _H.update()
        # Dump only the outputs whose stratum RAN (its __dirty flag is set) — the rest are unchanged, so carry
        # them forward from the previous result. A dirty output that recomputed to empty is dropped. This is the
        # incremental analogue of engine_inproc's full serialize: O(changed outputs), not O(all outputs).
        dirty_flags = _H.dump([f"__dirty_{o}" for o in _OUTPUTS])
        dirty = [o for o in _OUTPUTS if f"__dirty_{o}" in dirty_flags]
        changed = _H.dump(dirty) if dirty else {}
        _H.purge_staging()  # clear all diff_plus_*/diff_minus_*/__dirty_* in one C++ pass
        out = dict(_PREV_OUT)
        for o in dirty:
            if changed.get(o):
                out[o] = changed[o]
            else:
                out.pop(o, None)
        _PREV_OUT = out
    _LOADED = new
    return dict(_PREV_OUT)


if __name__ == "__main__":
    if not available():
        print("incremental engine: UNAVAILABLE (fork toolchain required; falls back to engine_inproc)")
    else:
        print("incremental engine: available (fork --incremental + update subroutine)")

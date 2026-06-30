"""engine_incremental.py — the compiled souffle engine driven by the INCREMENTAL `update` subroutine.

engine_inproc.py removed the subprocess + filesystem overhead but still recomputes the FULL fixpoint every
move (p->run()). This module goes further: it compiles the engine with the fork's `--incremental` strategy and,
per move, stages only the ACTUAL input diff into the `diff_plus_<R>` / `diff_minus_<R>` relations and calls the
`update` subroutine, which re-evaluates only the strata downstream of the change (selective-stratum + the
three-term delta/DRed update). At realistic scale (~485-fact states differing by ~1 move) this is ~2x faster
than a from-scratch recompute, and the resident relations are byte-identical to a full recompute (the Phase-3
oracle, verified in test_engine.py and the full-demo byte-identity check; benchmarked in bench.py).

It reuses the proven in-process harness (incremental/harness/harness.py) as the ctypes bridge — that Harness is
the incremental driver; this module wraps it with a stateful `evaluate(fkey)` matching engine_inproc.evaluate
(bootstrap once, then incremental updates). Staging is actual-diff for every relation EXCEPT the input+head
(SHIM_INPUTS) relations on the RECOMPUTE path: those are swap-cleared and re-merged from diff_plus by the update,
so they must be staged with the FULL new input (actual-diff would drop their unchanged facts — the has_trigger
bug). The recompute set is read from the update RAM (`SWAP (R, @swap_R)`); the other input+head relations are
delta-eligible and take cheap actual-diff staging (the perf recovery — see bench.py).

Verified: byte-identical to a from-scratch recompute across an entire real demo game (27 engine calls), and
2.0–2.4x faster than recompute at ~506 facts (up to 10x at small scale).

Graceful degradation (agent-first): available() is False if the fork toolchain can't build the .so, and the
caller falls back to engine_inproc (full recompute) and then the interpreter. Nothing here is required for
correctness — only speed.
"""

from __future__ import annotations

import re
import sys
from contextlib import contextmanager
from pathlib import Path

from mtg import engine_native

_HARNESS_DIR = Path(__file__).resolve().parent.parent.parent / "incremental" / "harness"  # repo root (module in packages/mtg/)
sys.path.insert(0, str(_HARNESS_DIR))
import harness  # noqa: E402  (the in-process incremental ctypes bridge — the fork's --incremental .so)

_H = None                     # the live Harness (one bootstrapped SouffleProgram, reused across moves)
_LOADED: dict | None = None   # {rel: frozenset(rows)} currently resident in the instance
_OUTPUTS: list | None = None  # the .output relation names to read back
_DECLS: list | None = None    # all declared relations (for staging-relation purge)
_INH: set | None = None       # input+head (SHIM_INPUTS) relations — staged FULL (see _stage)
_PREV_OUT: dict | None = None  # last result, carried forward and patched per move (dump only CHANGED outputs)
_STACK: list = []             # branching stack: (loaded, prev_out) snapshots for O(diff) rollback (push/pop)
_FAILED = False


def _prepare():
    """Compile + bootstrap-free setup: returns the (src, outputs, decls) for the wrapped engine, or None."""
    global _OUTPUTS, _DECLS, _INH
    rules = engine_native._SRC.read_text()
    _OUTPUTS = re.findall(r"^\.output\s+(\w+)", rules, re.M)
    _DECLS = re.findall(r"^\.decl\s+(\w+)", rules, re.M)
    heads = set(re.findall(r"^(\w+)\(", rules, re.M))
    src = engine_native._wrapper(rules, engine_native._edb(rules))
    # _INH = the input+head relations that must be staged with the FULL new input: those on the RECOMPUTE path
    # (the update swap-clears them and re-merges only diff_plus, so actual-diff staging would drop unchanged
    # input facts). Determined authoritatively from the update RAM — a relation R is recompute iff it has a
    # `SWAP (R, @swap_R)`. The other input+head relations (and all pure relations) take actual-diff staging.
    input_and_head = set(engine_native._edb(rules)) & heads
    _INH = input_and_head & _recompute_relations(src)
    return src


def _recompute_relations(src: str) -> set:
    """The relations the update recomputes (swap-clears), from `souffle --incremental --show=initial-ram`.
    Returns an empty set if the show pass fails (then no input+head is full-staged — only safe if all are
    eligible, so callers that need correctness should treat an empty result cautiously).

    The result is a pure function of `src` (and the souffle binary), so it is CACHED to a file keyed by their
    content hash — the `--show=initial-ram` subprocess parses the whole engine (~290ms), which would otherwise
    be re-paid on every bootstrap/reset (startup latency). Cache hit reads the saved set in <1ms."""
    import hashlib
    import subprocess
    import tempfile
    if not harness._SOUFFLE.exists():
        return set()
    sver = str(harness._SOUFFLE.stat().st_mtime_ns)
    key = hashlib.sha1((src + sver).encode()).hexdigest()[:16]
    cache = Path(tempfile.gettempdir()) / f"mtg_recompute_{key}.txt"
    if cache.exists():
        return set(cache.read_text().split())
    with tempfile.NamedTemporaryFile("w", suffix=".dl", delete=False) as f:
        f.write(src)
        path = f.name
    r = subprocess.run([str(harness._SOUFFLE), "--incremental", "--show=initial-ram", path],
                       capture_output=True, text=True)
    rels = set(re.findall(r"SWAP \((\w+), @swap_", r.stdout))
    if rels:
        try:
            cache.write_text("\n".join(sorted(rels)))
        except OSError:
            pass  # cache is best-effort; correctness doesn't depend on it
    return rels


def available() -> bool:
    return harness.available()


def reset() -> None:
    """Drop the live instance so the next evaluate() bootstraps fresh (e.g. starting a new game / test run)."""
    global _H, _LOADED, _PREV_OUT, _STACK
    if _H is not None:
        _H.close()
    _H = None
    _LOADED = None
    _PREV_OUT = None
    _STACK = []


def _stage(old: dict, new: dict) -> dict:
    """Stage the input diff. For a pure relation, diff_plus = added rows, diff_minus = removed rows. For an
    INPUT+HEAD (SHIM_INPUTS) relation, stage diff_plus = the FULL new input: such a relation can be on the
    RECOMPUTE path (e.g. has_trigger, which depends on a non-eligible relation), where the update swap-clears it
    and re-merges diff_plus to restore the input — actual-diff staging would lose the unchanged input facts and
    silently drop them. (Eligible input+head relations process the full diff_plus correctly too, just not as
    cheaply; correctness over the per-move marshalling cost.)"""
    stage = {}
    for rel in set(old) | set(new):
        added = set(new.get(rel, set())) - set(old.get(rel, set()))
        removed = set(old.get(rel, set())) - set(new.get(rel, set()))
        if rel in _INH:
            if new.get(rel):
                stage[f"diff_plus_{rel}"] = set(new[rel])
            if removed:
                stage[f"diff_minus_{rel}"] = removed
        else:
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
        # Collect only the outputs whose stratum RAN (their __dirty flag set) and purge staging, in ONE C++ pass
        # (collect_dirty) — the rest are unchanged, so carry them forward from the previous result. This is the
        # incremental analogue of engine_inproc's full serialize: O(changed outputs), not O(all outputs), and one
        # ctypes round-trip instead of three (the dominant per-call overhead at search scale; see search_bench).
        collected = _H.collect_dirty(_OUTPUTS)
        dirty = {o for (o,) in collected.get("@dirty", ())}
        out = dict(_PREV_OUT)
        for o in dirty:
            rows = collected.get(o)
            if rows:
                out[o] = rows
            else:
                out.pop(o, None)  # dirty but recomputed to empty
        _PREV_OUT = out
    _LOADED = new
    return dict(_PREV_OUT)


# ── Game-tree search primitives ─────────────────────────────────────────────────────────────────────────────
# The `update` subroutine is direction-agnostic: staging the INVERSE of a move's diff (its insertions as
# deletions and vice-versa) rolls the resident relations back to the parent state — byte-identically, and at
# O(diff) cost, with no full snapshot. That makes the incremental engine a search substrate: from a state, try a
# move (push), evaluate the child, then roll back (pop) to try the next — exactly what a game-tree search needs.
# Validated in test_branching.py: every child evaluation and every rollback equals a fresh from-scratch recompute
# across repeated branches and nested push/pop.

def push(fkey) -> dict:
    """Apply a move and descend one ply: update to `fkey`'s state, remembering the current state so a matching
    pop() can roll back to it. Returns the child state's result (same shape as evaluate). The first push from a
    fresh engine bootstraps the root (and pop() below the root raises). O(diff)."""
    _STACK.append((_LOADED, _PREV_OUT))  # (None, None) at the root — pop() guards against descending past it
    return evaluate(fkey)


def pop() -> dict:
    """Ascend one ply: roll back the most recent push() by applying the inverse diff (the parent state restored
    via the same `update` subroutine), and return the parent's result. O(diff); does not re-dump outputs — the
    parent's result is restored from the snapshot. Raises if there is no push to undo / already at the root."""
    global _H, _LOADED, _PREV_OUT
    if not _STACK:
        raise RuntimeError("pop() without a matching push()")
    prev_loaded, prev_out = _STACK.pop()
    if prev_loaded is None:
        raise RuntimeError("cannot pop() below the root state")
    _H.insert(_stage(_LOADED, prev_loaded))  # inverse diff: current -> parent
    _H.update()
    _H.purge_staging()
    _LOADED = prev_loaded
    _PREV_OUT = prev_out
    return dict(_PREV_OUT)


@contextmanager
def branch(fkey):
    """`with branch(child_state) as result:` — push the move, yield the child result, and pop on exit (even on
    exception). Ergonomic single-ply exploration for search code."""
    result = push(fkey)
    try:
        yield result
    finally:
        pop()


if __name__ == "__main__":
    if not available():
        print("incremental engine: UNAVAILABLE (fork toolchain required; falls back to engine_inproc)")
    else:
        print("incremental engine: available (fork --incremental + update subroutine)")

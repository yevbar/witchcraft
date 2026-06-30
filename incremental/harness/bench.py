"""Benchmark the incremental `update` vs a full recompute on the real engine, in process.

Measures, for a resident engine instance, the median time to advance state N -> N+1 by:
  - incremental update: stage the input diff (per the input+head convention) + executeSubroutine("update");
  - full recompute:     a fresh bootstrap of state N+1 (purge + insert + run).

Both produce identical resident relations (asserted). The ratio is the selective-stratum speedup.

FINDINGS (Mac, in-process). CORRECT AND FAST: staging actual-diff for ELIGIBLE input+head and FULL new input
only for the (few) RECOMPUTE input+head (those with a `SWAP (R, @swap_R)` in the update RAM — e.g. has_trigger,
which depends on a non-eligible relation and is swap-cleared + re-merged from diff_plus, so actual-diff would
silently drop its unchanged input facts and drift the demo). The recompute set is read from the update RAM here
(matches engine_incremental._recompute_relations). This is both byte-identical to recompute AND recovers the
speedup that the (briefly-shipped, unsound) blanket actual-diff staging had:
    TAP sweep:          2cr 10.0x, 10cr 7.4x, 30cr 4.5x, 60cr 3.1x, 100cr 2.4x  (all correct=✓)
    ADD-CREATURE sweep: 2cr 5.7x,  10cr 4.9x, 30cr 3.3x, 60cr 2.4x, 100cr 2.0x  (all correct=✓)
  (The intermediate FULL-staging-for-ALL-input+head convention was correct but ~0.7x — it re-inflated the whole
  input chain every move; narrowing FULL staging to the recompute set is what recovers the win.)

  CORRECTNESS (Phase 6): SIMULTANEOUS multi-atom deletion is retracted correctly via MERGE-BACK — before the
  per-atom over-delete, each positive dependency is temporarily restored to its OLD state (current ∪
  truly-deleted, staged through the dep's @swap scratch) so non-target atoms read old; the union over the k
  versions = the join over the old database (the exact deletion delta). Fixes the long-standing data-carrying
  xfail (test_simultaneous_delete). Perf-NEUTRAL (numbers above hold), full closure eligibility (no body-size
  cap), k versions not 2^k-1. (The interim 2^k-1 subset enumeration was correct but bloated codegen + needed a
  cap; merge-back supersedes it.)
  REJECTED: precise-publish (expand eligibility to 98% by having recompute strata publish a precise diff) — it is
  correct but a NET PERF LOSS (2.4x->1.7x) + 7 min compile; see incremental/experiments/README.md.
  Two overhead removals lifted it from ~0.97x to ~1.3x at 506 facts (DONE):
    (a) SWAP-based clear instead of erase-scratch: recompute into a @swap temp, ram::Swap it with R, clear the
        temp (a temp's purge is unconditional even in a subroutine) — removed the ~2x O(|R|) erase copy.
        Synthesiser swaps non-temp (exposed) relations by CONTENT (std::swap(*A,*B)) so the RelationWrapper
        reference stays valid; only temp/temp swaps stay pointer swaps.
    (b) A per-stratum nullary "__dirty" flag instead of the whole-relation publish — removed the O(|R|) publish.
  Remaining O(|R|) cost: each DIRTY stratum still RECOMPUTES the whole relation (empty + re-derive), so the
  per-dirty-stratum cost scales with |R|. The win comes only from SKIPPING clean strata.

  CONCLUSIVE FINDING (per-stratum delta eligibility + candidate-restricted re-derive landed, both neutral on
  the engine): the ~1.3x ceiling is set by the NON-ELIGIBLE negation-bearing RECOMPUTE strata. 37% of the
  engine's IDB strata are delta-eligible (negation/aggregate-free, EDB-fed) and now run O(diff), but they are
  the cheap near-EDB strata; the cost is dominated by the negation strata downstream, which recompute. Two
  sweeps below — TAP (feeds negation immediately) and ADD-CREATURE (feeds the negation-free type/P-T chain) —
  are both ~1.3x, confirming the eligible-strata optimizations don't move the total.
  The deeper fix (the real win at scale): NEGATION-DELTA — make the recompute strata O(diff) by seeding the
  three-term update from negation sign-flips (diff_plus of a negated atom over-deletes; diff_minus re-derives).
  See PHASE3_UPDATE_PLAN.md. Only delta evaluation of the negation strata breaks the O(|R|) floor.

Run: python3 incremental/harness/bench.py  (compiles the 328-relation engine once, ~40s)
"""

import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import harness


def make_state(n):
    s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", "20"), ("bob", "20")},
         "on_battlefield": set(), "printed_type": set(), "printed_control": set(),
         "printed_power": set(), "printed_toughness": set(),
         "current_step": {("combat",)}, "active_player": {("alice",)}, "tapped": set()}
    for i in range(n):
        c = f"cr{i}"
        owner = "alice" if i % 2 else "bob"
        s["on_battlefield"].add((c,))
        s["printed_type"].add((c, "creature"))
        s["printed_control"].add((owner, c))
        s["printed_power"].add((c, str(1 + i % 5)))
        s["printed_toughness"].add((c, str(1 + i % 4)))
    return s


def main():
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    try:
        from mtg import engine_native
        from mtg.driver import RULES
    except Exception as e:
        print(f"engine modules unavailable ({e}) — skipping")
        return 0

    src = engine_native._wrapper(RULES, engine_native._edb(RULES))
    decls = re.findall(r"^\.decl\s+(\w+)", RULES, re.M)
    # input+head relations need FULL staging only when they're on the RECOMPUTE path (swap-clear + re-merge
    # diff_plus). The eligible ones take actual-diff staging — that's the perf recovery. Authoritative source:
    # the update RAM's `SWAP (R, @swap_R)` statements (see engine_incremental._recompute_relations).
    with tempfile.NamedTemporaryFile("w", suffix=".dl", delete=False) as _f:
        _f.write(src)
        _rampath = _f.name
    rr = subprocess.run([str(harness._SOUFFLE), "--incremental", "--show=initial-ram", _rampath],
                        capture_output=True, text=True)
    recompute = set(re.findall(r"SWAP \((\w+), @swap_", rr.stdout))
    inh = (set(engine_native._edb(RULES)) & set(re.findall(r"^(\w+)\(", RULES, re.M))) & recompute
    allpurge = [f"diff_{s}_{r}" for s in ("plus", "minus") for r in decls] + [f"__dirty_{r}" for r in decls]

    def stage(a, b):
        # Input diff: pure relations get the actual diff; an INPUT+HEAD (SHIM_INPUTS) relation gets the FULL
        # new input in diff_plus, since it can be on the recompute path (which re-merges diff_plus after a
        # swap-clear) — actual-diff staging would drop its unchanged input facts (the has_trigger bug).
        st = {}
        for rel in set(a) | set(b):
            p = b.get(rel, set()) - a.get(rel, set())
            m = a.get(rel, set()) - b.get(rel, set())
            if rel in inh:
                if b.get(rel):
                    st[f"diff_plus_{rel}"] = set(b[rel])
                if m:
                    st[f"diff_minus_{rel}"] = m
            else:
                if p:
                    st[f"diff_plus_{rel}"] = p
                if m:
                    st[f"diff_minus_{rel}"] = m
        return st

    print("compiling engine with --incremental ...")
    h = harness.Harness(src, incremental=True)

    def run(s0, s1, label, n=50):
        # correctness
        h.bootstrap(s0)
        h.insert(stage(s0, s1))
        h.update()
        h.purge(allpurge)
        got = {k: v for k, v in h.dump().items() if v and not k.startswith(("diff_plus_", "diff_minus_", "__dirty_", "@"))}
        f = harness.Harness(src, incremental=True)
        f.bootstrap(s1)
        want = {k: v for k, v in f.dump().items() if v and not k.startswith(("diff_plus_", "diff_minus_", "__dirty_", "@"))}
        f.close()
        ok = not [k for k in set(got) | set(want) if got.get(k, set()) != want.get(k, set())]
        ut, rt = [], []
        for _ in range(n):
            h.bootstrap(s0)
            st = stage(s0, s1)
            t = time.perf_counter()
            h.insert(st)
            h.update()
            ut.append(time.perf_counter() - t)
            h.purge(allpurge)
        for _ in range(n):
            t = time.perf_counter()
            h.bootstrap(s1)
            rt.append(time.perf_counter() - t)
        um, rm = statistics.median(ut) * 1000, statistics.median(rt) * 1000
        print(f"  {label:28s} correct={'✓' if ok else 'FAIL'}  update={um:.3f}ms  recompute={rm:.3f}ms  "
              f"speedup={rm/um:.2f}x")

    print("  TAP sweep (toggle `tapped` — feeds NEGATION immediately, so the dirty strata recompute):")
    for n in (2, 10, 30, 60, 100):
        base = make_state(n)
        tap = {k: set(v) for k, v in base.items()}
        tap["tapped"].add(("cr0",))
        nf = sum(len(v) for v in base.values())
        run(base, tap, f"{n} creatures (~{nf} facts)")

    print("  ADD-CREATURE sweep (feeds the negation-free type/P-T derivation chain — exercises the delta path):")
    for n in (2, 10, 30, 60, 100):
        base = make_state(n)
        grown = make_state(n + 1)  # one extra creature: diff lands on printed_* / on_battlefield (EDB)
        nf = sum(len(v) for v in base.values())
        run(base, grown, f"{n}->{n+1} creatures (~{nf} facts)")
    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Benchmark the incremental `update` vs a full recompute on the real engine, in process.

Measures, for a resident engine instance, the median time to advance state N -> N+1 by:
  - incremental update: stage the input diff (per the input+head convention) + executeSubroutine("update");
  - full recompute:     a fresh bootstrap of state N+1 (purge + insert + run).

Both produce identical resident relations (asserted). The ratio is the selective-stratum speedup.

FINDINGS (Mac, in-process). ⚠️ CORRECTNESS-FIRST UPDATE: the earlier "~2.3x at 506 facts" was measured with
ACTUAL-DIFF staging of input+head (SHIM_INPUTS) relations, which is UNSOUND — NOT all input+head relations are
delta-eligible (e.g. has_trigger depends on a non-eligible relation and is on the RECOMPUTE path, which swap-
clears it and re-merges only the diff, silently dropping unchanged input facts; the demo game drifted). The
correct convention (now in stage() + engine_incremental._stage) stages the FULL new input for input+head
relations, which re-introduces the input-chain re-processing and makes the driver ~0.7x at 506 facts — i.e.
SLOWER than engine_inproc when CORRECT. The perf is recoverable by staging actual-diff for ELIGIBLE input+head
and full only for the (few) RECOMPUTE input+head — TODO, needs the eligibility set plumbed to the driver.
  (historical, on the unsound actual-diff staging: 2cr 9.8x, 10cr 7.2x, 30cr 4.0x, 60cr 3.0x, 100cr 2.3x.)
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
import sys
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
        import engine_native
        from driver import RULES
    except Exception as e:
        print(f"engine modules unavailable ({e}) — skipping")
        return 0

    src = engine_native._wrapper(RULES, engine_native._edb(RULES))
    decls = re.findall(r"^\.decl\s+(\w+)", RULES, re.M)
    inh = set(engine_native._edb(RULES)) & set(re.findall(r"^(\w+)\(", RULES, re.M))  # input+head — staged FULL
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

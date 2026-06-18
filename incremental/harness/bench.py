"""Benchmark the incremental `update` vs a full recompute on the real engine, in process.

Measures, for a resident engine instance, the median time to advance state N -> N+1 by:
  - incremental update: stage the input diff (per the input+head convention) + executeSubroutine("update");
  - full recompute:     a fresh bootstrap of state N+1 (purge + insert + run).

Both produce identical resident relations (asserted). The ratio is the selective-stratum speedup.

FINDINGS (Mac, in-process):
  - The speedup grows with state size and shrinks with the fixed per-update overhead. On a tiny state
    (2 creatures) it is ~5x; on a ~30-creature state it is ~1.3x, roughly independent of how localized the
    change is (tap 1 creature vs add 1 creature vs life change all ~1.3x).
  - The cap is fixed overhead, not dirty-set size: (a) the guard evaluates a clean-condition (emptiness checks
    over each stratum's dependency diffs) for ALL ~250 strata every update; (b) each recomputed stratum copies
    its whole relation (the erase scratch old->diff_minus, plus the conservative new->diff_plus publish). On an
    already-fast in-process recompute (~0.13ms) that overhead dominates the skip savings.
  - Optimization path to approach the Phase-0 ~3-4x: a cheap per-stratum "ran" flag instead of the
    whole-relation publish (removes one O(relation) copy per dirty stratum), and a cheaper guard (one flag
    check instead of many emptiness checks). The win should also widen on larger states where the recompute
    cost dominates the fixed overhead.

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
    heads = set(re.findall(r"^(\w+)\(", RULES, re.M))
    inh = set(engine_native._edb(RULES)) & heads
    allpurge = [f"diff_{s}_{r}" for s in ("plus", "minus") for r in decls]

    def stage(a, b):
        st = {}
        for rel in set(a) | set(b):
            if rel in inh:
                if b.get(rel):
                    st[f"diff_plus_{rel}"] = b[rel]
                d = a.get(rel, set()) - b.get(rel, set())
                if d:
                    st[f"diff_minus_{rel}"] = d
            else:
                p = b.get(rel, set()) - a.get(rel, set())
                m = a.get(rel, set()) - b.get(rel, set())
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
        got = {k: v for k, v in h.dump().items() if v and not k.startswith(("diff_plus_", "diff_minus_"))}
        f = harness.Harness(src, incremental=True)
        f.bootstrap(s1)
        want = {k: v for k, v in f.dump().items() if v and not k.startswith(("diff_plus_", "diff_minus_"))}
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

    for n in (2, 30):
        base = make_state(n)
        tap = {k: set(v) for k, v in base.items()}
        tap["tapped"].add(("cr0",))
        run(base, tap, f"{n} creatures, tap (1 fact)")
    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

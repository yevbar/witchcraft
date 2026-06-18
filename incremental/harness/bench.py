"""Benchmark the incremental `update` vs a full recompute on the real engine, in process.

Measures, for a resident engine instance, the median time to advance state N -> N+1 by:
  - incremental update: stage the input diff (per the input+head convention) + executeSubroutine("update");
  - full recompute:     a fresh bootstrap of state N+1 (purge + insert + run).

Both produce identical resident relations (asserted). The ratio is the selective-stratum speedup.

FINDINGS (Mac, in-process) — the speedup SHRINKS as the state grows:
      2 creatures ( 16 facts): ~5.3x       30 creatures (156 facts): ~1.3x
     10 creatures ( 56 facts): ~2.4x       60 creatures (306 facts): ~1.1x
                                          100 creatures (506 facts): ~0.97x  (SLOWER than recompute)
  Real engine states are ~485 facts, so AT REALISTIC SCALE the recompute-based update is not a win.
  Root cause: each DIRTY stratum does O(|R|) overhead that scales with the relation's size —
    (a) the erase scratch (copy R->diff_minus, then erase, ~2x O(|R|), with the costlier btree_delete erase),
    (b) the conservative publish (copy R->diff_plus, O(|R|)),
  on top of the recompute itself. As relations grow with the state, this per-dirty-stratum copy cost exceeds
  what is saved by skipping clean strata. The win is real only when relations are small.
  Optimization path (needed for a real win at scale):
    1. SWAP-based clear instead of erase-scratch: recompute into a temp, ram::Swap it with R, clear the temp
       (a temp's purge is unconditional even in a subroutine) — removes the ~2x O(|R|) erase copy.
    2. A per-stratum nullary "ran" flag instead of the whole-relation publish — removes the O(|R|) publish.
    3. The deeper fix: a DELTA-based update for non-monotone strata (the paper's three-term update with
       negation), which is O(diff) not O(|R|). The recompute approach is correct but fundamentally O(|R|) per
       dirty stratum; only delta evaluation breaks that.

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
    allpurge = [f"diff_{s}_{r}" for s in ("plus", "minus") for r in decls] + [f"__dirty_{r}" for r in decls]

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

    print("  state size sweep (tap 1 creature; the win shrinks as relations grow):")
    for n in (2, 10, 30, 60, 100):
        base = make_state(n)
        tap = {k: set(v) for k, v in base.items()}
        tap["tapped"].add(("cr0",))
        nf = sum(len(v) for v in base.values())
        run(base, tap, f"{n} creatures (~{nf} facts)")
    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

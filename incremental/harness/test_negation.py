"""Test the NON-MONOTONE (negation) update path: update == fresh recompute.

A program with negation is not monotone — inserting a fact can DELETE derived tuples (the negated atom now
holds) and deleting one can ADD them. The update handles this by recomputing the affected (non-monotone)
strata from scratch, so sign-flips are retracted/re-derived correctly. This is the path the engine (which has
negation) uses; it is correct here, and the selective-stratum optimization that skips unchanged strata is the
future throughput win.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

# derived(x) :- base(x), !blocked(x).  — single negated atom.
NEG = """\
.decl base(x:number)
.input base
.decl blocked(x:number)
.input blocked
.decl derived(x:number)
derived(x) :- base(x), !blocked(x).
.output derived
"""

# Stratified two-level negation: live = node and not dead; reachable derived over live, negated again.
STRAT = """\
.decl node(x:number)
.input node
.decl dead(x:number)
.input dead
.decl edge(a:number, b:number)
.input edge
.decl live(x:number)
.decl orphan(x:number)
live(x) :- node(x), !dead(x).
orphan(x) :- live(x), !edge(_, x).
.output live
.output orphan
"""

PURGE_NEG = ["diff_plus_base", "diff_minus_base", "diff_plus_blocked", "diff_minus_blocked",
             "diff_plus_derived", "diff_minus_derived"]
PURGE_STRAT = [f"{p}_{r}" for p in ("diff_plus", "diff_minus")
               for r in ("node", "dead", "edge", "live", "orphan")]


def check(dl, boot, stage, purge, rels, label, results):
    h = harness.Harness(dl, incremental=True)
    h.bootstrap(boot)
    h.insert(stage)
    h.update()
    h.purge(purge)
    got = h.dump(rels)
    h.close()
    # fresh recompute over the net facts
    net = {}
    for rel, rows in boot.items():
        net[rel] = set(rows)
    for sk, rows in stage.items():
        base_rel = sk.replace("diff_plus_", "").replace("diff_minus_", "")
        net.setdefault(base_rel, set())
        if sk.startswith("diff_plus_"):
            net[base_rel] |= set(rows)
        else:
            net[base_rel] -= set(rows)
    f = harness.Harness(dl, incremental=True)
    f.bootstrap(net)
    want = f.dump(rels)
    f.close()
    ok = all(got.get(r, set()) == want.get(r, set()) for r in rels)
    print(f"  {label}: {'✓' if ok else 'FAIL ' + str({r: (sorted(got.get(r,set())), sorted(want.get(r,set()))) for r in rels})}")
    results.append(ok)


def main():
    if not harness.available():
        print("harness UNAVAILABLE — skipping")
        return 0
    r = []
    base = {("1",), ("2",), ("3",)}
    check(NEG, {"base": base, "blocked": {("2",)}}, {"diff_plus_blocked": {("1",)}}, PURGE_NEG, ["derived"],
          "insert into negated atom retracts derived", r)
    check(NEG, {"base": base, "blocked": {("2",)}}, {"diff_minus_blocked": {("2",)}}, PURGE_NEG, ["derived"],
          "delete from negated atom re-derives derived", r)
    check(NEG, {"base": base, "blocked": {("2",)}}, {"diff_plus_base": {("5",)}}, PURGE_NEG, ["derived"],
          "insert into positive atom adds derived (with negation)", r)
    # stratified, two negation levels
    sb = {"node": {("1",), ("2",), ("3",)}, "dead": {("3",)}, "edge": {("1", "2")}}
    check(STRAT, sb, {"diff_plus_dead": {("1",)}}, PURGE_STRAT, ["live", "orphan"],
          "stratified: kill node 1 flips live AND orphan", r)
    check(STRAT, sb, {"diff_plus_edge": {("9", "3")}}, PURGE_STRAT, ["live", "orphan"],
          "stratified: new edge into 3 removes orphan(3)", r)

    print("NEGATION (non-monotone recompute):", "PASS ✓" if all(r) else "FAIL ✗")
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())

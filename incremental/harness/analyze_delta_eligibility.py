"""Measure the delta-eligible frontier of the real engine — does a delta-based update pay off?

The incremental `update` currently RECOMPUTES every dirty stratum (O(|R|)). The next lever is a delta path
(O(diff)). A non-recursive stratum can use the delta path EVEN IN A NON-MONOTONE PROGRAM iff:
  (1) it is non-recursive (a diff-seeded fixpoint can't retract), AND
  (2) its own clauses contain NO negation and NO aggregate — the current delta machinery (generateDeltaRules /
      DeltaRewriter) rewrites SCANS over diff_plus/diff_minus; a negated atom is an existence check, not a scan,
      and an aggregate must see the whole relation, so neither can be made delta without new machinery, AND
  (3) every dependency hands it a PRECISE small diff — i.e. each dependency is either an EDB input (small staged
      diff) or itself a delta-eligible stratum. A recomputed stratum only sets __dirty; it does not publish a
      precise diff, so anything downstream of a recompute must also recompute.

(3) makes eligibility a CLOSURE over the stratum DAG (eligible ⇐ non-recursive ∧ neg/agg-free ∧ every dep
EDB-or-eligible). This script computes that closure on the real engine and reports how much of the IDB it
covers — i.e. the ceiling of the delta win achievable WITHOUT building negation-delta machinery, and (relaxing
condition 2 to aggregates-only) the ceiling if negation-delta were also built.

Authoritative stratification comes from `souffle --show=scc-graph-text` (the fork binary); negation/aggregate
per relation is parsed from the rules. Pure analysis — compiles nothing, runs in ~1s.

Run: python3 incremental/harness/analyze_delta_eligibility.py
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

_ROOT = Path(__file__).resolve().parent.parent.parent
_SOUFFLE = _ROOT / "third_party" / "souffle" / "build" / "src" / "souffle"


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def split_statements(text):
    """Yield top-level statements (split on `.` outside parens/braces, not inside a decimal)."""
    depth = 0
    buf = []
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "." and depth == 0:
            prev = text[i - 1] if i else " "
            nxt = text[i + 1] if i + 1 < len(text) else " "
            if not (prev.isdigit() and nxt.isdigit()):  # not a decimal point
                stmt = "".join(buf).strip()
                if stmt:
                    yield stmt
                buf = []
                continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        yield tail


def parse_rule_flags(rules):
    """relation -> {'neg': bool, 'agg': bool} aggregated over all its clauses (a directive like .decl is skipped)."""
    flags = {}
    for stmt in split_statements(strip_comments(rules)):
        if stmt.startswith("."):  # directive
            continue
        if ":-" not in stmt:  # a fact, not a rule
            continue
        head, body = stmt.split(":-", 1)
        m = re.match(r"\s*(\w+)\s*\(", head)
        if not m:
            continue
        rel = m.group(1)
        neg = bool(re.search(r"!\s*\w+\s*\(", body))  # !atom (not !=)
        agg = "{" in body  # souffle uses braces only for aggregate bodies
        f = flags.setdefault(rel, {"neg": False, "agg": False})
        f["neg"] |= neg
        f["agg"] |= agg
    return flags


def parse_scc_graph(dot):
    """Parse `--show=scc-graph-text` dot output into (sccs: id->[relations], edges: id->set(dep_ids), recursive set)."""
    sccs = {}
    for m in re.finditer(r'_(\d+)\[label = "([^"]*)"', dot):
        sid = int(m.group(1))
        label = m.group(2)
        rels = [r.strip() for r in label.replace("\\n", "\n").split(",")]
        rels = [r for r in rels if r and not r.startswith("+disconnected")]
        sccs[sid] = rels
    # edge `_a -> _b` means a precedes b (a is a dependency of b) in souffle's scc-graph
    edges = {sid: set() for sid in sccs}  # edges[b] = set of deps a
    for m in re.finditer(r"_(\d+)\s*->\s*_(\d+)", dot):
        a, b = int(m.group(1)), int(m.group(2))
        if a in sccs and b in sccs:
            edges[b].add(a)
    return sccs, edges


def main():
    if not _SOUFFLE.exists():
        print("fork souffle binary not built — skipping")
        return 0
    try:
        import engine_native
        from driver import RULES
    except Exception as e:
        print(f"engine modules unavailable ({e}) — skipping")
        return 0

    edb = set(engine_native._edb(RULES))
    src = engine_native._wrapper(RULES, edb)
    with tempfile.NamedTemporaryFile("w", suffix=".dl", delete=False) as f:
        f.write(src)
        path = f.name
    dot = subprocess.run([str(_SOUFFLE), "--show=scc-graph-text", path],
                         capture_output=True, text=True).stdout
    sccs, deps = parse_scc_graph(dot)
    flags = parse_rule_flags(RULES)

    rel2scc = {r: sid for sid, rels in sccs.items() for r in rels}

    def recursive(sid):
        return len(sccs[sid]) > 1 or sid in deps.get(sid, set())

    def all_edb(sid):
        return all(r in edb for r in sccs[sid])

    def neg_free(sid):
        return not any(flags.get(r, {}).get("neg") for r in sccs[sid])

    def agg_free(sid):
        return not any(flags.get(r, {}).get("agg") for r in sccs[sid])

    # IDB strata: those with at least one non-EDB relation (the ones the update must (re)compute).
    idb_sccs = [sid for sid in sccs if not all_edb(sid)]

    # Closure: eligible ⇐ non-recursive ∧ neg-free ∧ agg-free ∧ every dep EDB-or-eligible.
    # Compute two frontiers: `delta` (condition 2 = neg-free AND agg-free, buildable today) and `ideal`
    # (condition 2 relaxed to agg-free only — the ceiling if negation-delta machinery were also built).
    def closure(blocked):
        elig = set()
        changed = True
        while changed:
            changed = False
            for sid in idb_sccs:
                if sid in elig or blocked(sid):
                    continue
                if all(d in elig or all_edb(d) for d in deps.get(sid, set())):
                    elig.add(sid)
                    changed = True
        return elig

    delta = closure(lambda s: recursive(s) or not neg_free(s) or not agg_free(s))
    ideal = closure(lambda s: recursive(s) or not agg_free(s))

    n_idb = len(idb_sccs)
    rec = sum(1 for s in idb_sccs if recursive(s))
    has_neg = sum(1 for s in idb_sccs if not neg_free(s))
    has_agg = sum(1 for s in idb_sccs if not agg_free(s))

    def rels_in(sset):
        return sum(len(sccs[s]) for s in sset)

    print("=== delta-eligibility of the real engine (IDB strata only) ===")
    print(f"  IDB strata (non-EDB SCCs):           {n_idb}   ({rels_in(idb_sccs)} relations)")
    print(f"    recursive:                         {rec}")
    print(f"    contain negation (own clauses):    {has_neg}")
    print(f"    contain aggregate (own clauses):   {has_agg}")
    print(f"  EDB (extensional input) relations:   {len(edb)}")
    print()
    print("  DELTA-eligible closure (buildable TODAY — neg-free ∧ agg-free ∧ non-rec ∧ deps eligible):")
    print(f"    strata:    {len(delta):4d} / {n_idb}  ({100*len(delta)/n_idb:.0f}% of IDB strata)")
    print(f"    relations: {rels_in(delta):4d} / {rels_in(idb_sccs)}")
    print()
    print("  IDEAL closure (IF negation-delta machinery existed — agg-free ∧ non-rec ∧ deps eligible):")
    print(f"    strata:    {len(ideal):4d} / {n_idb}  ({100*len(ideal)/n_idb:.0f}% of IDB strata)")
    print(f"    relations: {rels_in(ideal):4d} / {rels_in(idb_sccs)}")
    print()
    # What blocks the rest? The first non-eligible dependency layer is the useful target.
    blocked_by_neg = [s for s in idb_sccs if s not in delta and s in ideal]
    print(f"  Strata blocked from `delta` ONLY by negation (would unlock with negation-delta): {len(blocked_by_neg)}")
    print(f"  Strata blocked even in `ideal` (recursion/aggregate, or downstream of one):      {n_idb - len(ideal)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

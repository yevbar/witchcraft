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
        from mtg import engine_native
        from mtg.driver import RULES
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

    # PRECISE-PUBLISH ceiling: if every RECOMPUTE stratum (recursive or aggregate-bearing) published a PRECISE
    # diff (diff_plus = R_new\R_old, diff_minus = R_old\R_new — the old contents sit in @swap_R before the
    # clear), then requirement (3) "every dep hands a precise diff" is satisfied by ANY dep (recompute strata
    # become valid precise-diff sources too). Eligibility then reduces to JUST the LOCAL block: non-recursive ∧
    # aggregate-free. Negation is fine (negation-delta consumes the published diff). This is the ceiling the
    # precise-publish change would unlock — no transitive (downstream-of-recompute) blocking remains.
    pubideal = {s for s in idb_sccs if not recursive(s) and agg_free(s)}
    downstream_unlock = pubideal - ideal  # strata blocked ONLY by being downstream of a recompute stratum

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
    print("  LIVE delta-eligible closure (matches the C++: agg-free ∧ non-rec ∧ deps eligible — negation is")
    print("  handled by the negation-delta rules, so only aggregates and recursion force a recompute):")
    print(f"    strata:    {len(ideal):4d} / {n_idb}  ({100*len(ideal)/n_idb:.0f}% of IDB strata)")
    print(f"    relations: {rels_in(ideal):4d} / {rels_in(idb_sccs)}")
    print(f"  (neg-free-only closure, for reference: {len(delta)} strata — what was eligible before negation-delta)")
    print(f"  Strata blocked from delta (recursion/aggregate, or downstream of one): {n_idb - len(ideal)}")
    print()
    print("  PRECISE-PUBLISH ceiling (REJECTED experiment — see incremental/experiments/README.md):")
    print(f"    eligible strata:   {len(pubideal):4d} / {n_idb}  ({100*len(pubideal)/n_idb:.0f}% of IDB strata)")
    print(f"    DOWNSTREAM UNLOCK: {len(downstream_unlock):4d} strata ({rels_in(downstream_unlock)} relations) would move "
          f"from recompute O(|R|) to delta O(diff)")
    print(f"    still recompute:   {n_idb - len(pubideal)} (the {rec} recursive + {has_agg} aggregate source strata only)")
    print("    NOTE: implemented + correct but a NET PERF LOSS (2.4x->1.7x) — the unlocked strata are the cheap")
    print("    near-EDB ones, where delta machinery costs more than recompute. This is a strata-COUNT ceiling,")
    print("    not a perf ceiling. Kept as analysis only; not pursued.")
    print()

    # STAGING SET — which input+head (SHIM_INPUTS) relations need FULL-input staging. The AUTHORITATIVE source is
    # the update RAM, not this SCC analysis: a relation is on the RECOMPUTE path (swap-cleared + re-merged from
    # diff_plus, so it needs the full input re-staged) iff the RAM emits `SWAP (R, @swap_R)`. The SCC closure
    # above is only a *predictor* of that set — and it has a blind spot: relations absent from the scc-graph
    # parse (e.g. propositions, or anything the dot output collapses) were silently treated as eligible, which is
    # exactly the has_trigger bug that drifted the demo. So we read the RAM and CROSS-CHECK the SCC prediction
    # against it, surfacing any disagreement (especially relations the SCC parse never saw).
    heads = set(re.findall(r"^(\w+)\(", RULES, re.M))
    input_and_head = sorted((edb & heads))
    ram = subprocess.run([str(_SOUFFLE), "--incremental", "--show=initial-ram", path],
                         capture_output=True, text=True).stdout
    recompute_rels = set(re.findall(r"SWAP \((\w+), @swap_", ram))  # ground truth: needs FULL staging
    need_full = sorted(r for r in input_and_head if r in recompute_rels)
    eligible = sorted(r for r in input_and_head if r not in recompute_rels)
    print(f"  STAGING (authoritative, from update RAM) — input+head (SHIM_INPUTS) relations: {len(input_and_head)}")
    print(f"    delta-eligible (actual-diff staging):     {len(eligible)}")
    print(f"    on recompute path (FULL-input staging):   {len(need_full)}  {need_full[:8]}")
    # cross-check: does the SCC closure predict the same set the RAM dictates?
    scc_predicts_full = {r for r in input_and_head
                         if rel2scc.get(r) is not None and rel2scc[r] not in ideal and not all_edb(rel2scc[r])}
    missing_from_scc = [r for r in need_full if rel2scc.get(r) is None]
    mispredicted = sorted((scc_predicts_full ^ set(need_full)))
    if missing_from_scc:
        print(f"  ⚠ SCC-parse BLIND SPOT: {len(missing_from_scc)} recompute relations are absent from the "
              f"scc-graph parse and would be misjudged eligible by SCC analysis alone: {missing_from_scc[:8]}")
    if mispredicted:
        print(f"  ⚠ SCC closure disagrees with the RAM on {len(mispredicted)} relations: {mispredicted[:8]}")
        print("    (the RAM is authoritative; this is why engine_incremental/bench/test_engine read SWAP, not SCC)")
    else:
        print("  ✓ SCC closure matches the RAM-authoritative recompute set exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())

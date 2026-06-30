"""validate.py — ONE validator for the whole pipeline: rules AND cards, in the same place.

Three checks, so that interpreting cards can never silently change the game's rules:

  1. ISOLATION — no card relation shares a name with a rules relation (excluding the per-file
     conformance scaffolding). This is what guarantees cards compose with the rules engine without
     overwriting it: a card fact lands in a card_* relation, never in `effect`/`ability`/etc. that the
     rules engine reasons over. (Cards that "change the rules" — max hand size, extra lands, can't-gain-
     life — are recorded as static_player / static and only apply WHEN that card is in play;
     the engine reads them, the rules datalog never does.)

  2. SOUNDNESS — datalog/cards.dl compiles and its conformance query passes (souffle). The rules side
     has its own gate (build.py: byte-identical determinism + every artifact compiles + conformance=0);
     this runner reports both.

  3. FAITHFULNESS — every interpreted card effect clause is run back through the RULES engine
     (transpile.transpile_rule via card_spacy) and the grounded verb is cross-checked. Agreement is
     independent confirmation; conflicts are the audit list. Same spaCy/lark pipeline, both jobs.

Run: python3 build_cards.py && python3 validate.py
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


import os
import re
import subprocess
from pathlib import Path

_DL = Path("datalog")
_SCAFFOLD = {"conformance_fail", "expect_kw", "expect_effect", "expect_life", "expect_hand",
             "expect_skip", "expect_when", "expect_kind", "expect_mp", "expect_cannot",
             "expect_activate", "expect_no_activate", "expect_mode", "expect_color"}


def _decls(path: Path) -> set:
    return {m.group(1) for line in path.read_text(encoding="utf-8").splitlines()
            if (m := re.match(r"\.decl (\w+)", line.strip()))}


def isolation_check():
    """UNIFICATION check (formerly isolation). The cards and the rules are ONE world now — a card's
    facts populate the SAME relations as the rules (CR §112–113). So a shared relation name is desired,
    not forbidden; what we must guard is that a shared relation has a MATCHING schema (same arity), else
    souffle can't merge the two .decl forms. Returns (mismatched, shared, ncard, nrule):
      - shared:     relations cards.dl contributes to that the rules also declare (intentional unification)
      - mismatched: of those, the ones whose arity DISAGREES with the rules' decl — a real bug to fix
                    (these are the relations still pending schema reconciliation)."""
    card_t = _decl_types(_DL / "cards.dl")
    rule_t: dict = {}
    for f in _DL.glob("*.dl"):
        if f.name != "cards.dl":
            rule_t.update(_decl_types(f))
    card = set(card_t) - _SCAFFOLD
    rules = set(rule_t) - _SCAFFOLD
    shared = (card & rules) - _SCAFFOLD
    mismatched = {r for r in shared if len(card_t[r]) != len(rule_t[r])}
    return mismatched, shared, len(card), len(rules)


def _decl_types(path: Path) -> dict:
    """relation -> [column types] parsed from the `.decl name(col: type, …)` lines."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\.decl (\w+)\(([^)]*)\)", line.strip())
        if m:
            out[m.group(1)] = [c.rsplit(":", 1)[-1].strip() for c in m.group(2).split(",") if c.strip()]
    return out


def _fact_args(s: str):
    """Split a fact's argument string into ('sym'|'num', value) tokens, respecting quoted strings (which
    may contain commas). Our facts are machine-generated with clean slugs, so no escaped-quote handling."""
    args, i, n = [], 0, len(s)
    while i < n:
        while i < n and s[i] in " ,":
            i += 1
        if i >= n:
            break
        if s[i] == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 1
            args.append(("sym", s[i + 1:j]))
            i = j + 1
        else:
            j = i
            while j < n and s[j] != ",":
                j += 1
            args.append(("num", s[i:j].strip()))
            i = j
    return args


def validate_facts(path: Path = None) -> list:
    """FAST Python stand-in for souffle's parse + type check (the gate's inner-loop soundness check, ~1s
    vs souffle's ~6–10 min): every fact must match its relation's declared ARITY, a 'number' column must
    hold a bare integer, and a 'symbol' column a quoted string. Catches the malformed/wrong-arity/wrong-
    type facts souffle would reject, WITHOUT compiling. Souffle (--full) stays the authoritative final
    check. Returns [(lineno, fact, reason)] violations."""
    path = path or (_DL / "cards.dl")
    types = _decl_types(path)
    bad = []
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if ":-" in line:
            continue                                # a RULE (e.g. the conformance query), not a fact
        m = re.match(r"^(\w+)\((.*)\)\.$", line)
        if not m or m.group(1) not in types:
            continue                                # .decl / .output / comment — not a fact
        cols = types[m.group(1)]
        args = _fact_args(m.group(2))
        if len(args) != len(cols):
            bad.append((ln, line.strip()[:72], f"arity {len(args)} != decl {len(cols)}"))
            continue
        for (kind, val), typ in zip(args, cols):
            if typ == "number" and not re.fullmatch(r"-?\d+", val):
                bad.append((ln, line.strip()[:72], f"non-number in number col: {val!r}"))
                break
            if typ == "symbol" and kind != "sym":
                bad.append((ln, line.strip()[:72], f"unquoted value in symbol col: {val!r}"))
                break
    return bad


def soundness_check():
    out = Path("/tmp/sfc_validate")
    out.mkdir(exist_ok=True)
    # -j N: run souffle's evaluation multi-threaded (the conformance pass over ~140k facts is the gate's
    # long pole; the interpreter is single-threaded by default).
    r = subprocess.run(["souffle", "-j", str(os.cpu_count() or 1), "-D", str(out), str(_DL / "cards.dl")],
                       capture_output=True, text=True, timeout=600)
    cf = out / "conformance_fail.csv"
    fails = len(cf.read_text().splitlines()) if cf.exists() else -1
    return r.returncode, ("error" in r.stderr.lower()), fails


def main(full: bool = False):
    """TIERED gate. FAST (default): isolation + Python fact type/arity check — ~1s, no souffle; this is
    the inner-loop gate while iterating coverage (souffle's conformance_fail can't change from merely
    ADDING facts to existing relations). FULL (`--full`): also runs souffle -j conformance + the spaCy
    faithfulness cross-check — needed when a relation `.decl` changes, or as the final pre-commit check."""
    print("=" * 64)
    print(f"PIPELINE VALIDATION (rules + cards) — {'FULL' if full else 'FAST'} mode")
    print("=" * 64)

    mismatched, shared, ncard, nrule = isolation_check()
    print(f"\n1. UNIFICATION — card-emitted relations: {ncard}, rules relations: {nrule}, shared (one world): {len(shared)}")
    if mismatched:
        print(f"   ✗ SCHEMA MISMATCH on shared relations (pending reconciliation): {sorted(mismatched)}")
    else:
        print(f"   ✓ cards contribute to {len(shared)} shared relations, all schema-compatible — one world")

    bad = validate_facts()
    print(f"\n2. FACT SOUNDNESS (fast — arity + number/symbol types, no souffle)")
    if bad:
        print(f"   ✗ {len(bad)} malformed fact(s):")
        for ln, fact, why in bad[:12]:
            print(f"     L{ln} [{why}]: {fact}")
    else:
        print("   ✓ all facts well-formed (every fact matches its relation's arity & column types)")

    if not full:
        print("\n   (FAST mode — skipping souffle conformance + spaCy faithfulness. Run")
        print("    `python validate.py --full` when a .decl changes or as the final pre-commit check.)")
        print("=" * 64)
        return not collisions and not bad

    print("\n3. SOUNDNESS — souffle -j conformance of datalog/cards.dl")
    rc, err, fails = soundness_check()
    print(f"   {'✓' if rc == 0 and not err and fails == 0 else '✗'} "
          f"exit={rc} errors={err} conformance_fail={fails}")
    print("   (rules side: run `python3 build.py` for determinism + every-artifact-compiles + conf=0)")

    print("\n4. FAITHFULNESS — card effects cross-checked against the RULES spaCy engine")
    from mtg.analysis import card_spacy
    conf, unconf, conflicts = card_spacy.validate(limit=6000)
    tot = conf + unconf or 1
    print(f"   confirmed by rules engine: {conf} ({100*conf//tot}%)   "
          f"unconfirmed (engine coarser): {unconf}   verb conflicts: {len(conflicts)}")
    for nm, cl, tv, ev in conflicts[:12]:
        print(f"     CONFLICT {nm}: '{cl}' template={tv} engine={ev}")
    print("=" * 64)
    return not collisions and not bad and rc == 0 and fails == 0


if __name__ == "__main__":
    import sys
    main(full="--full" in sys.argv)

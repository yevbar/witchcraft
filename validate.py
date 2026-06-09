"""validate.py — ONE validator for the whole pipeline: rules AND cards, in the same place.

Three checks, so that interpreting cards can never silently change the game's rules:

  1. ISOLATION — no card relation shares a name with a rules relation (excluding the per-file
     conformance scaffolding). This is what guarantees cards compose with the rules engine without
     overwriting it: a card fact lands in a card_* relation, never in `effect`/`ability`/etc. that the
     rules engine reasons over. (Cards that "change the rules" — max hand size, extra lands, can't-gain-
     life — are recorded as card_static_player / card_static and only apply WHEN that card is in play;
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
    card = _decls(_DL / "cards.dl") - _SCAFFOLD
    rules = set()
    for f in _DL.glob("*.dl"):
        if f.name != "cards.dl":
            rules |= _decls(f)
    collisions = (card & rules) - _SCAFFOLD
    return collisions, len(card), len(rules)


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


def main():
    print("=" * 64)
    print("UNIFIED PIPELINE VALIDATION (rules + cards)")
    print("=" * 64)

    collisions, ncard, nrule = isolation_check()
    print(f"\n1. ISOLATION — card relations: {ncard}, rules relations: {nrule}")
    if collisions:
        print(f"   ✗ COLLISIONS (cards could overwrite rules!): {sorted(collisions)}")
    else:
        print("   ✓ no card relation collides with a rules relation — cards can't mutate the rules")

    print("\n2. SOUNDNESS — souffle compile + conformance of datalog/cards.dl")
    rc, err, fails = soundness_check()
    print(f"   {'✓' if rc == 0 and not err and fails == 0 else '✗'} "
          f"exit={rc} errors={err} conformance_fail={fails}")
    print("   (rules side: run `python3 build.py` for determinism + every-artifact-compiles + conf=0)")

    print("\n3. FAITHFULNESS — card effects cross-checked against the RULES spaCy engine")
    import card_spacy
    conf, unconf, conflicts = card_spacy.validate(limit=6000)
    tot = conf + unconf or 1
    print(f"   confirmed by rules engine: {conf} ({100*conf//tot}%)   "
          f"unconfirmed (engine coarser): {unconf}   verb conflicts: {len(conflicts)}")
    for nm, cl, tv, ev in conflicts[:12]:
        print(f"     CONFLICT {nm}: '{cl}' template={tv} engine={ev}")
    print("=" * 64)


if __name__ == "__main__":
    main()

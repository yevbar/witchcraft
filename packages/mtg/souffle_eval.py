"""souffle_eval.py — evaluate a state against a rules PROGRAM for tests that need a relation the committed
engine doesn't `.output` (e.g. trigger_effect / spell_effect).

Prefers the COMPILED native binary (engine_native) over the souffle interpreter: the interpreter's
compiled mode is broken on some installs (it aborts with SIGABRT on larger programs), so routing through
the native binary makes these checks toolchain-independent. A handful of cards trip an unrelated §122
`to_number("X")` that aborts EITHER souffle backend — `eval_state` returns None for those so the caller
can skip+count them rather than crash (faithful: don't assert over an input the backend can't evaluate).
"""

from __future__ import annotations

import csv
import os
import subprocess
import tempfile
from pathlib import Path

from mtg import engine_native


def eval_state(rules: str, state: dict) -> dict | None:
    """{relation: set(tuples)} for every output of `rules` on `state`. Uses the native binary when one
    can be built, else the souffle interpreter. Returns None if the chosen backend ABORTS on this state."""
    fkey = frozenset((rel, frozenset(rows)) for rel, rows in state.items() if rows)
    if not os.environ.get("MTG_NO_NATIVE") and engine_native.available():
        try:
            return engine_native.evaluate_program(rules, fkey)
        except subprocess.CalledProcessError:
            return None
    from mtg.driver import _lit                                    # interpreter fallback (no native toolchain)
    facts = "\n".join(f"{rel}({', '.join(map(_lit, row))})." for rel, rows in state.items() for row in rows)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "e.dl").write_text(rules + "\n" + facts)
        try:
            subprocess.run(["souffle", f"{d}/e.dl", "-D", d], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            return None
        return {f.stem: {tuple(r) for r in csv.reader(f.open(), delimiter="\t")}
                for f in Path(d).glob("*.csv")}

"""test_no_interpreter_import.py — the mtg ENGINE DRIVER must import zero interpreter Python.

mtg drives the Datalog/souffle build (the way python drives a stockfish binary); the English->Datalog
`interpreter` PRODUCES that build and is a separate package. This guard fails if any module under the mtg
package imports `interpreter`, so the decoupling can't silently regress. mtg.analysis is EXEMPT on purpose:
those are interpreter-BASED analysis tools (card_spacy/card_synergy transpile cards), shipped as an
optional extra, never imported by the core (mtg/__init__ is lazy).
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ast

_MTG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # packages/mtg/


def _imports_interpreter(path: str) -> list[str]:
    """Return the interpreter import lines in a module (empty if none)."""
    hits = []
    tree = ast.parse(open(path, encoding="utf-8").read())
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "interpreter" or a.name.startswith("interpreter."):
                    hits.append(f"L{n.lineno}: import {a.name}")
        elif isinstance(n, ast.ImportFrom):
            if n.module and (n.module == "interpreter" or n.module.startswith("interpreter.")):
                hits.append(f"L{n.lineno}: from {n.module} import ...")
    return hits


def run() -> None:
    offenders = {}
    for dp, dn, fn in os.walk(_MTG):
        if "analysis" in dp.split(os.sep) or "__pycache__" in dp or "tests" in dp.split(os.sep):
            continue                                          # analysis = interpreter-based extra; tests may import it
        for f in fn:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                hits = _imports_interpreter(p)
                if hits:
                    offenders[os.path.relpath(p, _MTG)] = hits
    if offenders:
        for mod, hits in sorted(offenders.items()):
            print(f"  FAIL {mod}: {hits}")
        raise SystemExit(f"{len(offenders)} mtg driver module(s) import the interpreter package")
    print("ok   the mtg engine driver imports zero interpreter Python (mtg.analysis exempt)")
    print("\n1/1 checks passed")


if __name__ == "__main__":
    run()
